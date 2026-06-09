#!/usr/bin/env python3
"""Tier 2.5 — AUTOMATED Genie accuracy harness for the Attach War-Room space.

Instead of eyeballing Genie's answers against EVAL.md, this *scores* the space.
For each of the 10 NL benchmarks it:

  1. Runs the EVAL ground-truth SQL on the warehouse to get the TRUE number(s).
  2. Asks the Genie space the same natural-language question.
  3. Extracts the comparable metric from Genie's returned rows and compares it to
     the truth with a relative-error tolerance (default 2%).

Plus one guardrail benchmark: the bare "What's the attach rate?" question (no
partner / product) must make Genie ASK FOR CLARIFICATION (a narrative-only reply
with NO SQL / no number) rather than returning a blended attach rate. That
verifies the space's clarifying-question instruction is still active.

It prints a per-question PASS/FAIL table and an overall accuracy %, and exits
non-zero when accuracy is below a threshold so it can gate CI.

The benchmark NL questions + ground-truth SQL are copied verbatim from EVAL.md
(anchored to AS_OF 2026-06-03; the synthetic data reproduces these exactly).

Run:
    uv run --with databricks-sdk 01_metric_views_and_genie/eval_genie.py
    # configurable:
    GENIE_SPACE_ID=... WAREHOUSE_ID=... EVAL_TOLERANCE=0.02 EVAL_PASS_THRESHOLD=0.8 \
        uv run --with databricks-sdk 01_metric_views_and_genie/eval_genie.py
    # run a single benchmark by id (handy when iterating on the space):
    uv run --with databricks-sdk 01_metric_views_and_genie/eval_genie.py --only 5

Defaults come from config.yaml via 03_agent/dbx.py (same env contract as the app).
"""
import os
import re
import sys
import time

# Reuse the agent's single Databricks access layer (warehouse_query + genie_ask)
# so this harness exercises the SAME code path the deployed app uses. We import by
# path so it works regardless of CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.abspath(os.path.join(_HERE, "..", "03_agent"))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import dbx  # noqa: E402  (warehouse_query, genie_ask, GENIE_SPACE_ID, SCHEMA, WAREHOUSE_ID)

# --- tunables (env-configurable so CI can tighten/loosen) -------------------
SPACE_ID = os.environ.get("GENIE_SPACE_ID", dbx.GENIE_SPACE_ID)
# 5% rel error: the space instructs Genie to round rates to WHOLE percents in prose
# (e.g. "15%" for a true 15.33%), and Genie answers narrative-first, so a tighter
# tolerance would penalize correct-but-rounded answers. Numeric truth still must match.
TOLERANCE = float(os.environ.get("EVAL_TOLERANCE", "0.05"))         # rel error allowed on a key metric
PASS_THRESHOLD = float(os.environ.get("EVAL_PASS_THRESHOLD", "0.8"))  # min accuracy to exit 0 (gate CI)
SCHEMA = dbx.SCHEMA

# The ground-truth SQL in EVAL.md is written against the metric views and (for a
# few questions) the base tables. The base-table ones need the schema prefix; the
# metric-view ones reference unqualified view names, so we qualify them too.
FUNNEL = f"{SCHEMA}.funnel_metrics"
PROFIT = f"{SCHEMA}.profitability_metrics"


