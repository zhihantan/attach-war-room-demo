# 02 — Lakebase (Provisioned Postgres)

Lakebase is **load-bearing two ways**: (1) low-latency **serving** of `offer_config` that the simulated partner checkout reads, and (2) the app/agent **state** layer (audit, thresholds, scenarios, chat memory) with **multi-table ACID writes**.

## Resource
**Provisioned** Lakebase instance `attach-war-room-db` (AWS us-east-1), database `attach_war_room` — created by `install.py` (or `setup_provisioned.py`). The deployed App uses the provisioned tier because it supports the `DatabaseInstanceRole` API (register the app's service principal as a Postgres role for OAuth) and app-resource binding. *(An earlier **autoscaling** project `attach-war-room` → branch `production` → endpoint `primary` is **superseded**; the autoscaling commands below and `branch_demo.py` are kept only as a reference aside.)*

## Files
| File | Purpose |
|---|---|
| `schema.sql` | Postgres DDL: `offer_config` (serving) + `offer_config_audit`, `alert_thresholds`, `scenarios`, `chat_messages` (state) |
| `lakebase.py` | Connection helper (`get_connection`, `cursor`); CLI/profile locally, `PG*` env in the deployed app |
| `setup_provisioned.py` | **Primary/live path:** provision `attach-war-room-db` → create DB → apply schema → seed `offer_config`/`alert_thresholds` from Delta → register the app SP's Postgres role → validate |
| `setup_lakebase.py` | Older **autoscaling/dev** variant of the same seed flow (superseded by `setup_provisioned.py`) |
| `branch_demo.py` | Reference aside: copy-on-write branching of candidate configs (autoscaling concept) |

## Setup
`install.py` provisions and seeds Lakebase automatically. To run this step standalone:
```bash
uv run --with databricks-sdk --with psycopg2-binary 02_lakebase/setup_provisioned.py
```
*(Superseded autoscaling path, for reference only: `databricks postgres create-project attach-war-room --json '{"spec":{"display_name":"Acme Attach War-Room"}}' -p DEFAULT`, then `setup_lakebase.py`.)*

## Connection pattern
OAuth token auth (token ~1h TTL → fetch per unit of work):
```
host  = the provisioned instance's read/write DNS
        (w.database.get_database_instance("attach-war-room-db").read_write_dns)
token = w.database.generate_database_credential(...)   # OAuth DB token, ~1h TTL
user  = workspace email locally (databricks current-user me -> userName);
        the app's service-principal client id in the deployed App
psycopg2.connect(host, 5432, dbname=attach_war_room, user=user, password=token, sslmode=require)
```
The deployed Databricks App sets `PGHOST/PGUSER/PGDATABASE` via `app.yaml` (written by `install.py`); `dbx.py` mints a fresh DB token per connection (`generate_database_credential`), so `PGPASSWORD` is intentionally unset.

## Delta → Postgres sync
`setup_lakebase.py` does a **snapshot sync** (reads the Delta golden `offer_config` and loads it into Postgres) — enough to demonstrate the serving pattern and keep the demo deterministic. **Prod path:** a Lakebase **synced table** (continuous/triggered reverse-ETL) keeps the serving copy fresh automatically.

## The ACID write (agent's `propose_offer_change` on approval)
One transaction: `UPDATE offer_config SET impression_enabled=…, version=version+1, updated_by='agent'` **+** `INSERT offer_config_audit(...)` **+** `UPDATE scenarios SET status='shipped'`. A warehouse cannot serve checkout-latency reads or do this transactional write — this is the Postgres/OLTP strength the demo shows.

## Initial state
`offer_config` opens in the **broken** state: Velora Telecom (P01) mid-tier impressions = OFF (5 configs). The agent flips them ON during the demo and the on-screen checkout immediately reflects it.
