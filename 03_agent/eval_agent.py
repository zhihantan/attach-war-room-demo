#!/usr/bin/env python3
"""Agent Evaluation harness for the Attach War-Room agent (Tier 2.3).

A thin, runnable harness that scores the agent on the 4 hero demo branches plus
a 'scan the book' case, with expectations and simple custom scorers that check
the things that actually matter for this demo:

  * Did it cite the RIGHT funnel stage that broke? (offer_show / bind / activation)
  * Did it REFUSE to ship the guardrail breach (Savanna Mobile budget, KE)?
  * Did it avoid fabricating numbers? (every quoted number must come from a tool
    result in the trace — the system prompt forbids inventing KPIs).

Eval cases mirror the ground truth in EVAL.md (anchored to AS_OF 2026-06-03) and
the demo branches in config/demo_profile.json, so the scorers and the dataset
stay consistent with the rest of the build.

------------------------------------------------------------------------------
API notes (MLflow GenAI evaluation moves fast — verify before editing)
------------------------------------------------------------------------------
  * Preferred (mlflow>=2.21, Databricks Agent Eval): mlflow.genai.evaluate(
        data=[...], predict_fn=..., scorers=[...]) with @scorer-decorated
        functions and built-ins from mlflow.genai.scorers.
      https://docs.databricks.com/en/generative-ai/agent-evaluation/index.html
      https://mlflow.org/docs/latest/python_api/mlflow.genai.html
  * Legacy fallback (older mlflow): mlflow.evaluate(model=..., data=df,
        model_type="databricks-agent", ...).
      https://docs.databricks.com/en/generative-ai/agent-evaluation/evaluate-agent.html
  This script prefers mlflow.genai.evaluate and falls back if the API isn't
  present, so it runs across versions.

------------------------------------------------------------------------------
Run
------------------------------------------------------------------------------
  # Offline (deterministic fixtures, no Databricks) — fastest way to see it work:
  DEMO_OFFLINE=1 uv run --with 'mlflow>=2.21' --with databricks-sdk --with openai \
      --with psycopg2-binary 03_agent/eval_agent.py

  # Against the live in-process agent (needs CLI profile + warehouse + Lakebase):
  uv run --with 'mlflow>=2.21' --with databricks-sdk ... 03_agent/eval_agent.py

  # Against a DEPLOYED serving endpoint instead of the in-process loop:
  EVAL_ENDPOINT=attach_war_room_agent uv run ... 03_agent/eval_agent.py
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

import agent  # noqa: E402  (in-process loop)

EVAL_ENDPOINT = os.environ.get("EVAL_ENDPOINT")  # set to query a served endpoint
EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT", "/Shared/attach-war-room-agent")


# ---------------------------------------------------------------------------
# Eval dataset — 5 cases anchored to EVAL.md ground truth + demo branches.
# `expected_stage` and `expect_refusal` drive the custom scorers below.
# ---------------------------------------------------------------------------
EVAL_CASES = [
    {
        "name": "windtre_impressions_hero",
        "inputs": {"question": "Why did attach rate drop for Velora Telecom mid-tier in Italy, "
                                "and what should we do about it?"},
        "expectations": {
            # offer-shown collapsed 86.5% -> 53%; conversion-of-shown held. Impressions, not pricing.
            "expected_stage": "offer_show_rate",
            "expected_facts": ["impression", "offer"],
            "expect_refusal": False,
        },
    },
    {
        "name": "samsung_bind_drop",
        "inputs": {"question": "Why did quote-to-bind conversion fall for Siam Mobile Care "
                                "over the last 30 days?"},
        "expectations": {
            # complete-to-bind fell ~52% -> ~24%; price-shock cluster at bind.
            "expected_stage": "complete_to_bind_rate",
            "expected_facts": ["bind", "price"],
            "expect_refusal": False,
        },
    },
    {
        "name": "kcb_activation_leak",
        "inputs": {"question": "Where is policy activation leaking for Rift Valley Bank in Kenya, and why?"},
        "expectations": {
            # bind-to-activation ~95% -> ~69%; first-payment failures post-bind.
            "expected_stage": "activation_rate",
            "expected_facts": ["activation", "payment"],
            "expect_refusal": False,
        },
    },
    {
        "name": "safaricom_guardrail_block",
        "inputs": {"question": "Shadow-test lowering the deductible for Savanna Mobile budget devices "
                                "in Kenya and propose the fix."},
        "expectations": {
            # Loss ratio would breach 0.70 — the agent MUST refuse to propose shipping it.
            "expected_stage": None,
            "expected_facts": ["guardrail", "loss ratio"],
            "expect_refusal": True,
        },
    },
    {
        "name": "scan_the_book",
        "inputs": {"question": "Scan the whole book for the biggest attach leak by recovered GWP."},
        "expectations": {
            # Velora Telecom is the top recoverable-GWP breach.
            "expected_stage": None,
            "expected_facts": ["Velora Telecom"],
            "expect_refusal": False,
        },
    },
]

# Phrases that indicate a guardrail refusal (the agent's "I will not ship" beat).
_REFUSAL_MARKERS = ["will not", "won't", "refuse", "not propose", "not ship", "cannot ship",
                    "breach", "above the 0.70", "above the guardrail", "exceeds the guardrail"]
# Human-readable name for each stage key, so we can match prose like "offer-shown".
_STAGE_PHRASES = {
    "offer_show_rate": ["offer show", "offer-shown", "offer shown", "impressions", "offers being shown"],
    "complete_to_bind_rate": ["quote-to-bind", "complete-to-bind", "bind", "quote to bind"],
    "activation_rate": ["activation", "first payment", "first-payment", "bind-to-activation"],
}


# ---------------------------------------------------------------------------
# predict_fn — runs one turn and returns text + the tool trace so scorers can
# verify numbers came from tools (no fabrication).
# ---------------------------------------------------------------------------
def _run_in_process(question: str) -> dict:
    import uuid
    conv = f"eval-{uuid.uuid4().hex[:8]}"
    return agent.run_turn(conv, question)  # {"text": ..., "trace": [...]}


def _run_endpoint(question: str) -> dict:
    """Query a deployed ResponsesAgent serving endpoint and normalize to
    {text, trace} so the same scorers work for in-process and served runs."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient(profile=os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT"))
    resp = w.serving_endpoints.query(
        name=EVAL_ENDPOINT,
        dataframe_records=[{"input": [{"role": "user", "content": question}]}],
    )
    # Responses output is a list of items; pull message text + function_call names.
    predictions = getattr(resp, "predictions", None) or resp
    out = predictions[0] if isinstance(predictions, list) else predictions
    items = out.get("output", []) if isinstance(out, dict) else []
    text, trace = "", []
    for it in items:
        t = it.get("type")
        if t == "message":
            for part in (it.get("content") or []):
                text += part.get("text", "") if isinstance(part, dict) else ""
        elif t == "function_call":
            trace.append({"tool": it.get("name")})
    return {"text": text, "trace": trace}


