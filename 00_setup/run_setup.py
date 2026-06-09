#!/usr/bin/env python3
"""Attach War-Room — data foundation runner.

Executes 00_setup/generate_data.sql against the serverless SQL warehouse,
statement by statement, then prints validation metrics (row counts, attach rate,
loss ratio, and the three planted funnel anomalies).

Idempotent: re-running fully rebuilds the tables (CREATE OR REPLACE + re-add FKs).

Run:
    uv run --with databricks-sdk 00_setup/run_setup.py
"""
import os
import sys
import time

from databricks.sdk import WorkspaceClient

# --- config (mirrors ../config.yaml) ---------------------------------------
PROFILE = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "")
SCHEMA_FQN = os.environ.get("SCHEMA_FQN", "main.attach_war_room")
SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_data.sql")

WAIT = "50s"
POLL_SECONDS = 2
MAX_WAIT_SECONDS = 900


def run_sql(w, stmt, label=""):
    """Execute one statement, polling until terminal. Returns the response."""
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=stmt, wait_timeout=WAIT
    )
    waited = 0
    while True:
        state = resp.status.state
        name = getattr(state, "value", None) or getattr(state, "name", None) or str(state)
        if name in ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED"):
            break
        if waited >= MAX_WAIT_SECONDS:
            raise TimeoutError(f"Statement timed out after {waited}s: {label}")
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        resp = w.statement_execution.get_statement(resp.statement_id)
    if name != "SUCCEEDED":
        err = resp.status.error
        msg = f"{err.error_code}: {err.message}" if err else "(no error detail)"
        raise RuntimeError(f"Statement FAILED [{label}]: {msg}\n--- SQL ---\n{stmt[:800]}")
    return resp


def fetch_rows(resp):
    cols = [c.name for c in resp.manifest.schema.columns]
    data = resp.result.data_array if (resp.result and resp.result.data_array) else []
    return cols, data


def main():
    with open(SQL_PATH) as f:
        raw = f.read().replace("{{S}}", SCHEMA_FQN)

    # split on the "-- @@" delimiter; keep blocks that contain at least one
    # non-empty, non-comment line (drops the pure-comment header block)
    statements = [s for s in (b.strip() for b in raw.split("-- @@")) if s and any(
        line.strip() and not line.strip().startswith("--") for line in s.splitlines())]

    print(f"Connecting (profile={PROFILE}) ...")
    w = WorkspaceClient(profile=PROFILE)
    print(f"Executing {len(statements)} statements against warehouse {WAREHOUSE_ID}\n")

    for i, stmt in enumerate(statements, 1):
        first = next((ln.strip() for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")), "")
        label = first[:72]
        t0 = time.time()
        run_sql(w, stmt, label)
        print(f"  [{i:>2}/{len(statements)}] OK  ({time.time()-t0:4.1f}s)  {label}")

    print("\n=== VALIDATION ===")
    checks = [
        ("Row counts", f"""
            SELECT 'markets' t, count(*) n FROM {SCHEMA_FQN}.markets
            UNION ALL SELECT 'partners', count(*) FROM {SCHEMA_FQN}.partners
            UNION ALL SELECT 'products', count(*) FROM {SCHEMA_FQN}.products
            UNION ALL SELECT 'fx_rates', count(*) FROM {SCHEMA_FQN}.fx_rates
            UNION ALL SELECT 'sessions', count(*) FROM {SCHEMA_FQN}.sessions
            UNION ALL SELECT 'policies', count(*) FROM {SCHEMA_FQN}.policies
            UNION ALL SELECT 'claims', count(*) FROM {SCHEMA_FQN}.claims
            UNION ALL SELECT 'offer_config', count(*) FROM {SCHEMA_FQN}.offer_config
            UNION ALL SELECT 'offer_config_audit', count(*) FROM {SCHEMA_FQN}.offer_config_audit
            ORDER BY t"""),
        ("Blended KPIs (last 90d)", f"""
            SELECT
              round(100.0*sum(case when bound then 1 else 0 end)/count(*),2) AS attach_rate_pct,
              round(100.0*sum(case when quote_completed then 1 else 0 end)/nullif(sum(case when quote_started then 1 else 0 end),0),2) AS quote_to_complete_pct,
              round(sum(case when bound then premium_usd else 0 end),0) AS gwp_usd
            FROM {SCHEMA_FQN}.sessions
            WHERE session_date >= date_add(DATE'2026-06-03',-90)"""),
        ("Blended loss ratio", f"""
            SELECT round(100.0 * (SELECT sum(claim_amount_usd) FROM {SCHEMA_FQN}.claims WHERE status<>'denied')
                         / (SELECT sum(gwp_usd) FROM {SCHEMA_FQN}.policies WHERE status='active'), 1) AS loss_ratio_pct"""),
        ("Loss-ratio trap (budget x KE)", f"""
            SELECT round(100.0 *
                (SELECT sum(claim_amount_usd) FROM {SCHEMA_FQN}.claims   WHERE device_tier='budget' AND market_id='KE' AND status<>'denied')
                / (SELECT sum(gwp_usd)        FROM {SCHEMA_FQN}.policies WHERE device_tier='budget' AND market_id='KE' AND status='active'), 1) AS trap_loss_ratio_pct"""),
        ("Anomaly #1 Velora Telecom (P01) IT mid-tier offer_shown rate", f"""
            SELECT case when session_date >= date_add(DATE'2026-06-03',-45) then 'last_45d' else 'prior' end period,
                   round(100.0*avg(case when offer_shown then 1 else 0 end),1) AS offer_shown_pct,
                   round(100.0*avg(case when bound then 1 else 0 end),1) AS attach_pct, count(*) n
            FROM {SCHEMA_FQN}.sessions WHERE partner_id='P01' AND device_tier='mid'
            GROUP BY 1 ORDER BY 1"""),
        ("Anomaly #2 Siam Mobile Care (P02) TH bind rate", f"""
            SELECT case when session_date >= date_add(DATE'2026-06-03',-30) then 'last_30d' else 'prior' end period,
                   round(100.0*sum(case when bound then 1 else 0 end)/nullif(sum(case when quote_completed then 1 else 0 end),0),1) AS complete_to_bind_pct, count(*) n
            FROM {SCHEMA_FQN}.sessions WHERE partner_id='P02'
            GROUP BY 1 ORDER BY 1"""),
        ("Anomaly #3 Rift Valley Bank (P08) KE activation rate", f"""
            SELECT case when session_date >= date_add(DATE'2026-06-03',-25) then 'last_25d' else 'prior' end period,
                   round(100.0*sum(case when activated then 1 else 0 end)/nullif(sum(case when bound then 1 else 0 end),0),1) AS bind_to_activation_pct, count(*) n
            FROM {SCHEMA_FQN}.sessions WHERE partner_id='P08'
            GROUP BY 1 ORDER BY 1"""),
    ]
    for title, q in checks:
        resp = run_sql(w, q, title)
        cols, data = fetch_rows(resp)
        print(f"\n• {title}")
        print("   " + " | ".join(cols))
        for row in data:
            print("   " + " | ".join("" if v is None else str(v) for v in row))

    print("\nDone.")


if __name__ == "__main__":
    main()
