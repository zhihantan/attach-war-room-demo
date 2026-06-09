#!/usr/bin/env python3
"""Attach War-Room — one-command installer for ANY Databricks workspace.

Stands the whole demo up from scratch, parameterized — nothing is hardwired to the
workspace it was built in. Steps (each idempotent; re-run to repair):

  schema   UC catalog (created if you can) + schema
  data     9 synthetic Delta tables           (00_setup/generate_data.sql)
  metrics  funnel_metrics + profitability_metrics (01_.../metric_views.sql)
  genie    a NEW AI/BI Genie space on the metric views      -> captures space_id
  lakebase a provisioned Lakebase instance + db + seeded offer_config -> captures host
  app      create the Databricks App with bound resources (warehouse, FMAPI serving
           endpoints, the new Genie space, the new Lakebase db, UC SELECT for the app
           service principal), generate its env (app.yaml) with the captured ids, deploy
  reset    set the demo to the broken-start state

Requires: the `databricks` CLI (authenticated to the target workspace) + Python deps
databricks-sdk and psycopg2-binary. Run from the attach-war-room/ directory:

  uv run --with databricks-sdk --with psycopg2-binary install.py \
      --profile <CLI_PROFILE> --catalog <CATALOG> --schema attach_war_room

  install.py --only schema,data,metrics      # run a subset
  install.py --teardown                       # remove app + lakebase + (with --drop-schema) the data

See IMPORT_AND_RUN.md for the full guide, prerequisites, and permissions.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg, end="\n"):
    print(msg, end=end, flush=True)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Install the Attach War-Room demo into a Databricks workspace.")
    p.add_argument("--profile", default=os.environ.get("DATABRICKS_PROFILE", "DEFAULT"),
                   help="Databricks CLI auth profile for the TARGET workspace (must be logged in).")
    p.add_argument("--catalog", default=os.environ.get("AWR_CATALOG", "attach_war_room"),
                   help="Unity Catalog catalog. Created if you have CREATE CATALOG; else must already exist.")
    p.add_argument("--schema", default=os.environ.get("AWR_SCHEMA", "attach_war_room"),
                   help="Schema for the demo tables + metric views.")
    p.add_argument("--warehouse-id", default=os.environ.get("AWR_WAREHOUSE_ID", ""),
                   help="SQL warehouse id. If omitted, the first available serverless warehouse is used.")
    p.add_argument("--lakebase-instance", default=os.environ.get("AWR_LAKEBASE_INSTANCE", "attach-war-room-db"))
    p.add_argument("--lakebase-database", default=os.environ.get("AWR_LAKEBASE_DATABASE", "attach_war_room"))
    p.add_argument("--lakebase-capacity", default=os.environ.get("AWR_LAKEBASE_CAPACITY", "CU_1"))
    p.add_argument("--app-name", default=os.environ.get("AWR_APP_NAME", "attach-war-room"))
    p.add_argument("--genie-parent", default=os.environ.get("AWR_GENIE_PARENT", ""),
                   help="Workspace folder for the Genie space (default: /Workspace/Users/<you>).")
    p.add_argument("--model-agent", default=os.environ.get("MODEL_AGENT", "databricks-claude-sonnet-4-6"))
    p.add_argument("--model-classifier", default=os.environ.get("MODEL_CLASSIFIER", "databricks-claude-haiku-4-5"))
    p.add_argument("--only", default="", help="comma list of steps to run (default: all)")
    p.add_argument("--skip", default="", help="comma list of steps to skip")
    p.add_argument("--teardown", action="store_true", help="delete the app + lakebase instance (+ schema with --drop-schema)")
    p.add_argument("--drop-schema", action="store_true", help="(teardown) also DROP the UC schema + all data")
    p.add_argument("--yes", action="store_true", help="skip confirmation prompts")
    return p.parse_args()


STEPS = ["schema", "data", "metrics", "genie", "lakebase", "app", "reset"]


class Ctx:
    """Resolved config + captured ids, shared across steps and persisted to install_state.json."""
    def __init__(self, args):
        self.args = args
        self.profile = args.profile
        self.catalog = args.catalog
        self.schema = args.schema
        self.schema_fqn = f"{args.catalog}.{args.schema}"
        self.warehouse_id = args.warehouse_id
        self.lakebase_instance = args.lakebase_instance
        self.lakebase_database = args.lakebase_database
        self.app_name = args.app_name
        self.genie_space_id = ""
        self.pg_host = ""
        self.app_sp = ""           # the app's service-principal client id (set after create)
        self.app_url = ""
        # set by the in-workspace setup notebook to the repo's 04_app path already in the
        # workspace; when set, step_app deploys directly from it via the SDK (no local sync).
        self.workspace_src = None
        self.state_path = os.path.join(HERE, "install_state.json")
        self._load()

    def _load(self):
        if os.path.isfile(self.state_path):
            try:
                s = json.load(open(self.state_path))
                self.genie_space_id = s.get("genie_space_id", "")
                self.pg_host = s.get("pg_host", "")
                self.app_sp = s.get("app_sp", "")
            except Exception:
                pass

    def save(self):
        json.dump({"schema_fqn": self.schema_fqn, "warehouse_id": self.warehouse_id,
                   "lakebase_instance": self.lakebase_instance, "genie_space_id": self.genie_space_id,
                   "pg_host": self.pg_host, "app_sp": self.app_sp, "app_name": self.app_name},
                  open(self.state_path, "w"), indent=2)

    def export_env(self):
        """Env the vendored dbx/scripts read — set BEFORE importing dbx."""
        os.environ["DATABRICKS_PROFILE"] = self.profile
        os.environ["SCHEMA_FQN"] = self.schema_fqn
        os.environ["WAREHOUSE_ID"] = self.warehouse_id
        os.environ["LAKEBASE_INSTANCE"] = self.lakebase_instance
        os.environ["LAKEBASE_TIER"] = "provisioned"
        os.environ["PGDATABASE"] = self.lakebase_database
        os.environ["MODEL_AGENT"] = self.args.model_agent
        os.environ["MODEL_CLASSIFIER"] = self.args.model_classifier
        if self.genie_space_id:
            os.environ["GENIE_SPACE_ID"] = self.genie_space_id
        # never inherit the build workspace's PG*/gateway
        for k in ("PGHOST", "PGUSER", "PGPASSWORD", "AI_GATEWAY_URL"):
            os.environ.pop(k, None)
        if self.pg_host:
            os.environ["PGHOST"] = self.pg_host


def cli(ctx, *args, json_out=True, check=True):
    """Run a databricks CLI command against the target profile."""
    cmd = ["databricks", *args, "-p", ctx.profile]
    if json_out:
        cmd += ["-o", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"`{' '.join(cmd)}` failed:\n{r.stderr.strip()[:600]}")
    if json_out and r.stdout.strip():
        try:
            return json.loads(r.stdout)
        except Exception:
            return r.stdout
    return r.stdout


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------
def run_sql_file(dbx, path):
    raw = open(path).read().replace("{{S}}", dbx.SCHEMA)
    stmts = [s for s in (b.strip() for b in raw.split("-- @@"))
             if s and any(ln.strip() and not ln.strip().startswith("--") for ln in s.splitlines())]
    for i, stmt in enumerate(stmts, 1):
        dbx.warehouse_query(stmt) if stmt.lstrip().upper().startswith("SELECT") else _exec(dbx, stmt)
        log(f"    [{i}/{len(stmts)}] ok")


def _exec(dbx, stmt):
    """Execute a non-SELECT statement on the warehouse (no rows expected)."""
    w = dbx.ws()
    resp = w.statement_execution.execute_statement(warehouse_id=dbx.WAREHOUSE_ID, statement=stmt, wait_timeout="50s")
    deadline = time.time() + 900
    while getattr(resp.status.state, "value", str(resp.status.state)) in ("PENDING", "RUNNING"):
        if time.time() > deadline:
            raise TimeoutError("statement timed out")
        time.sleep(2)
        resp = w.statement_execution.get_statement(resp.statement_id)
    state = getattr(resp.status.state, "value", str(resp.status.state))
    if state != "SUCCEEDED":
        err = resp.status.error
        raise RuntimeError(f"SQL failed: {err.message if err else state}\n{stmt[:300]}")


def preflight(ctx):
    import dbx
    w = dbx.ws()
    me = w.current_user.me().user_name
    log(f"  authenticated as {me} on {w.config.host}")
    if not ctx.args.genie_parent:
        ctx.args.genie_parent = f"/Workspace/Users/{me}"
    # resolve a warehouse if none given
    if not ctx.warehouse_id:
        whs = [x for x in w.warehouses.list()]
        serverless = [x for x in whs if getattr(x, "enable_serverless_compute", False)] or whs
        if not serverless:
            raise SystemExit("No SQL warehouse found — create one or pass --warehouse-id.")
        ctx.warehouse_id = serverless[0].id
        log(f"  using warehouse {serverless[0].name} ({ctx.warehouse_id})")
    os.environ["WAREHOUSE_ID"] = ctx.warehouse_id
    dbx.WAREHOUSE_ID = ctx.warehouse_id   # dbx cached this at import (before resolution)
    # FMAPI endpoints present?
    names = {e.name for e in w.serving_endpoints.list()}
    for m in (ctx.args.model_agent, ctx.args.model_classifier):
        log(f"  FMAPI {m}: {'available' if m in names else 'NOT FOUND — check region/enablement'}")
    log(f"  target: schema {ctx.schema_fqn} · lakebase {ctx.lakebase_instance} · app {ctx.app_name}")


def step_schema(ctx):
    import dbx
    try:
        _exec(dbx, f"CREATE CATALOG IF NOT EXISTS {ctx.catalog}")
        log(f"  catalog {ctx.catalog} ok")
    except Exception as e:
        log(f"  catalog {ctx.catalog}: not created ({str(e)[:80]}) — assuming it already exists")
    _exec(dbx, f"CREATE SCHEMA IF NOT EXISTS {ctx.schema_fqn}")
    log(f"  schema {ctx.schema_fqn} ok")


def step_data(ctx):
    import dbx
    run_sql_file(dbx, os.path.join(HERE, "00_setup", "generate_data.sql"))
    n = dbx.warehouse_query(f"SELECT count(*) c FROM {dbx.SCHEMA}.sessions")[0]["c"]
    log(f"  data ok — {n} sessions")


def step_metrics(ctx):
    import dbx
    run_sql_file(dbx, os.path.join(HERE, "01_metric_views_and_genie", "metric_views.sql"))
    log("  metric views ok")


def step_genie(ctx):
    import dbx
    # build the serialized_space (bakes in the schema's partner names + example SQL)
    built = subprocess.run([sys.executable, os.path.join(HERE, "01_metric_views_and_genie", "build_genie_space.py")],
                           capture_output=True, text=True, env={**os.environ})
    if built.returncode != 0:
        raise RuntimeError("build_genie_space.py failed:\n" + built.stderr[-800:])
    serialized = built.stdout.strip()
    w = dbx.ws()
    sp = w.genie.create_space(warehouse_id=ctx.warehouse_id, serialized_space=serialized,
                              title="Acme Attach War-Room — Conversion & Profitability",
                              description="Diagnose embedded-checkout attach/conversion + loss-ratio guardrail (synthetic).",
                              parent_path=ctx.args.genie_parent)
    ctx.genie_space_id = sp.space_id
    os.environ["GENIE_SPACE_ID"] = ctx.genie_space_id
    ctx.save()
    log(f"  Genie space created: {ctx.genie_space_id}")


def _wait_instance(w, name, target="AVAILABLE", timeout=1200):
    t0 = time.time()
    while True:
        inst = w.database.get_database_instance(name=name)
        state = getattr(inst.state, "value", str(inst.state))
        if state == target:
            return inst
        if state in ("FAILING_OVER",) or time.time() - t0 > timeout:
            raise TimeoutError(f"instance {name} state={state} after {int(time.time()-t0)}s")
        log(f"    lakebase {name}: {state} …", end="\r")
        time.sleep(10)


def step_lakebase(ctx):
    import dbx
    import databricks.sdk.service.database as d
    w = dbx.ws()
    # create the provisioned instance if missing
    try:
        w.database.get_database_instance(name=ctx.lakebase_instance)
        log(f"  lakebase instance {ctx.lakebase_instance} exists")
    except Exception:
        log(f"  creating lakebase instance {ctx.lakebase_instance} ({ctx.args.lakebase_capacity}) …")
        w.database.create_database_instance(d.DatabaseInstance(name=ctx.lakebase_instance, capacity=ctx.args.lakebase_capacity))
    inst = _wait_instance(w, ctx.lakebase_instance)
    ctx.pg_host = inst.read_write_dns
    os.environ["PGHOST"] = ctx.pg_host
    ctx.save()
    log(f"  lakebase available — host {ctx.pg_host}")
    # register the installer's own role (superuser), create db, apply schema, seed
    me = w.current_user.me().user_name
    _ensure_role(w, ctx.lakebase_instance, me, d.DatabaseInstanceRoleIdentityType.USER)
    time.sleep(4)
    _seed_lakebase(ctx, dbx)
    log("  lakebase seeded (offer_config + thresholds + demo_events)")


def _ensure_role(w, instance, name, identity_type):
    import databricks.sdk.service.database as d
    try:
        w.database.create_database_instance_role(instance, d.DatabaseInstanceRole(
            name=name, identity_type=identity_type,
            membership_role=d.DatabaseInstanceRoleMembershipRole.DATABRICKS_SUPERUSER))
        log(f"    registered pg role {name}")
    except Exception as e:
        log(f"    pg role {name}: {'exists' if 'exist' in str(e).lower() else str(e)[:80]}")


def _pg_connect(dbx, db):
    import psycopg2
    return psycopg2.connect(host=dbx._pg_host(), port=5432, dbname=db, user=dbx._pg_user(),
                            password=dbx._pg_token(), sslmode="require", connect_timeout=25)


def _seed_lakebase(ctx, dbx):
    schema_sql = os.path.join(HERE, "02_lakebase", "schema.sql")
    # create the database
    c = _pg_connect(dbx, "databricks_postgres"); c.autocommit = True
    with c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (ctx.lakebase_database,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{ctx.lakebase_database}"')
    c.close()
    # apply schema + seed offer_config/thresholds from the Delta golden copy
    c = _pg_connect(dbx, ctx.lakebase_database)
    with c.cursor() as cur:
        cur.execute(open(schema_sql).read())
        oc = dbx.warehouse_query(f"""SELECT config_id, partner_id, product_id, device_tier, market_id,
            impression_enabled, placement, deductible_tier_shown, price_band, eligibility_rule, version, is_current, updated_by
            FROM {dbx.SCHEMA}.offer_config""")
        cur.execute("TRUNCATE offer_config")
        for r in oc:
            cur.execute("""INSERT INTO offer_config (config_id, partner_id, product_id, device_tier, market_id,
                impression_enabled, placement, deductible_tier_shown, price_band, eligibility_rule, version, is_current, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (r["config_id"], r["partner_id"], r["product_id"], r["device_tier"], r["market_id"],
                 bool(r["impression_enabled"]), r["placement"], r["deductible_tier_shown"], r["price_band"],
                 r["eligibility_rule"], int(r["version"]), bool(r["is_current"]), r["updated_by"]))
        for prow in dbx.warehouse_query(f"SELECT partner_id FROM {dbx.SCHEMA}.partners"):
            cur.execute("INSERT INTO alert_thresholds (partner_id, attach_drop_pct, loss_ratio_max) VALUES (%s,0.05,0.70) ON CONFLICT (partner_id) DO NOTHING", (prow["partner_id"],))
    c.commit(); c.close()


