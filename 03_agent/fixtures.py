#!/usr/bin/env python3
"""Break-glass offline mode (Tier 0.6).

When DEMO_OFFLINE=1, the app serves deterministic fixtures instead of touching
Databricks — so the 90-second hero flow (diagnose -> propose -> approve -> flip)
still renders when the workspace / FMAPI / Lakebase is unreachable or the
conference wifi dies mid-pitch. Nothing here calls the network.

This is a faithful replay of the Velora Telecom impressions hero path; other partners
return plausible-but-generic data. Pair with the recorded MP4 in the README.
"""
import json
import time

# in-process state so Approve flips the checkout + GWP tile within the session
_STATE = {"shipped": False, "recovered": 0.0}

PARTNERS = [
    {"partner_id": "P01", "partner_name": "Velora Telecom", "partner_type": "telco", "market_id": "IT"},
    {"partner_id": "P02", "partner_name": "Siam Mobile Care", "partner_type": "OEM", "market_id": "TH"},
    {"partner_id": "P03", "partner_name": "Marina Mobile", "partner_type": "telco", "market_id": "SG"},
    {"partner_id": "P04", "partner_name": "Savanna Mobile", "partner_type": "telco", "market_id": "KE"},
    {"partner_id": "P05", "partner_name": "Brightway Electronics", "partner_type": "retail", "market_id": "GB"},
    {"partner_id": "P08", "partner_name": "Rift Valley Bank", "partner_type": "bank", "market_id": "KE"},
]
_MARKET = {"P01": "Italy", "P02": "Thailand", "P03": "Singapore", "P04": "Kenya", "P05": "United Kingdom", "P08": "Kenya"}

_VELORA_FUNNEL = {
    "partner": "Velora Telecom", "device_tier": "mid", "largest_stage_drop": "offer_show_rate",
    "prior": {"offer_show_rate": 0.865, "quote_start_rate": 0.62, "quote_complete_rate": 0.72,
              "complete_to_bind_rate": 0.56, "activation_rate": 0.96, "attach_rate": 0.223, "sessions": 41000},
    "recent": {"offer_show_rate": 0.530, "quote_start_rate": 0.62, "quote_complete_rate": 0.72,
               "complete_to_bind_rate": 0.56, "activation_rate": 0.96, "attach_rate": 0.152, "sessions": 22000},
}
_GENERIC_FUNNEL = {
    "partner": None, "device_tier": "mid", "largest_stage_drop": "complete_to_bind_rate",
    "prior": {"offer_show_rate": 0.84, "quote_start_rate": 0.63, "quote_complete_rate": 0.72,
              "complete_to_bind_rate": 0.55, "activation_rate": 0.95, "attach_rate": 0.21, "sessions": 30000},
    "recent": {"offer_show_rate": 0.83, "quote_start_rate": 0.62, "quote_complete_rate": 0.71,
               "complete_to_bind_rate": 0.53, "activation_rate": 0.95, "attach_rate": 0.20, "sessions": 16000},
}


def partners():
    return PARTNERS


def funnel(partner, device_tier="mid", recent_days=45):
    base = dict(_VELORA_FUNNEL if (partner or "").lower().startswith("velora") else _GENERIC_FUNNEL)
    base["partner"] = partner
    base["device_tier"] = device_tier
    keys = ["offer_show_rate", "quote_start_rate", "quote_complete_rate", "complete_to_bind_rate", "activation_rate", "attach_rate"]
    base["deltas"] = {k: round((base["recent"][k] - base["prior"][k]), 4) for k in keys}
    return base


def loss_ratio(partner=None, device_tier=None, market_name=None):
    lr = 0.89 if (partner or "").lower().startswith("savanna") else 0.29
    return {"loss_ratio": lr, "claims_frequency": 0.14 if lr < 0.5 else 0.30, "gwp_usd": 120000,
            "avg_settlement_days": 7.5, "policy_count": 5400, "within_guardrail": lr <= 0.70,
            "partner": partner, "device_tier": device_tier, "market": market_name}


def checkout(partner, device_tier="mid"):
    is_velora = (partner or "").lower().startswith("velora")
    shown = (not is_velora) or _STATE["shipped"]
    return {"partner": partner, "device_tier": device_tier, "offer_shown": shown,
            "placement": "checkout_confirmation", "deductible_tier_shown": "std",
            "products_enabled": 5 if shown else 0, "products_total": 5, "configs": []}


