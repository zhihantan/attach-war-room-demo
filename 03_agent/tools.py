#!/usr/bin/env python3
"""Attach War-Room agent tools.

Each tool returns a JSON-serializable dict. Data tools hit the metric views /
tables on the warehouse; state tools hit Lakebase; classify/draft use FMAPIs.
The shadow simulation is DETERMINISTIC (funnel-restore replay) so demo numbers
are defensible and reproducible — FMAPI is used for the genuinely-LLM work
(free-text cause classification, partner-note drafting), not for inventing KPIs.

Hardening (Tier 0/2):
  * All warehouse SQL uses bound :params + allow-listed enums — no value is ever
    f-string-interpolated into SQL (the old injection vector is gone).
  * The loss-ratio elasticity used by the shadow-sim is DERIVED from the claims
    book (observed loss ratio at std vs high deductible), not a hardcoded 1.12.
  * scan_for_anomalies makes the agent FIND the broken partner (reads the
    previously-dead alert_thresholds table); rollback_offer_change undoes a ship.
"""
import json

import dbx

S = dbx.SCHEMA
AS_OF = "current_date()"
STAGE_BY_CHANGE = {
    "restore_impressions": "offer_show_rate",
    "restore_deductible": "complete_to_bind_rate",
    "lower_price": "complete_to_bind_rate",
    "fix_activation": "activation_rate",
}
TIERS = {"premium", "mid", "budget"}
ABANDON_STAGES = {"offer_shown", "quote_started", "quote_completed", "bound", "activated"}


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def _validate_tier(device_tier):
    if device_tier and device_tier not in TIERS:
        raise ValueError(f"invalid device_tier '{device_tier}'; expected one of {sorted(TIERS)}")
    return device_tier


def _resolve_partner(partner):
    """Accept a partner_id (P01) or a name; return (partner_id, partner_name)."""
    if partner and partner.upper().startswith("P") and partner[1:].isdigit():
        rows = dbx.warehouse_query(
            f"SELECT partner_id, partner_name FROM {S}.partners WHERE partner_id = :pid",
            {"pid": partner.upper()})
    else:
        rows = dbx.warehouse_query(
            f"SELECT partner_id, partner_name FROM {S}.partners WHERE lower(partner_name) LIKE lower(:pat) LIMIT 1",
            {"pat": f"%{partner}%"})
    if not rows:
        return None, None
    return rows[0]["partner_id"], rows[0]["partner_name"]


def _filters(partner_id=None, device_tier=None, market_name=None):
    """Return (clauses, params) for a parameterized WHERE — no string interpolation."""
    clauses, params = [], {}
    if partner_id:
        clauses.append("partner_id = :pid")
        params["pid"] = partner_id
    if device_tier:
        _validate_tier(device_tier)
        clauses.append("device_tier = :tier")
        params["tier"] = device_tier
    if market_name:
        clauses.append("market_name = :mkt")
        params["mkt"] = market_name
    return clauses, params


def _rates(where, start_days, end_days):
    """Stage rates over [today-start_days, today-end_days). end_days=0 -> up to today.
    `where` is the (clauses, params) tuple from _filters. start/end_days are ints we control."""
    clauses, params = where
    start_days, end_days = int(start_days), int(end_days)
    period = [f"session_date >= date_add({AS_OF}, -{start_days})"]
    if end_days:
        period.append(f"session_date < date_add({AS_OF}, -{end_days})")
    clause = " AND ".join(clauses + period) or "true"
    rows = dbx.warehouse_query(f"""
        SELECT MEASURE(sessions) sessions, MEASURE(offer_show_rate) offer_show_rate,
               MEASURE(quote_start_rate) quote_start_rate, MEASURE(quote_complete_rate) quote_complete_rate,
               MEASURE(complete_to_bind_rate) complete_to_bind_rate, MEASURE(activation_rate) activation_rate,
               MEASURE(attach_rate) attach_rate, MEASURE(avg_premium_usd) avg_premium_usd,
               MEASURE(gross_written_premium_usd) gwp_usd
        FROM {S}.funnel_metrics WHERE {clause}""", params)
    return rows[0] if rows else {}