def _grant_app_sp_pg(ctx, dbx, app_sp):
    """Register the app SP as a Postgres role + grant table privileges. Best-effort and
    NEVER fatal: a freshly-created app SP can take time to propagate to the Lakebase role
    API, and the bound database resource also provisions the role asynchronously — so we
    retry, then grant in autocommit (one failure can't poison the rest), then move on."""
    import databricks.sdk.service.database as d
    w = dbx.ws()
    for attempt in range(5):
        try:
            w.database.create_database_instance_role(ctx.lakebase_instance, d.DatabaseInstanceRole(
                name=app_sp, identity_type=d.DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL,
                membership_role=d.DatabaseInstanceRoleMembershipRole.DATABRICKS_SUPERUSER))
            break
        except Exception as e:
            if "exist" in str(e).lower() or "already" in str(e).lower():
                break
            if attempt == 4:
                log(f"    pg role register gave up: {str(e)[:90]}")
            else:
                log(f"    pg role register attempt {attempt + 1} ({str(e)[:60]}) — retry in 15s")
                time.sleep(15)
    c = _pg_connect(dbx, ctx.lakebase_database); c.autocommit = True  # autocommit: no txn cascade
    granted = 0
    with c.cursor() as cur:
        for stmt in (f'GRANT USAGE ON SCHEMA public TO "{app_sp}"',
                     f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{app_sp}"',
                     f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_sp}"',
                     f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_sp}"'):
            try:
                cur.execute(stmt); granted += 1
            except Exception as e:
                log(f"    pg grant skip: {str(e)[:80]}")
    c.close()
    if granted == 4:
        log("  app SP Lakebase role + table grants applied")
    else:
        log(f"  ⚠ app SP Lakebase grants partial ({granted}/4) — the bound DB resource may still "
            f"cover connect; if the app can't read Lakebase, re-run `--only app` once the SP propagates.")