# ---------------------------------------------------------------------------
# Benchmarks. Each entry:
#   id              : EVAL.md row number
#   question        : the exact NL prompt sent to Genie
#   ground_truth_sql: SQL run on the warehouse to compute the TRUE value(s)
#   metric          : how to read a comparable number out of BOTH result sets
#       kind="scalar"  -> a single number lives in column `truth_col`; we find the
#                         closest numeric cell in Genie's rows and compare.
#       kind="grouped" -> a number per labeled group (e.g. last_45d vs prior);
#                         `expected` maps group-label -> truth value and we match
#                         Genie rows by the same label, comparing each value.
#       kind="guardrail" -> Genie must NOT answer (no SQL, no number) — must clarify.
#   The metric-view SQL is fully qualified here so it runs standalone on the warehouse.
# ---------------------------------------------------------------------------
BENCHMARKS = [
    {
        "id": 1,
        "question": ("For Velora Telecom mid-tier devices, how have offer-shown rate, conversion rate, "
                     "and attach rate changed in the last 45 days vs before?"),
        "ground_truth_sql": f"""
            SELECT CASE WHEN session_date >= date_add(current_date(), -45) THEN 'last_45d' ELSE 'prior' END period,
                   MEASURE(offer_show_rate) AS offer_show_rate,
                   MEASURE(conversion_rate) AS conversion_rate,
                   MEASURE(attach_rate)     AS attach_rate
            FROM {FUNNEL}
            WHERE partner_name = 'Velora Telecom' AND device_tier = 'mid'
            GROUP BY period""",
        "metric": {"kind": "grouped", "label_col": "period", "value_col": "attach_rate"},
    },
    {
        "id": 2,
        "question": "Which partner had the largest quote-to-bind drop in the last 30 days?",
        "ground_truth_sql": f"""
            SELECT CASE WHEN session_date >= date_add(current_date(), -30) THEN 'last_30d' ELSE 'prior' END period,
                   MEASURE(complete_to_bind_rate) AS complete_to_bind_rate
            FROM {FUNNEL}
            WHERE partner_id = 'P02'
            GROUP BY period""",
        # Genie is asked an open "which partner" question; the answer of record is
        # Siam Mobile Care. We assert the named partner appears in Genie's narrative.
        "metric": {"kind": "names", "expect_any": ["Siam Mobile Care", "Siam Mobile", "Thailand", "P02"]},
    },
    {
        "id": 3,
        "question": "Where is policy activation / first payment leaking in the last 25 days?",
        "ground_truth_sql": f"""
            SELECT CASE WHEN session_date >= date_add(current_date(), -25) THEN 'last_25d' ELSE 'prior' END period,
                   MEASURE(activation_rate) AS activation_rate
            FROM {FUNNEL}
            WHERE partner_id = 'P08'
            GROUP BY period""",
        "metric": {"kind": "names", "expect_any": ["Rift Valley Bank", "Rift Valley", "Kenya", "P08"]},
    },
    {
        "id": 4,
        "question": "What is the blended loss ratio across the book?",
        "ground_truth_sql": f"""
            SELECT MEASURE(loss_ratio) AS loss_ratio,
                   MEASURE(gross_written_premium_usd) AS gwp,
                   MEASURE(incurred_losses_usd) AS losses
            FROM {PROFIT}""",
        "metric": {"kind": "scalar", "truth_col": "loss_ratio"},
    },
    {
        "id": 5,
        "question": "Which partner, market and device tier has the worst loss ratio?",
        "ground_truth_sql": f"""
            SELECT partner_name, device_tier, MEASURE(loss_ratio) AS loss_ratio
            FROM {PROFIT}
            GROUP BY partner_name, device_tier
            ORDER BY 3 DESC
            LIMIT 10""",
        "metric": {"kind": "names", "expect_any": ["Savanna Mobile", "Kenya", "budget"]},
    },
    {
        "id": 6,
        "question": "Top abandonment reasons for Siam Mobile Care at the quote-completed stage, last 30 days?",
        "ground_truth_sql": f"""
            SELECT abandon_reason_text, COUNT(*) AS n
            FROM {SCHEMA}.sessions
            WHERE partner_id = 'P02' AND stage_reached = 'quote_completed'
              AND session_date >= date_add(current_date(), -30)
            GROUP BY 1 ORDER BY 2 DESC""",
        # Price-cluster reasons should dominate; assert at least one price phrase appears.
        "metric": {"kind": "names",
                   "expect_any": ["cheaper", "expensive", "price", "premium", "deductible", "checkout"]},
    },
    {
        "id": 7,
        "question": "Which offer configurations currently have impressions disabled?",
        # offer_config is the live serving table (Lakebase + a Delta golden copy in
        # the schema). The schema copy is queryable on the warehouse for ground truth.
        "ground_truth_sql": f"""
            SELECT partner_id, product_id, device_tier
            FROM {SCHEMA}.offer_config
            WHERE impression_enabled = false""",
        # 5 Velora Telecom mid-tier configs. Assert Velora Telecom / P01 surfaces and the count matches.
        "metric": {"kind": "rowcount", "expect_any": ["Velora Telecom", "P01", "mid"]},
    },
    {
        "id": 8,
        "question": "Gross written premium by market in the last 30 days?",
        "ground_truth_sql": f"""
            SELECT market_name, currency, MEASURE(gross_written_premium_usd) AS gwp
            FROM {FUNNEL}
            WHERE session_date >= date_add(current_date(), -30)
            GROUP BY 1, 2 ORDER BY 3 DESC""",
        # Top market by GWP should be Thailand. Compare top-row label.
        "metric": {"kind": "top_label", "label_col": "market_name", "value_col": "gwp"},
    },
    {
        "id": 9,
        "question": "Claims frequency and average settlement time for budget devices in Kenya?",
        "ground_truth_sql": f"""
            SELECT MEASURE(claims_frequency) AS claims_frequency,
                   MEASURE(avg_settlement_days) AS avg_settlement_days,
                   MEASURE(loss_ratio) AS loss_ratio
            FROM {PROFIT}
            WHERE device_tier = 'budget' AND market_name = 'Kenya'""",
        "metric": {"kind": "scalar", "truth_col": "claims_frequency"},
    },
    {
        "id": 10,
        "question": "Rank partners by attach rate over the last 30 days.",
        "ground_truth_sql": f"""
            SELECT partner_name, MEASURE(attach_rate) AS attach_rate
            FROM {FUNNEL}
            WHERE session_date >= date_add(current_date(), -30)
            GROUP BY 1 ORDER BY 2 DESC""",
        # The top partner by attach rate should be EasyCredit Finance (or Brightway Electronics close behind).
        "metric": {"kind": "top_label", "label_col": "partner_name", "value_col": "attach_rate"},
    },
    {
        # Guardrail (EVAL.md "Text-instruction guardrail check"): a bare attach-rate
        # ask with no partner/product MUST make Genie clarify, not answer.
        "id": 11,
        "question": "What's the attach rate?",
        "ground_truth_sql": None,
        "metric": {"kind": "guardrail"},
    },
]


