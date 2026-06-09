"""Demo operations: profile, health/pre-warm, one-click reset, proactive scan,
rollback, and telemetry events. Backs the Tier 0/2/3 reliability + ops features."""
from fastapi import APIRouter
from pydantic import BaseModel

import dbx
import fixtures
import profile as P
import telemetry
import tools

router = APIRouter()


@router.get("/profile")
def profile():
    """The re-skinnable demo profile the frontend renders (branding, branches, walkthrough, currencies)."""
    return P.get_profile()


@router.get("/health")
def health():
    """Pre-warm + probe warehouse, Lakebase and Genie — call ~60s before presenting."""
    if dbx.OFFLINE:
        return fixtures.health()
    return dbx.health()


@router.get("/scan")
def scan(days: int = 45, limit: int = 5):
    """Proactive book scan — the agent's discovery view (activates alert_thresholds)."""
    if dbx.OFFLINE:
        return fixtures.scan()
    return tools.scan_for_anomalies(days, limit)


class Rollback(BaseModel):
    scenario_id: int | None = None
    audit_id: int | None = None
    rolled_back_by: str = "demo_user"
    conversation_id: str | None = None


@router.post("/rollback")
def rollback(req: Rollback):
    if dbx.OFFLINE:
        fixtures.reset()
        return {"rolled_back": True, "offline": True}
    res = tools.rollback_offer_change(req.scenario_id, req.audit_id, req.rolled_back_by)
    telemetry.event("rollback", res, req.conversation_id)
    return res


class Event(BaseModel):
    name: str
    detail: dict | None = None
    conversation_id: str | None = None


@router.post("/event")
def event(req: Event):
    return telemetry.event(req.name, req.detail, req.conversation_id)


@router.post("/reset")
def reset():
    """One-click demo reset (Tier 0.1): restore the broken-Velora Telecom starting state and
    clear all run state, so back-to-back demos never break and an accidental approve
    is fully recoverable. Restores the live offer_config the checkout reads."""
    if dbx.OFFLINE:
        return fixtures.reset()
    with dbx.cursor() as cur:
        # Velora Telecom (P01) mid-tier impressions OFF = the planted anomaly the demo opens on
        cur.execute("""UPDATE offer_config SET impression_enabled=false, version=1, updated_by='reset'
                       WHERE partner_id='P01' AND device_tier='mid'""")
        velora = cur.rowcount
        # everything else ON (undo any shipped restore on other partners)
        cur.execute("""UPDATE offer_config SET impression_enabled=true, version=1, updated_by='reset'
                       WHERE NOT (partner_id='P01' AND device_tier='mid') AND impression_enabled=false""")
        # Siam Mobile Care (P02) deductible back to the broken 'high'; everyone else 'std'
        cur.execute("UPDATE offer_config SET deductible_tier_shown='high', version=1 WHERE partner_id='P02'")
        cur.execute("UPDATE offer_config SET deductible_tier_shown='std', version=1 WHERE partner_id<>'P02' AND deductible_tier_shown<>'std'")
        # clear run state
        cur.execute("TRUNCATE TABLE scenarios")
        cur.execute("TRUNCATE TABLE offer_config_audit")
        cur.execute("DELETE FROM chat_messages")
    telemetry.event("reset", {"velora_configs_reset": velora})
    return {"reset": True, "velora_configs_reset": velora}