def _overview_rows(start_days, end_days):
    """Per-partner attach / sessions / avg-premium over a window (for the book scan)."""
    start_days, end_days = int(start_days), int(end_days)
    period = [f"session_date >= date_add({AS_OF}, -{start_days})"]
    if end_days:
        period.append(f"session_date < date_add({AS_OF}, -{end_days})")
    clause = " AND ".join(period)
    return dbx.warehouse_query(f"""
        SELECT partner_id, partner_name, MEASURE(sessions) sessions,
               ROUND(MEASURE(attach_rate),4) attach_rate, ROUND(MEASURE(avg_premium_usd),2) avg_premium_usd
        FROM {S}.funnel_metrics WHERE {clause} GROUP BY partner_id, partner_name""")


# ---------------------------------------------------------------------------
# diagnosis tools
# ---------------------------------------------------------------------------
def partner_funnel_overview(days: int = 30):
    """Attach, conversion, bind, activation and GWP by partner over the last `days`."""
    rows = dbx.warehouse_query(f"""
        SELECT partner_name, ROUND(MEASURE(attach_rate),4) attach_rate,
               ROUND(MEASURE(offer_show_rate),4) offer_show_rate, ROUND(MEASURE(conversion_rate),4) conversion_rate,
               ROUND(MEASURE(complete_to_bind_rate),4) complete_to_bind_rate, ROUND(MEASURE(activation_rate),4) activation_rate,
               ROUND(MEASURE(gross_written_premium_usd),0) gwp_usd
        FROM {S}.funnel_metrics WHERE session_date >= date_add({AS_OF}, -{int(days)})
        GROUP BY partner_name ORDER BY gwp_usd DESC""")
    return {"days": int(days), "partners": rows}


def scan_for_anomalies(days: int = 45, limit: int = 5):
    """Proactively scan EVERY partner for an attach drop that breaches its alert
    threshold (read from Lakebase alert_thresholds), ranked by recoverable GWP/month.
    Turns the demo from 'I tell it Velora Telecom is broken' into 'it finds the broken partner itself.'"""
    thresholds = {}
    try:
        with dbx.cursor(commit=False) as cur:
            cur.execute("SELECT partner_id, attach_drop_pct, loss_ratio_max FROM alert_thresholds")
            for pid, drop, lrmax in cur.fetchall():
                thresholds[pid] = {"attach_drop_pct": float(drop), "loss_ratio_max": float(lrmax)}
    except Exception as e:
        dbx.log.warning("scan_for_anomalies: alert_thresholds read failed: %s", e)
    default_drop = 0.05
    recent = {r["partner_id"]: r for r in _overview_rows(days, 0)}
    prior = {r["partner_id"]: r for r in _overview_rows(90, days)}
    breaches = []
    for pid, r in recent.items():
        p = prior.get(pid, {})
        ra, pa = (r.get("attach_rate") or 0), (p.get("attach_rate") or 0)
        if pa <= 0:
            continue
        rel_drop = (pa - ra) / pa
        thr = thresholds.get(pid, {}).get("attach_drop_pct", default_drop)
        if rel_drop > thr:
            sessions = r.get("sessions") or 0
            monthly = sessions * 30.0 / days if days else sessions
            recoverable = (pa - ra) * monthly * (r.get("avg_premium_usd") or 0)
            breaches.append({
                "partner_id": pid, "partner_name": r.get("partner_name"),
                "attach_recent": _r(ra), "attach_prior": _r(pa),
                "attach_rel_drop": _r(rel_drop), "alert_threshold": _r(thr),
                "recoverable_gwp_per_month_usd": _r(recoverable, 0)})
    breaches.sort(key=lambda x: -(x["recoverable_gwp_per_month_usd"] or 0))
    return {"days": int(days), "partners_scanned": len(recent), "breaches": breaches[:int(limit)],
            "top_opportunity": breaches[0] if breaches else None}


