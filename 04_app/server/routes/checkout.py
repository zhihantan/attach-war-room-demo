"""Simulated partner checkout — reads the LIVE offer_config from Lakebase, exactly
as a real partner checkout would, so the UI shows the offer appear the instant the
agent ships the fix."""
from fastapi import APIRouter

import dbx
import fixtures
import tools

router = APIRouter()


@router.get("/checkout")
def checkout(partner: str, device_tier: str = "mid"):
    if dbx.OFFLINE:
        return fixtures.checkout(partner, device_tier)
    cfg = tools.get_offer_config(partner, device_tier)
    configs = cfg.get("configs", [])
    shown = any(c.get("impression_enabled") for c in configs)
    sample = configs[0] if configs else {}
    return {
        "partner": cfg.get("partner"),
        "device_tier": device_tier,
        "offer_shown": shown,
        "placement": sample.get("placement"),
        "deductible_tier_shown": sample.get("deductible_tier_shown"),
        "products_enabled": sum(1 for c in configs if c.get("impression_enabled")),
        "products_total": len(configs),
        "configs": configs,
    }