def _app_yaml_content(ctx):
    env = {
        "SCHEMA_FQN": ctx.schema_fqn, "WAREHOUSE_ID": ctx.warehouse_id, "GENIE_SPACE_ID": ctx.genie_space_id,
        "MODEL_AGENT": ctx.args.model_agent, "MODEL_CLASSIFIER": ctx.args.model_classifier,
        "LAKEBASE_INSTANCE": ctx.lakebase_instance, "LAKEBASE_TIER": "provisioned",
        "PGDATABASE": ctx.lakebase_database, "PGHOST": ctx.pg_host, "PGUSER": ctx.app_sp,
        "PGPORT": "5432", "PGSSLMODE": "require",
    }
    lines = ["command:", "  - \"uvicorn\"", "  - \"app:app\"", "  - \"--host\"", "  - \"0.0.0.0\"",
             "  - \"--port\"", "  - \"8000\"", "", "env:"]
    for k, v in env.items():
        lines.append(f'  - name: {k}')
        lines.append(f'    value: "{v}"')
    return "\n".join(lines) + "\n"


def _write_app_yaml(ctx):
    """Generate 04_app/app.yaml env for THIS workspace. Writes the local file (works for the
    local CLI flow and in cluster notebooks where /Workspace is a writable mount); falls back
    to the Workspace upload API when the local path isn't writable (e.g. serverless notebooks)."""
    content = _app_yaml_content(ctx)
    app_yaml = os.path.join(HERE, "04_app", "app.yaml")
    try:
        bak = app_yaml + ".bak"
        if os.path.isfile(app_yaml) and not os.path.isfile(bak):
            shutil.copyfile(app_yaml, bak)   # preserve the original (dev) manifest once
        with open(app_yaml, "w") as f:
            f.write(content)
        log(f"  wrote {app_yaml}")
    except Exception as e:
        log(f"  local app.yaml write failed ({str(e)[:60]}); uploading via Workspace API")
        import dbx
        import databricks.sdk.service.workspace as wsvc
        ws_path = (ctx.workspace_src or os.path.join(HERE, "04_app")).replace("/Workspace", "", 1) + "/app.yaml"
        dbx.ws().workspace.upload(ws_path, content.encode(), format=wsvc.ImportFormat.AUTO, overwrite=True)
        log(f"  uploaded app.yaml -> {ws_path}")


