#!/usr/bin/env python3
"""Ask the Attach War-Room Genie space a question and print the generated SQL,
any narrative text, and the first result rows. Used to validate the space and
to run EVAL benchmark prompts.

Run:
    uv run --with databricks-sdk 01_metric_views_and_genie/ask_genie.py "Rank partners by attach rate over the last 30 days" <space_id>
"""
import os
import sys
import time

from databricks.sdk import WorkspaceClient

PROFILE = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")


def main():
    question = sys.argv[1]
    space_id = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("GENIE_SPACE_ID", "")
    if not space_id:
        try:
            space_id = open("/tmp/awr_space_id.txt").read().strip()
        except OSError:
            sys.exit("provide space_id as 2nd arg or GENIE_SPACE_ID")

    w = WorkspaceClient(profile=PROFILE)
    g = w.genie
    print(f"Q: {question}\n(space {space_id}) ...")

    # Start conversation and wait for the message to reach a terminal state.
    if hasattr(g, "start_conversation_and_wait"):
        msg = g.start_conversation_and_wait(space_id, question)
    else:
        started = g.start_conversation(space_id, question)
        conv_id = getattr(started, "conversation_id", None) or started.conversation.id
        msg_id = getattr(started, "message_id", None) or started.message.id
        for _ in range(60):
            msg = g.get_message(space_id, conv_id, msg_id)
            st = getattr(msg.status, "value", str(msg.status))
            if st in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(3)

    conv_id = msg.conversation_id
    msg_id = msg.id
    print(f"status: {getattr(msg.status, 'value', msg.status)}\n")

    for att in (msg.attachments or []):
        if getattr(att, "text", None) and att.text and att.text.content:
            print("NARRATIVE:\n" + att.text.content + "\n")
        if getattr(att, "query", None) and att.query:
            print("GENERATED SQL:\n" + (att.query.query or "") + "\n")
            try:
                qr = g.get_message_query_result(space_id, conv_id, msg_id)
                sr = qr.statement_response
                cols = [c.name for c in sr.manifest.schema.columns]
                print("RESULT: " + " | ".join(cols))
                for row in (sr.result.data_array or [])[:10]:
                    print("        " + " | ".join("" if v is None else str(v) for v in row))
            except Exception as e:  # noqa: BLE001
                print(f"(could not fetch result rows: {e})")


if __name__ == "__main__":
    main()
