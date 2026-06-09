#!/usr/bin/env python3
"""Shared data + LLM access for the Attach War-Room agent and FastAPI backend.

Self-contained and SDK-based so it works BOTH locally (Databricks CLI profile)
and inside a deployed Databricks App (auto-injected service-principal creds +
PG* env from the Lakebase resource binding) — no CLI subprocess, no profile
needed in the container. Single place that touches Databricks; the agent and
API never hold raw credentials.

Hardening (Tier 0/2):
  * Lakebase OAuth token is cached for its TTL instead of re-minted per connection.
  * A small thread-safe connection pool replaces connect-per-cursor.
  * warehouse_query takes bound :params (no SQL string interpolation).
  * Genie / warehouse calls have timeouts so a cold start can't hang the SSE turn.
  * Structured logging with call timings (observability).
  * DEMO_OFFLINE short-circuits to fixtures (break-glass fallback).
"""
import logging
import os
import threading
import time
import warnings
from contextlib import contextmanager
from datetime import timedelta

import psycopg2
from databricks.sdk import WorkspaceClient

# --- logging / observability ------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [awr.%(name)s] %(message)s",
)
log = logging.getLogger("dbx")

# --- environment ------------------------------------------------------------
IS_APP = bool(os.environ.get("DATABRICKS_APP_NAME") or os.environ.get("DATABRICKS_APP_PORT"))
OFFLINE = os.environ.get("DEMO_OFFLINE", "").lower() in ("1", "true", "yes")
PROFILE = os.environ.get("DATABRICKS_PROFILE", "" if IS_APP else "DEFAULT")
WAREHOUSE_ID = os.environ.get("WAREHOUSE_ID", "")
SCHEMA = os.environ.get("SCHEMA_FQN", "main.attach_war_room")
GENIE_SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
MODEL_AGENT = os.environ.get("MODEL_AGENT", "databricks-claude-sonnet-4-6")
MODEL_CLASSIFIER = os.environ.get("MODEL_CLASSIFIER", "databricks-claude-haiku-4-5")

WAREHOUSE_TIMEOUT_S = int(os.environ.get("WAREHOUSE_TIMEOUT_S", "45"))
GENIE_TIMEOUT_S = int(os.environ.get("GENIE_TIMEOUT_S", "90"))
PG_POOL_MAX = int(os.environ.get("PG_POOL_MAX", "4"))
PG_TOKEN_TTL_S = int(os.environ.get("PG_TOKEN_TTL_S", "2700"))  # ~45 min (token lives ~1h)

LAKEBASE_DATABASE = os.environ.get("PGDATABASE", os.environ.get("LAKEBASE_DATABASE", "attach_war_room"))
# Provisioned instance (preferred for Databricks Apps: resource-bindable + SP role API).
LAKEBASE_INSTANCE = os.environ.get("LAKEBASE_INSTANCE", "attach-war-room-db")
# Autoscaling fallback (project/branch/endpoint).
LAKEBASE_PROJECT = os.environ.get("LAKEBASE_PROJECT", "attach-war-room")
LAKEBASE_BRANCH = os.environ.get("LAKEBASE_BRANCH", "production")
LAKEBASE_ENDPOINT = os.environ.get("LAKEBASE_ENDPOINT", "primary")
BRANCH_PATH = f"projects/{LAKEBASE_PROJECT}/branches/{LAKEBASE_BRANCH}"
ENDPOINT_PATH = os.environ.get("LAKEBASE_ENDPOINT_PATH", f"{BRANCH_PATH}/endpoints/{LAKEBASE_ENDPOINT}")
LAKEBASE_TIER = os.environ.get("LAKEBASE_TIER", "provisioned" if LAKEBASE_INSTANCE else "autoscaling")

_w = None
_llm = None
_pg_host_cache = None
_pg_token_cache = {"token": None, "exp": 0.0}
_pg_token_lock = threading.Lock()


def ws():
    global _w
    if _w is None:
        _w = WorkspaceClient(profile=PROFILE) if PROFILE else WorkspaceClient()
    return _w


# --- Foundation Model API (OpenAI-compatible) -------------------------------
def llm():
    """OpenAI-compatible FMAPI client. If AI_GATEWAY_URL is set, route through the
    workspace AI Gateway (token metering, inference tables, guardrails); else use the
    direct serving-endpoints path. Gateway clients are built per-call with a fresh
    token (the SDK refreshes it) so the 1h OAuth token never goes stale in a cache."""
    gw = os.environ.get("AI_GATEWAY_URL")
    if gw:
        from openai import OpenAI
        token = ws().config.authenticate().get("Authorization", "").replace("Bearer ", "")
        return OpenAI(base_url=gw.rstrip("/"), api_key=token)
    global _llm
    if _llm is None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _llm = ws().serving_endpoints.get_open_ai_client()
    return _llm