def recovered_gwp():
    return {"recovered_gwp_per_month_usd": _STATE["recovered"], "shipped_count": 1 if _STATE["shipped"] else 0}


def scenarios():
    if not _STATE["shipped"]:
        return []
    return [{"scenario_id": 9001, "partner_id": "P01", "device_tier": "mid", "title": "restore_impressions for Velora Telecom mid",
             "projected_attach_delta": 0.097, "projected_loss_ratio": 0.29, "projected_gwp_delta_usd": 8192.0,
             "within_guardrail": True, "status": "shipped"}]


def audit():
    if not _STATE["shipped"]:
        return []
    return [{"audit_id": 1, "partner_id": "P01", "device_tier": "mid", "field_changed": "impression_enabled",
             "before_value": "false", "after_value": "true", "rationale": "Approved shadow-tested change: restore_impressions for Velora Telecom mid",
             "projected_attach_delta": 0.097, "projected_loss_ratio": 0.29, "scenario_id": 9001}]


def scan():
    # Mirrors the LIVE scan ranking (ranked by recoverable GWP/month). Siam Mobile Care's
    # bind-stage price-shock is the biggest $ leak; Velora Telecom's impressions bug is
    # next; the rest are minor noise. Rift Valley's leak is in ACTIVATION (post-bind), so
    # it does not lower attach rate and correctly never surfaces in an attach scan.
    breaches = [
        {"partner_id": "P02", "partner_name": "Siam Mobile Care", "attach_recent": 0.176, "attach_prior": 0.248,
         "attach_rel_drop": 0.290, "alert_threshold": 0.05, "recoverable_gwp_per_month_usd": 10438.0},
        {"partner_id": "P01", "partner_name": "Velora Telecom", "attach_recent": 0.165, "attach_prior": 0.192,
         "attach_rel_drop": 0.142, "alert_threshold": 0.05, "recoverable_gwp_per_month_usd": 6137.0},
        {"partner_id": "P03", "partner_name": "Marina Mobile", "attach_recent": 0.210, "attach_prior": 0.227,
         "attach_rel_drop": 0.076, "alert_threshold": 0.05, "recoverable_gwp_per_month_usd": 2114.0},
        {"partner_id": "P11", "partner_name": "Aquila Mobile", "attach_recent": 0.190, "attach_prior": 0.211,
         "attach_rel_drop": 0.099, "alert_threshold": 0.05, "recoverable_gwp_per_month_usd": 1441.0},
    ]
    return {"days": 45, "partners_scanned": 12, "top_opportunity": breaches[0], "breaches": breaches}


def approve(scenario_id=9001, approved_by="demo_user"):
    _STATE["shipped"] = True
    _STATE["recovered"] = 8192.0
    return {"scenario_id": scenario_id, "shipped": True, "configs_updated": 5, "field_changed": "impression_enabled",
            "before": "false", "after": "true", "audit_id": 1, "projected_gwp_delta_usd": 8192.0, "projected_attach_delta": 0.097}


def reset():
    _STATE["shipped"] = False
    _STATE["recovered"] = 0.0
    return {"reset": True, "offline": True}


def health():
    return {"ok": True, "offline": True, "checks": {"mode": {"ok": True, "detail": "DEMO_OFFLINE — serving fixtures"}}}


_PROPOSAL = {
    "scenario_id": 9001, "title": "restore_impressions for Velora Telecom mid", "needs_approval": True,
    "blocked_by_guardrail": False,
    "simulation": {"current_attach": 0.152, "projected_attach": 0.249, "attach_delta": 0.097,
                   "recovered_gwp_per_month_usd": 8192.0, "projected_loss_ratio": 0.29, "within_guardrail": True},
}
_GUARDRAIL_PROPOSAL = {
    "scenario_id": 9002, "title": "lower_price for Savanna Mobile budget", "needs_approval": True,
    "blocked_by_guardrail": True,
    "simulation": {"current_attach": 0.11, "projected_attach": 0.16, "attach_delta": 0.05,
                   "recovered_gwp_per_month_usd": 3100.0, "projected_loss_ratio": 0.93, "within_guardrail": False},
}


def _emit(events, slow=True):
    for ev in events:
        if slow and ev["type"] == "delta":
            time.sleep(0.012)
        yield ev


def _stream_text(text):
    for word in text.split(" "):
        yield {"type": "delta", "content": word + " "}