def funnel_diagnosis(partner: str, device_tier: str = None, recent_days: int = 45):
    """Compare each funnel stage rate for a partner (optionally a device tier) in the
    last `recent_days` vs the prior period, to localize where the funnel broke."""
    pid, pname = _resolve_partner(partner)
    if not pid:
        return {"error": f"partner '{partner}' not found"}
    where = _filters(pid, _validate_tier(device_tier))
    recent = _rates(where, recent_days, 0)
    prior = _rates(where, 90, recent_days)
    keys = ["offer_show_rate", "quote_start_rate", "quote_complete_rate", "complete_to_bind_rate",
            "activation_rate", "attach_rate"]
    deltas = {k: _r((recent.get(k) or 0) - (prior.get(k) or 0)) for k in keys}
    # biggest relative drop among the stage rates (exclude attach which is the outcome)
    stage_keys = keys[:-1]
    worst = min(stage_keys, key=lambda k: ((recent.get(k) or 0) - (prior.get(k) or 0)))
    return {
        "partner": pname, "device_tier": device_tier,
        "recent": {k: _r(recent.get(k)) for k in keys} | {"sessions": recent.get("sessions")},
        "prior": {k: _r(prior.get(k)) for k in keys} | {"sessions": prior.get("sessions")},
        "deltas": deltas,
        "largest_stage_drop": worst,
        "reading": (f"{worst} fell {deltas[worst]:+.3f} while downstream conversion held"
                    if deltas[worst] is not None else None),
    }


def abandonment_reasons(partner: str, stage: str = "quote_completed", days: int = 30, limit: int = 6):
    """Top free-text abandonment reasons for a partner at a given funnel stage."""
    pid, pname = _resolve_partner(partner)
    if not pid:
        return {"error": f"partner '{partner}' not found"}
    if stage not in ABANDON_STAGES:
        return {"error": f"invalid stage '{stage}'; expected one of {sorted(ABANDON_STAGES)}"}
    rows = dbx.warehouse_query(f"""
        SELECT abandon_reason_text, COUNT(*) n FROM {S}.sessions
        WHERE partner_id = :pid AND stage_reached = :stage AND session_date >= date_add({AS_OF}, -{int(days)})
          AND abandon_reason_text IS NOT NULL
        GROUP BY abandon_reason_text ORDER BY n DESC LIMIT {int(limit)}""",
        {"pid": pid, "stage": stage})
    return {"partner": pname, "stage": stage, "days": int(days), "reasons": rows}


def classify_abandonment(reasons: list):
    """FMAPI: classify free-text abandonment reasons into a cause taxonomy."""
    labels = ["price_shock", "payment_failure", "form_friction", "offer_not_relevant", "other"]
    joined = "\n".join(f"- {r}" for r in reasons)
    msg = [{"role": "system", "content": f"Classify each reason into exactly one of {labels}. Return a JSON object mapping each reason text to its label. Return only JSON."},
           {"role": "user", "content": joined}]
    txt = dbx.chat(msg, model=dbx.MODEL_CLASSIFIER, max_tokens=400, temperature=0).choices[0].message.content
    try:
        mapping = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception:
        mapping = {r: "other" for r in reasons}
    # dominant cause
    from collections import Counter
    dom = Counter(mapping.values()).most_common(1)
    return {"classification": mapping, "dominant_cause": dom[0][0] if dom else None}


def get_offer_config(partner: str, device_tier: str = None):
    """Read current offer-serving rules from Lakebase (what the checkout reads now)."""
    pid, pname = _resolve_partner(partner)
    _validate_tier(device_tier)
    clause = "partner_id=%s" + (" AND device_tier=%s" if device_tier else "")
    args = [pid] + ([device_tier] if device_tier else [])
    with dbx.cursor(commit=False) as cur:
        cur.execute(f"""SELECT config_id, partner_id, product_id, device_tier, impression_enabled,
                        placement, deductible_tier_shown, price_band, version, updated_by
                        FROM offer_config WHERE {clause} ORDER BY product_id""", args)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"partner": pname, "device_tier": device_tier, "configs": rows}


