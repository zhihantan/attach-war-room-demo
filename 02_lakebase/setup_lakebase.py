#!/usr/bin/env python3
"""Set up the Attach War-Room Lakebase database:
  1. create database attach_war_room (if absent)
  2. apply schema.sql
  3. seed offer_config + alert_thresholds from the Delta golden copy
  4. validate (counts + the broken P01 (Velora Telecom) mid-tier impression row)

Idempotent. Run:
    uv run --with databricks-sdk --with psycopg2-binary 02_lakebase/setup_lakebase.py
"""
import json
import os
import subprocess
import sys

import psycopg2
from databricks.sdk import WorkspaceClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lakebase  # noqa: E402

PROFILE = lakebase.PROFILE
DATABASE = lakebase.DATABASE
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "")
DELTA_SCHEMA = os.environ.get("SCHEMA_FQN", "bolttech_workshop_demo.attach_war_room")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def ensure_database():
    host, user, token = lakebase.get_host(), lakebase.get_user(), lakebase.get_token()
    conn = psycopg2.connect(host=host, port=5432, dbname="postgres", user=user,
                            password=token, sslmode="require", connect_timeout=20)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{DATABASE}"')
            print(f"  created database {DATABASE}")
        else:
            print(f"  database {DATABASE} already exists")
    conn.close()


def apply_schema():
    with open(SCHEMA_SQL) as f:
        ddl = f.read()
    with lakebase.cursor() as cur:
        cur.execute(ddl)
    print("  schema applied")


def read_delta(sql):
    w = WorkspaceClient(profile=PROFILE)
    resp = w.statement_execution.execute_statement(warehouse_id=WAREHOUSE_ID, statement=sql, wait_timeout="50s")
    import time
    while getattr(resp.status.state, "value", str(resp.status.state)) in ("PENDING", "RUNNING"):
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)
    cols = [c.name for c in resp.manifest.schema.columns]
    return cols, (resp.result.data_array or [])


def seed_offer_config():
    cols, rows = read_delta(f"""
        SELECT config_id, partner_id, product_id, device_tier, market_id, impression_enabled,
               placement, deductible_tier_shown, price_band, eligibility_rule, version, is_current, updated_by
        FROM {DELTA_SCHEMA}.offer_config""")
    tobool = lambda v: str(v).lower() == "true"
    with lakebase.cursor() as cur:
        cur.execute("TRUNCATE offer_config")
        for r in rows:
            d = dict(zip(cols, r))
            cur.execute("""
                INSERT INTO offer_config (config_id, partner_id, product_id, device_tier, market_id,
                    impression_enabled, placement, deductible_tier_shown, price_band, eligibility_rule,
                    version, is_current, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (d["config_id"], d["partner_id"], d["product_id"], d["device_tier"], d["market_id"],
                 tobool(d["impression_enabled"]), d["placement"], d["deductible_tier_shown"], d["price_band"],
                 d["eligibility_rule"], int(d["version"]), tobool(d["is_current"]), d["updated_by"]))
    print(f"  seeded offer_config ({len(rows)} rows)")


def seed_thresholds():
    cols, rows = read_delta(f"SELECT partner_id FROM {DELTA_SCHEMA}.partners")
    with lakebase.cursor() as cur:
        for r in rows:
            cur.execute("""INSERT INTO alert_thresholds (partner_id, attach_drop_pct, loss_ratio_max)
                           VALUES (%s, 0.05, 0.70) ON CONFLICT (partner_id) DO NOTHING""", (r[0],))
    print(f"  seeded alert_thresholds ({len(rows)} partners)")


def validate():
    with lakebase.cursor(commit=False) as cur:
        for t in ("offer_config", "alert_thresholds", "scenarios", "chat_messages", "offer_config_audit"):
            cur.execute(f"SELECT count(*) FROM {t}")
            print(f"    {t:22s} {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM offer_config WHERE partner_id='P01' AND device_tier='mid' AND impression_enabled = false")
        print(f"    P01 (Velora Telecom) mid impressions OFF: {cur.fetchone()[0]} (expect 5 — the broken state)")


def main():
    print("Lakebase setup:")
    ensure_database()
    apply_schema()
    seed_offer_config()
    seed_thresholds()
    print("Validation:")
    validate()
    print("Done.")


if __name__ == "__main__":
    main()
