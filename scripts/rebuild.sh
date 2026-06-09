#!/usr/bin/env bash
# =============================================================================
# rebuild.sh — Attach War-Room demo cost lifecycle (Tier 3.6)
# Run with: bash scripts/rebuild.sh
#
# Full recreate of the demo from scratch, running stages 00 -> 04 IN ORDER using
# the documented `uv` commands (see each stage's README). Use this after a
# teardown.sh, or to stand the demo up in a fresh workspace.
#
# Stages:
#   00  data foundation       — 9 governed Delta tables (sessions/policies/claims…)
#   01  metric views + Genie  — funnel_metrics + profitability_metrics; Genie space
#   02  Lakebase (provisioned)— instance + DB + schema + seed from Delta golden copy
#   03  agent smoke test      — one CLI turn to prove the tool-calling loop works
#   04  App build + deploy     — Vite build, sync to Workspace, deploy to Apps
#
# Each stage runs from the repo root. uv resolves deps per-invocation (--with …),
# so no venv juggling. Set SKIP_GENIE_SPACE=1 / SKIP_DEPLOY=1 to stop short.
# Provisioning the Lakebase INSTANCE itself (if it does not exist) is a one-time
# step flagged below — by default this assumes the provisioned instance exists.
# =============================================================================
set -euo pipefail

PROFILE="${PROFILE:-DEFAULT}"
export DATABRICKS_PROFILE="${PROFILE}"   # 00/01/02/03 helpers read this
APP_NAME="${APP_NAME:-attach-war-room}"
LAKEBASE_INSTANCE="${LAKEBASE_INSTANCE:-attach-war-room-db}"
WORKSPACE_USER="${WORKSPACE_USER:-you@example.com}"
WORKSPACE_PATH="${WORKSPACE_PATH:-/Workspace/Users/${WORKSPACE_USER}/attach-war-room}"
SKIP_GENIE_SPACE="${SKIP_GENIE_SPACE:-0}"   # 1 = skip the manual Genie-space create step
SKIP_DEPLOY="${SKIP_DEPLOY:-0}"             # 1 = build/seed only, do not deploy the App

# Resolve repo root from this script's location (scripts/ lives at the repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Usage: bash scripts/rebuild.sh   (env overrides: PROFILE, APP_NAME, LAKEBASE_INSTANCE, WORKSPACE_USER, SKIP_GENIE_SPACE=0|1, SKIP_DEPLOY=0|1)"
echo "Rebuilding Attach War-Room from stages 00 -> 04."
echo "  profile=${PROFILE}  repo=${REPO_ROOT}"
echo "  app=${APP_NAME}  lakebase=${LAKEBASE_INSTANCE}  workspace_path=${WORKSPACE_PATH}"
echo

cd "${REPO_ROOT}"

# --- 00. Data foundation -----------------------------------------------------
echo "===================================================================="
echo "[00/04] Data foundation — 9 governed Delta tables (idempotent rebuild)"
echo "===================================================================="
uv run --with databricks-sdk 00_setup/run_setup.py
echo

# --- 01. Metric views + Genie ------------------------------------------------
echo "===================================================================="
echo "[01/04] Metric views (funnel_metrics + profitability_metrics)"
echo "===================================================================="
uv run --with databricks-sdk \
  01_metric_views_and_genie/run_sql.py 01_metric_views_and_genie/metric_views.sql

if [[ "${SKIP_GENIE_SPACE}" == "1" ]]; then
  echo
  echo "[01/04] SKIP_GENIE_SPACE=1 — leaving the existing Genie space in place."
  echo "        (The metric views the space sits on were just rebuilt above,"
  echo "         so it keeps working.)"
else
  echo
  echo "[01/04] Building serialized_space for the Genie space..."
  uv run --with databricks-sdk \
    01_metric_views_and_genie/build_genie_space.py > /tmp/awr_serialized_space.json
  echo "        Wrote /tmp/awr_serialized_space.json."
  echo "        NOTE: creating a NEW Genie space is a manual POST (it mints a new"
  echo "        space_id you must wire back into config.yaml + 04_app/app.yaml)."
  echo "        Only do this if the space was deleted. Exact command (01 README):"
  echo
  echo "          jq -n --arg title \"Acme Attach War-Room — Conversion & Profitability\" \\"
  echo "            --arg description \"Diagnose embedded-checkout attach and conversion; watch the loss-ratio guardrail.\" \\"
  echo "            --arg parent_path \"/Workspace/Users/${WORKSPACE_USER}\" --arg warehouse_id \"\" \\"
  echo "            --rawfile serialized_space /tmp/awr_serialized_space.json \\"
  echo "            '{title:\$title,description:\$description,parent_path:\$parent_path,warehouse_id:\$warehouse_id,serialized_space:\$serialized_space}' \\"
  echo "            > /tmp/create_genie_space.json"
  echo "          databricks api post /api/2.0/genie/spaces --profile ${PROFILE} --json @/tmp/create_genie_space.json"
