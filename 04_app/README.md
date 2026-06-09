# 04 — The Databricks App (FastAPI + React, single app)

One Databricks App: **FastAPI** serves the built **Vite** bundle *and* hosts the agent loop, Genie/metric-view queries, and Lakebase state behind `/api`. All Databricks credentials stay server-side; the browser only talks to `/api`.

```
04_app/
├── app.yaml            # Databricks App config (command + env)
├── app.py              # FastAPI entry: routers + StaticFiles SPA serving
├── requirements.txt / pyproject.toml
├── server/
│   ├── dbx.py          # SDK-based access (warehouse, Lakebase, FMAPI, Genie) — env-aware
│   ├── tools.py        # agent tools (vendored from 03_agent)
│   ├── agent.py        # agent loop (vendored from 03_agent)
│   └── routes/         # chat (SSE), metrics, state, checkout
└── frontend/           # React + Vite (chat, funnel chart, guardrail gauge, checkout, GWP tile)
```

> `server/` vendors `dbx.py`/`tools.py`/`agent.py` so the app is self-contained for deployment. `dbx.py` is SDK-based and **env-aware**: local → CLI profile; in-app → auto-injected service-principal creds + `PG*` env. Same code both ways.

## API surface
| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | Agent turn, streamed as SSE events (`tool_call`/`tool_result`/`final`) |
| `GET /api/funnel` `?partner&device_tier` | Funnel diagnosis (recent vs prior stage rates) |
| `GET /api/lossratio`, `/api/overview`, `/api/abandonment`, `/api/genie` | Metric-view + Genie reads |
| `GET /api/checkout` `?partner&device_tier` | Live offer-config state the simulated checkout reads (Lakebase) |
| `GET /api/scenarios`, `/api/audit`, `/api/recovered_gwp` | Lakebase state |
| `POST /api/approve` `{scenario_id}` | Approve + ship — the multi-table ACID write |

## Local dev
```bash
# 1. backend (terminal A) — from 04_app/
DATABRICKS_PROFILE=DEFAULT uv run --with fastapi --with "uvicorn[standard]" --with psycopg2-binary \
  --with openai --with databricks-sdk --with pydantic uvicorn app:app --port 8000
# 2a. frontend dev (terminal B) — hot reload, proxies /api -> :8000
cd frontend && npm install && npm run dev          # http://localhost:5173
# 2b. OR production path: build once, FastAPI serves it
cd frontend && npm run build                        # -> frontend/dist ; reload http://127.0.0.1:8000
```
**Validated locally** against the real workspace (warehouse, Lakebase, Genie, FMAPIs): full diagnose→propose→approve→checkout-flip flow, recovered GWP **$8,192/mo**, 0 console/network errors.

## Deploy to Databricks Apps
```bash
databricks apps create attach-war-room -p DEFAULT
cd frontend && npm run build && cd ..                # ensure frontend/dist exists
databricks sync . /Workspace/Users/you@example.com/attach-war-room \
  --exclude node_modules --exclude .venv --exclude __pycache__ --exclude frontend/node_modules -p DEFAULT
databricks apps deploy attach-war-room \
  --source-code-path /Workspace/Users/you@example.com/attach-war-room -p DEFAULT
```

### Deployment status: LIVE ✓
Deployed at **https://attach-war-room-<workspace-id>.aws.databricksapps.com** as its service principal. What was configured (all done):
- **Resources bound** via `apps update`: SQL warehouse (CAN_USE) + both serving endpoints (CAN_QUERY).
- **Lakebase**: a **provisioned** instance `attach-war-room-db` (Autoscaling lacks the SP-OAuth role API / app-resource binding in this Beta). The SP is registered as a `DatabaseInstanceRole` (superuser) via the SDK, plus explicit `public`-schema table grants.
- **Unity Catalog**: `GRANT SELECT/USE` on `main.attach_war_room` to the SP.
- `app.yaml` sets `PGHOST/PGUSER/PGDATABASE/LAKEBASE_INSTANCE/LAKEBASE_TIER`; `dbx.py` mints the DB token via `w.database.generate_database_credential`.

All four ingredients validated end-to-end as the SP. Logs: `https://<app-url>/logz`.

#### Reference — equivalent SP grants (if re-creating):
1. **Bind resources** (`databricks apps update --json` or Apps UI): SQL warehouse (Can use); serving endpoints (Can query). For a provisioned Lakebase you can also bind it as a Database resource (Can connect).
2. **Lakebase role for the SP** — register via the SDK (`w.database.create_database_instance_role`) or grant in Postgres as owner:
   ```sql
   -- run against attach_war_room as the DB owner; replace <APP_SP_CLIENT_ID>
   GRANT CONNECT ON DATABASE attach_war_room TO "<APP_SP_CLIENT_ID>";
   GRANT USAGE ON SCHEMA public TO "<APP_SP_CLIENT_ID>";
   GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "<APP_SP_CLIENT_ID>";
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "<APP_SP_CLIENT_ID>";
   ```
3. **Unity Catalog grants for the SP** on `main.attach_war_room` (USE CATALOG/SCHEMA, SELECT) so the warehouse queries + Genie work under the SP identity.

Logs: `https://<app-url>/logz`.

## Production note — AI Gateway
For token/observability + guardrails, point the FMAPI client at the workspace **AI Gateway** URL (`https://<ws-id>.ai-gateway.cloud.databricks.com/mlflow/v1`) via an `AI_GATEWAY_URL` env var instead of the direct serving-endpoints path. The demo uses the direct (SDK `get_open_ai_client`) path for simplicity.
