#!/usr/bin/env python3
"""Lakebase BRANCHING — stage a candidate offer_config on a branch, preview it,
then promote or discard, WITHOUT touching the live serving config the checkout reads.

This is the Tier 2.7 story the top-level README promises:
    "branching to stage candidate configs"

Why branching matters for THIS demo
------------------------------------
`offer_config` on Lakebase is the hot table the simulated partner checkout reads on
every page view. The agent's `ship_offer_change` writes to it transactionally. Before
shipping a real config change you want to *rehearse* it against a full, isolated copy of
production data — flip "Velora Telecom (P01) mid-tier impressions ON", point a preview reader at
the branch, confirm it looks right — all while production keeps serving the OLD config.
That is exactly what a copy-on-write branch buys you: a cheap, instant, fork of the
primary at a point in time, isolated for writes, that you can throw away or promote.

PROVISIONED vs AUTOSCALING — they expose branching DIFFERENTLY
-------------------------------------------------------------
The live deploy (config.yaml) uses a *PROVISIONED* instance: `attach-war-room-db`. On the
provisioned tier a "branch" is a **child DatabaseInstance** created copy-on-write from a
parent at a point in time. The SDK primitive is:

    w.database.create_database_instance(DatabaseInstance(
        name="attach-war-room-db-candidate",
        capacity="CU_1",
        parent_instance_ref=DatabaseInstanceRef(
            name="attach-war-room-db",          # the parent (primary)
            branch_time="2026-06-04T00:00:00Z"  # OR lsn="..." — point-in-time fork
        ),
    ))

`DatabaseInstance` carries `parent_instance_ref` / `child_instance_refs` and
`DatabaseInstanceRef` carries `branch_time` / `lsn` — those fields ARE the provisioned
branching surface. (Verified against databricks-sdk's
`databricks.sdk.service.database`.) The child gets its own endpoint/DNS and its own
OAuth credential; writes to it never touch the parent.

The *AUTOSCALING* tier (the earlier `attach-war-room` project, now superseded — see
config.yaml note) models branches as first-class objects *inside one project*:
`projects/<p>/branches/<b>` with `databricks postgres create-branch …`. That's the
Neon-style branch tree `lakebase.py` was originally wired for (PROJECT/BRANCH/ENDPOINT).
See the autoscaling helper below.

What is HONESTLY demonstrated here
----------------------------------
* PROVISIONED path (default, runs against the live instance): create a copy-on-write
  CHILD instance → connect to the child → apply the candidate config ON THE CHILD ONLY →
  read it back to "preview" → then DISCARD (delete the child). The primary
  `attach-war-room-db` is never mutated. PROMOTE is described but NOT auto-run (see below).
* The exact verb to *promote/merge* a provisioned child back into its parent (a true
  "merge branch") is NOT a settled public SDK call at time of writing — branches are
  forks, not merge-back targets. The honest production pattern is: validate on the child,
  then re-apply the now-trusted change to the primary via the SAME ACID write the agent's
  `ship_offer_change` uses (UPDATE offer_config + INSERT audit). We show that re-apply as
  the "promote" step, gated behind an explicit flag, and DEFAULT TO DISCARD.
  TODO(promote): if/when a first-class provisioned branch-merge API ships, wire it here.
        docs: https://docs.databricks.com/aws/en/oltp/instances/branching
        sdk:  https://databricks-sdk-py.readthedocs.io/ -> w.database (DatabaseInstance)

Run (provisioned, against the live workspace via the DEFAULT profile):
    uv run --with databricks-sdk --with psycopg2-binary 02_lakebase/branch_demo.py

    # safe by default: create -> preview -> DISCARD (never mutates the primary)
    # opt into the promote (re-apply to primary) step explicitly:
    BRANCH_DEMO_PROMOTE=1 uv run ... 02_lakebase/branch_demo.py
    # opt into the autoscaling path narration (requires an autoscaling project to exist):
    BRANCH_DEMO_TIER=autoscaling uv run ... 02_lakebase/branch_demo.py
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import databricks.sdk.service.database as d

# Reuse the agent's hardened, SDK-based connection helpers (host/user/token resolution,
# provisioned-vs-autoscaling awareness) rather than re-implementing them here.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "03_agent"))
import dbx  # noqa: E402

# --- config (all overridable via env; nothing destructive hardcoded) --------
PRIMARY = dbx.LAKEBASE_INSTANCE                       # 'attach-war-room-db' (the live primary — read-only here)
DB = dbx.LAKEBASE_DATABASE                            # 'attach_war_room'
TIER = os.environ.get("BRANCH_DEMO_TIER", dbx.LAKEBASE_TIER)  # 'provisioned' (live) | 'autoscaling'
# A clearly-named, disposable child so it's obvious in the UI this is NOT the primary.
CANDIDATE = os.environ.get("BRANCH_DEMO_CHILD", f"{PRIMARY}-candidate")
PROMOTE = os.environ.get("BRANCH_DEMO_PROMOTE", "").lower() in ("1", "true", "yes")
# The candidate change we stage: flip Velora Telecom (P01) mid-tier impressions back ON.
# This is the demo's planted "broken" state (impression_enabled=false on 5 configs).
CAND_PARTNER, CAND_TIER, CAND_NEW_VALUE = "P01", "mid", True

w = dbx.ws()


def _connect(host, database):
    """psycopg2 connection to a SPECIFIC host (so we can target the CHILD endpoint, not
    the cached primary host in dbx). User + OAuth token come from dbx (workspace identity).
    NOTE: the child instance issues its own credential — see _child_token()."""
    return psycopg2.connect(host=host, port=5432, dbname=database, user=dbx._pg_user(),
                            password=_child_token(), sslmode="require", connect_timeout=25)


def _child_token():
    """OAuth DB credential. On provisioned, the credential is scoped to instance NAMES;
    the child is a distinct instance so we mint a token covering both primary + child.
    (Falls back to dbx's cached primary-only token off-instance, e.g. autoscaling.)"""
    if TIER == "provisioned":
        names = [PRIMARY, CANDIDATE]
        return w.database.generate_database_credential(instance_names=names).token
    return dbx._pg_token()


# ============================================================================
# PROVISIONED tier: branch == copy-on-write CHILD DatabaseInstance
# ============================================================================
def create_branch_provisioned():
    """Fork the primary into a copy-on-write child instance at a point in time.

    branch_time picks the fork point inside the parent's retention window (7 days on
    this instance). We branch from 'a few minutes ago' to guarantee the LSN exists.
    Returns the child's read_write DNS once AVAILABLE."""
    # Idempotency: if a previous run left the child around, reuse it.
    try:
        existing = w.database.get_database_instance(name=CANDIDATE)
        print(f"  child '{CANDIDATE}' already exists (state={existing.state}); reusing")
        return existing.read_write_dns
    except Exception:
        pass

    branch_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    parent = w.database.get_database_instance(name=PRIMARY)
    print(f"  forking '{PRIMARY}' (capacity={parent.capacity}) -> child '{CANDIDATE}' "
          f"@ branch_time={branch_time}")
    child = w.database.create_database_instance_and_wait(d.DatabaseInstance(
        name=CANDIDATE,
        capacity=parent.capacity or "CU_1",   # match the parent's compute size
        parent_instance_ref=d.DatabaseInstanceRef(name=PRIMARY, branch_time=branch_time),
    ))
    print(f"  child AVAILABLE: dns={child.read_write_dns} state={child.state}")
    return child.read_write_dns


def discard_branch_provisioned():
    """Throw the branch away — deletes ONLY the child instance. The primary is untouched."""
    try:
        # purge=True fully removes the disposable child (no soft-delete to clean up later).
        w.database.delete_database_instance(name=CANDIDATE, force=True, purge=True)
        print(f"  discarded child instance '{CANDIDATE}' (primary '{PRIMARY}' untouched)")
    except Exception as e:
        print(f"  discard note: {str(e)[:160]}")


# ============================================================================
# AUTOSCALING tier: branch == first-class object inside the project
# ============================================================================
def create_branch_autoscaling():
    """Autoscaling branching (the superseded 'attach-war-room' project model).

    Branches live under projects/<project>/branches/<branch> and are created from a
    parent branch copy-on-write. The CLI verb is `databricks postgres create-branch`;
    the SDK surface is `w.postgres.*`. We don't run this against the live deploy (the
    project is superseded per config.yaml) — this is here for completeness so the demo
    can narrate the difference. TODO(autoscaling): confirm exact create-branch payload:
        docs: https://docs.databricks.com/aws/en/oltp/ (Lakebase autoscaling branches)
    """
    project = dbx.LAKEBASE_PROJECT
    parent_branch = dbx.LAKEBASE_BRANCH            # 'production'
    cand_branch = os.environ.get("BRANCH_DEMO_BRANCH", "candidate-velora-mid-on")
    print(f"  [autoscaling] would create projects/{project}/branches/{cand_branch} "
          f"copy-on-write from '{parent_branch}'")
    print(f"  [autoscaling] CLI: databricks postgres create-branch "
          f"projects/{project} --json '{{\"spec\":{{\"display_name\":\"{cand_branch}\","
          f"\"parent_branch_id\":\"{parent_branch}\"}}}}' -p {dbx.PROFILE}")
    print("  [autoscaling] NOT executed (project superseded; provisioned is the live tier)")
    return None  # no live host to return for the superseded project


# ============================================================================
# branch-tier-agnostic steps
# ============================================================================
def apply_candidate_on_branch(host):
    """Apply the CANDIDATE config change ON THE BRANCH ONLY.

    This is the same shape of write the agent's `ship_offer_change` does on approval,
    but pointed at the BRANCH endpoint — so the live serving copy is never touched.
    We deliberately do NOT bump version / write the audit row here: this is a rehearsal
    on a throwaway fork, not the real ship."""
    conn = _connect(host, DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE offer_config SET impression_enabled=%s, updated_by=%s "
                "WHERE partner_id=%s AND device_tier=%s",
                (CAND_NEW_VALUE, "branch_demo(candidate)", CAND_PARTNER, CAND_TIER))
            print(f"  staged on branch: {cur.rowcount} Velora Telecom mid-tier configs -> "
                  f"impression_enabled={CAND_NEW_VALUE}")
        conn.commit()
    finally:
        conn.close()


def preview_branch(host):
    """Read the staged config back FROM THE BRANCH to 'preview' what the checkout WOULD
    serve if promoted — without the live checkout ever seeing it."""
    conn = _connect(host, DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FILTER (WHERE impression_enabled), count(*) "
                "FROM offer_config WHERE partner_id=%s AND device_tier=%s",
                (CAND_PARTNER, CAND_TIER))
            on, total = cur.fetchone()
            print(f"  branch preview: Velora Telecom mid-tier impressions ON {on}/{total} "
                  f"(candidate state)")
        conn.commit()
    finally:
        conn.close()


def show_primary_unchanged():
    """Prove isolation: read the SAME rows on the PRIMARY and show they are still the
    OLD (broken) state. dbx.cursor() targets the primary host."""
    with dbx.cursor(commit=False) as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE impression_enabled), count(*) "
            "FROM offer_config WHERE partner_id=%s AND device_tier=%s",
            (CAND_PARTNER, CAND_TIER))
        on, total = cur.fetchone()
        print(f"  primary STILL serving: Velora Telecom mid-tier impressions ON {on}/{total} "
              f"(unchanged — branch is isolated)")