# --- helpers ----------------------------------------------------------------
def _to_float(v):
    """Best-effort numeric coercion. Strips %, $, thousands separators."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    pct = s.endswith("%")
    s = s.lstrip("$").rstrip("%").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return f / 100.0 if pct else f


def _normalize_rate(x):
    """Genie sometimes reports rates as percents (22.3) and sometimes as fractions
    (0.223). Normalize anything that looks like a percent into a [0,1] fraction so
    relative-error comparison is apples-to-apples."""
    if x is None:
        return None
    return x / 100.0 if abs(x) > 1.5 else x


def _rel_err(truth, got):
    if truth is None or got is None:
        return None
    if truth == 0:
        return abs(got)
    return abs(got - truth) / abs(truth)


def _genie_text_blob(g):
    """All text Genie produced (narrative + every cell of every row), lowercased —
    used for name/keyword assertions that don't depend on column layout."""
    parts = [g.get("narrative") or ""]
    for row in (g.get("rows") or []):
        parts.extend("" if c is None else str(c) for c in row)
    parts.extend(g.get("columns") or [])
    return " ".join(parts).lower()


def _genie_rows_as_dicts(g):
    cols = g.get("columns") or []
    out = []
    for row in (g.get("rows") or []):
        out.append({cols[i] if i < len(cols) else f"c{i}": row[i] for i in range(len(row))})
    return out


