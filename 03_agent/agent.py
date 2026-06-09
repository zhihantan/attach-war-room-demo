#!/usr/bin/env python3
"""Attach War-Room agent — a tool-calling loop on Foundation Model APIs.

Runs a stateful diagnose -> shadow-test -> approve -> ship -> verify workflow.
FMAPI does the reasoning; tools hit the metric views, Lakebase, and Genie.
Conversation is persisted to Lakebase for continuity across sessions.

The backend imports `stream_turn` (yields events for SSE) / `run_turn` (collects).
Events: tool_call | tool_result | delta (streamed assistant text) | final | notice | error.
CLI:  uv run --with databricks-sdk --with openai --with psycopg2-binary 03_agent/agent.py "why did attach drop for Velora Telecom?"
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbx  # noqa: E402
import profile as P  # noqa: E402
import tools as T  # noqa: E402

log = dbx.logging.getLogger("agent")


def build_system_prompt(prof):
    persona = prof.get("persona", "conversion-growth lead")
    account = prof.get("account", {}).get("name", "the exchange")
    guardrail = prof.get("guardrail_loss_ratio", 0.70)
    return f"""You are the Attach War-Room agent for {account}, a global B2B2C insurtech running an embedded-insurance exchange. You help a {persona} diagnose and FIX attach-rate and conversion problems inside partner checkouts, across markets, partners, products and currencies.

Definitions: attach_rate = policies bound / eligible sessions. conversion_rate = bound / offers_shown. Funnel order: offer_shown -> quote_started -> quote_completed -> bound -> activated. GWP = gross written premium. The loss-ratio guardrail is {guardrail} — never recommend buying attach with underpriced cover.

Your workflow:
1. DIAGNOSE: localize the problem. If the user names a partner, use funnel_diagnosis to find which stage dropped (recent vs prior). If the user asks where the biggest problem is, or to "scan the book", use scan_for_anomalies to find the worst partner by recoverable GWP before drilling in. Use partner_funnel_overview to compare partners. Key insight: if attach fell but conversion_rate (bound/offers_shown) held while offer_show_rate fell, it is an IMPRESSIONS problem, not a pricing/conversion problem. For bind-stage drops, pull abandonment_reasons at the quote_completed stage and classify_abandonment. For activation drops, look at the bound stage.
2. CONFIRM ROOT CAUSE: use get_offer_config to check the live serving rules (e.g., impressions disabled), and loss_ratio_for to check risk.
3. SHADOW-TEST: call run_shadow_sim for the candidate fix (restore_impressions / restore_deductible / lower_price / fix_activation). Report projected attach delta, recovered GWP/month, and projected loss ratio vs the {guardrail} guardrail. The loss-ratio elasticity is derived from the claims book, not guessed.
4. PROPOSE: call propose_offer_change to save the scenario (status=proposed) and present it for approval. NEVER ship without explicit human approval. If a change breaches the guardrail, say so plainly and do not propose shipping it.
5. SHIP (only after the user explicitly approves): call ship_offer_change(scenario_id). This writes the live offer_config the checkout reads. Then state the recovered GWP and confirm what changed. If the user asks to undo a change, call rollback_offer_change.

If the user asks what has already been shipped or changed (or to recap), call recent_activity for the authoritative Lakebase state — do NOT infer shipped/not-shipped from the conversation, because approvals can be made via the UI Approve button outside this chat.