def stream_turn(conversation_id, user_message):
    """Scripted offline replay keyed off the user's intent."""
    m = (user_message or "").lower()
    if any(k in m for k in ("approve", "ship it", "ship to")):
        approve()
        yield from _stream_text("Shipped — 5 offer configs flipped for Velora Telecom mid-tier. Recovered GWP is now **$8,192/month** and the checkout is live again.")
        yield {"type": "final", "content": "Shipped — 5 offer configs flipped for Velora Telecom mid-tier. Recovered GWP is now **$8,192/month** and the checkout is live again."}
        return
    if any(k in m for k in ("shadow", "propose", "restore", "fix")) and "savanna" not in m:
        yield {"type": "tool_call", "name": "run_shadow_sim", "args": {"partner": "Velora Telecom", "device_tier": "mid", "change_type": "restore_impressions"}}
        yield {"type": "tool_result", "name": "run_shadow_sim", "result": _PROPOSAL["simulation"]}
        yield {"type": "tool_call", "name": "propose_offer_change", "args": {"partner": "Velora Telecom", "device_tier": "mid", "change_type": "restore_impressions"}}
        yield {"type": "tool_result", "name": "propose_offer_change", "result": _PROPOSAL}
        txt = ("Shadow-test: restoring impressions lifts attach **15% → 25% (+9.7pts)**, recovering **$8,192/mo** GWP, "
               "with projected loss ratio **29%** — well within the 0.70 guardrail. Proposed change is ready for your approval.")
        yield from _stream_text(txt)
        yield {"type": "final", "content": txt}
        return
    if "savanna" in m or "kenya" in m and ("deductible" in m or "lower" in m):
        yield {"type": "tool_call", "name": "run_shadow_sim", "args": {"partner": "Savanna Mobile", "device_tier": "budget", "change_type": "lower_price"}}
        yield {"type": "tool_result", "name": "run_shadow_sim", "result": _GUARDRAIL_PROPOSAL["simulation"]}
        yield {"type": "tool_call", "name": "propose_offer_change", "args": {"partner": "Savanna Mobile", "device_tier": "budget", "change_type": "lower_price"}}
        yield {"type": "tool_result", "name": "propose_offer_change", "result": _GUARDRAIL_PROPOSAL}
        txt = ("⛔ I will **not** propose shipping this. Lowering the deductible would lift attach ~5pts but push projected "
               "loss ratio to **93%** — far above the 0.70 guardrail. That buys attach with underpriced cover.")
        yield from _stream_text(txt)
        yield {"type": "final", "content": txt}
        return
    if "scan" in m or "biggest" in m or "book" in m:
        yield {"type": "tool_call", "name": "scan_for_anomalies", "args": {"days": 45}}
        yield {"type": "tool_result", "name": "scan_for_anomalies", "result": scan()}
        txt = ("Scanned all 12 partners and ranked every attach leak by recovered GWP. The biggest is "
               "**Siam Mobile Care** — a price-shock at the bind stage, ~**$10.4k/mo** recoverable — with "
               "**Velora Telecom**'s impressions bug next (~$6.1k/mo). I'd start with Velora Telecom: it's the cleanest to "
               "fix — the offer simply stopped showing, so restoring it recovers attach at no added risk.")
        yield from _stream_text(txt)
        yield {"type": "final", "content": txt}
        return
    # default: diagnose Velora Telecom
    yield {"type": "tool_call", "name": "funnel_diagnosis", "args": {"partner": "Velora Telecom", "device_tier": "mid"}}
    yield {"type": "tool_result", "name": "funnel_diagnosis", "result": funnel("Velora Telecom", "mid")}
    yield {"type": "tool_call", "name": "get_offer_config", "args": {"partner": "Velora Telecom", "device_tier": "mid"}}
    yield {"type": "tool_result", "name": "get_offer_config", "result": {"configs": [{"impression_enabled": False}] * 5}}
    yield {"type": "tool_call", "name": "loss_ratio_for", "args": {"partner": "Velora Telecom", "device_tier": "mid"}}
    yield {"type": "tool_result", "name": "loss_ratio_for", "result": loss_ratio("Velora Telecom", "mid")}
    txt = ("Attach fell **22% → 15%** for Velora Telecom mid-tier because **offer-shown** collapsed **86% → 53%**, while "
           "conversion-of-shown held ~28% — an **impressions** problem, not pricing. Root cause: `impression_enabled=false` "
           "across 5 mid-tier configs after a catalog refresh. Loss ratio is 0.29 — safe to fix.")
    yield from _stream_text(txt)
    yield {"type": "final", "content": txt}