def loss_ratio_for(partner: str = None, device_tier: str = None, market_name: str = None):
    """Loss ratio, claims frequency and GWP for a slice (the guardrail check)."""
    pid = None
    pname = None
    if partner:
        pid, pname = _resolve_partner(partner)
    clauses, params = _filters(pid, _validate_tier(device_tier), market_name)
    clause = " AND ".join(clauses) or "true"
    rows = dbx.warehouse_query(f"""
        SELECT ROUND(MEASURE(loss_ratio),4) loss_ratio, ROUND(MEASURE(claims_frequency),4) claims_frequency,
               ROUND(MEASURE(gross_written_premium_usd),0) gwp_usd, ROUND(MEASURE(avg_settlement_days),1) avg_settlement_days,
               MEASURE(policy_count) policy_count
        FROM {S}.profitability_metrics WHERE {clause}""", params)
    out = rows[0] if rows else {}
    out["partner"] = pname
    out["device_tier"] = device_tier
    out["market"] = market_name
    out["within_guardrail"] = (out.get("loss_ratio") is not None and out["loss_ratio"] <= 0.70)
    return out


def _deductible_loss_elasticity(partner_id=None):
    """Observed loss-ratio multiplier from LOWERING the deductible one tier (high -> std),
    DERIVED from the synthetic claims book (loss ratio at std deductible / at high deductible)
    rather than a hardcoded constant. Falls back to a labelled placeholder if the slice is thin."""
    params = {}
    where_p = "WHERE p.status <> 'cancelled'"
    if partner_id:
        where_p += " AND p.partner_id = :pid"
        params["pid"] = partner_id
    try:
        rows = dbx.warehouse_query(f"""
            SELECT p.deductible_tier dt,
                   SUM(coalesce(c.loss_usd,0)) / NULLIF(SUM(p.gwp_usd),0) AS loss_ratio,
                   COUNT(1) n
            FROM {S}.policies p
            LEFT JOIN (SELECT policy_id, SUM(CASE WHEN status<>'denied' THEN claim_amount_usd ELSE 0 END) loss_usd
                       FROM {S}.claims GROUP BY policy_id) c ON p.policy_id = c.policy_id
            {where_p}
            GROUP BY p.deductible_tier""", params)
    except Exception as e:
        dbx.log.warning("elasticity derivation failed (%s); using placeholder", e)
        return {"multiplier": 1.12, "derived": False, "by_tier": {}}
    by = {r["dt"]: r for r in rows}
    hi = (by.get("high") or {}).get("loss_ratio")
    std = (by.get("std") or {}).get("loss_ratio")
    if hi and std and hi > 0 and (by.get("std") or {}).get("n", 0) > 50:
        return {"multiplier": max(1.0, round(std / hi, 4)), "derived": True,
                "by_tier": {k: _r(v.get("loss_ratio")) for k, v in by.items()}}
    return {"multiplier": 1.12, "derived": False, "by_tier": {k: _r(v.get("loss_ratio")) for k, v in by.items()}}


