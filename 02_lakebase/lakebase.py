#!/usr/bin/env python3
"""Lakebase (Autoscaling Postgres) connection helper for the Attach War-Room app.

Resolves the endpoint host, a short-lived OAuth token, and the Postgres user,
then returns a psycopg2 connection. Works two ways:
  * Local / build: shells out to the Databricks CLI (profile-based).
  * Deployed app: reads PGHOST / PGUSER / PGPASSWORD from env (set by the
    Databricks App's Lakebase resource binding) and skips the CLI.

OAuth tokens expire ~1h, so get a fresh connection per unit of work (or cache
with refresh). The FastAPI backend imports get_connection() from here.
"""
import json
import os
import subprocess
from contextlib import contextmanager

import psycopg2

PROFILE = os.environ.get("DATABRICKS_PROFILE", "DEFAULT")
PROJECT = os.environ.get("LAKEBASE_PROJECT", "attach-war-room")
BRANCH = os.environ.get("LAKEBASE_BRANCH", "production")
ENDPOINT = os.environ.get("LAKEBASE_ENDPOINT", "primary")
DATABASE = os.environ.get("LAKEBASE_DATABASE", "attach_war_room")

BRANCH_PATH = f"projects/{PROJECT}/branches/{BRANCH}"
ENDPOINT_PATH = f"{BRANCH_PATH}/endpoints/{ENDPOINT}"


def _cli(args):
    out = subprocess.run(["databricks", *args, "--profile", PROFILE, "--output", "json"],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def get_host():
    if os.environ.get("PGHOST"):
        return os.environ["PGHOST"]
    endpoints = _cli(["postgres", "list-endpoints", BRANCH_PATH])
    return endpoints[0]["status"]["hosts"]["host"]


def get_token():
    if os.environ.get("PGPASSWORD"):
        return os.environ["PGPASSWORD"]
    return _cli(["postgres", "generate-database-credential", ENDPOINT_PATH])["token"]


def get_user():
    if os.environ.get("PGUSER"):
        return os.environ["PGUSER"]
    return _cli(["current-user", "me"])["userName"]


def get_connection(database=DATABASE, autocommit=False):
    conn = psycopg2.connect(
        host=get_host(), port=5432, dbname=database,
        user=get_user(), password=get_token(), sslmode="require",
        connect_timeout=15,
    )
    conn.autocommit = autocommit
    return conn


@contextmanager
def cursor(database=DATABASE, commit=True):
    """Context-managed cursor; commits on success, rolls back on error."""
    conn = get_connection(database)
    try:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    # smoke test
    with cursor(commit=False) as cur:
        cur.execute("SELECT current_database(), version()")
        print(cur.fetchone())
