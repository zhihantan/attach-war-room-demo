"""Demo self-instrumentation (Tier 3.5).

Best-effort milestone pings to a Lakebase demo_events table so the SA org can see
which demos actually completed the diagnose->ship loop, by whom, for which account.
Never raises — telemetry must not break a live demo.
"""
import json

import dbx

log = dbx.logging.getLogger("telemetry")


def event(name, detail=None, conversation_id=None):
    if dbx.OFFLINE:
        return {"logged": False, "offline": True}
    try:
        with dbx.cursor() as cur:
            cur.execute(
                "INSERT INTO demo_events (event_name, detail, conversation_id) VALUES (%s,%s,%s)",
                (name, json.dumps(detail, default=str) if detail is not None else None, conversation_id))
        return {"logged": True}
    except Exception as e:  # table may not exist yet on older deployments — never fatal
        log.warning("telemetry event %s failed: %s", name, e)
        return {"logged": False, "error": str(e)[:120]}
