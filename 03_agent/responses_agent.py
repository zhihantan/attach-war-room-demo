#!/usr/bin/env python3
"""MLflow ResponsesAgent wrapper for the Attach War-Room agent (Tier 2.3).

This is the *productionization* shim the agent README promises: it wraps the
EXISTING in-process tool-calling loop (agent.stream_turn / agent.run_turn) in an
``mlflow.pyfunc.ResponsesAgent`` so the same agent can be logged to MLflow,
registered in Unity Catalog, served on a Model Serving endpoint, and scored with
Agent Evaluation — without touching tools.py / dbx.py / agent.py.

Nothing about the agent's behavior changes here. We only translate between our
native event vocabulary (delta | tool_call | tool_result | final | notice | error,
see agent.stream_turn) and MLflow's ResponsesAgent schema:

  our event            ->  MLflow Responses item / event
  ------------------------------------------------------------------
  tool_call            ->  output item  type="function_call"
  tool_result          ->  output item  type="function_call_output"
  final / delta text   ->  output item  type="message" (assistant text)
  delta (stream)       ->  ResponsesAgentStreamEvent "response.output_text.delta"
  final (stream)       ->  ResponsesAgentStreamEvent "response.output_item.done"
  notice / error       ->  surfaced as a text item / raised

API reference (verify before changing — MLflow APIs move quickly):
  - ResponsesAgent authoring guide:
    https://docs.databricks.com/en/generative-ai/agent-framework/author-agent.html
  - mlflow.types.responses (Request/Response/StreamEvent dataclasses) + the
    create_* helper methods on the base class:
    https://mlflow.org/docs/latest/python_api/mlflow.types.html
  - ResponsesAgent is available in mlflow >= 2.18 (Databricks Agent Framework).

Requires: mlflow>=2.18, databricks-sdk, openai, psycopg2-binary (the agent's
runtime deps). The agent reads its config from env (SCHEMA_FQN, WAREHOUSE_ID,
GENIE_SPACE_ID, MODEL_AGENT, MODEL_CLASSIFIER, LAKEBASE_*, PG*, DEMO_OFFLINE) —
exactly as in 04_app/app.yaml. Set DEMO_OFFLINE=1 for a no-network smoke test.

CLI smoke (offline, no Databricks needed):
  DEMO_OFFLINE=1 uv run --with mlflow --with databricks-sdk --with openai \
      --with psycopg2-binary 03_agent/responses_agent.py
"""
import os
import sys
import uuid

# Make the sibling agent modules (agent.py, tools.py, dbx.py, profile.py,
# fixtures.py) importable both locally and when MLflow loads this file as the
# model's python_model with the same files passed via code_paths.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent  # noqa: E402  (our existing loop: stream_turn / run_turn)

from mlflow.pyfunc import ResponsesAgent  # noqa: E402
from mlflow.types.responses import (  # noqa: E402
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)


def _conversation_id(request: ResponsesAgentRequest) -> str:
    """Stable conversation id for Lakebase chat memory.

    Model Serving requests can carry a custom_inputs.conversation_id (the App
    threads one per chat). If absent we mint an ephemeral one — a served turn
    without a thread id simply doesn't reuse cross-session memory, which is the
    correct, safe default.
    """
    ci = getattr(request, "custom_inputs", None) or {}
    if isinstance(ci, dict) and ci.get("conversation_id"):
        return str(ci["conversation_id"])
    return f"mlflow-{uuid.uuid4().hex[:12]}"


def _latest_user_message(request: ResponsesAgentRequest) -> str:
    """The agent loop takes a single user turn + pulls prior turns from Lakebase
    itself (agent._history). So we forward only the last user input item; the
    rest of the Responses ``input`` list is reconstructed server-side from memory.
    """
    last = ""
    for item in (request.input or []):
        # input items are dicts (or objects) with role/content per the Responses schema
        role = item.get("role") if isinstance(item, dict) else getattr(item, "role", None)
        if role != "user":
            continue
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if isinstance(content, str):
            last = content
        elif isinstance(content, list):
            # content parts: [{"type":"input_text","text":"..."}, ...]
            texts = []
            for part in content:
                if isinstance(part, dict):
                    texts.append(part.get("text") or part.get("content") or "")
                else:
                    texts.append(getattr(part, "text", "") or "")
            last = "".join(t for t in texts if t)
    return last


