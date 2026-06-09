#!/usr/bin/env python3
"""Apply build_genie_space.py's serialized_space to the EXISTING Genie space (update in place).

Builds the serialized_space (which bakes in the current partner names + example SQL),
then calls genie.update_space(space_id, ...) so the SAME space id keeps working for the
app (no GENIE_SPACE_ID change). Idempotent — safe to re-run after editing build_genie_space.py.

Run:
    uv run --with databricks-sdk 01_metric_views_and_genie/apply_genie_space.py
"""
import os
import subprocess
import sys

from databricks.sdk import WorkspaceClient

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
WID = os.environ.get("WAREHOUSE_ID", "")
TITLE = os.environ.get("GENIE_TITLE", "Acme Attach War-Room — Conversion & Profitability")
DESC = ("Diagnose embedded-checkout attach and conversion across partners, markets, and products, "
        "and watch the loss-ratio guardrail. Built on governed metric views (synthetic data).")

# 1. build the serialized_space JSON (no network; bakes in the fictional names)
built = subprocess.run([sys.executable, os.path.join(HERE, "build_genie_space.py")],
                       capture_output=True, text=True, env={**os.environ})
if built.returncode != 0:
    sys.exit("build_genie_space.py failed:\n" + built.stderr[-2000:])
serialized = built.stdout.strip()
if not serialized.startswith("{"):
    sys.exit("build_genie_space.py did not emit JSON:\n" + serialized[:500])

# 2. update the existing space in place
w = WorkspaceClient(profile=PROFILE)
sp = w.genie.update_space(SPACE_ID, serialized_space=serialized, title=TITLE,
                          description=DESC, warehouse_id=WID)
print("Updated Genie space:", getattr(sp, "space_id", SPACE_ID))
print("  title:", getattr(sp, "title", TITLE))
