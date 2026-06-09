# 03 — Agent (tool-calling loop on Foundation Model APIs)

A stateful **diagnose → shadow-test → approve → ship → verify** agent. FMAPI does the reasoning; tools hit the metric views, Lakebase, and Genie. Conversation is persisted to Lakebase for continuity. The FastAPI backend imports `stream_turn` / `run_turn`.

## Files
| File | Purpose |
|---|---|
| `dbx.py` | Single access layer: warehouse SQL (typed), Lakebase conn, FMAPI client, Genie ask |
| `tools.py` | 11 agent tools + OpenAI tool schemas |
| `agent.py` | System prompt + tool-calling loop + Lakebase chat memory; `stream_turn`/`run_turn` |

## Models (FMAPIs, configurable)
- Reasoning/orchestration: `databricks-claude-sonnet-4-6`
- Abandonment-cause classification: `databricks-claude-haiku-4-5`

## Tools
`partner_funnel_overview`, `funnel_diagnosis`, `abandonment_reasons`, `classify_abandonment` (FMAPI), `get_offer_config` (Lakebase), `loss_ratio_for`, `run_shadow_sim`, `propose_offer_change` (Lakebase), `ship_offer_change` (**multi-table ACID**), `query_genie`, `draft_partner_note` (FMAPI).

## Shadow sim = deterministic
`run_shadow_sim` restores the broken funnel stage to its healthy *prior* rate and replays it over recent traffic — defensible, reproducible numbers. FMAPI is used for the genuinely-LLM work (classification, drafting), never to invent KPIs. Production would swap in a trained attach-propensity model behind the same tool.

## Validated hero flow (Velora Telecom mid-tier)
diagnosis: offer-shown 0.53 vs 0.866 prior, conversion-of-shown held → impressions problem · loss ratio 0.29 (within 0.70 guardrail) · sim: attach **0.152 → 0.249 (+9.7pts)**, **$8.2k/mo recovered GWP** · propose → approve → ship: 5 configs flipped, audit written, scenario shipped — one ACID transaction.

## Run (CLI)
```bash
uv run --with databricks-sdk --with openai --with psycopg2-binary 03_agent/agent.py "Why did attach drop for Velora Telecom mid-tier? Shadow-test a fix." conv-id
```

## Production hardening
The demo runs the loop in-process (FastAPI hosts it). For production, register it as an MLflow **`ResponsesAgent`** in Unity Catalog and serve it on a Model Serving endpoint (gives versioning, Agent Evaluation, scaling, governance), and move FMAPIs to **provisioned throughput** for SLAs. The tool layer (`tools.py`) is unchanged.
