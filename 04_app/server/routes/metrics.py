"""Genie / metric-view backed dashboard endpoints."""
from fastapi import APIRouter

import dbx
import fixtures
import tools

router = APIRouter()


@router.get("/partners")
def partners():
    if dbx.OFFLINE:
        return fixtures.partners()
    return dbx.warehouse_query(
        f"SELECT partner_id, partner_name, partner_type, market_id FROM {dbx.SCHEMA}.partners ORDER BY partner_name")


@router.get("/overview")
def overview(days: int = 30):
    return tools.partner_funnel_overview(days)


@router.get("/funnel")
def funnel(partner: str, device_tier: str = None, recent_days: int = 45):
    if dbx.OFFLINE:
        return fixtures.funnel(partner, device_tier or "mid", recent_days)
    return tools.funnel_diagnosis(partner, device_tier, recent_days)


@router.get("/lossratio")
def lossratio(partner: str = None, device_tier: str = None, market_name: str = None):
    if dbx.OFFLINE:
        return fixtures.loss_ratio(partner, device_tier, market_name)
    return tools.loss_ratio_for(partner, device_tier, market_name)


@router.get("/abandonment")
def abandonment(partner: str, stage: str = "quote_completed", days: int = 30):
    return tools.abandonment_reasons(partner, stage, days)


@router.get("/genie")
def genie(q: str):
    return tools.query_genie(q)
