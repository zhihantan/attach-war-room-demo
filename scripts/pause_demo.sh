#!/usr/bin/env bash
# =============================================================================
# pause_demo.sh — Attach War-Room demo cost lifecycle (Tier 3.6)
# Run with: bash scripts/pause_demo.sh
#
# Stops idle DBU burn BETWEEN engagements without destroying anything:
#   1. Stop the Databricks App  (attach-war-room)        -> stops App compute
#   2. Stop the PROVISIONED Lakebase instance            -> stops Postgres compute
#      (provisioned Lakebase does NOT scale to zero on its own)
# Resume with: bash scripts/resume_demo.sh
# Nothing is deleted — data, config, grants, and bindings all survive a pause.
#
# -----------------------------------------------------------------------------
# 💸 COST NOTE  (READ ME)
# -----------------------------------------------------------------------------
# This demo's always-on App + provisioned Lakebase + serverless SQL warehouse
# bill 24/7. The App and the provisioned Lakebase instance burn DBUs whenever
# they are RUNNING — even with zero traffic. The serverless warehouse bills
# per query (auto-suspends when idle) but is woken by any /api/* read.
#   -> PAUSE between engagements. This script handles the two always-on pieces.
#   -> The warehouse is left alone (it auto-suspends; pausing it would only slow
#      the next resume's pre-warm). Stop it in the SQL Warehouses UI if needed.
#   -> For exact $ figures use Quicksizer (broad sizing) or Lakemeter
#      (precise per-SKU costs) — do NOT eyeball DBU rates.
# What a pause does NOT stop: nothing else bills meaningfully while paused.
# =============================================================================
set -euo pipefail

PROFILE="${PROFILE:-DEFAULT}"
APP_NAME="${APP_NAME:-attach-war-room}"
LAKEBASE_INSTANCE="${LAKEBASE_INSTANCE:-attach-war-room-db}"

echo "Usage: bash scripts/pause_demo.sh   (env overrides: PROFILE, APP_NAME, LAKEBASE_INSTANCE)"
echo "Pausing Attach War-Room demo to cut idle DBU burn."
echo "  profile=${PROFILE}  app=${APP_NAME}  lakebase=${LAKEBASE_INSTANCE}"
echo

# --- 1. Stop the Databricks App ---------------------------------------------
echo "[1/2] Stopping Databricks App '${APP_NAME}'..."
databricks apps stop "${APP_NAME}" -p "${PROFILE}"
echo "      App stop requested (App compute will spin down)."
echo

# --- 2. Stop the PROVISIONED Lakebase instance ------------------------------
# Verified against CLI v0.299.x: `databricks database update-database-instance`
# takes NAME + an UPDATE_MASK positional, plus a boolean `--stopped` flag.
# Setting stopped=true halts the provisioned Postgres compute (the only way to
# zero out its DBU burn — provisioned tier does not auto-scale-to-zero).
# Docs: https://docs.databricks.com/aws/en/oltp/  (Lakebase / Database Instances)
#       CLI ref: https://docs.databricks.com/aws/en/dev-tools/cli/
echo "[2/2] Stopping PROVISIONED Lakebase instance '${LAKEBASE_INSTANCE}'..."
databricks database update-database-instance "${LAKEBASE_INSTANCE}" stopped \
  --stopped -p "${PROFILE}"
echo "      Lakebase stop requested."
echo
echo "      (Alternative if you prefer to keep it reachable but cheaper: instead"
echo "       of stopping, DOWNSCALE capacity. The smallest provisioned size is"
echo "       already CU_1 — there is no smaller provisioned SKU, so stopping is"
echo "       the only way to actually zero the spend. To downscale a larger one:"
echo "         databricks database update-database-instance ${LAKEBASE_INSTANCE} capacity --capacity CU_1 -p ${PROFILE} )"
echo

echo "Paused. Verify state:"
echo "  databricks apps get ${APP_NAME} -p ${PROFILE} -o json   # app_status.state"
echo "  databricks database get-database-instance ${LAKEBASE_INSTANCE} -p ${PROFILE} -o json   # effective_stopped=true"
echo
echo "Resume with: bash scripts/resume_demo.sh"