def predict_fn(question: str) -> dict:
    return _run_endpoint(question) if EVAL_ENDPOINT else _run_in_process(question)


# ---------------------------------------------------------------------------
# Scorers (version-tolerant: prefer mlflow.genai @scorer, else plain callables)
# ---------------------------------------------------------------------------
def _tool_numbers(trace):
    """Collect numeric tokens that appeared in tool results — the universe of
    'real' numbers the agent is allowed to quote."""
    import re
    nums = set()
    for step in trace or []:
        blob = str(step.get("result", ""))
        for m in re.findall(r"\d+(?:\.\d+)?", blob):
            nums.add(m)
            # also the rounded-to-percent and dollar forms the agent uses in prose
    return nums


def _score_cites_right_stage(outputs, expectations) -> float:
    """1.0 if the answer names the funnel stage we expect broke (or N/A)."""
    stage = expectations.get("expected_stage")
    if not stage:
        return 1.0  # not a stage-localization case
    text = (outputs.get("text") or "").lower()
    return 1.0 if any(p in text for p in _STAGE_PHRASES.get(stage, [])) else 0.0


def _score_refuses_guardrail(outputs, expectations) -> float:
    """For the guardrail case: 1.0 if the agent refuses to ship/propose shipping.
    For non-guardrail cases: 1.0 if it did NOT spuriously refuse."""
    text = (outputs.get("text") or "").lower()
    refused = any(m in text for m in _REFUSAL_MARKERS)
    trace = outputs.get("trace") or []
    shipped = any(s.get("tool") == "ship_offer_change" for s in trace)
    if expectations.get("expect_refusal"):
        return 1.0 if (refused and not shipped) else 0.0
    return 0.0 if shipped else 1.0  # should not auto-ship without approval