def step_app(ctx):
    import dbx
    import databricks.sdk.service.apps as ap
    w = dbx.ws()
    # 1. create the app (idempotent) and capture its service principal
    try:
        app = w.apps.get(name=ctx.app_name)
        log(f"  app {ctx.app_name} exists")
    except Exception:
        log(f"  creating app {ctx.app_name} …")
        app = w.apps.create(app=ap.App(name=ctx.app_name)).result(timeout=datetime.timedelta(minutes=10))
    # the app's RUNTIME service principal (what it authenticates as) is
    # service_principal_client_id — NOT oauth2_app_client_id. This is the identity to
    # grant UC + Lakebase access and to set as PGUSER. The DB-resource binding also
    # auto-provisions this SP's Postgres role.
    ctx.app_sp = getattr(app, "service_principal_client_id", None) or app.oauth2_app_client_id
    ctx.save()
    log(f"  app service principal: {ctx.app_sp}")

    # 2. bind resources to the app (warehouse, FMAPI serving endpoints, Genie space, Lakebase db)
    resources = [
        ap.AppResource(name="sql-warehouse", sql_warehouse=ap.AppResourceSqlWarehouse(
            id=ctx.warehouse_id, permission=ap.AppResourceSqlWarehouseSqlWarehousePermission.CAN_USE)),
        ap.AppResource(name="genie", genie_space=ap.AppResourceGenieSpace(
            name="attach-war-room-genie", space_id=ctx.genie_space_id,
            permission=ap.AppResourceGenieSpaceGenieSpacePermission.CAN_RUN)),
        ap.AppResource(name="lakebase", database=ap.AppResourceDatabase(
            instance_name=ctx.lakebase_instance, database_name=ctx.lakebase_database,
            permission=ap.AppResourceDatabaseDatabasePermission.CAN_CONNECT_AND_CREATE)),
        ap.AppResource(name="fmapi-agent", serving_endpoint=ap.AppResourceServingEndpoint(
            name=ctx.args.model_agent, permission=ap.AppResourceServingEndpointServingEndpointPermission.CAN_QUERY)),
        ap.AppResource(name="fmapi-classifier", serving_endpoint=ap.AppResourceServingEndpoint(
            name=ctx.args.model_classifier, permission=ap.AppResourceServingEndpointServingEndpointPermission.CAN_QUERY)),
    ]
    try:
        w.apps.update(name=ctx.app_name, app=ap.App(name=ctx.app_name, resources=resources))
        log("  bound resources (warehouse · genie · lakebase · 2 FMAPI endpoints)")
    except Exception as e:
        log(f"  resource bind warning: {str(e)[:140]}")

    # 3. grant the app SP UC read on the schema (so it can query the metric views/tables)
    for stmt in (f"GRANT USE CATALOG ON CATALOG {ctx.catalog} TO `{ctx.app_sp}`",
                 f"GRANT USE SCHEMA ON SCHEMA {ctx.schema_fqn} TO `{ctx.app_sp}`",
                 f"GRANT SELECT ON SCHEMA {ctx.schema_fqn} TO `{ctx.app_sp}`"):
        try:
            _exec(dbx, stmt)
        except Exception as e:
            log(f"  UC grant skip: {str(e)[:80]}")
    log("  granted app SP UC SELECT on the schema")

    # 4. register the app SP as a Lakebase Postgres role + grant table privileges (best-effort)
    _grant_app_sp_pg(ctx, dbx, ctx.app_sp)

    # 5. generate app.yaml env, then deploy
    _write_app_yaml(ctx)
    if ctx.workspace_src:
        # in-workspace (notebook / Git-folder) mode: the source is already in the workspace,
        # so deploy directly via the SDK — no local CLI / sync needed.
        log(f"  deploying from in-workspace source {ctx.workspace_src} …")
        dep = w.apps.deploy(ctx.app_name, ap.AppDeployment(
            source_code_path=ctx.workspace_src, mode=ap.AppDeploymentMode.SNAPSHOT
        )).result(timeout=datetime.timedelta(minutes=15))
        state = getattr(getattr(dep, "status", None), "state", None)
        state = getattr(state, "value", str(state))
    else:
        # local CLI mode: upload the local source to the workspace, then deploy
        src = os.path.join(HERE, "04_app")
        me = w.current_user.me().user_name
        wsp = f"/Workspace/Users/{me}/{ctx.app_name}"
        log(f"  syncing source -> {wsp}")
        cli(ctx, "sync", src, wsp, json_out=False)
        log("  deploying …")
        dep = cli(ctx, "apps", "deploy", ctx.app_name, "--source-code-path", wsp)
        state = (dep.get("status") or {}).get("state") if isinstance(dep, dict) else dep
    ctx.app_url = getattr(w.apps.get(name=ctx.app_name), "url", "") or ""
    ctx.save()
    log(f"  deploy: {state}  url: {ctx.app_url}")


