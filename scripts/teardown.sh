#!/usr/bin/env bash
# =============================================================================
# teardown.sh — Attach War-Room demo cost lifecycle (Tier 3.6)
# Run with: bash scripts/teardown.sh
#
# 🛑🛑🛑 DESTRUCTIVE 🛑🛑🛑
# Permanently deletes the deployed demo infrastructure:
#   1. The Databricks App        (attach-war-room)            — IRREVERSIBLE
#   2. The PROVISIONED Lakebase  instance (attach-war-room-db)   — IRREVERSIBLE
#      (drops the offer_config serving table + all state tables + their data)
#   3. OPTIONAL: the Unity Catalog schema (main.attach_war_room)
#      — the 9 governed Delta tables + the 2 metric views. OFF by default.
#
# Requires typing the word  DELETE  to confirm. Rebuild with:
#   bash scripts/rebuild.sh
#
# This does NOT delete the Genie space (cheap, no idle compute) — remove it by
# hand if you want a truly clean slate:
#   databricks api delete /api/2.0/genie/spaces/ -p <PROFILE>
# =============================================================================
set -euo pipefail

PROFILE="${PROFILE:-DEFAULT}"
APP_NAME="${APP_NAME:-attach-war-room}"
LAKEBASE_INSTANCE="${LAKEBASE_INSTANCE:-attach-war-room-db}"
SCHEMA_FQN="${SCHEMA_FQN:-main.attach_war_room}"
WAREHOUSE_ID="${WAREHOUSE_ID:-}"
# Set DROP_SCHEMA=1 to ALSO drop the UC schema + all Delta tables/metric views.
DROP_SCHEMA="${DROP_SCHEMA:-0}"

echo "Usage: bash scripts/teardown.sh   (env overrides: PROFILE, APP_NAME, LAKEBASE_INSTANCE, SCHEMA_FQN, DROP_SCHEMA=0|1)"
echo
echo "🛑 DESTRUCTIVE TEARDOWN — this will permanently DELETE:"
echo "    • Databricks App        : ${APP_NAME}"
echo "    • PROVISIONED Lakebase  : ${LAKEBASE_INSTANCE}  (all serving + state data)"
if [[ "${DROP_SCHEMA}" == "1" ]]; then
  echo "    • UC schema (DELTA DATA): ${SCHEMA_FQN}  (9 tables + 2 metric views) — DROP_SCHEMA=1"
else
  echo "    • UC schema             : KEPT (set DROP_SCHEMA=1 to also drop ${SCHEMA_FQN})"
fi
echo "    profile=${PROFILE}"
echo
echo "Type DELETE (all caps) to proceed, anything else to abort:"
read -r CONFIRM
if [[ "${CONFIRM}" != "DELETE" ]]; then
  echo "Aborted. Nothing was deleted."
  exit 1
fi
echo

# --- 1. Delete the Databricks App -------------------------------------------
echo "[1/3] Deleting Databricks App '${APP_NAME}'..."
databricks apps delete "${APP_NAME}" -p "${PROFILE}"
echo "      App deleted."
echo

# --- 2. Delete the PROVISIONED Lakebase instance ----------------------------
# Verified CLI verb: `databricks database delete-database-instance NAME`.
# This destroys the Postgres instance and ALL data in it (no soft-delete here).
# --force is needed only if the instance has PITR (point-in-time-recovery)
# descendant instances; it is harmless otherwise. Set LAKEBASE_FORCE=0 to drop it.
# Docs: https://docs.databricks.com/aws/en/oltp/
LAKEBASE_FORCE="${LAKEBASE_FORCE:-1}"
echo "[2/3] Deleting PROVISIONED Lakebase instance '${LAKEBASE_INSTANCE}'..."
if [[ "${LAKEBASE_FORCE}" == "1" ]]; then
  databricks database delete-database-instance "${LAKEBASE_INSTANCE}" --force -p "${PROFILE}"
else
  databricks database delete-database-instance "${LAKEBASE_INSTANCE}" -p "${PROFILE}"
fi
echo "      Lakebase instance deleted."
echo

# --- 3. (Optional) Drop the Unity Catalog schema ----------------------------
if [[ "${DROP_SCHEMA}" == "1" ]]; then
  echo "[3/3] Dropping UC schema '${SCHEMA_FQN}' (CASCADE — Delta data + metric views)..."
  # Second guard for the data-destroying step (highest-value asset to rebuild).
  echo "      Re-confirm: type DELETE again to drop the schema, anything else to skip:"
  read -r CONFIRM2
  if [[ "${CONFIRM2}" == "DELETE" ]]; then
    databricks api post /api/2.0/sql/statements -p "${PROFILE}" --json "{
      \"warehouse_id\": \"${WAREHOUSE_ID}\",
      \"statement\": \"DROP SCHEMA IF EXISTS ${SCHEMA_FQN} CASCADE\",
      \"wait_timeout\": \"50s\"
    }"
    echo "      Schema drop submitted (DROP SCHEMA IF EXISTS ${SCHEMA_FQN} CASCADE)."
  else
    echo "      Schema drop SKIPPED (re-confirm did not match). ${SCHEMA_FQN} kept."
  fi
else
  echo "[3/3] Skipping UC schema drop (DROP_SCHEMA != 1). ${SCHEMA_FQN} kept."
fi
echo

echo "Teardown complete."
echo "Rebuild everything with: bash scripts/rebuild.sh"