def chat(messages, model=MODEL_AGENT, **kw):
    t0 = time.time()
    try:
        return llm().chat.completions.create(model=model, messages=messages, **kw)
    finally:
        log.info("fmapi chat model=%s stream=%s %.0fms", model, kw.get("stream", False), (time.time() - t0) * 1000)


def chat_stream(messages, model=MODEL_AGENT, **kw):
    """Streaming chat completion (used to stream the agent's final reasoning token-by-token)."""
    kw["stream"] = True
    return llm().chat.completions.create(model=model, messages=messages, **kw)


# --- warehouse SQL (typed, parameterized) -----------------------------------
def warehouse_query(sql, params=None):
    """Execute SQL on the warehouse and return typed dict rows.

    `params` is a dict of bound parameters referenced as :name in `sql` — used so
    no caller-supplied value is ever interpolated into the SQL string."""
    if OFFLINE:
        raise RuntimeError("warehouse_query called in DEMO_OFFLINE mode")
    w = ws()
    t0 = time.time()
    sdk_params = None
    if params:
        from databricks.sdk.service.sql import StatementParameterListItem
        sdk_params = [StatementParameterListItem(name=k, value=(None if v is None else str(v)))
                      for k, v in params.items()]
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=sql, parameters=sdk_params, wait_timeout="30s")
    deadline = time.time() + WAREHOUSE_TIMEOUT_S
    while getattr(resp.status.state, "value", str(resp.status.state)) in ("PENDING", "RUNNING"):
        if time.time() > deadline:
            try:
                w.statement_execution.cancel_execution(resp.statement_id)
            except Exception:
                pass
            raise TimeoutError(f"warehouse query exceeded {WAREHOUSE_TIMEOUT_S}s")
        time.sleep(1)
        resp = w.statement_execution.get_statement(resp.statement_id)
    state = getattr(resp.status.state, "value", str(resp.status.state))
    if state != "SUCCEEDED":
        err = resp.status.error
        raise RuntimeError(f"query failed: {err.message if err else state}\nSQL: {sql[:500]}")
    schema_cols = resp.manifest.schema.columns
    cols = [c.name for c in schema_cols]

    def _typename(c):
        tn = getattr(c, "type_name", None)
        return str(getattr(tn, "value", None) or getattr(tn, "name", None) or tn or "").upper()

    types = [_typename(c) for c in schema_cols]

    def _cast(v, t):
        if v is None:
            return None
        try:
            if t.startswith("DECIMAL") or t in ("DOUBLE", "FLOAT"):
                return float(v)
            if t in ("INT", "INTEGER", "SHORT", "BYTE", "LONG", "BIGINT"):
                return int(v)
        except (TypeError, ValueError):
            return v
        if t == "BOOLEAN":
            return str(v).lower() == "true"
        return v

    rows = [{cols[i]: _cast(row[i], types[i]) for i in range(len(cols))} for row in (resp.result.data_array or [])]
    log.info("warehouse_query %d rows %.0fms", len(rows), (time.time() - t0) * 1000)
    return rows


# --- Lakebase (Postgres) ----------------------------------------------------
def _pg_host():
    global _pg_host_cache
    if os.environ.get("PGHOST"):
        return os.environ["PGHOST"]
    if _pg_host_cache is None:
        if LAKEBASE_TIER == "provisioned":
            _pg_host_cache = ws().database.get_database_instance(name=LAKEBASE_INSTANCE).read_write_dns
        else:
            _pg_host_cache = list(ws().postgres.list_endpoints(parent=BRANCH_PATH))[0].status.hosts.host
    return _pg_host_cache


def _pg_user():
    return os.environ.get("PGUSER") or ws().current_user.me().user_name


def _pg_token():
    """Cached OAuth DB token. The token lives ~1h; minting it is a control-plane
    round-trip, so we reuse it within PG_TOKEN_TTL_S instead of per-connection."""
    if os.environ.get("PGPASSWORD"):
        return os.environ["PGPASSWORD"]
    now = time.time()
    with _pg_token_lock:
        if _pg_token_cache["token"] and now < _pg_token_cache["exp"]:
            return _pg_token_cache["token"]
        t0 = time.time()
        if LAKEBASE_TIER == "provisioned":
            token = ws().database.generate_database_credential(instance_names=[LAKEBASE_INSTANCE]).token
        else:
            token = ws().postgres.generate_database_credential(endpoint=ENDPOINT_PATH).token
        _pg_token_cache["token"] = token
        _pg_token_cache["exp"] = now + PG_TOKEN_TTL_S
        log.info("minted Lakebase DB token %.0fms (ttl %ds)", (time.time() - t0) * 1000, PG_TOKEN_TTL_S)
        return token


def get_connection(database=LAKEBASE_DATABASE, autocommit=False):
    conn = psycopg2.connect(host=_pg_host(), port=int(os.environ.get("PGPORT", "5432")), dbname=database,
                            user=_pg_user(), password=_pg_token(), sslmode=os.environ.get("PGSSLMODE", "require"),
                            connect_timeout=15)
    conn.autocommit = autocommit
    return conn