# ---------------------------------------------------------------------------
# simulation + write tools
# ---------------------------------------------------------------------------
def run_shadow_sim(partner: str, device_tier: str, change_type: str, recent_days: int = 45):
    """Deterministically project the impact of an offer change by restoring the
    affected funnel stage to its healthy prior level, replayed over recent traffic.
    change_type in: restore_impressions | restore_deductible | lower_price | fix_activation."""
    pid, pname = _resolve_partner(partner)
    if not pid:
        return {"error": f"partner '{partner}' not found"}
    if change_type not in STAGE_BY_CHANGE:
        return {"error": f"unknown change_type; use one of {list(STAGE_BY_CHANGE)}"}
    _validate_tier(device_tier)
    where = _filters(pid, device_tier)
    cur_r, prior_r = _rates(where, recent_days, 0), _rates(where, 90, recent_days)
    stage = STAGE_BY_CHANGE[change_type]
    g = lambda d, k: float(d.get(k) or 0)

    def bound_rate(r, override=None):
        rates = {k: g(r, k) for k in ["offer_show_rate", "quote_start_rate", "quote_complete_rate", "complete_to_bind_rate"]}
        if override:
            rates.update(override)
        return rates["offer_show_rate"] * rates["quote_start_rate"] * rates["quote_complete_rate"] * rates["complete_to_bind_rate"]

    target = g(prior_r, stage)  # healthy baseline for the broken stage
    cur_bound = bound_rate(cur_r)
    cur_act = cur_bound * g(cur_r, "activation_rate")
    if change_type == "fix_activation":
        proj_bound = cur_bound
        proj_act = cur_bound * target
        realized_cur, realized_proj = cur_act, proj_act
    else:
        proj_bound = bound_rate(cur_r, {stage: target})
        proj_act = proj_bound * g(cur_r, "activation_rate")
        realized_cur, realized_proj = cur_bound, proj_bound

    sessions = g(cur_r, "sessions")
    monthly_sessions = sessions * 30.0 / recent_days if recent_days else sessions
    avg_premium = g(cur_r, "avg_premium_usd")
    recovered_gwp_month = (realized_proj - realized_cur) * monthly_sessions * avg_premium

    lr = loss_ratio_for(partner=pid, device_tier=device_tier)
    base_lr = lr.get("loss_ratio") or 0.0
    # deductible/price moves change risk; impression/activation fixes do not. The
    # elasticity is DERIVED from the claims book, not a magic number.
    if change_type in ("restore_deductible", "lower_price"):
        el = _deductible_loss_elasticity(pid)
    else:
        el = {"multiplier": 1.0, "derived": True, "by_tier": {}}
    proj_lr = base_lr * el["multiplier"]

    if change_type == "restore_impressions":
        note = "conversion-of-shown held, so restoring impressions recovers attach without changing risk"
    elif change_type in ("restore_deductible", "lower_price"):
        src = ("observed loss ratio at std vs high deductible in the claims book"
               if el["derived"] else "illustrative elasticity placeholder (thin claims slice)")
        note = (f"lowering the deductible lifts bind but raises projected loss ratio "
                f"x{el['multiplier']:.2f} ({src})")
    else:
        note = "fixing first-payment recovers activated policies post-bind"

    return {
        "partner": pname, "device_tier": device_tier, "change_type": change_type, "stage_restored": stage,
        "current_attach": _r(cur_bound), "projected_attach": _r(proj_bound),
        "attach_delta": _r(proj_bound - cur_bound),
        "current_activated_rate": _r(cur_act), "projected_activated_rate": _r(proj_act),
        "recent_sessions": int(sessions), "monthly_sessions_est": int(monthly_sessions),
        "avg_premium_usd": _r(avg_premium, 2),
        "recovered_gwp_per_month_usd": _r(recovered_gwp_month, 0),
        "baseline_loss_ratio": _r(base_lr), "projected_loss_ratio": _r(proj_lr),
        "loss_ratio_elasticity": el["multiplier"], "elasticity_derived_from_data": el["derived"],
        "loss_ratio_by_deductible": el["by_tier"],
        "within_guardrail": bool(proj_lr <= 0.70),
        "guardrail": 0.70,
        "note": note,
    }


def propose_offer_change(partner: str, device_tier: str, change_type: str, title: str = None, recent_days: int = 45):
    """Save a shadow-tested scenario to Lakebase (status=proposed). Re-runs the sim
    internally so the stored numbers are authoritative. Returns scenario_id + sim."""
    pid, pname = _resolve_partner(partner)
    sim = run_shadow_sim(pid, device_tier, change_type, recent_days)
    if sim.get("error"):
        return sim
    title = title or f"{change_type} for {pname} {device_tier or ''}".strip()
    with dbx.cursor() as cur:
        cur.execute("""
            INSERT INTO scenarios (created_by, partner_id, device_tier, title, proposed_change,
                baseline_attach, projected_attach, projected_attach_delta, baseline_loss_ratio,
                projected_loss_ratio, projected_gwp_delta_usd, within_guardrail, status)
            VALUES ('agent', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'proposed') RETURNING scenario_id""",
            (pid, device_tier, title, json.dumps({"change_type": change_type}),
             sim["current_attach"], sim["projected_attach"], sim["attach_delta"], sim["baseline_loss_ratio"],
             sim["projected_loss_ratio"], sim["recovered_gwp_per_month_usd"], sim["within_guardrail"]))
        scenario_id = cur.fetchone()[0]
    return {"scenario_id": scenario_id, "title": title, "simulation": sim,
            "needs_approval": True, "blocked_by_guardrail": not sim["within_guardrail"]}