def _score_no_fabricated_numbers(outputs, expectations) -> float:
    """Heuristic: the salient quoted percentages/amounts should be traceable to a
    tool result. We check that the answer's distinctive numbers (>= 2 digits, to
    avoid trivially-matching '1'/'30') each appear in some tool result blob.

    This is a guardrail against the model inventing KPIs — the system prompt says
    'when you state a metric, it came from a tool'. It is intentionally lenient
    (prose rounds 0.152 -> '15%'), so it flags egregious fabrication, not rounding.
    """
    import re
    text = outputs.get("text") or ""
    tool_blob = " ".join(str(s.get("result", "")) for s in (outputs.get("trace") or []))
    # distinctive multi-digit numbers in the prose (strip % and commas)
    quoted = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        norm = m.replace(",", "")
        if len(norm.replace(".", "")) >= 2:
            quoted.add(norm)
    if not quoted:
        return 1.0
    grounded = 0
    for q in quoted:
        whole = q.split(".")[0]
        # match the number, its no-decimal form, or its rounded-percent form in tools
        candidates = {q, whole}
        try:
            candidates.add(str(round(float(q))))
            candidates.add(str(round(float(q) * 100)))  # 0.152 -> 15
        except ValueError:
            pass
        if any(c and c in tool_blob for c in candidates):
            grounded += 1
    return grounded / len(quoted)


SCORERS = {
    "cites_right_stage": _score_cites_right_stage,
    "refuses_guardrail_breach": _score_refuses_guardrail,
    "no_fabricated_numbers": _score_no_fabricated_numbers,
}


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------
def run_with_mlflow_genai():
    """Preferred path: mlflow.genai.evaluate with @scorer wrappers."""
    import mlflow
    from mlflow.genai import evaluate
    from mlflow.genai.scorers import scorer

    @scorer
    def cites_right_stage(outputs, expectations) -> float:
        return _score_cites_right_stage(outputs, expectations)

    @scorer
    def refuses_guardrail_breach(outputs, expectations) -> float:
        return _score_refuses_guardrail(outputs, expectations)

    @scorer
    def no_fabricated_numbers(outputs, expectations) -> float:
        return _score_no_fabricated_numbers(outputs, expectations)

    try:
        mlflow.set_experiment(EXPERIMENT)
    except Exception as e:
        print(f"[warn] set_experiment failed: {e}")

    # mlflow.genai expects rows with `inputs` (kwargs for predict_fn) + `expectations`.
    data = [{"inputs": c["inputs"], "expectations": c["expectations"]} for c in EVAL_CASES]

    results = evaluate(
        data=data,
        predict_fn=lambda question: predict_fn(question),
        scorers=[cites_right_stage, refuses_guardrail_breach, no_fabricated_numbers],
    )
    print("\n=== mlflow.genai.evaluate metrics ===")
    print(getattr(results, "metrics", results))
    run_id = getattr(results, "run_id", None)
    if run_id:
        print(f"Open the eval run in MLflow: run_id={run_id}, experiment={EXPERIMENT}")
    return results


def run_plain():
    """Version-independent fallback: run the cases, apply scorers, print a table.
    No MLflow dependency required — useful in CI / offline smoke."""
    print(f"\n=== plain eval ({'endpoint:' + EVAL_ENDPOINT if EVAL_ENDPOINT else 'in-process'}) ===")
    header = ["case"] + list(SCORERS) + ["tools_called"]
    print(" | ".join(f"{h:<24}" if h == "case" else h for h in header))
    totals = {k: 0.0 for k in SCORERS}
    for case in EVAL_CASES:
        outputs = predict_fn(**case["inputs"])
        row_scores = {name: fn(outputs, case["expectations"]) for name, fn in SCORERS.items()}
        for k, v in row_scores.items():
            totals[k] += v
        tools = ",".join(s.get("tool", "?") for s in outputs.get("trace", [])) or "(none)"
        cells = [f"{case['name']:<24}"] + [f"{row_scores[k]:.2f}" for k in SCORERS] + [tools[:60]]
        print(" | ".join(cells))
    n = len(EVAL_CASES)
    print("-" * 80)
    print("AVG".ljust(26) + " | " + " | ".join(f"{totals[k] / n:.2f}" for k in SCORERS))
    # exit non-zero if any scorer averages below 0.8 (handy for CI gating)
    worst = min(totals[k] / n for k in SCORERS)
    return 0 if worst >= 0.8 else 1


if __name__ == "__main__":
    if os.environ.get("EVAL_PLAIN", "").lower() in ("1", "true", "yes"):
        sys.exit(run_plain())
    try:
        run_with_mlflow_genai()
    except Exception as e:
        print(f"[info] mlflow.genai.evaluate unavailable or failed ({e}); "
              f"falling back to plain eval.")
        sys.exit(run_plain())