def promote_to_primary():
    """PROMOTE = re-apply the now-validated change to the PRIMARY via the real ACID write.

    Honest framing: a provisioned branch is a FORK, not a merge-back target, so we don't
    'merge the branch' — we re-run the trusted change against the live primary using the
    same transaction shape as the agent's `ship_offer_change` (UPDATE offer_config +
    version bump + audit INSERT). Gated behind BRANCH_DEMO_PROMOTE so the default cycle
    never mutates production.
    TODO(promote): replace with a first-class provisioned branch-merge API if one ships:
        https://docs.databricks.com/aws/en/oltp/instances/branching
    """
    print("  PROMOTE: re-applying validated change to PRIMARY (real ACID write)…")
    with dbx.cursor(commit=True) as cur:
        cur.execute(
            "UPDATE offer_config SET impression_enabled=%s, version=version+1, "
            "updated_by=%s WHERE partner_id=%s AND device_tier=%s",
            (CAND_NEW_VALUE, "branch_demo(promote)", CAND_PARTNER, CAND_TIER))
        n = cur.rowcount
        cur.execute(
            "INSERT INTO offer_config_audit (partner_id, device_tier, changed_by, "
            "field_changed, before_value, after_value, rationale) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (CAND_PARTNER, CAND_TIER, "branch_demo(promote)", "impression_enabled",
             "false", str(CAND_NEW_VALUE).lower(),
             "Validated on copy-on-write branch before promote"))
        print(f"  promoted: {n} configs updated on PRIMARY + audit row written")


