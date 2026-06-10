# Attach War-Room — Diagnose-to-Live-Fix Conversion Copilot

> A flagship **Databricks App** demo for **bolttech** (global insurtech exchange). An ops/growth lead asks *"why did attach drop in the Velora Telecom device flow?"* — the agent localizes the exact funnel stage and root cause, **shadow-tests** a corrective offer rule against recent traffic (with a **loss-ratio guardrail** so it never buys attach with underpriced cover), and on **one approval writes the fix to a Lakebase config the live checkout reads** — and the recovered-GWP tile, reconciled to governed Genie metrics, ticks up live.
>
> **All data is synthetic. No real PII, carriers, or partners.**

**Stand it up in your own Databricks workspace in ~15 minutes** — see [Run it in your own workspace](#-run-it-in-your-own-databricks-workspace-one-command) below. The full diagnose → propose → approve → ship → checkout-flip flow runs end-to-end on a provisioned Lakebase + a Databricks App (recovered GWP **$8,192/mo** in the reference dataset).

## ▶ Run it in your own Databricks workspace (one command)

This repo is **self-installing** — a customer can clone it and stand up the entire demo
(synthetic data → metric views → a new Genie space → a Lakebase instance → the deployed App,
all wired together with the right grants) in their own workspace. Nothing is hardwired.

There are **two ways** to run it — pick whichever fits. Both run the *same* installer logic.

### Option A — from a notebook, inside your workspace (no local tools)
1. In your Databricks workspace: **Create → Git folder** → `https://github.com/zhihantan/attach-war-room-demo`.
2. Open **`setup_notebook`** from the cloned folder, attach to serverless or a cluster (DBR 14+).
3. Set the widgets at the top (catalog, schema, …) and **Run All** (~10–15 min). It authenticates as
   you and prints the App URL at the end. Nothing to install locally.

### Option B — from your laptop (local CLI)
Run these in a **terminal on your own computer** (macOS / Linux / WSL), from the cloned repo root —
**not** in a notebook. `databricks auth login` opens a browser; `install.py` then drives your
workspace remotely. First-time setup: install the
[Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/zhihantan/attach-war-room-demo.git
cd attach-war-room-demo
databricks auth login --profile myws --host https://<your-workspace-host>
uv run --with databricks-sdk --with psycopg2-binary install.py \
    --profile myws --catalog bolttech_workshop_demo --schema attach_war_room
```

The installer (`install.py`) is parameterized and idempotent; it captures the new Genie space id,
the Lakebase host, and the App's service principal and wires them automatically, then deploys. Full
prerequisites, options, troubleshooting, and teardown are in
**[`IMPORT_AND_RUN.md`](IMPORT_AND_RUN.md)**. *(`04_app/app.yaml` is generated per-install.)*

## Capabilities

Beyond the core hero flow, the demo includes a hardening + UX pass. Highlights:

- **Demo-day reliability (Tier 0):** one-click in-app **Reset** (header `↺`) + `POST /api/reset`; **parameterized warehouse SQL** (the f-string injection vector is gone — bound `:params` + allow-listed enums); **cached Lakebase OAuth token + a connection pool** (the per-call control-plane round-trip is gone); real **loading / error / empty states**; persistence failures now **surface** instead of silently losing memory; per-tool **timeouts**; and a **break-glass offline mode** (`DEMO_OFFLINE=1`) that replays the hero flow from fixtures when the backend is unreachable.
- **Wow (Tier 1):** the recovered-GWP tile **counts up** and the checkout **pulses** on ship; **four one-click demo branches** (incl. the **guardrail-block** as a first-class beat); an **annualized ROI** line on the tile; the agent's reasoning now **streams token-by-token** with a friendly **step ledger**; a **decision/audit timeline**; and a **cold-open "silent GWP leak" banner**.
- **Credibility (Tier 2):** the loss-ratio elasticity is now **derived from the claims book** (no more hardcoded `1.12`); a **`scan_for_anomalies`** tool makes the agent **find** the broken partner itself (activates the previously-dead `alert_thresholds`); a **`rollback_offer_change`** ACID undo; a **"Verify in Genie"** trust reveal; an **automated Genie eval harness**; **observability** (timed structured logs); **MLflow `ResponsesAgent` + Agent Evaluation** scripts; a **Lakebase branching** script; and **combined-ratio** data depth (commission/ceded/expense → `combined_ratio` measure).
- **Reuse & trust (Tier 3):** a re-skinnable **`demo_profile`** layer (`config/demo_profile.json` — already fictional) so this becomes a 1-day re-skin for the next account; **`COMPLIANCE.md`** (operator bolttech is real; all partners fictional); **`docs/BRING_YOUR_OWN_DATA.md`** onboarding contract; **local-currency** rendering; **demo telemetry** (`demo_events`); **cost/teardown** scripts; and an **`ONBOARDING.md`** runbook.
- **Product walkthrough:** a welcome-modal carousel on first entry (story → loop → 4 ingredients → try a branch), replayable via the header `?`, content driven by the profile.

## The four ingredients (all load-bearing)

| Ingredient | Where it's used | Why it matters to bolttech |
|---|---|---|
| **Agent** (tool-calling loop on FMAPIs) | Stateful diagnose→scan→shadow-test→approve→ship→rollback→verify loop; 14 tools; streamed reasoning; Lakebase chat memory | Turns "attach fell" into a shipped, governed fix in one sitting |
| **AI/BI Genie** on **metric views** | `funnel_metrics` + `profitability_metrics`: diagnostic engine + governed sim baseline + reconciliation | One source of truth for attach/conversion/GWP/loss-ratio across partners, markets, currencies |
| **Lakebase** (Provisioned Postgres) | Checkout-latency `offer_config` serving + agent/app state; approved change = **multi-table ACID write** | The realistic low-latency serving layer a partner checkout reads — a warehouse cannot |
| **Foundation Model APIs** | Agent reasoning (`claude-sonnet-4-6`) + abandonment-cause **classification** (`claude-haiku-4-5`) | LLM intelligence doing real decision work, not just narration |

## Architecture

```
                 ┌──────────────────── Databricks App (single) ─────────────────────┐
   Browser ────► │  React + Vite SPA   ──/api──►   FastAPI (hosts the agent loop)    │
   chat ·        │  • agent chat (SSE)             • SSE /api/chat  (stream_turn)     │
   funnel ·      │  • funnel diagnosis chart       • metrics · state · checkout       │
   guardrail ·   │  • loss-ratio guardrail gauge   • all Databricks creds server-side │
   checkout ·    │  • simulated partner checkout                                      │
   GWP tile      │  • recovered-GWP tile                                              │
                 └─────────┬───────────────┬───────────────┬───────────────┬─────────┘
                  Foundation Model      AI/BI Genie      Lakebase        Unity Catalog
                  APIs (FMAPIs)       (metric views)    (Postgres)       (Delta)
                  reason · classify   funnel_metrics    offer_config ◄── snapshot sync ── sessions
                                      profitability_    (serving) +                       policies
                                      metrics  ◄──────  state: audit,                     claims
                                      governed KPIs     scenarios, chat,                  offer_config
                                                        thresholds                        (+ metric views)
```

**Agent loop:** `funnel_diagnosis` (Genie metric views, recent vs prior) → `get_offer_config` (Lakebase) → `loss_ratio_for` (guardrail) → `run_shadow_sim` (deterministic funnel-restore) → `propose_offer_change` (Lakebase scenario) → **human approval** → `ship_offer_change` (ACID: update `offer_config` + insert `offer_config_audit` + mark scenario shipped) → the simulated checkout reads the new config → recovered GWP.

## 5-minute demo script (exact prompts)

First-run shows a **welcome carousel** (replay any time via the header `?`). Default Partner = **Velora Telecom**, Tier = **mid**; the funnel bar is collapsed, the checkout shows **"No offer shown"** (with a red **"silent GWP leak"** banner), Recovered GWP = $0. The chat has **four one-click demo branches**.

1. Click the **"Velora Telecom · impressions broke"** branch.
   → The agent's reasoning **streams live** with a step ledger (scan/diagnose/check config/check guardrail): *attach fell 22% → 15% because **offer-shown** fell 86% → 53%, while conversion-of-shown held ~28% — an **impressions** problem, not pricing.* Root cause: `impression_enabled=false` across 5 mid-tier configs. Loss ratio 0.29 — safe. *(Click **"Verify in Genie ↗"** to show the same number answered conversationally by the governed metric views.)*
2. Click **"Shadow-test restoring the impressions and propose the fix."**
   → A **Proposed change** card appears: attach **15% → 25% (+9.7pts)**, **+$8,192/mo** recovered GWP (≈ local currency), projected loss ratio **29%** (elasticity derived from the claims book) — green **within guardrail**.
3. Click **"✅ Approve & ship to live checkout."** *(the wow)*
   → The checkout card **pulses** to green **"● Protection offer shown"**, the header **Recovered GWP / month counts up** to **$8,192** (with **≈ $98k/yr**), and the agent confirms *"Shipped — 5 offer configs flipped."* A multi-table ACID write to the Lakebase config the checkout reads — logged to the **decision/audit timeline**.
4. Click the **"Savanna Mobile KE · guardrail blocks it"** branch *(the credibility beat)* → the agent **refuses to ship**: lowering the deductible would push projected loss ratio **>70%**, so the Approve button is **blocked**. This is the moment that wins the skeptical actuary.
5. *(optional)* The **"Siam Mobile Care"** (bind-stage price shock) and **"Rift Valley Bank"** (activation leak) branches show the two other planted anomalies; "Draft a note to the partner" produces a partner-ready summary.
6. *(optional, "it finds it itself")* **"Scan the whole book…"** makes the agent rank every attach leak by recovered GWP on its own. It surfaces **Siam Mobile Care** as the biggest (~$10k/mo) with **Velora Telecom** next (~$6k/mo) — proof it discovers problems without being told. *(Note: we lead the walk-through above with Velora Telecom because its impressions fix is the cleanest, zero-risk illustration — the offer literally stopped showing. Rift Valley's leak is in **activation**, post-bind, so it correctly never appears in an attach-drop scan.)*

**Commercial close (ROI):** the tile annualizes one recovered anomaly to **~$98k/yr** — and this is **one of three** planted anomalies across a 12-partner book; recurring attach leaks compound across partners, markets and currencies.

To re-run: click **`↺ Reset`** in the header (or `POST /api/reset`) — it restores the broken-Velora Telecom start and clears run state server-side (a Lakebase UPDATE/TRUNCATE) instantly. No manual/CLI step needed.

## Verified build environment (AWS · us-east-1)

| | |
|---|---|
| Workspace | `https://YOUR-WORKSPACE.cloud.databricks.com` (profile `DEFAULT`) |
| Data namespace | `bolttech_workshop_demo.attach_war_room` (catalog `bolttech_workshop_demo`, schema `attach_war_room` — see `config.yaml`) |
| SQL warehouse | Serverless Starter Warehouse (auto-resolved by `install.py`) |
| FMAPIs | `databricks-claude-sonnet-4-6` (agent), `databricks-claude-haiku-4-5` (classify) |
| Lakebase | Provisioned instance `attach-war-room-db`, db `attach_war_room` |
| Genie space | created per-install by `install.py` |

## Repo layout & run order

```
attach-war-room-demo/
├── install.py                   # ← one-command installer (local CLI)
├── setup_notebook.py            # ← in-workspace setup notebook (Git folder → Run All)
├── config.yaml                  # reference config (defaults)
├── config/                      # demo_profile.json — the re-skin layer (account / partners / branches / theme)
├── 00_setup/                    # generate_data.sql + run_setup.py   → 9 governed Delta tables
├── 01_metric_views_and_genie/   # metric_views.sql + run_sql.py + build_genie_space.py + apply_genie_space.py + ask_genie.py + eval_genie.py
├── 02_lakebase/                 # schema.sql + lakebase.py + setup_*.py + branch_demo.py
├── 03_agent/                    # dbx.py + tools.py + agent.py + profile.py + fixtures.py + responses_agent.py + register_and_serve.py + eval_agent.py
├── 04_app/                      # app.py + server/ (FastAPI + routes incl. admin) + frontend/ (React+Vite)  → single App
├── scripts/                     # migrate_combined_ratio.sql + pause/resume/teardown/rebuild.sh
├── docs/                        # BRING_YOUR_OWN_DATA.md
└── README.md · IMPORT_AND_RUN.md · ONBOARDING.md · COMPLIANCE.md · EVAL.md
```

```bash
# 0. data foundation (idempotent, deterministic)
uv run --with databricks-sdk 00_setup/run_setup.py
# 1. metric views + Genie space
uv run --with databricks-sdk 01_metric_views_and_genie/run_sql.py 01_metric_views_and_genie/metric_views.sql
uv run --with databricks-sdk 01_metric_views_and_genie/build_genie_space.py > /tmp/ss.json   # then POST (see 01 README)
# 2. lakebase  (note: setup_lakebase.py is the older autoscaling/dev variant; the deployed App uses
#    the PROVISIONED instance, which install.py provisions — see 02_lakebase/setup_provisioned.py)
uv run --with databricks-sdk --with psycopg2-binary 02_lakebase/setup_lakebase.py
# 3. agent (CLI)
uv run --with databricks-sdk --with openai --with psycopg2-binary 03_agent/agent.py "Why did attach drop for Velora Telecom mid-tier?"
# 4. app — local, then deploy (see 04_app/README.md)
cd 04_app/frontend && npm install && npm run build && cd ..
DATABRICKS_PROFILE=DEFAULT uv run --with fastapi --with "uvicorn[standard]" --with psycopg2-binary --with openai --with databricks-sdk --with pydantic uvicorn app:app --port 8000
```

## Production hardening (where bolttech would change things for prod)
- **FMAPIs → provisioned throughput** for checkout-path SLAs/throughput; optionally routed via **AI Gateway** (set `AI_GATEWAY_URL` — off by default) for token metering, inference tables, and guardrails. Batch scoring via **AI Functions** (`ai_query`).
- **Agent → MLflow `ResponsesAgent`** — scaffolded in `03_agent/responses_agent.py` + `register_and_serve.py` (register to UC + serve) + `eval_agent.py` (Agent Evaluation). The tool layer is unchanged.
- **Shadow-sim → trained attach-propensity model** (Mosaic AI) behind the same `run_shadow_sim` tool. The demo's sim is deterministic and defensible — and the loss-ratio elasticity is now **derived from the claims book**, not hardcoded.
- **Lakebase → continuous synced tables** (reverse-ETL) instead of the snapshot seed; read replicas for checkout QPS; **branching to stage candidate configs** demonstrated in `02_lakebase/branch_demo.py`. Connection pooling + token caching are in `dbx.py`.
- **Governance & scale**: combined-ratio depth (`combined_ratio` measure) added; demo self-telemetry in `demo_events`; UC lineage from decision → audit → served value; multi-region, multi-currency modeled and now rendered in local currency.

### Cost & lifecycle
An always-on App + a **provisioned** Lakebase (does **not** scale to zero) + a serverless warehouse bill 24/7 between engagements. Use `scripts/pause_demo.sh` to stop the app + downscale Lakebase, `resume_demo.sh` to bring it back, and `scripts/teardown.sh` / `rebuild.sh` for full lifecycle. Size exact $ with Quicksizer/Lakemeter. See `ONBOARDING.md` to stand it up in a new workspace and `COMPLIANCE.md` before showing it externally (operator is bolttech; all partners are fictional).

## Build status — ✅ complete & validated end-to-end
- [x] **00 — Data foundation** — 9 governed Delta tables (300k sessions, 60k policies, 7k claims); 3 funnel anomalies + a >85% loss-ratio trap tier; abandonment text clusters
- [x] **01 — Metric views + Genie** — 2 metric views; Genie space (8 objects, 6-rule instruction, 8 verified example SQL, 10 SQL benchmarks); hero question validated live
- [x] **02 — Lakebase** — Provisioned instance `attach-war-room-db`; serving `offer_config` synced + state tables; ACID ship validated; opens in the broken state
- [x] **03 — Agent** — tool-calling loop (14 tools), deterministic shadow-sim, Lakebase memory; full diagnose→ship flow validated
- [x] **04 — App** — FastAPI + React single app; browser-validated locally AND **deployed live to Databricks Apps** (provisioned Lakebase, SP resource-bindings + grants applied); full hero flow validated end-to-end as the SP (recovered GWP $8,192/mo, 0 console/network errors)
- [x] **README + EVAL**
- [x] **Reproducible deploy** — `install.py` deploys the App and resets the demo to the broken-Velora Telecom start; validated end-to-end

## Data & safety
All data is **synthetic** (deterministic hash-based generation; reproducible). No real customers, PII, carriers, or partner-confidential data. The **operator is bolttech** (the real company this demo was built for) and **all distribution partners are fictional** (Velora Telecom, Siam Mobile Care, Rift Valley Bank, Savanna Mobile, … are invented) — the synthetic figures describe the demo exchange, not any published bolttech KPI, so no fabricated metric is asserted of bolttech as fact and no real partner is shown attached to a fabricated loss ratio or "broken funnel." The fictional partner set is the **active default everywhere** (data, profile, Genie space, agent, offline fixtures); `partner_id` codes `P01`–`P12` are unchanged, so all anomalies/joins/EVAL hold. To re-skin to a different account, edit `config/demo_profile.json` and regenerate the data with matching names. See **`COMPLIANCE.md`** for the full posture and pre-demo checklist.
