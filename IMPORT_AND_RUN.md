# Import & Run — stand up the Attach War-Room in your own Databricks workspace

This repo ships a **one-command installer** (`install.py`) that provisions the entire demo —
synthetic data, governed metric views, an AI/BI Genie space, a Lakebase (Postgres) instance,
and the Databricks App with all its resource bindings — in **any** Databricks workspace.
Nothing is hardwired to the workspace it was built in; you pass your catalog/warehouse and it
captures the rest (the new Genie space id, the Lakebase host, the App's service principal) and
wires them together automatically.

> All data is **synthetic**. Distribution-partner names are **fictional** (Velora Telecom, Siam
> Mobile Care, …) so no real company is shown attached to a fabricated metric — see `COMPLIANCE.md`.

---

## 1. Prerequisites

- **Databricks CLI** (v0.230+), authenticated to your target workspace:
  ```bash
  databricks auth login --profile myws --host https://<your-workspace-host>
  ```
- **uv** (or Python 3.10+ with `pip`). The installer needs `databricks-sdk` and `psycopg2-binary`.
- In your workspace / region:
  - A **serverless SQL warehouse** (or pass `--warehouse-id`).
  - **Lakebase** available in your region (the installer creates a provisioned instance).
  - **Foundation Model APIs** serving endpoints present: `databricks-claude-sonnet-4-6` and
    `databricks-claude-haiku-4-5` (the installer's preflight reports if they're missing).
  - **Databricks Apps** enabled.
- **Permissions** for the running user: create a schema (or use an existing one), `CREATE`
  on a Lakebase instance, create a Genie space, create/deploy a Databricks App, and `USE`/`SELECT`
  on the target catalog. (You do **not** need `CREATE CATALOG` — if you lack it, the installer
  uses an existing catalog you name and just creates the schema.)

---

## 2. Two ways to run it

Both run the **same** installer logic — pick whichever fits.

### Option A — in-workspace notebook (no local tools needed)

1. In your Databricks workspace: **Create → Git folder** → `https://github.com/zhihantan/attach-war-room-demo`.
2. Open **`setup_notebook`** from the cloned folder; attach to **serverless** or a **cluster** (DBR 14+).
3. Set the widgets at the top (catalog, schema, …) and **Run All**. ~10–15 min; it prints the App URL.

The notebook authenticates as **you** (its runtime identity — no `databricks auth login`), and because
the repo is already in your workspace it deploys the App directly from the Git folder (no upload step).
It just calls the same `install.py` functions, one per cell, so you can watch each step.

### Option B — local CLI

**Run all of this from your own computer** — a macOS / Linux / WSL **terminal**, from the root of
the cloned repo. **Do not** run it inside a Databricks notebook or the workspace web terminal:
`databricks auth login` opens a browser to authenticate, and the installer uploads the App source
and provisions resources by driving your workspace **remotely** via the Databricks CLI + SDK.

```bash
# 1. clone the repo and enter it
git clone https://github.com/zhihantan/attach-war-room-demo.git
cd attach-war-room-demo

# 2. authenticate to YOUR workspace (opens a browser; pick any profile name)
databricks auth login --profile myws --host https://<your-workspace-host>

# 3. run the installer
uv run --with databricks-sdk --with psycopg2-binary install.py \
    --profile myws \
    --catalog bolttech_workshop_demo \
    --schema attach_war_room
```

Either way, ~10–15 minutes later (most of it Lakebase provisioning) it prints:

```
✅ Done.
   schema:   bolttech_workshop_demo.attach_war_room
   genie:    01f1...
   lakebase: attach-war-room-db (instance-....database.cloud.databricks.com)
   app:      attach-war-room  https://attach-war-room-....databricksapps.com
```

Open the App URL and run the 5-minute demo in `README.md`.

---

## 3. What it provisions (7 idempotent steps)

| Step | What it does |
|---|---|
| `schema` | Creates the UC catalog (if you have permission) + the schema. |
| `data` | 9 governed Delta tables of synthetic data (~300k sessions) via `00_setup/generate_data.sql`. |
| `metrics` | `funnel_metrics` + `profitability_metrics` metric views (`01_.../metric_views.sql`). |
| `genie` | Creates a **new** AI/BI Genie space on the metric views; captures its `space_id`. |
| `lakebase` | Creates a **provisioned** Lakebase instance, db, schema, and seeds `offer_config` from the Delta golden copy; captures the host. |
| `app` | Creates the Databricks App, **binds its resources** (warehouse `CAN_USE`, the two FMAPI endpoints `CAN_QUERY`, the new Genie space `CAN_RUN`, the Lakebase db), grants its **service principal** UC `SELECT` on the schema + a Postgres role, generates the App env (`app.yaml`) with the captured ids, syncs the source, and deploys. |
| `reset` | Sets the demo to the broken-start state (Velora Telecom/Velora impressions off). |

Captured ids are written to `install_state.json` so re-runs and `--only` steps stay consistent.

---

## 4. Options

| Flag | Default | Notes |
|---|---|---|
| `--profile` | `DEFAULT` | Databricks CLI auth profile for the target workspace. |
| `--catalog` | `bolttech_workshop_demo` | Created if you can; otherwise must already exist. |
| `--schema` | `attach_war_room` | Schema for tables + metric views. |
| `--warehouse-id` | *(auto)* | First serverless warehouse if omitted. |
| `--lakebase-instance` | `attach-war-room-db` | Provisioned instance name. |
| `--lakebase-database` | `attach_war_room` | Database name inside the instance. |
| `--lakebase-capacity` | `CU_1` | Provisioned capacity. |
| `--app-name` | `attach-war-room` | Databricks App name. |
| `--genie-parent` | `/Workspace/Users/<you>` | Folder for the Genie space. |
| `--model-agent` / `--model-classifier` | `claude-sonnet-4-6` / `claude-haiku-4-5` | FMAPI endpoints. |
| `--only` / `--skip` | — | Comma list of steps, e.g. `--only schema,data,metrics`. |
| `--teardown` [`--drop-schema`] | — | Remove the app + Lakebase instance (and the schema/data with `--drop-schema`). |

**Re-running is safe** — each step checks for and reuses what already exists. To repair just one
piece: `install.py --profile myws --catalog ... --schema ... --only app`.

---

## 5. Teardown

```bash
uv run --with databricks-sdk --with psycopg2-binary install.py \
    --profile myws --catalog bolttech_workshop_demo --schema attach_war_room \
    --teardown --drop-schema       # omit --drop-schema to keep the data
```

This deletes the App and the Lakebase instance (and, with `--drop-schema`, the UC schema + tables).
The Genie space is left in place (no idle compute) — delete it by hand if you want a clean slate.

---

## 6. Cost & lifecycle

A provisioned Lakebase instance + an always-on App + a serverless warehouse bill while running.
Pause between engagements with `scripts/pause_demo.sh` (stops the App + downscales Lakebase) and
`scripts/resume_demo.sh`; tear down fully with `--teardown`. Size exact $ with Quicksizer/Lakemeter.

---

## 7. Troubleshooting

- **`PERMISSION_DENIED: ... CREATE CATALOG`** — expected if you can't create catalogs. Pass an
  existing `--catalog` you have `USE`/`CREATE SCHEMA` on; the installer only creates the schema.
- **FMAPI endpoint NOT FOUND** in preflight — the model isn't enabled in your region. Pass
  `--model-agent` / `--model-classifier` with endpoints that exist in your workspace.
- **`refresh token is invalid`** — your CLI session expired; re-run `databricks auth login --profile myws`.
- **Lakebase create fails / unavailable** — Lakebase may not be enabled in your region; check with
  your Databricks account team.
- **App SP can't read the schema / Lakebase** — re-run `--only app` (it re-applies the UC grant and
  the Postgres role for the app's service principal).
