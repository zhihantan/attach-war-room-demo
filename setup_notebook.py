# Databricks notebook source
# MAGIC %md
# MAGIC # Attach War-Room — in-workspace setup notebook
# MAGIC
# MAGIC Stand up the entire demo **from inside your own Databricks workspace** — no local CLI needed.
# MAGIC It authenticates as **you** (the user running this notebook) and provisions everything:
# MAGIC synthetic data → metric views → a new AI/BI Genie space → a Lakebase instance → the Databricks App.
# MAGIC
# MAGIC ### How to use
# MAGIC 1. **Clone this repo into a Git folder**: Workspace → *Create → Git folder* →
# MAGIC    `https://github.com/zhihantan/attach-war-room-demo`.
# MAGIC 2. Open **`setup_notebook`** (this file) from the cloned folder.
# MAGIC 3. Attach to **serverless** or a **cluster** (DBR 14+), set the widgets at the top, then **Run All**.
# MAGIC 4. ~10–15 minutes later it prints the App URL.
# MAGIC
# MAGIC ### You need permission to
# MAGIC create a schema (or use an existing catalog), create a **Lakebase** instance, create a **Genie**
# MAGIC space, and create/deploy a **Databricks App**; plus a serverless SQL warehouse and the FMAPI
# MAGIC endpoints `databricks-claude-sonnet-4-6` / `databricks-claude-haiku-4-5` in your region.
# MAGIC
# MAGIC > Prefer a local terminal instead? Use `install.py` — see **IMPORT_AND_RUN.md**. This notebook
# MAGIC > runs the *same* installer logic, just in-workspace.

# COMMAND ----------
# MAGIC %pip install -q --upgrade "databricks-sdk>=0.76" psycopg2-binary
# MAGIC # NOTE: the version floor is required. DBR ships an older databricks-sdk, and an
# MAGIC # unpinned `pip install databricks-sdk` is a no-op when any version is already present —
# MAGIC # leaving a stale SDK whose Genie API lacks create_space (needed in step 4/7).

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
# Parameters — edit these (widgets appear at the top of the notebook).
dbutils.widgets.text("catalog", "bolttech_workshop_demo", "1 · Catalog (created if you can; else must exist)")
dbutils.widgets.text("schema", "attach_war_room", "2 · Schema")
dbutils.widgets.text("warehouse_id", "", "3 · SQL warehouse id (blank = auto-pick serverless)")
dbutils.widgets.text("lakebase_instance", "attach-war-room-db", "4 · Lakebase instance name")
dbutils.widgets.text("lakebase_database", "attach_war_room", "5 · Lakebase database name")
dbutils.widgets.text("app_name", "attach-war-room", "6 · Databricks App name")
dbutils.widgets.dropdown("lakebase_tier", "provisioned", ["provisioned", "autoscaling"],
                         "7 · Lakebase connection tier (same instance; 'autoscaling' uses the postgres endpoint API)")

# COMMAND ----------
import argparse
import os
import sys

# Locate the cloned repo root from this notebook's path (the notebook lives at the repo root),
# and make the installer + its agent modules importable.
nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
REPO_ROOT = "/Workspace" + os.path.dirname(nb_path)
sys.path.insert(0, os.path.join(REPO_ROOT, "03_agent"))
sys.path.insert(0, REPO_ROOT)
print("repo root:", REPO_ROOT)
assert os.path.isfile(os.path.join(REPO_ROOT, "install.py")), \
    "install.py not found — run this notebook from the cloned attach-war-room repo (Git folder)."

# COMMAND ----------
import install

P = dbutils.widgets.get
args = argparse.Namespace(
    profile="",                          # empty -> use the notebook's runtime auth (you)
    catalog=P("catalog"), schema=P("schema"), warehouse_id=P("warehouse_id"),
    lakebase_instance=P("lakebase_instance"), lakebase_database=P("lakebase_database"),
    lakebase_capacity="CU_1", lakebase_tier=P("lakebase_tier"),
    lakebase_branch="production", lakebase_endpoint="primary",
    app_name=P("app_name"), genie_parent="",
    model_agent="databricks-claude-sonnet-4-6", model_classifier="databricks-claude-haiku-4-5",
    only="", skip="", teardown=False, drop_schema=False, yes=True,
)
ctx = install.Ctx(args)
ctx.workspace_src = os.path.join(REPO_ROOT, "04_app")   # deploy the App from the in-workspace repo
ctx.export_env()
print("Installing ->", ctx.schema_fqn)

# COMMAND ----------
# Preflight: confirm auth, resolve a warehouse, check the FMAPI endpoints.
install.preflight(ctx)
ctx.export_env()

# COMMAND ----------
# 1/7 — Unity Catalog schema
install.step_schema(ctx)

# COMMAND ----------
# 2/7 — synthetic data (~300k sessions; a couple of minutes)
install.step_data(ctx)

# COMMAND ----------
# 3/7 — metric views
install.step_metrics(ctx)

# COMMAND ----------
# 4/7 — a new AI/BI Genie space on the metric views
install.step_genie(ctx)

# COMMAND ----------
# 5/7 — provisioned Lakebase instance + seeded offer_config (this is the slow step)
install.step_lakebase(ctx)

# COMMAND ----------
# 6/7 — the Databricks App: create, bind resources, grant the app SP, deploy from this repo
install.step_app(ctx)

# COMMAND ----------
# 7/7 — set the demo to its broken-start state
install.step_reset(ctx)
ctx.save()

# COMMAND ----------
# MAGIC %md ## ✅ Done
print("Schema:  ", ctx.schema_fqn)
print("Genie:   ", ctx.genie_space_id)
print("Lakebase:", ctx.lakebase_instance, f"({ctx.pg_host})")
print("App:     ", ctx.app_name, " ", ctx.app_url)
displayHTML(f'<h3>Open your demo: <a href="{ctx.app_url}" target="_blank">{ctx.app_url}</a></h3>') if ctx.app_url else None