# --- tiny thread-safe connection pool ---------------------------------------
# Postgres authenticates at connect time, so a borrowed connection stays valid for
# its lifetime even after the token's TTL — only NEW physical connections need a
# fresh (cached) token. This pool removes the connect+auth latency from the hot path.
class _ConnPool:
    """Idle connections keyed by database name (psycopg2 connections don't accept
    custom attributes, so we can't tag the connection — we bucket by db instead)."""
    def __init__(self, maxsize):
        self.maxsize = maxsize
        self._idle = {}  # database -> [conn, ...]
        self._lock = threading.Lock()

    def get(self, database):
        with self._lock:
            bucket = self._idle.get(database)
            while bucket:
                conn = bucket.pop()
                if conn.closed == 0:
                    try:
                        conn.rollback()  # clear any aborted txn state
                        return conn
                    except Exception:
                        try:
                            conn.close()
                        except Exception:
                            pass
        return get_connection(database)

    def put(self, conn, database):
        if conn is None:
            return
        with self._lock:
            bucket = self._idle.setdefault(database, [])
            if conn.closed == 0 and len(bucket) < self.maxsize:
                bucket.append(conn)
                return
        try:
            conn.close()
        except Exception:
            pass


_pool = _ConnPool(PG_POOL_MAX)


@contextmanager
def cursor(database=LAKEBASE_DATABASE, commit=True):
    if OFFLINE:
        raise RuntimeError("Lakebase cursor opened in DEMO_OFFLINE mode")
    conn = _pool.get(database)
    try:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()
        _pool.put(conn, database)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.close()  # don't return a poisoned connection to the pool
        except Exception:
            pass
        raise


# --- Genie ------------------------------------------------------------------
def genie_ask(question, space_id=GENIE_SPACE_ID):
    g = ws().genie
    t0 = time.time()
    try:
        msg = g.start_conversation_and_wait(space_id, question, timeout=timedelta(seconds=GENIE_TIMEOUT_S))
    except Exception as e:
        log.warning("genie_ask failed/timed out after %.0fms: %s", (time.time() - t0) * 1000, e)
        return {"sql": None, "narrative": None, "rows": [], "columns": [], "error": str(e)}
    out = {"sql": None, "narrative": None, "rows": [], "columns": []}
    for att in (msg.attachments or []):
        if getattr(att, "text", None) and att.text and att.text.content:
            out["narrative"] = att.text.content
        if getattr(att, "query", None) and att.query:
            out["sql"] = att.query.query
            # Per-attachment result fetch returns the EXECUTED rows; the older
            # message-level get_message_query_result returns the manifest (columns)
            # but an empty data_array, which is why rows used to come back empty.
            aid = getattr(att, "attachment_id", None)
            try:
                if aid:
                    qr = g.get_message_attachment_query_result(space_id, msg.conversation_id, msg.id, aid)
                else:
                    qr = g.get_message_query_result(space_id, msg.conversation_id, msg.id)
                sr = qr.statement_response
                if sr and sr.manifest and sr.manifest.schema:
                    out["columns"] = [c.name for c in sr.manifest.schema.columns]
                if sr and sr.result:
                    out["rows"] = (sr.result.data_array or [])[:50]
            except Exception as e:
                log.warning("genie query-result fetch failed: %s", e)
    log.info("genie_ask %.0fms rows=%d", (time.time() - t0) * 1000, len(out["rows"]))
    return out


def health():
    """Pre-warm + probe warehouse, Lakebase, and Genie reachability (Tier 3.5)."""
    out = {"offline": OFFLINE, "checks": {}}
    if OFFLINE:
        out["ok"] = True
        out["checks"] = {"mode": {"ok": True, "detail": "DEMO_OFFLINE — serving fixtures"}}
        return out

    def _probe(name, fn):
        t0 = time.time()
        try:
            fn()
            out["checks"][name] = {"ok": True, "ms": round((time.time() - t0) * 1000)}
        except Exception as e:
            out["checks"][name] = {"ok": False, "ms": round((time.time() - t0) * 1000), "error": str(e)[:200]}

    _probe("warehouse", lambda: warehouse_query("SELECT 1 AS ok"))
    _probe("lakebase", lambda: _lb_ping())
    out["ok"] = all(c.get("ok") for c in out["checks"].values())
    return out


def _lb_ping():
    with cursor(commit=False) as cur:
        cur.execute("SELECT 1")
        cur.fetchone()


if __name__ == "__main__":
    print("env IS_APP:", IS_APP, "OFFLINE:", OFFLINE, "| host:", _pg_host())
    print("warehouse:", warehouse_query(f"SELECT count(*) n FROM {SCHEMA}.sessions"))
    with cursor(commit=False) as cur:
        cur.execute("SELECT count(*) FROM offer_config")
        print("lakebase offer_config:", cur.fetchone()[0])
    print("llm:", chat([{"role": "user", "content": "say OK"}], max_tokens=5).choices[0].message.content)