class AttachWarRoomAgent(ResponsesAgent):
    """ResponsesAgent over the existing Attach War-Room tool-calling loop.

    predict()        -> one shot, collected via agent.run_turn (text + trace).
    predict_stream() -> live, iterating agent.stream_turn and re-emitting the
                        real event shapes as Responses stream events.
    """

    # -- non-streaming -------------------------------------------------------
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        conversation_id = _conversation_id(request)
        user_message = _latest_user_message(request)

        # run_turn collects the whole loop into {text, trace:[{tool,args,result}]}.
        result = agent.run_turn(conversation_id, user_message)

        output_items = []
        # Reasoning/tool trace as function_call + function_call_output items, so
        # Agent Evaluation and the MLflow trace UI show the tools the agent ran
        # (this is the "reasoning/items" the demo wants to expose).
        for step in result.get("trace", []):
            call_id = f"call_{uuid.uuid4().hex[:12]}"
            output_items.append(
                self.create_function_call_item(
                    id=f"fc_{uuid.uuid4().hex[:12]}",
                    call_id=call_id,
                    name=step.get("tool", "unknown"),
                    arguments=_json(step.get("args", {})),
                )
            )
            if "result" in step:
                output_items.append(
                    self.create_function_call_output_item(
                        call_id=call_id,
                        output=_json(step.get("result")),
                    )
                )

        # Final assistant message.
        output_items.append(
            self.create_text_output_item(
                text=result.get("text", ""),
                id=f"msg_{uuid.uuid4().hex[:12]}",
            )
        )
        return ResponsesAgentResponse(output=output_items, custom_outputs={"conversation_id": conversation_id})

    # -- streaming -----------------------------------------------------------
    def predict_stream(self, request: ResponsesAgentRequest):
        conversation_id = _conversation_id(request)
        user_message = _latest_user_message(request)

        # One streamed assistant message accumulates deltas; tool steps are
        # emitted as completed output items in between.
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        final_text_parts = []
        emitted_final_item = False

        for ev in agent.stream_turn(conversation_id, user_message):
            etype = ev.get("type")

            if etype == "delta":
                # token-by-token assistant text -> output_text.delta
                final_text_parts.append(ev.get("content", ""))
                yield ResponsesAgentStreamEvent(
                    **self.create_text_delta(delta=ev.get("content", ""), item_id=msg_id)
                )

            elif etype == "tool_call":
                call_id = f"call_{uuid.uuid4().hex[:12]}"
                # remember the call_id so the matching tool_result can reference it
                ev["_call_id"] = call_id
                _LAST_CALL_ID["id"] = call_id
                yield ResponsesAgentStreamEvent(
                    type="response.output_item.done",
                    item=self.create_function_call_item(
                        id=f"fc_{uuid.uuid4().hex[:12]}",
                        call_id=call_id,
                        name=ev.get("name", "unknown"),
                        arguments=_json(ev.get("args", {})),
                    ),
                )

            elif etype == "tool_result":
                yield ResponsesAgentStreamEvent(
                    type="response.output_item.done",
                    item=self.create_function_call_output_item(
                        call_id=_LAST_CALL_ID.get("id", f"call_{uuid.uuid4().hex[:12]}"),
                        output=_json(ev.get("result")),
                    ),
                )

            elif etype == "final":
                # Close out the assistant message. If we streamed deltas, the text
                # already accumulated; otherwise (non-streaming model fallback)
                # use the final payload directly.
                text = "".join(final_text_parts) or ev.get("content", "") or ""
                yield ResponsesAgentStreamEvent(
                    type="response.output_item.done",
                    item=self.create_text_output_item(text=text, id=msg_id),
                )
                emitted_final_item = True

            elif etype == "notice":
                # Soft, non-fatal signal (e.g. memory unavailable) — surface as a
                # short text item so it's visible in the trace without derailing.
                yield ResponsesAgentStreamEvent(
                    type="response.output_item.done",
                    item=self.create_text_output_item(
                        text=f"[notice] {ev.get('message', '')}",
                        id=f"msg_{uuid.uuid4().hex[:12]}",
                    ),
                )

            elif etype == "error":
                # Fail loud — the served endpoint should return an error, not a
                # silently-empty answer.
                raise RuntimeError(f"agent error: {ev.get('message')}")

        # Safety: if the loop ended without a 'final' event (shouldn't happen),
        # still emit whatever text we accumulated so the response is well-formed.
        if not emitted_final_item:
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=self.create_text_output_item(text="".join(final_text_parts), id=msg_id),
            )


# Track the most recent tool call_id so a tool_result item can reference it.
# (Our native events don't carry an id; the agent runs tools serially, so the
# last-seen call_id is the correct pairing.)
_LAST_CALL_ID = {"id": None}


def _json(obj) -> str:
    import json
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, default=str)


# MLflow needs a module-level model instance set via mlflow.models.set_model
# so log_model(python_model="responses_agent.py", ...) can load it.
from mlflow.models import set_model  # noqa: E402

AGENT = AttachWarRoomAgent()
set_model(AGENT)


if __name__ == "__main__":
    # Local smoke test. Use DEMO_OFFLINE=1 to run with zero Databricks access
    # (serves fixtures); otherwise needs a CLI profile + warehouse + Lakebase.
    import json

    question = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Why did attach rate drop for Velora Telecom mid-tier in Italy, and what should we do about it?"
    )
    req = ResponsesAgentRequest(input=[{"role": "user", "content": question}])

    print("=== predict_stream ===")
    for event in AGENT.predict_stream(req):
        # ResponsesAgentStreamEvent is a pydantic model; dump compactly.
        d = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        if d.get("type") == "response.output_text.delta":
            print(d.get("delta", ""), end="", flush=True)
        else:
            print(f"\n[{d.get('type')}] {json.dumps(d.get('item', {}), default=str)[:240]}")
    print("\n\n=== predict ===")
    resp = AGENT.predict(req)
    out = resp.model_dump() if hasattr(resp, "model_dump") else resp
    print(json.dumps(out, default=str, indent=2)[:2000])
