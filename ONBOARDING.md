# Attach War-Room — SA Onboarding Runbook

> **Goal:** a second SA, who has never touched this repo, can stand it up in **their own** Databricks
> workspace and run the on-stage demo in **~30 minutes**. Copy-paste friendly. Real paths, real commands.
>
> **All data is synthetic.** No real PII or partner-confidential data. Every named entity is **fictional** — the
> operator (**Acme Embedded Insurance**) and all partners (Velora Telecom, Siam Mobile Care, Marina Mobile, Savanna
> Mobile, Brightway Electronics, Rift Valley Bank, …) are invented, and this fictional set is the active default
> everywhere (data, profile, Genie space, agent, fixtures) — safe to show externally as-is. See
> [Re-skin](#3-re-skin-rename-rebrand) only if you want to rebrand to a *different* account.

What you're shipping: a single **Databricks App** (FastAPI + React) where a growth lead asks *"why did attach
drop in the Velora Telecom flow?"*, the agent localizes the broken funnel stage, **shadow-tests** a fix against a
**loss-ratio guardrail**, and on **one Approve** writes the fix to a **Lakebase** config the simulated checkout
reads — and the recovered-GWP tile ticks up live. Four load-bearing ingredients: **Agent** (tool-calling loop on
FMAPIs), **AI/BI Genie** on metric views, **Lakebase** (Postgres serving + ACID state), **Foundation Model APIs**.

---

## 0. Prerequisites (install once, ~5 min)

| Need | Check | Install |
|---|---|---|
| **Databricks CLI** (v0.2xx, the Go CLI — not the legacy pip one) | `databricks --version` | `brew install databricks` or https://docs.databricks.com/dev-tools/cli |
| **uv** (runs Python with inline deps, no venv juggling) | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node 18+ / npm** (builds the Vite frontend) | `node --version` | `brew install node` |
| **jq** (for the Genie-space POST one-liner) | `jq --version` | `brew install jq` |

**Configure a CLI profile** (OAuth U2M) pointing at YOUR workspace. The repo uses the profile name `DEFAULT`
everywhere; the fastest path is to reuse that name so the default env vars in the helper scripts just work:

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile DEFAULT
databricks current-user me --profile DEFAULT      # sanity check: prints your email
```

> If you name your profile something else, pass `--profile <name>` to every `databricks` call **and** set
> `DATABRICKS_PROFILE=<name>` for the `uv run` scripts (they all read that env var, defaulting to `DEFAULT`).

**The 4 ingredients you must have provisioned in YOUR workspace:**
1. **A serverless SQL warehouse** — copy its ID (Warehouses → your warehouse → *Connection details* → the 32-char hex).
2. **Foundation Model API access** (pay-per-token) to a Sonnet-class agent model + a Haiku-class classifier
   model. The defaults are `databricks-claude-sonnet-4-6` / `databricks-claude-haiku-4-5` — substitute whatever
   your workspace serves (see Serving → *... Foundation models*). Never hardcode a model that may be retired;
   they're all env-configurable below.
3. **Lakebase enabled** — you'll create a **provisioned** Postgres instance in step 2 (provisioned is required
   for the deployed App: it supports the SP-OAuth role API + app resource binding that Autoscaling lacks in Beta).
4. **AI/BI Genie enabled** (you'll create the space in step 1).

Repo root for every command below:
```bash
export AWR=$(pwd)      # run from the cloned repo root (the dir git clone created: attach-war-room-demo)
```

---

## 1. Quick check — is this already deployed?

If you only need to *present* the existing live deployment (no rebuild), skip to **§5 Demo**. The current live app:

- **URL:** https://attach-war-room-<workspace-id>.aws.databricksapps.com
- **Workspace:** `https://YOUR-WORKSPACE.cloud.databricks.com` (profile `DEFAULT`)
- Pre-warm it (`GET /api/health`) ~60s before you present — see [§6](#6-pre-warm-before-you-present).

To stand up your **own** copy, continue.

---

## 2. Clone to YOUR workspace — exactly what to change

Everything workspace-specific is centralized. There are **two** files of record plus one optional re-skin file.

### 2a. `config.yaml` — source of truth for the *build* scripts (00/01/02/03)
`$AWR/config.yaml`. Change these lines to your workspace:

| Key | Current value | Change to |
|---|---|---|
| `databricks.host` | `https://YOUR-WORKSPACE.cloud.databricks.com` | your workspace URL |
| `databricks.profile` | `DEFAULT` | your CLI profile name |
| `databricks.warehouse_id` | `` | **your** warehouse ID |
| `unity_catalog.catalog` / `.schema` / `.fqn` | `bolttech_workshop_demo` / `attach_war_room` / `bolttech_workshop_demo.attach_war_room` | your catalog + schema (the installer creates the `bolttech_workshop_demo` catalog if you have `CREATE CATALOG`; otherwise point all three at a catalog you can already write to) |
| `lakebase.instance` | `attach-war-room-db` | your provisioned instance name |
| `lakebase.database` | `attach_war_room` | your DB name |
| `lakebase.host` | `<lakebase-host>` | your instance's Postgres host (printed by step 2) |
| `genie.space_id` | `` | filled in after you create the space in step 1 |
| `foundation_models.agent` / `.classifier` | `databricks-claude-sonnet-4-6` / `databricks-claude-haiku-4-5` | the model names your workspace serves |

> The Python helpers read **env vars first**, falling back to these defaults. So the lowest-friction path is to
> `export DATABRICKS_PROFILE=… WAREHOUSE_ID=… SCHEMA_FQN=…` before running them (they all honor those three).
> `config.yaml` is the human source of truth; keep it in sync.

### 2b. `04_app/app.yaml` — env for the *deployed* App (the runtime reads these)
`$AWR/04_app/app.yaml`. These are baked into the App at deploy time. Change to match your workspace:

| Env var | Current | Change to |
|---|---|---|
| `SCHEMA_FQN` | `bolttech_workshop_demo.attach_war_room` | your catalog.schema |
| `WAREHOUSE_ID` | `` | your warehouse ID |
| `GENIE_SPACE_ID` | `` | your Genie space id (from step 1) |
| `MODEL_AGENT` / `MODEL_CLASSIFIER` | sonnet-4-6 / haiku-4-5 | your served model names |
| `AI_GATEWAY_URL` | *(not set — optional)* | **Optional, off by default** (`app.yaml` ships it commented out; `install.py` doesn't emit it). Set it only to route FMAPI calls via AI Gateway; otherwise `dbx.py` uses the direct path (`serving_endpoints.get_open_ai_client()`). |
| `LAKEBASE_INSTANCE` | `attach-war-room-db` | your instance name |
| `PGDATABASE` | `attach_war_room` | your DB |
| `PGHOST` | `<lakebase-host>` | your instance Postgres host |
| `PGUSER` | `<app-service-principal>` (app SP client id) | **your app's SP client id** (the Postgres role) |
| `PGPORT` / `PGSSLMODE` / `LAKEBASE_TIER` | `5432` / `require` / `provisioned` | leave as-is |

> Note `PGPASSWORD` is intentionally **unset**: `dbx.py` mints a fresh ~1h OAuth DB token per connection via
> `w.database.generate_database_credential(...)`. Don't add a static password.

### 2c. Local-dev env (no app.yaml)
When running the FastAPI server locally you don't use `app.yaml`; `dbx.py` is env-aware. Either rely on
`config.yaml` defaults (if you kept the `DEFAULT` profile + same IDs) or export overrides:
```bash
export DATABRICKS_PROFILE=DEFAULT
export WAREHOUSE_ID=<your_warehouse_id>
export SCHEMA_FQN=<your_catalog>.<your_schema>
export GENIE_SPACE_ID=<your_space_id>
export LAKEBASE_INSTANCE=<your_instance>
export PGDATABASE=<your_db>
```

### 3. Re-skin (rename / rebrand)
All branding (account name, persona, partner display names, demo branches, walkthrough copy, theme color,
currencies) lives in **one** JSON: `$AWR/config/demo_profile.json` (active default = **Acme** operator + **fictional** partner names).

- **Quick text-only re-skin** (account name, tagline, theme, walkthrough): edit `config/demo_profile.json` in
  place and rebuild the frontend. No data regen needed.
- **Full re-skin to a NEW account's partner set:** copy → edit → point at it via env:
  ```bash
  cp $AWR/config/demo_profile.json $AWR/config/demo_profile.<account>.json
  # edit names/markets/persona/branches in the new file
  export DEMO_PROFILE_PATH=$AWR/config/demo_profile.<account>.json   # resolution order: this wins
  ```
  Profile resolution order (first hit wins): `$DEMO_PROFILE_PATH` → `./demo_profile.json` (vendored next to
  `profile.py`) → `../config/demo_profile.json` → embedded `DEFAULT`.
  > **Important:** the *demo branches* (e.g. "Velora Telecom · impressions broke") reference partner names that must
  > exist in the data. The shipped data already uses the active profile's **fictional** names, so the one-click
  > branches resolve out of the box. If you rename partners to a **different** set, you must **regenerate the data
  > with matching names** (edit the partner INSERTs in `00_setup/generate_data.sql` and re-run step 4.0) or the
  > branches won't resolve. A text-only re-skin that keeps the same partner names works without a regen.

---

## 4. Build & run order

Run from `$AWR`. Each stage is idempotent. Total wall-clock ~12–18 min (most of it is the 00 data build).

### 4.0 — Data foundation (Delta) → ~5–10 min
Generates 9 governed tables (300k sessions, 60k policies, 7k claims) with 3 planted funnel anomalies + the
Kenya budget loss-ratio trap. Prints validation row counts and the anomaly deltas at the end.
```bash
uv run --with databricks-sdk $AWR/00_setup/run_setup.py
```

### 4.1 — Metric views + Genie space
```bash
# (a) create the two metric views (funnel_metrics + profitability_metrics)
uv run --with databricks-sdk \
  $AWR/01_metric_views_and_genie/run_sql.py $AWR/01_metric_views_and_genie/metric_views.sql

# (b) build the serialized space, then POST it to create the Genie space
uv run --with databricks-sdk $AWR/01_metric_views_and_genie/build_genie_space.py > /tmp/awr_space.json
jq -n --arg title "Acme Attach War-Room — Conversion & Profitability" \
  --arg description "Diagnose embedded-checkout attach/conversion; watch the loss-ratio guardrail." \
  --arg parent_path "/Workspace/Users/$(databricks current-user me --profile DEFAULT | jq -r .userName)" \
  --arg warehouse_id "<YOUR_WAREHOUSE_ID>" \
  --rawfile serialized_space /tmp/awr_space.json \
  '{title:$title,description:$description,parent_path:$parent_path,warehouse_id:$warehouse_id,serialized_space:$serialized_space}' \
  > /tmp/create_space.json
databricks api post /api/2.0/genie/spaces --profile DEFAULT --json @/tmp/create_space.json
#   ^ copy the returned space_id into config.yaml (genie.space_id) AND app.yaml (GENIE_SPACE_ID)

# (c) validate
uv run --with databricks-sdk $AWR/01_metric_views_and_genie/ask_genie.py \
  "Why did attach rate drop for Velora Telecom mid-tier devices in Italy?" <space_id>
```
> `01_metric_views_and_genie/run_sql.py` **does exist** (it's the generic `{{S}}`-templated SQL runner; the
> top-level README's file list omits it). It splits on `-- @@` statement separators and substitutes `{{S}}`
> with `SCHEMA_FQN`.

### 4.2 — Lakebase (provisioned — the path the deployed App uses)
First create the provisioned instance once, then seed it. The seed registers the DB owner + app SP as Postgres
roles, creates the DB, applies `schema.sql`, snapshot-syncs `offer_config` + `alert_thresholds` from Delta, and
opens in the **broken** state (Velora Telecom P01 mid impressions = OFF).
```bash
# one-time: create the provisioned instance (skip if it already exists)
databricks database create-database-instance --profile DEFAULT --json \
  '{"name":"attach-war-room-db","capacity":"CU_1"}'
#   ^ then read its Postgres host and put it in config.yaml (lakebase.host) + app.yaml (PGHOST)

# seed it (provisioned-aware; registers SP role, applies schema, seeds from Delta)
APP_SP_CLIENT_ID=<your_app_sp_client_id> \
  uv run --with databricks-sdk --with psycopg2-binary $AWR/02_lakebase/setup_provisioned.py
```
> `02_lakebase/setup_lakebase.py` is the **Autoscaling** variant (earlier iteration). The deployed App uses
> **provisioned** → use `setup_provisioned.py`. If you don't yet have the app SP id (you create the app in 4.4),
> run the seed once after `apps create`, or seed now as owner and add the SP role afterward.

### 4.3 — Agent CLI smoke test
Confirms FMAPI + metric views + Lakebase all answer **before** you bring up the web app. You'll see the tool
calls stream, then a diagnosis.
```bash
uv run --with databricks-sdk --with openai --with psycopg2-binary \
  $AWR/03_agent/agent.py "Why did attach drop for Velora Telecom mid-tier, and what should we do about it?"
```
Expected: tool calls (`funnel_diagnosis`, `get_offer_config`, `loss_ratio_for`) then prose:
*attach fell ~22%→15% because offer-shown collapsed ~86%→53% while conversion-of-shown held — an impressions
problem; loss ratio 0.29, safe.* You can also probe the plumbing directly: `… 03_agent/dbx.py` (the
`__main__` prints warehouse count, Lakebase `offer_config` count, and an LLM "OK").

### 4.4 — The App: local, then deploy

**Local (validate in a browser first):**
```bash
# build the frontend bundle (FastAPI serves frontend/dist)
cd $AWR/04_app/frontend && npm install && npm run build && cd $AWR/04_app

# run the FastAPI server (serves SPA + /api). Local uses your CLI profile.
DATABRICKS_PROFILE=DEFAULT uv run \
  --with fastapi --with "uvicorn[standard]" --with psycopg2-binary \
  --with openai --with databricks-sdk --with pydantic \
  uvicorn app:app --port 8000
# open http://127.0.0.1:8000   (health dot top-left should go green)
```
> For frontend hot-reload during dev: in a second terminal `cd frontend && npm run dev` → http://localhost:5173
> (Vite proxies `/api` → :8000). For the demo, use the built `dist` served by FastAPI.

**Deploy to Databricks Apps:**
```bash
databricks apps create attach-war-room --profile DEFAULT
# ^ grab the SP client id it prints -> put in app.yaml PGUSER + config.yaml service_principal,
#   then (if not done) run 4.2's setup_provisioned.py with that APP_SP_CLIENT_ID to register the SP role.

cd $AWR/04_app/frontend && npm run build && cd $AWR/04_app   # ensure frontend/dist exists
databricks sync . /Workspace/Users/<you>@databricks.com/attach-war-room \
  --exclude node_modules --exclude .venv --exclude __pycache__ --exclude frontend/node_modules \
  --profile DEFAULT
databricks apps deploy attach-war-room \
  --source-code-path /Workspace/Users/<you>@databricks.com/attach-war-room --profile DEFAULT
```
**Then grant the App's SP** (the App runs as its own service principal, not you):
1. **Bind resources** (Apps UI → your app → *Edit* → Resources, or `databricks apps update --json`):
   SQL warehouse = **Can use**; both serving endpoints (agent + classifier model) = **Can query**.
2. **Lakebase role for the SP** — already handled if you ran `setup_provisioned.py` with `APP_SP_CLIENT_ID`.
   Otherwise grant in Postgres as owner: `GRANT CONNECT ON DATABASE … / USAGE ON SCHEMA public /
   SELECT,INSERT,UPDATE ON ALL TABLES … / USAGE,SELECT ON ALL SEQUENCES …` to `"<APP_SP_CLIENT_ID>"`.
3. **Unity Catalog grants for the SP** on your `catalog.schema`: `USE CATALOG`, `USE SCHEMA`, `SELECT`.
4. **Genie space**: grant the SP **CAN RUN** on the space.

Logs (if anything's off after deploy): `https://<your-app-url>/logz`.

---

## 5. On-stage demo — exact click sequence (~3 min)

App opens with **Partner = Velora Telecom, Tier = mid**. The funnel chart shows the offer-shown bar collapsed; the
simulated checkout reads **"○ No offer shown"**; **Recovered GWP / month = $0**. A welcome walkthrough modal
appears on first visit — click **"Let's go"** (or *Skip*) to dismiss; it won't reappear (localStorage).

1. **Click the branch button → `Velora Telecom · impressions broke`** (top of the demo-branch row under the chat).
   → Watch the tool chips run live (*Localizing the funnel stage → Checking live offer config → Checking the
   loss-ratio guardrail*), then the agent explains: **attach fell ~22%→15% because offer-shown collapsed
   ~86%→53% while conversion-of-shown held — an impressions problem, not pricing.** Loss ratio **29%** = safe.
2. **Click the follow-up prompt → `Shadow-test restoring the impressions and propose the fix.`**
   → A **"📋 Proposed change — awaiting approval"** card appears: Attach **15% → 25% (+~9.7pts)**, Recovered GWP
   **+$8,192/mo**, projected loss ratio **29%**, tagged **within guardrail** (green).
3. **Click `✅ Approve & ship to live checkout`** (the green button on the proposal card). *(the wow)*
   → The simulated checkout flips to green **"● Protection offer shown"** (it pulses), and the header
   **Recovered GWP / month** **counts up** to **$8,192**. Agent: *"Shipped — 5 offer configs flipped."*
   This is a real multi-table ACID write to the Lakebase config the checkout reads.
4. *(optional)* Follow-up **`Draft a note to the partner about the fix.`** → partner-ready summary.
5. *(optional, Genie reconciliation)* On the funnel card click **`Verify in Genie ↗`** → a modal shows the
   **same numbers** answered in AI/BI Genie (SQL + narrative) — proof the agent and analysts read one source.
6. *(optional, the risk beat)* Click branch **`Savanna Mobile KE · guardrail blocks it`**. The proposal card shows
   **⛔ BLOCKED by guardrail** (projected loss ratio >70%) and the Approve button is **disabled** — the agent
   refuses to buy attach with underpriced cover. The other two branches (`Siam Mobile TH · bind-stage drop`,
   `Rift Valley Bank · activation leak`) show different anomaly shapes.

**To run it again → click `↺ Reset`** (top-right of the header) — see §7.

---

## 6. Pre-warm before you present

Cold Lakebase token mint + first FMAPI call + warehouse wake can add several seconds to the first turn. **~60
seconds before you go on stage**, hit the health endpoint — it probes warehouse, Lakebase, and (in the app)
keeps the SDK clients warm:
```bash
curl -s https://<your-app-url>/api/health | jq
# expect: {"ok": true, "checks": {"warehouse": {...ok...}, "lakebase": {...ok...}}}
```
The header has a **health dot** (top-left, next to the brand): **green** = all systems ready, **amber** =
waiting, **red** = backend not ready (re-hit `/api/health`, check `/logz`). Loading the app in your browser a
minute early also fires `GET /api/health` automatically.

---

## 7. One-click Reset — what it restores

The **`↺ Reset`** button (header) calls `POST /api/reset`, which restores the **broken-Velora Telecom starting state**
so back-to-back demos never drift and an accidental Approve is fully recoverable. Specifically it:

- Sets **Velora Telecom (P01) mid-tier** `impression_enabled = false` (the planted anomaly the demo opens on);
- Turns impressions back **ON** for everything else (undoes any restore you shipped on other partners);
- Resets **Siam Mobile Care (P02)** deductible back to the broken `high`; everyone else to `std`;
- **TRUNCATEs** `scenarios` and `offer_config_audit`, and **DELETEs** all `chat_messages`;
- Frontend clears the chat, proposal, timeline, and starts a fresh conversation id; the GWP tile drops to $0.

Restores the live `offer_config` the checkout reads, so the checkout flips back to "No offer shown." Safe to
click anytime (it's disabled only while a turn is streaming).

---

## 8. Break-glass: `DEMO_OFFLINE=1` (conference wifi died)

If the workspace / FMAPI / Lakebase is unreachable (or you have no network on stage), set **`DEMO_OFFLINE=1`**
and the app serves deterministic **fixtures** (`04_app/server/fixtures.py` / `03_agent/fixtures.py`) — **nothing
touches the network.** It's a faithful scripted replay of the Velora Telecom impressions hero path (diagnose → propose
→ approve → checkout flip → GWP $8,192); other partners return plausible-but-generic data, and Savanna Mobile still
triggers the guardrail block.

- **Local:** add `DEMO_OFFLINE=1` to the env before launching uvicorn (drop it from §4.4's command):
  ```bash
  DEMO_OFFLINE=1 DATABRICKS_PROFILE=DEFAULT uv run --with fastapi --with "uvicorn[standard]" \
    --with psycopg2-binary --with openai --with databricks-sdk --with pydantic uvicorn app:app --port 8000
  ```
- **Deployed:** add `- name: DEMO_OFFLINE` / `value: "1"` to `app.yaml` env and redeploy.
- The health dot tooltip reads **"Offline demo mode (fixtures)"**; `/api/health` returns `{"offline": true}`.
- The same click sequence in §5 works. Reset/Approve flip in-process. Pair with the recorded MP4 as backup.

---

## 9. If a step hangs — recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| `00/01` SQL runner hangs >60s on a statement | Serverless warehouse cold-starting | Wait — first statement wakes it (scripts poll up to 900s). If it *fails*: check the warehouse is running and `WAREHOUSE_ID` is correct for your profile. |
| Genie POST returns 4xx | bad `parent_path` (must be a workspace path you can write) or warehouse id | Re-check the `jq` args; `parent_path` = `/Workspace/Users/<your-email>`. |
| Lakebase connect: auth/`role does not exist` | OAuth token expired (~1h TTL) or SP not registered as a Postgres role | Re-run; tokens are minted per-connection. For the deployed SP, re-run `setup_provisioned.py` with `APP_SP_CLIENT_ID`. |
| Agent CLI: `streaming unavailable … falling back` | endpoint rejected streaming-with-tools | Harmless — it auto-falls back to non-streaming. |
| First chat turn slow / appears stuck | cold FMAPI + Lakebase + warehouse | This is why you **pre-warm** (§6). Wait ~10s; the tool chips will appear. |
| App health dot **red**, `/api/health` shows a failing check | a resource grant missing for the SP | Check warehouse=Can use, serving=Can query, UC SELECT, Lakebase role, Genie CAN RUN (§4.4). Logs: `https://<app-url>/logz`. |
| Approve button greyed out on a non-guardrail proposal | a turn is still streaming, or proposal was cleared | Wait for the turn to finish; re-run the propose follow-up. |
| `Couldn't load funnel/checkout` red box in a card | backend `/api/*` errored (creds/warehouse) | Check `/logz`; verify env (§2). As a last resort flip to **`DEMO_OFFLINE=1`** (§8) and keep the show going. |
| Demo state looks wrong / stale | leftover from a prior run | Click **`↺ Reset`** (§7). |
| Total wipe-out, no network | — | **`DEMO_OFFLINE=1`** (§8) + the recorded MP4. |

---

### Repo map (where things live)
```
config.yaml                         build-script source of truth (host/profile/warehouse/schema/genie/lakebase/SP)
config/demo_profile.json            the re-skin layer (active = Acme operator + fictional partner names)
00_setup/run_setup.py               data foundation (runs generate_data.sql)
01_metric_views_and_genie/run_sql.py        generic {{S}} SQL runner (EXISTS; README list omits it)
01_metric_views_and_genie/build_genie_space.py / ask_genie.py   Genie create + validate
02_lakebase/setup_provisioned.py    provisioned Lakebase seed (the deployed-App path)
02_lakebase/setup_lakebase.py       Autoscaling variant (earlier iteration)
03_agent/agent.py · dbx.py · tools.py · profile.py · fixtures.py   the agent + plumbing
04_app/app.yaml                     deployed-App env vars (the runtime reads these)
04_app/app.py · server/             FastAPI: serves the SPA + /api (chat, metrics, state, checkout, admin)
04_app/frontend/                    React + Vite SPA (build -> frontend/dist)
EVAL.md                             10 NL Genie benchmarks + ground-truth SQL (anchored to AS_OF 2026-06-03)
```