def ship_offer_change(scenario_id: int, approved_by: str = "demo_user"):
    """Apply an approved scenario as a multi-table ACID write: update the live
    offer_config the checkout reads, append an audit row, and mark the scenario shipped.
    Blocked if the scenario breaches the loss-ratio guardrail."""
    with dbx.cursor() as cur:  # single transaction
        cur.execute("""SELECT partner_id, device_tier, proposed_change, projected_attach_delta,
                       projected_loss_ratio, projected_gwp_delta_usd, within_guardrail, status, title
                       FROM scenarios WHERE scenario_id=%s""", (scenario_id,))
        row = cur.fetchone()
        if not row:
            return {"error": f"scenario {scenario_id} not found"}
        pid, tier, change_json, attach_delta, proj_lr, gwp_delta, within, status, title = row
        if not within:
            return {"error": "blocked by loss-ratio guardrail", "projected_loss_ratio": proj_lr, "guardrail": 0.70}
        change_type = (change_json or {}).get("change_type") if isinstance(change_json, dict) else json.loads(change_json or "{}").get("change_type")

        field, before_val, after_val, updated = None, None, None, 0
        if change_type == "restore_impressions":
            field, before_val, after_val = "impression_enabled", "false", "true"
            cur.execute("""UPDATE offer_config SET impression_enabled=true, version=version+1,
                           updated_by='agent', updated_at=now()
                           WHERE partner_id=%s AND device_tier=%s AND impression_enabled=false""", (pid, tier))
            updated = cur.rowcount
        elif change_type in ("restore_deductible", "lower_price"):
            field, before_val, after_val = "deductible_tier_shown", "high", "std"
            cur.execute("""UPDATE offer_config SET deductible_tier_shown='std', version=version+1,
                           updated_by='agent', updated_at=now()
                           WHERE partner_id=%s AND deductible_tier_shown='high'""", (pid,))
            updated = cur.rowcount
        else:  # fix_activation — operational fix, recorded but no offer_config field
            field, before_val, after_val = "payment_provider", "provider_b", "provider_a"

        cur.execute("""INSERT INTO offer_config_audit (partner_id, device_tier, changed_by, field_changed,
                       before_value, after_value, rationale, projected_attach_delta, projected_loss_ratio, scenario_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING audit_id""",
                    (pid, tier, approved_by, field, before_val, after_val,
                     f"Approved shadow-tested change: {title}", attach_delta, proj_lr, scenario_id))
        audit_id = cur.fetchone()[0]
        cur.execute("UPDATE scenarios SET status='shipped' WHERE scenario_id=%s", (scenario_id,))
    return {"scenario_id": scenario_id, "shipped": True, "configs_updated": updated, "field_changed": field,
            "before": before_val, "after": after_val, "audit_id": audit_id,
            "projected_gwp_delta_usd": gwp_delta, "projected_attach_delta": attach_delta}