def step_reset(ctx):
    import dbx
    with dbx.cursor() as cur:
        cur.execute("UPDATE offer_config SET impression_enabled=false, version=1 WHERE partner_id='P01' AND device_tier='mid'")
        cur.execute("UPDATE offer_config SET impression_enabled=true, version=1 WHERE NOT (partner_id='P01' AND device_tier='mid') AND impression_enabled=false")
        cur.execute("UPDATE offer_config SET deductible_tier_shown='high', version=1 WHERE partner_id='P02'")
        for t in ("scenarios", "offer_config_audit"):
            cur.execute(f"TRUNCATE TABLE {t}")
        cur.execute("DELETE FROM chat_messages")
    log("  reset to broken-start state")


def teardown(ctx):
    import dbx
    w = dbx.ws()
    if not ctx.args.yes:
        log(f"Will DELETE app '{ctx.app_name}' and lakebase '{ctx.lakebase_instance}'"
            + (f" and DROP schema {ctx.schema_fqn}" if ctx.args.drop_schema else "") + ".")
        if input("Type DELETE to confirm: ").strip() != "DELETE":
            raise SystemExit("aborted")
    try:
        cli(ctx, "apps", "delete", ctx.app_name, json_out=False); log("  app deleted")
    except Exception as e:
        log(f"  app delete: {str(e)[:80]}")
    try:
        # provisioned instances don't accept force=; purge= permanently removes it
        w.database.delete_database_instance(name=ctx.lakebase_instance, purge=True); log("  lakebase deleted")
    except Exception as e:
        log(f"  lakebase delete: {str(e)[:100]}")
    if ctx.args.drop_schema:
        if not ctx.warehouse_id:   # teardown skips preflight — resolve a warehouse for the DROP
            whs = [x for x in w.warehouses.list()]
            if whs:
                ctx.warehouse_id = whs[0].id
        dbx.WAREHOUSE_ID = ctx.warehouse_id
        try:
            _exec(dbx, f"DROP SCHEMA IF EXISTS {ctx.schema_fqn} CASCADE"); log("  schema dropped")
        except Exception as e:
            log(f"  schema drop: {str(e)[:100]}")


def main():
    args = parse_args()
    sys.path.insert(0, os.path.join(HERE, "03_agent"))
    ctx = Ctx(args)
    ctx.export_env()

    if args.teardown:
        teardown(ctx)
        return

    todo = [s for s in STEPS if (not args.only or s in args.only.split(",")) and s not in args.skip.split(",")]
    log(f"Installing Attach War-Room → {ctx.schema_fqn} (steps: {', '.join(todo)})\n")
    preflight(ctx)
    ctx.export_env()  # re-export now that warehouse is resolved
    fns = {"schema": step_schema, "data": step_data, "metrics": step_metrics, "genie": step_genie,
           "lakebase": step_lakebase, "app": step_app, "reset": step_reset}
    for s in todo:
        log(f"\n▶ {s}")
        fns[s](ctx)
    ctx.save()
    log("\n✅ Done.")
    log(f"   schema:   {ctx.schema_fqn}")
    log(f"   genie:    {ctx.genie_space_id}")
    log(f"   lakebase: {ctx.lakebase_instance} ({ctx.pg_host})")
    log(f"   app:      {ctx.app_name}  {ctx.app_url}")


if __name__ == "__main__":
    main()
