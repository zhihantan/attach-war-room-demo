#!/usr/bin/env python3
"""Provision Attach War-Room Lakebase on a PROVISIONED instance (attach-war-room-db).

Provisioned tier is used for the deployed Databricks App because it supports the
DatabaseInstanceRole API (register a service principal as a Postgres role for OAuth)
and app resource binding — Autoscaling projects don't expose those yet (Beta).

Steps: register instance roles (owner + app SP) -> create DB -> apply schema ->
seed offer_config + thresholds from the Delta golden copy -> validate.

Run:
    uv run --with databricks-sdk --with psycopg2-binary 02_lakebase/setup_provisioned.py
"""
import os
import sys
import time

import psycopg2
import databricks.sdk.service.database as d

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03_agent"))
import dbx  # noqa: E402  (provisioned-aware)

INSTANCE = dbx.LAKEBASE_INSTANCE
DB = dbx.LAKEBASE_DATABASE
APP_SP = os.environ.get("APP_SP_CLIENT_ID", "<app-service-principal>")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
w = dbx.ws()


def ensure_role(name, identity_type):
    try:
        w.database.create_database_instance_role(INSTANCE, d.DatabaseInstanceRole(
            name=name, identity_type=identity_type,
            membership_role=d.DatabaseInstanceRoleMembershipRole.DATABRICKS_SUPERUSER))
        print(f"  registered instance role {name} ({identity_type.value})")
    except Exception as e:
        msg = str(e)
        print(f"  role {name}: {'already exists' if 'exist' in msg.lower() or 'already' in msg.lower() else msg[:140]}")


def connect(db):
    return psycopg2.connect(host=dbx._pg_host(), port=5432, dbname=db, user=dbx._pg_user(),
                            password=dbx._pg_token(), sslmode="require", connect_timeout=25)


def main():
    me = w.current_user.me().user_name
    print("Registering instance roles:")
    ensure_role(me, d.DatabaseInstanceRoleIdentityType.USER)
    ensure_role(APP_SP, d.DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL)
    time.sleep(4)

    print("Creating database:")
    c = connect("databricks_postgres"); c.autocommit = True
    with c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{DB}"'); print(f"  created {DB}")
        else:
            print(f"  {DB} exists")
    c.close()

    print("Applying schema + grants:")
    c = connect(DB)
    with c.cursor() as cur:
        cur.execute(open(SCHEMA_SQL).read())
        for stmt in [
            f'GRANT USAGE ON SCHEMA public TO "{APP_SP}"',
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{APP_SP}"',
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{APP_SP}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{APP_SP}"',
        ]:
            try:
                cur.execute(stmt)
            except Exception as e:
                print("  grant skip:", str(e)[:90])
    c.commit()
    print("  schema applied + SP granted")

    print("Seeding from Delta:")
    oc = dbx.warehouse_query(f"""SELECT config_id, partner_id, product_id, device_tier, market_id,
        impression_enabled, placement, deductible_tier_shown, price_band, eligibility_rule, version, is_current, updated_by
        FROM {dbx.SCHEMA}.offer_config""")
    with c.cursor() as cur:
        cur.execute("TRUNCATE offer_config")
        for r in oc:
            cur.execute("""INSERT INTO offer_config (config_id, partner_id, product_id, device_tier, market_id,
                impression_enabled, placement, deductible_tier_shown, price_band, eligibility_rule, version, is_current, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (r["config_id"], r["partner_id"], r["product_id"], r["device_tier"], r["market_id"],
                 bool(r["impression_enabled"]), r["placement"], r["deductible_tier_shown"], r["price_band"],
                 r["eligibility_rule"], int(r["version"]), bool(r["is_current"]), r["updated_by"]))
        for p in dbx.warehouse_query(f"SELECT partner_id FROM {dbx.SCHEMA}.partners"):
            cur.execute("INSERT INTO alert_thresholds (partner_id, attach_drop_pct, loss_ratio_max) VALUES (%s,0.05,0.70) ON CONFLICT (partner_id) DO NOTHING", (p["partner_id"],))
    c.commit()
    print(f"  seeded offer_config ({len(oc)} rows) + alert_thresholds")

    print("Validation:")
    with c.cursor() as cur:
        for t in ("offer_config", "alert_thresholds", "scenarios", "chat_messages", "offer_config_audit"):
            cur.execute(f"SELECT count(*) FROM {t}")
            print(f"    {t:22s} {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM offer_config WHERE partner_id='P01' AND device_tier='mid' AND impression_enabled=false")
        print(f"    P01 (Velora Telecom) mid impressions OFF: {cur.fetchone()[0]} (expect 5)")
    c.close()
    print("Done.")


if __name__ == "__main__":
    main()