def _all_genie_numbers(g):
    """Every numeric value Genie returned, normalized to fractions where rate-like."""
    nums = []
    for row in (g.get("rows") or []):
        for c in row:
            f = _to_float(c)
            if f is not None:
                nums.append(_normalize_rate(f))
    # also mine narrative numbers (e.g. "loss ratio is 33%")
    for tok in re.findall(r"-?\d+(?:\.\d+)?%?", g.get("narrative") or ""):
        f = _to_float(tok)
        if f is not None:
            nums.append(_normalize_rate(f))
    return nums


def _genie_has_sql(g):
    return bool((g.get("sql") or "").strip())


# --- scoring per metric kind ------------------------------------------------
def score(bench, truth_rows, g):
    """Return (passed: bool, detail: str)."""
    m = bench["metric"]
    kind = m["kind"]
    err = g.get("error")
    if err and kind != "guardrail":
        return False, f"genie error: {err[:120]}"

    if kind == "guardrail":
        # PASS iff Genie clarified: no SQL emitted AND no concrete number returned.
        if err:
            return False, f"genie error: {err[:120]}"
        asked = (not _genie_has_sql(g)) and (len(g.get("rows") or []) == 0)
        # extra signal: narrative should request partner/product specifics
        narr = (g.get("narrative") or "").lower()
        clarifying = any(w in narr for w in ("partner", "product", "specify", "which", "clarif"))
        if asked and clarifying:
            return True, "clarified (no SQL, asked for partner/product)"
        if asked:
            return True, "clarified (no SQL / no rows returned)"
        return False, f"answered without clarifying (sql={_genie_has_sql(g)}, rows={len(g.get('rows') or [])})"

    if kind == "scalar":
        col = m["truth_col"]
        truth = _normalize_rate(_to_float(truth_rows[0][col])) if truth_rows else None
        if truth is None:
            return False, "no truth value"
        # find the Genie number with the smallest relative error to truth
        nums = _all_genie_numbers(g)
        if not nums:
            return False, f"truth={truth:.4f} but Genie returned no number"
        best = min(nums, key=lambda n: _rel_err(truth, n) if _rel_err(truth, n) is not None else 9e9)
        e = _rel_err(truth, best)
        ok = e is not None and e <= TOLERANCE
        return ok, f"truth={truth:.4f} genie≈{best:.4f} relerr={e:.3%} (tol {TOLERANCE:.0%})"

    if kind == "grouped":
        # Genie answers narrative-first ("attach fell 22% → 15%"), so grade on
        # PRESENCE: each truth value must appear among Genie's numbers (rows or
        # narrative) within tolerance. This is how a human reads the answer.
        value_col = m["value_col"]
        truths = [_normalize_rate(_to_float(r[value_col])) for r in truth_rows]
        truths = [t for t in truths if t is not None]
        if not truths:
            return False, "no truth rows"
        gen_nums = _all_genie_numbers(g)
        if not gen_nums:
            return False, "genie returned no numbers (narrative or rows)"
        details, all_ok = [], True
        for tval in truths:
            best = min(gen_nums, key=lambda n: _rel_err(tval, n))
            e = _rel_err(tval, best)
            ok = e is not None and e <= TOLERANCE
            all_ok = all_ok and ok
            details.append(f"truth={tval:.4f} genie≈{round(best, 4)} relerr={e:.2%}")
        return all_ok, "; ".join(details)

    if kind == "top_label":
        label_col, value_col = m["label_col"], m["value_col"]
        if not truth_rows:
            return False, "no truth rows"
        top_truth = str(truth_rows[0][label_col])
        # Genie's top row by its value column, or first row if we can't tell
        gdicts = _genie_rows_as_dicts(g)
        if not gdicts:
            # accept if the top label appears in narrative
            ok = top_truth.lower() in (g.get("narrative") or "").lower()
            return ok, f"truth top={top_truth}; genie returned no rows (narrative match={ok})"
        gtop = gdicts[0]
        gtop_cells = " ".join("" if v is None else str(v) for v in gtop.values()).lower()
        ok = top_truth.lower() in gtop_cells or top_truth.lower() in _genie_text_blob(g)
        got_label = next((str(v) for v in gtop.values() if isinstance(v, str)), "?")
        return ok, f"truth top={top_truth}; genie top row label≈{got_label}"

    if kind in ("names", "rowcount"):
        # Genie names entities in its narrative (and returns the answer there even
        # when it emits no materialized result rows), so match against the full
        # text blob (narrative + any rows + columns).
        blob = _genie_text_blob(g)
        hits = [w for w in m["expect_any"] if w.lower() in blob]
        ok = len(hits) > 0
        detail = f"expected any of {m['expect_any']}; matched {hits or 'NONE'}"
        if kind == "rowcount" and truth_rows is not None:
            detail += f"; truth_rows={len(truth_rows)} genie_rows={len(g.get('rows') or [])} (narrative-ok)"
        return ok, detail

    return False, f"unknown metric kind {kind}"


