#!/usr/bin/env bash
# =============================================================================
# resume_demo.sh — Attach War-Room demo cost lifecycle (Tier 3.6)
# Run with: bash scripts/resume_demo.sh
#
# Reverses pause_demo.sh ahead of an engagement:
#   1. Start the PROVISIONED Lakebase instance  (needs to be up before the App)
#   2. Start the Databricks App                 (attach-war-room)
#   3. Wait for the App URL to come up, then curl pre-warm /api/health
#      (wakes the serverless warehouse + Lakebase connection pool + agent so the
#       first live demo click is fast, not a cold-start)
#
# Nothing here is destructive. Re-running is safe (start is idempotent).
# =============================================================================
set -euo pipefail

PROFILE="${PROFILE:-DEFAULT}"
APP_NAME="${APP_NAME:-attach-war-room}"
LAKEBASE_INSTANCE="${LAKEBASE_INSTANCE:-attach-war-room-db}"
APP_URL="${APP_URL:-https://attach-war-room-<workspace-id>.aws.databricksapps.com}"
HEALTH_PATH="${HEALTH_PATH:-/api/health}"
WARM_RETRIES="${WARM_RETRIES:-30}"   # ~30 * 10s = up to 5 min for cold start

echo "Usage: bash scripts/resume_demo.sh   (env overrides: PROFILE, APP_NAME, LAKEBASE_INSTANCE, APP_URL)"
echo "Resuming Attach War-Room demo."
echo "  profile=${PROFILE}  app=${APP_NAME}  lakebase=${LAKEBASE_INSTANCE}"
echo

# --- 1. Start the PROVISIONED Lakebase instance first -----------------------
# The App's Lakebase resource binding expects the instance to be up; bring the
# Postgres compute back before the App so /api/checkout + state reads don't 500.
# Docs: https://docs.databricks.com/aws/en/oltp/
echo "[1/3] Starting PROVISIONED Lakebase instance '${LAKEBASE_INSTANCE}'..."
databricks database update-database-instance "${LAKEBASE_INSTANCE}" stopped \
  --stopped=false -p "${PROFILE}"
echo "      Lakebase start requested (provisioning compute can take a minute)."
echo

# --- 2. Start the Databricks App --------------------------------------------
echo "[2/3] Starting Databricks App '${APP_NAME}'..."
databricks apps start "${APP_NAME}" -p "${PROFILE}"
echo "      App start requested."
echo

# --- 3. Pre-warm /api/health ------------------------------------------------
echo "[3/3] Pre-warming ${APP_URL}${HEALTH_PATH} (up to ${WARM_RETRIES} tries)..."
# App auth: the App is OAuth-protected. A bare curl gets a 200 only if the
# caller is authenticated; an unauthenticated probe still proves the App
# compute is serving (it returns a login redirect / 401 rather than a
# connection error). For an authenticated health check, pass a token:
#   export DATABRICKS_TOKEN=$(databricks auth token -p ${PROFILE} | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
# and this script will send it as a Bearer header.
AUTH_HEADER=()
if [[ -n "${DATABRICKS_TOKEN:-}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${DATABRICKS_TOKEN}")
  echo "      (using DATABRICKS_TOKEN bearer for an authenticated probe)"
fi

attempt=0
until [[ "${attempt}" -ge "${WARM_RETRIES}" ]]; do
  attempt=$((attempt + 1))
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
    "${AUTH_HEADER[@]}" "${APP_URL}${HEALTH_PATH}" || echo "000")"
  echo "      attempt ${attempt}/${WARM_RETRIES}: HTTP ${code}"
  # 200 = healthy; 30x/401/403 = App is up but auth-gated (still a successful warm).
  if [[ "${code}" == "200" || "${code}" =~ ^3 || "${code}" == "401" || "${code}" == "403" ]]; then
    echo "      App is serving (HTTP ${code}). Pre-warm complete."
    break
  fi
  if [[ "${attempt}" -ge "${WARM_RETRIES}" ]]; then
    echo "      WARNING: App not serving after ${WARM_RETRIES} tries (last HTTP ${code})."
    echo "      Check: databricks apps get ${APP_NAME} -p ${PROFILE} -o json"
    echo "      Logs:  ${APP_URL}/logz"
    break
  fi
  sleep 10
done
echo

echo "Resumed. Open the demo:"
echo "  ${APP_URL}"
echo "Verify:"
echo "  databricks apps get ${APP_NAME} -p ${PROFILE} -o json                       # app_status.state == RUNNING"
echo "  databricks database get-database-instance ${LAKEBASE_INSTANCE} -p ${PROFILE} -o json   # state == AVAILABLE, effective_stopped=false"
