# 02 — Lakebase (Autoscaling Postgres)

Lakebase is **load-bearing two ways**: (1) low-latency **serving** of `offer_config` that the simulated partner checkout reads, and (2) the app/agent **state** layer (audit, thresholds, scenarios, chat memory) with **multi-table ACID writes**.

## Resource
Autoscaling project `attach-war-room` → branch `production` → endpoint `primary` (AWS us-east-1), database `attach_war_room`.

## Files
| File | Purpose |
|---|---|
| `schema.sql` | Postgres DDL: `offer_config` (serving) + `offer_config_audit`, `alert_thresholds`, `scenarios`, `chat_messages` (state) |
| `lakebase.py` | Connection helper (`get_connection`, `cursor`); CLI/profile locally, `PG*` env in the deployed app |
| `setup_lakebase.py` | Create DB → apply schema → seed `offer_config`/`alert_thresholds` from Delta → validate |

## Setup
```bash
# project (one-time): databricks postgres create-project attach-war-room --json '{"spec":{"display_name":"Acme Attach War-Room"}}' -p DEFAULT
uv run --with databricks-sdk --with psycopg2-binary 02_lakebase/setup_lakebase.py
```

## Connection pattern
OAuth token auth (token ~1h TTL → fetch per unit of work):
```
host  = databricks postgres list-endpoints projects/attach-war-room/branches/production -> status.hosts.host
token = databricks postgres generate-database-credential projects/.../endpoints/primary -> token
user  = workspace email (databricks current-user me -> userName)
psycopg2.connect(host, 5432, dbname=attach_war_room, user=email, password=token, sslmode=require)
```
The deployed Databricks App injects `PGHOST/PGUSER/PGPASSWORD` via its Lakebase resource binding; `lakebase.py` prefers those and skips the CLI.

## Delta → Postgres sync
`setup_lakebase.py` does a **snapshot sync** (reads the Delta golden `offer_config` and loads it into Postgres) — enough to demonstrate the serving pattern and keep the demo deterministic. **Prod path:** a Lakebase **synced table** (continuous/triggered reverse-ETL) keeps the serving copy fresh automatically.

## The ACID write (agent's `propose_offer_change` on approval)
One transaction: `UPDATE offer_config SET impression_enabled=…, version=version+1, updated_by='agent'` **+** `INSERT offer_config_audit(...)` **+** `UPDATE scenarios SET status='shipped'`. A warehouse cannot serve checkout-latency reads or do this transactional write — this is the Postgres/OLTP strength the demo shows.

## Initial state
`offer_config` opens in the **broken** state: Velora Telecom (P01) mid-tier impressions = OFF (5 configs). The agent flips them ON during the demo and the on-screen checkout immediately reflects it.