def rollback_offer_change(scenario_id: int = None, audit_id: int = None, rolled_back_by: str = "demo_user"):
    """Reverse a shipped offer change as a multi-table ACID undo: restore the prior
    offer_config value, append a compensating audit row, and mark the scenario rolled_back.
    With no id, rolls back the most recent shipped change. Recovered-GWP ticks back down."""
    with dbx.cursor() as cur:
        sel = ("SELECT audit_id, partner_id, device_tier, field_changed, before_value, after_value, scenario_id "
               "FROM offer_config_audit ")
        if audit_id:
            cur.execute(sel + "WHERE audit_id=%s", (audit_id,))
        elif scenario_id:
            cur.execute(sel + "WHERE scenario_id=%s ORDER BY audit_id DESC LIMIT 1", (scenario_id,))
        else:
            cur.execute(sel + "ORDER BY audit_id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return {"error": "no shipped change found to roll back"}
        aud_id, pid, tier, field, before_val, after_val, scen_id = row
        reverted = 0
        if field == "impression_enabled":
            want = (str(before_val).lower() == "true")
            cur.execute("""UPDATE offer_config SET impression_enabled=%s, version=version+1,
                           updated_by='agent(rollback)', updated_at=now()
                           WHERE partner_id=%s AND device_tier=%s AND impression_enabled=%s""",
                        (want, pid, tier, not want))
            reverted = cur.rowcount
        elif field == "deductible_tier_shown":
            cur.execute("""UPDATE offer_config SET deductible_tier_shown=%s, version=version+1,
                           updated_by='agent(rollback)', updated_at=now()
                           WHERE partner_id=%s AND deductible_tier_shown=%s""",
                        (before_val, pid, after_val))
            reverted = cur.rowcount
        cur.execute("""INSERT INTO offer_config_audit (partner_id, device_tier, changed_by, field_changed,
                       before_value, after_value, rationale, scenario_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING audit_id""",
                    (pid, tier, rolled_back_by, field, after_val, before_val,
                     f"Rollback of audit {aud_id}", scen_id))
        new_audit = cur.fetchone()[0]
        if scen_id:
            cur.execute("UPDATE scenarios SET status='rolled_back' WHERE scenario_id=%s", (scen_id,))
    return {"rolled_back": True, "reverted_configs": reverted, "field_changed": field,
            "restored_to": before_val, "audit_id": new_audit, "scenario_id": scen_id}


def query_genie(question: str):
    """Open-ended natural-language analytics via the governed Genie space."""
    return dbx.genie_ask(question)


def draft_partner_note(partner: str, context: str):
    """FMAPI: draft a concise, professional partner-facing note summarizing the finding and fix."""
    msg = [{"role": "system", "content": "You are an Acme partnerships lead. Draft a concise, professional note (<=120 words) to a distribution partner summarizing a conversion finding and the corrective action taken. No fabricated numbers beyond those given."},
           {"role": "user", "content": f"Partner: {partner}\nContext/numbers:\n{context}"}]
    note = dbx.chat(msg, model=dbx.MODEL_AGENT, max_tokens=320, temperature=0.3).choices[0].message.content
    return {"partner": partner, "note": note}


def recent_activity(limit: int = 5):
    """Ground truth for what has been shipped/changed: recent scenarios + offer-config audit from Lakebase."""
    with dbx.cursor(commit=False) as cur:
        cur.execute("SELECT scenario_id, partner_id, device_tier, title, status, projected_attach_delta, projected_gwp_delta_usd FROM scenarios ORDER BY scenario_id DESC LIMIT %s", (limit,))
        scen = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        cur.execute("SELECT audit_id, partner_id, device_tier, field_changed, before_value, after_value, rationale, projected_attach_delta, projected_loss_ratio FROM offer_config_audit ORDER BY audit_id DESC LIMIT %s", (limit,))
        audit = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    return {"shipped_scenarios": [s for s in scen if s.get("status") == "shipped"], "recent_scenarios": scen, "recent_audit": audit}


# ---------------------------------------------------------------------------
# registry + OpenAI tool schemas
# ---------------------------------------------------------------------------
TOOLS = {
    "partner_funnel_overview": partner_funnel_overview,
    "scan_for_anomalies": scan_for_anomalies,
    "recent_activity": recent_activity,
    "funnel_diagnosis": funnel_diagnosis,
    "abandonment_reasons": abandonment_reasons,
    "classify_abandonment": classify_abandonment,
    "get_offer_config": get_offer_config,
    "loss_ratio_for": loss_ratio_for,
    "run_shadow_sim": run_shadow_sim,
    "propose_offer_change": propose_offer_change,
    "ship_offer_change": ship_offer_change,
    "rollback_offer_change": rollback_offer_change,
    "query_genie": query_genie,
    "draft_partner_note": draft_partner_note,
}


def _schema(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


TOOL_SCHEMAS = [
    _schema("partner_funnel_overview", "Attach, conversion, bind, activation and GWP by partner over the last N days. Use first to spot which partner is off.",
            {"days": {"type": "integer", "description": "lookback days (default 30)"}}, []),
    _schema("scan_for_anomalies", "Proactively scan EVERY partner for an attach drop that breaches its per-partner alert threshold (from Lakebase), ranked by recoverable GWP/month. Use when the user asks 'where is the biggest problem?' or 'scan the book' rather than naming a partner.",
            {"days": {"type": "integer", "description": "recent window in days (default 45)"}, "limit": {"type": "integer"}}, []),
    _schema("funnel_diagnosis", "Compare each funnel stage rate (offer_show, quote_start, quote_complete, complete_to_bind, activation, attach) for a partner (optionally a device tier) recent vs prior, to localize where the funnel broke.",
            {"partner": {"type": "string"}, "device_tier": {"type": "string", "enum": ["premium", "mid", "budget"]}, "recent_days": {"type": "integer"}}, ["partner"]),
    _schema("abandonment_reasons", "Top free-text abandonment reasons for a partner at a funnel stage (e.g., quote_completed for bind-stage drop, bound for activation-stage drop).",
            {"partner": {"type": "string"}, "stage": {"type": "string", "enum": ["offer_shown", "quote_started", "quote_completed", "bound"]}, "days": {"type": "integer"}}, ["partner"]),
    _schema("classify_abandonment", "Classify free-text abandonment reasons into a cause taxonomy (price_shock, payment_failure, form_friction, offer_not_relevant, other).",
            {"reasons": {"type": "array", "items": {"type": "string"}}}, ["reasons"]),
    _schema("get_offer_config", "Read the current offer-serving rules from Lakebase (what the live checkout reads now), e.g. whether impressions are enabled.",
            {"partner": {"type": "string"}, "device_tier": {"type": "string"}}, ["partner"]),
    _schema("loss_ratio_for", "Loss ratio, claims frequency and GWP for a slice (the guardrail check). Loss ratio above 0.70 breaches the guardrail.",
            {"partner": {"type": "string"}, "device_tier": {"type": "string"}, "market_name": {"type": "string"}}, []),
    _schema("run_shadow_sim", "Deterministically project the impact of an offer change by restoring the affected funnel stage to its healthy prior level over recent traffic. Returns projected attach delta, recovered GWP/month, and projected loss ratio (with a data-derived elasticity) vs the 0.70 guardrail.",
            {"partner": {"type": "string"}, "device_tier": {"type": "string"}, "change_type": {"type": "string", "enum": ["restore_impressions", "restore_deductible", "lower_price", "fix_activation"]}, "recent_days": {"type": "integer"}}, ["partner", "change_type"]),
    _schema("propose_offer_change", "Save a shadow-tested scenario to Lakebase (status=proposed) for human approval. Re-runs the sim so stored numbers are authoritative.",
            {"partner": {"type": "string"}, "device_tier": {"type": "string"}, "change_type": {"type": "string", "enum": ["restore_impressions", "restore_deductible", "lower_price", "fix_activation"]}, "title": {"type": "string"}}, ["partner", "change_type"]),
    _schema("ship_offer_change", "Apply an APPROVED scenario as a multi-table ACID write to Lakebase (updates the live offer_config the checkout reads + audit + scenario status). Only call after the user approves. Blocked if it breaches the guardrail.",
            {"scenario_id": {"type": "integer"}, "approved_by": {"type": "string"}}, ["scenario_id"]),
    _schema("rollback_offer_change", "Reverse a shipped offer change as a multi-table ACID undo (restores the prior offer_config value + compensating audit + marks the scenario rolled_back). With no id, rolls back the most recent ship. Use when the user asks to undo/revert/roll back a change.",
            {"scenario_id": {"type": "integer"}, "audit_id": {"type": "integer"}, "rolled_back_by": {"type": "string"}}, []),
    _schema("query_genie", "Open-ended natural-language analytics via the governed Genie space (use for ad-hoc questions not covered by the other tools).",
            {"question": {"type": "string"}}, ["question"]),
    _schema("draft_partner_note", "Draft a concise partner-facing note summarizing the finding and corrective action.",
            {"partner": {"type": "string"}, "context": {"type": "string"}}, ["partner", "context"]),
    _schema("recent_activity", "Ground truth from Lakebase for what has been shipped/changed (recent scenarios + offer-config audit). Use this whenever the user asks what was shipped/changed or to recap — do NOT infer shipped state from the conversation.",
            {"limit": {"type": "integer"}}, []),
]
