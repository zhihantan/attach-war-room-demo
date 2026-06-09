#!/usr/bin/env python3
"""Generic SQL runner — executes a {{S}}-templated .sql file on the warehouse.

Statements are separated by a line containing only `-- @@`. Prints OK/timing per
statement and any returned rows. Idempotent if the SQL is (CREATE OR REPLACE).

Run:
    uv run --with databricks-sdk 01_metric_views_and_genie/run_sql.py 01_metric_views_and_genie/metric_views.sql
"""
import os
import sys
import time

from databricks.sdk import WorkspaceClient

PROFILE = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "")
SCHEMA_FQN = os.environ.get("SCHEMA_FQN", "bolttech_workshop_demo.attach_war_room")
WAIT, POLL, MAX_WAIT = "50s", 2, 900


def run_sql(w, stmt, label=""):
    resp = w.statement_execution.execute_statement(warehouse_id=WAREHOUSE_ID, statement=stmt, wait_timeout=WAIT)
    waited = 0
    while True:
        name = getattr(resp.status.state, "value", None) or getattr(resp.status.state, "name", None) or str(resp.status.state)
        if name in ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED"):
            break
        if waited >= MAX_WAIT:
            raise TimeoutError(f"timeout: {label}")
        time.sleep(POLL)
        waited += POLL
        resp = w.statement_execution.get_statement(resp.statement_id)
    if name != "SUCCEEDED":
        err = resp.status.error
        msg = f"{err.error_code}: {err.message}" if err else "(no detail)"
        raise RuntimeError(f"FAILED [{label}]: {msg}\n--- SQL ---\n{stmt[:1200]}")
    return resp


def main():
    sql_path = sys.argv[1]
    with open(sql_path) as f:
        raw = f.read().replace("{{S}}", SCHEMA_FQN)
    statements = [s for s in (b.strip() for b in raw.split("-- @@")) if s and any(
        ln.strip() and not ln.strip().startswith("--") for ln in s.splitlines())]

    w = WorkspaceClient(profile=PROFILE)
    print(f"Running {len(statements)} statements from {sql_path}\n")
    for i, stmt in enumerate(statements, 1):
        first = next((ln.strip() for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")), "")
        t0 = time.time()
        resp = run_sql(w, stmt, first[:72])
        print(f"  [{i:>2}/{len(statements)}] OK ({time.time()-t0:4.1f}s)  {first[:72]}")
        if resp.result and resp.result.data_array:
            cols = [c.name for c in resp.manifest.schema.columns]
            print("        " + " | ".join(cols))
            for row in resp.result.data_array[:50]:
                print("        " + " | ".join("" if v is None else str(v) for v in row))
    print("\nDone.")


if __name__ == "__main__":
    main()