Be concise and commercially legible. Show the numbers that matter. Cite the stage that broke and why. When you state a metric, it came from a tool — never invent numbers. Round rates to whole percents in prose and money to USD."""


SYSTEM_PROMPT = build_system_prompt(P.get_profile())


def _history(conversation_id, limit=10):
    try:
        with dbx.cursor(commit=False) as cur:
            cur.execute("""SELECT role, content FROM chat_messages
                           WHERE conversation_id=%s AND role IN ('user','assistant') AND content IS NOT NULL
                           ORDER BY message_id DESC LIMIT %s""", (conversation_id, limit))
            rows = cur.fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]
    except Exception as e:
        log.warning("history load failed for %s: %s", conversation_id, e)
        return []


def _persist(conversation_id, role, content=None, tool_name=None):
    """Persist one message. Returns True on success — callers can surface failures
    instead of silently losing cross-session memory."""
    try:
        with dbx.cursor() as cur:
            cur.execute("INSERT INTO chat_messages (conversation_id, role, content, tool_name) VALUES (%s,%s,%s,%s)",
                        (conversation_id, role, content, tool_name))
        return True
    except Exception as e:
        log.warning("persist failed (%s/%s): %s", conversation_id, role, e)
        return False


def _call_tool(name, args):
    fn = T.TOOLS.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**args)
    except Exception as e:  # surface tool errors to the model
        log.warning("tool %s failed: %s", name, e)
        return {"error": f"{type(e).__name__}: {e}"}


def _model_step(messages):
    """Run one model step. Yields {'type':'delta'} for streamed assistant text, then a
    terminal {'type':'_step', 'text':..., 'tools':[...]}. Streams when supported, with a
    non-streaming fallback if the endpoint rejects streaming-with-tools."""
    stream = None
    try:
        stream = dbx.chat_stream(messages, tools=T.TOOL_SCHEMAS, tool_choice="auto", temperature=0, max_tokens=1200)
    except Exception as e:
        log.info("streaming unavailable (%s); falling back to non-streaming", e)
        stream = None
    if stream is not None:
        parts, frags = [], {}
        for chunk in stream:  # a mid-stream error propagates to the caller as an error event
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if getattr(d, "content", None):
                parts.append(d.content)
                yield {"type": "delta", "content": d.content}
            for tc in (getattr(d, "tool_calls", None) or []):
                slot = frags.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn:
                    if fn.name:
                        slot["name"] = fn.name
                    if fn.arguments:
                        slot["args"] += fn.arguments
        yield {"type": "_step", "text": "".join(parts), "tools": [frags[i] for i in sorted(frags)]}
        return
    # non-streaming fallback
    resp = dbx.chat(messages, tools=T.TOOL_SCHEMAS, tool_choice="auto", temperature=0, max_tokens=1200)
    m = resp.choices[0].message
    if m.content:
        yield {"type": "delta", "content": m.content}
    ordered = [{"id": tc.id, "name": tc.function.name, "args": tc.function.arguments} for tc in (m.tool_calls or [])]
    yield {"type": "_step", "text": m.content or "", "tools": ordered}


def stream_turn(conversation_id, user_message, max_steps=8):
    """Yield events: delta | tool_call | tool_result | final | notice | error."""
    if dbx.OFFLINE:
        import fixtures
        yield from fixtures.stream_turn(conversation_id, user_message)
        return

    if not _persist(conversation_id, "user", user_message):
        yield {"type": "notice", "message": "memory unavailable — continuing without cross-session continuity"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + _history(conversation_id) + \
               [{"role": "user", "content": user_message}]
    final_text = None
    for _ in range(max_steps):
        step = None
        try:
            for ev in _model_step(messages):
                if ev["type"] == "_step":
                    step = ev
                else:
                    yield ev
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return
        ordered = step["tools"]
        if not ordered:
            final_text = step["text"]
            yield {"type": "final", "content": final_text}
            break
        messages.append({"role": "assistant", "content": step["text"],
                         "tool_calls": [{"id": tc["id"] or f"call_{i}", "type": "function",
                                         "function": {"name": tc["name"], "arguments": tc["args"] or "{}"}}
                                        for i, tc in enumerate(ordered)]})
        for i, tc in enumerate(ordered):
            name, cid = tc["name"], (tc["id"] or f"call_{i}")
            try:
                args = json.loads(tc["args"] or "{}")
            except Exception:
                args = {}
            yield {"type": "tool_call", "name": name, "args": args}
            result = _call_tool(name, args)
            yield {"type": "tool_result", "name": name, "result": result}
            _persist(conversation_id, "tool", content=json.dumps(result, default=str)[:4000], tool_name=name)
            messages.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(result, default=str)})
    if final_text is None:
        # safety: stream a wrap-up if we hit the step cap
        try:
            parts = []
            for chunk in dbx.chat_stream(messages + [{"role": "user", "content": "Summarize your findings and recommendation now."}],
                                         temperature=0, max_tokens=600):
                if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
                    parts.append(chunk.choices[0].delta.content)
                    yield {"type": "delta", "content": chunk.choices[0].delta.content}
            final_text = "".join(parts)
            yield {"type": "final", "content": final_text}
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return
    _persist(conversation_id, "assistant", final_text)


def run_turn(conversation_id, user_message, max_steps=8):
    """Collect a full turn into {text, trace}."""
    text, trace = "", []
    for ev in stream_turn(conversation_id, user_message, max_steps):
        if ev["type"] == "tool_call":
            trace.append({"tool": ev["name"], "args": ev["args"]})
        elif ev["type"] == "tool_result":
            if trace:
                trace[-1]["result"] = ev["result"]
        elif ev["type"] == "final":
            text = ev["content"]
        elif ev["type"] == "error":
            text = f"[error] {ev['message']}"
    return {"text": text, "trace": trace}


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Why did attach rate drop for Velora Telecom, and what should we do about it?"
    conv = sys.argv[2] if len(sys.argv) > 2 else "cli-demo"
    for ev in stream_turn(conv, q):
        if ev["type"] == "tool_call":
            print(f"\n🔧 {ev['name']}({json.dumps(ev['args'])})")
        elif ev["type"] == "tool_result":
            r = json.dumps(ev["result"], default=str)
            print(f"   → {r[:300]}{'…' if len(r) > 300 else ''}")
        elif ev["type"] == "delta":
            print(ev["content"], end="", flush=True)
        elif ev["type"] == "final":
            if not ev["content"]:
                continue
        elif ev["type"] == "notice":
            print(f"\n⚠️  {ev['message']}")
        elif ev["type"] == "error":
            print(f"\n❌ {ev['message']}")
    print()
