"""Lakebase-backed app/agent state: scenarios, audit, recovered GWP, approval."""
from fastapi import APIRouter
from pydantic import BaseModel

import dbx
import fixtures
import telemetry
import tools

router = APIRouter()


def _rows(sql):
    with dbx.cursor(commit=False) as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@router.get("/scenarios")
def scenarios():
    if dbx.OFFLINE:
        return fixtures.scenarios()
    return _rows("""SELECT scenario_id, created_at, partner_id, device_tier, title,
                    projected_attach_delta, projected_loss_ratio, projected_gwp_delta_usd,
                    within_guardrail, status FROM scenarios ORDER BY scenario_id DESC LIMIT 25""")


@router.get("/audit")
def audit():
    if dbx.OFFLINE:
        return fixtures.audit()
    return _rows("""SELECT audit_id, change_ts, partner_id, device_tier, field_changed,
                    before_value, after_value, rationale, projected_attach_delta, projected_loss_ratio,
                    scenario_id FROM offer_config_audit ORDER BY audit_id DESC LIMIT 25""")


@router.get("/recovered_gwp")
def recovered_gwp():
    if dbx.OFFLINE:
        return fixtures.recovered_gwp()
    with dbx.cursor(commit=False) as cur:
        cur.execute("SELECT COALESCE(SUM(projected_gwp_delta_usd),0), COUNT(*) FROM scenarios WHERE status='shipped'")
        total, n = cur.fetchone()
    return {"recovered_gwp_per_month_usd": float(total or 0), "shipped_count": n}


class Approve(BaseModel):
    scenario_id: int
    approved_by: str = "demo_user"
    conversation_id: str | None = None


@router.post("/approve")
def approve(req: Approve):
    """Approve + ship a scenario — the multi-table ACID write to the live offer_config.
    Also records the ship in the conversation's Lakebase memory so the agent stays
    consistent with out-of-band (button) ships."""
    if dbx.OFFLINE:
        return fixtures.approve(req.scenario_id, req.approved_by)
    result = tools.ship_offer_change(req.scenario_id, req.approved_by)
    telemetry.event("ship", result, req.conversation_id)
    if req.conversation_id and result.get("shipped"):
        try:
            with dbx.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_messages (conversation_id, role, content) VALUES (%s, 'assistant', %s)",
                    (req.conversation_id,
                     f"Shipped scenario {req.scenario_id} (approved by {req.approved_by}): "
                     f"{result.get('configs_updated')} offer configs updated "
                     f"({result.get('field_changed')} {result.get('before')}→{result.get('after')}); "
                     f"recovered GWP +${result.get('projected_gwp_delta_usd')}/month."))
        except Exception:
            pass
    return result