fi
echo

# --- 02. Lakebase (provisioned) ----------------------------------------------
echo "===================================================================="
echo "[02/04] Lakebase — seed offer_config + state into the provisioned DB"
echo "===================================================================="
echo "Checking the provisioned instance '${LAKEBASE_INSTANCE}' exists..."
if databricks database get-database-instance "${LAKEBASE_INSTANCE}" -p "${PROFILE}" >/dev/null 2>&1; then
  echo "  instance present — proceeding to seed."
else
  echo "  instance NOT found. Create it ONCE (provisioned tier, smallest SKU CU_1):"
  echo "    databricks database create-database-instance --json '{\"name\":\"${LAKEBASE_INSTANCE}\",\"capacity\":\"CU_1\"}' -p ${PROFILE}"
  echo "  then re-run this script. (Provisioned tier is required for the App's"
  echo "  SP-OAuth DatabaseInstanceRole + resource binding — see config.yaml.)"
  exit 1
fi
# setup_provisioned.py registers the SP/owner roles, creates the DB, applies
# schema.sql, and snapshot-seeds offer_config + alert_thresholds from the Delta
# golden copy (opens in the broken Velora Telecom-mid state — see 02 README).
uv run --with databricks-sdk --with psycopg2-binary 02_lakebase/setup_provisioned.py
echo

# --- 03. Agent smoke test ----------------------------------------------------
echo "===================================================================="
echo "[03/04] Agent smoke test — one CLI turn through the tool-calling loop"
echo "===================================================================="
uv run --with databricks-sdk --with openai --with psycopg2-binary \
  03_agent/agent.py "Why did attach drop for Velora Telecom mid-tier?"
echo

# --- 04. App build + deploy --------------------------------------------------
echo "===================================================================="
echo "[04/04] App — build the Vite frontend, then deploy to Databricks Apps"
echo "===================================================================="
echo "Building frontend bundle (frontend/dist)..."
( cd 04_app/frontend && npm install && npm run build )
echo "  frontend/dist built."

if [[ "${SKIP_DEPLOY}" == "1" ]]; then
  echo
  echo "[04/04] SKIP_DEPLOY=1 — built only. Run locally with (from 04_app/):"
  echo "  DATABRICKS_PROFILE=${PROFILE} uv run --with fastapi --with \"uvicorn[standard]\" \\"
  echo "    --with psycopg2-binary --with openai --with databricks-sdk --with pydantic \\"
  echo "    uvicorn app:app --port 8000"
else
  echo
  echo "Ensuring App '${APP_NAME}' exists..."
  if databricks apps get "${APP_NAME}" -p "${PROFILE}" >/dev/null 2>&1; then
    echo "  App present — will re-deploy a new version."
  else
    echo "  App not found — creating it."
    databricks apps create "${APP_NAME}" -p "${PROFILE}"
    echo "  NOTE: a freshly created App needs its resource bindings + grants"
    echo "  (SQL warehouse CAN_USE, serving CAN_QUERY, Lakebase role, UC SELECT)"
    echo "  re-applied as documented in 04_app/README.md before the hero flow works."
  fi
  echo
  echo "Syncing source to ${WORKSPACE_PATH}..."
  databricks sync 04_app "${WORKSPACE_PATH}" \
    --exclude node_modules --exclude .venv --exclude __pycache__ \
    --exclude frontend/node_modules -p "${PROFILE}"
  echo
  echo "Deploying App '${APP_NAME}'..."
  databricks apps deploy "${APP_NAME}" \
    --source-code-path "${WORKSPACE_PATH}" -p "${PROFILE}"
fi
echo

echo "Rebuild complete."
echo "Open the demo:  databricks apps get ${APP_NAME} -p ${PROFILE} -o json   # url + app_status.state"
echo "If you just resumed from a pause instead, use: bash scripts/resume_demo.sh"