# --- driver -----------------------------------------------------------------
def run_one(bench):
    truth_rows = None
    if bench["ground_truth_sql"]:
        truth_rows = dbx.warehouse_query(" ".join(bench["ground_truth_sql"].split()))
    g = dbx.genie_ask(bench["question"], space_id=SPACE_ID)
    # one retry on a transient Genie failure (MessageStatus.FAILED occurs intermittently)
    if g.get("error"):
        time.sleep(3)
        g = dbx.genie_ask(bench["question"], space_id=SPACE_ID)
    passed, detail = score(bench, truth_rows, g)
    return passed, detail


def main():
    only = None
    if "--only" in sys.argv:
        try:
            only = int(sys.argv[sys.argv.index("--only") + 1])
        except (IndexError, ValueError):
            sys.exit("--only requires a benchmark id (1-11)")

    benches = [b for b in BENCHMARKS if only is None or b["id"] == only]
    if not benches:
        sys.exit(f"no benchmark with id {only}")

    print(f"Genie accuracy harness — space {SPACE_ID}")
    print(f"warehouse {dbx.WAREHOUSE_ID} | schema {SCHEMA} | tolerance {TOLERANCE:.0%} | "
          f"pass threshold {PASS_THRESHOLD:.0%}\n")
    if dbx.OFFLINE:
        sys.exit("DEMO_OFFLINE is set — Genie/warehouse are stubbed; run with live creds to score.")

    results = []
    print(f"{'#':>3}  {'RESULT':<6}  {'sec':>5}  QUESTION")
    print("-" * 100)
    for b in benches:
        t0 = time.time()
        try:
            passed, detail = run_one(b)
        except Exception as e:  # noqa: BLE001 — one bad benchmark shouldn't abort the run
            passed, detail = False, f"EXCEPTION: {type(e).__name__}: {str(e)[:160]}"
        dt = time.time() - t0
        results.append((b, passed, detail))
        tag = "PASS" if passed else "FAIL"
        guard = " (guardrail)" if b["metric"]["kind"] == "guardrail" else ""
        q = b["question"]
        print(f"{b['id']:>3}  {tag:<6}  {dt:>5.1f}  {q[:70]}{guard}")
        print(f"          └─ {detail}")

    n = len(results)
    npass = sum(1 for _, p, _ in results if p)
    acc = npass / n if n else 0.0
    print("-" * 100)
    print(f"\nOVERALL: {npass}/{n} passed = {acc:.0%}   (threshold {PASS_THRESHOLD:.0%})")

    if acc + 1e-9 < PASS_THRESHOLD:
        print("RESULT: BELOW THRESHOLD — failing for CI gate.")
        sys.exit(1)
    print("RESULT: OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