# ============================================================================
def main():
    print(f"Lakebase branching demo  tier={TIER}  primary={PRIMARY}  child={CANDIDATE}")
    print(f"Candidate change: Velora Telecom ({CAND_PARTNER}) {CAND_TIER}-tier "
          f"impressions -> {CAND_NEW_VALUE}\n")

    if TIER == "autoscaling":
        print("1) create branch (autoscaling project/branch model):")
        host = create_branch_autoscaling()
        if host is None:
            print("\n(autoscaling path is narration-only against the superseded project; "
                  "run without BRANCH_DEMO_TIER for the live provisioned cycle)")
            return
    else:
        print("1) create branch (provisioned copy-on-write child instance):")
        host = create_branch_provisioned()

    try:
        print("\n2) apply candidate on branch ONLY:")
        apply_candidate_on_branch(host)

        print("\n3) preview the staged config on the branch:")
        preview_branch(host)

        print("\n4) confirm the live primary is UNCHANGED (isolation):")
        show_primary_unchanged()

        if PROMOTE:
            print("\n5) PROMOTE (BRANCH_DEMO_PROMOTE=1 set) — mutates the PRIMARY:")
            promote_to_primary()
        else:
            print("\n5) promote SKIPPED (default). Set BRANCH_DEMO_PROMOTE=1 to re-apply "
                  "to the primary via the real ACID write.")
    finally:
        # Always discard the throwaway branch, success or failure — never leave the
        # child instance (or its cost) lingering.
        print("\n6) discard branch:")
        if TIER == "provisioned":
            discard_branch_provisioned()
        else:
            print("  [autoscaling] would: databricks postgres delete-branch "
                  f"projects/{dbx.LAKEBASE_PROJECT}/branches/<candidate> -p {dbx.PROFILE}")

    print("\nDone. The primary serving config was never mutated"
          + (" (except the explicit PROMOTE step)." if PROMOTE else "."))


if __name__ == "__main__":
    main()
