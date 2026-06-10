#!/usr/bin/env python3
"""Demo profile — the single re-skin layer for Attach War-Room (Tier 3.1).

Everything account-specific (persona, partner display names, demo branches,
currencies, walkthrough copy, theme) lives HERE, not scattered across the agent
prompt, the frontend, and the Genie builder. One profile turns a multi-week
bolttech build into a one-day re-skin for the next embedded-insurance account.

Resolution order (first hit wins):
  1. $DEMO_PROFILE_PATH                       (explicit file)
  2. ./demo_profile.json  (next to this module — vendored into the deployed App)
  3. ../config/demo_profile.json  ·  ../../config/demo_profile.json   (repo dev)
  4. the embedded DEFAULT below  (so the deployed App always works with no file)

The default profile's operator is bolttech (the real customer) with fictional partners. To
re-skin to a NEW account: copy config/demo_profile.json -> config/demo_profile.<account>.json,
edit names/markets/persona, set DEMO_PROFILE_PATH (and regenerate data with the
matching partner names on the next rebuild — see docs/BRING_YOUR_OWN_DATA.md).
"""
import json
import os

# ---------------------------------------------------------------------------
# Embedded DEFAULT — the live bolttech profile. Keep in sync with
# config/demo_profile.json (that file overrides this when present).
# ---------------------------------------------------------------------------
DEFAULT = {
    "account": {
        "name": "bolttech",
        "product": "Attach War-Room",
        "tagline": "Diagnose why embedded-insurance attach dropped — then ship the fix to the live checkout in one approval.",
        "synthetic_notice": "All data is synthetic. No real PII, carriers, or partners.",
    },
    "persona": "distribution-partner / conversion-growth lead",
    "guardrail_loss_ratio": 0.70,
    "theme": {"accent": "#ff5a36", "accent2": "#38bdf8", "prior": "#3c4a68"},
    "genie_url": "",
    # market_name -> local-currency rendering (usd_rate = USD per 1 local unit; ~anchored to the synthetic fx_rates)
    "currencies": {
        "Italy": {"code": "EUR", "symbol": "€", "usd_rate": 1.08},
        "Thailand": {"code": "THB", "symbol": "฿", "usd_rate": 0.029},
        "Singapore": {"code": "SGD", "symbol": "S$", "usd_rate": 0.74},
        "United Kingdom": {"code": "GBP", "symbol": "£", "usd_rate": 1.27},
        "United States": {"code": "USD", "symbol": "$", "usd_rate": 1.0},
        "Kenya": {"code": "KES", "symbol": "KSh", "usd_rate": 0.0078},
    },
    # The first-class demo branches (Tier 1.2). Each is a one-click hero path.
    "branches": [
        {"id": "impressions", "label": "Velora Telecom · impressions broke", "partner": "Velora Telecom", "tier": "mid",
         "prompt": "Why did attach rate drop for Velora Telecom mid-tier in Italy, and what should we do about it?",
         "kind": "diagnose", "blurb": "Offer-shown collapsed — an impressions bug, not pricing. The classic fix."},
        {"id": "bind", "label": "Siam Mobile TH · bind-stage drop", "partner": "Siam Mobile Care", "tier": "mid",
         "prompt": "Why did quote-to-bind conversion fall for Siam Mobile Care over the last 30 days?",
         "kind": "diagnose", "blurb": "A different anomaly: price shock at bind after a deductible hike."},
        {"id": "activation", "label": "Rift Valley Bank · activation leak", "partner": "Rift Valley Bank", "tier": "mid",
         "prompt": "Where is policy activation leaking for Rift Valley Bank in Kenya, and why?",
         "kind": "diagnose", "blurb": "First-payment failures post-bind after a payment-provider migration."},
        {"id": "guardrail", "label": "Savanna Mobile KE · guardrail blocks it", "partner": "Savanna Mobile", "tier": "budget",
         "prompt": "Shadow-test lowering the deductible for Savanna Mobile budget devices in Kenya and propose the fix.",
         "kind": "guardrail", "blurb": "The agent REFUSES to ship — it would push loss ratio over the 0.70 guardrail."},
    ],
    "followups": [
        "Shadow-test restoring the impressions and propose the fix.",
        "Approve and ship it to the live checkout.",
        "Draft a note to the partner about the fix.",
        "Scan the whole book for the biggest attach leak by recovered GWP.",
    ],
    # Welcome-modal carousel (the product walkthrough).
    "walkthrough": [
        {"icon": "\U0001f6e1️", "title": "Welcome to the Attach War-Room",
         "body": "bolttech's model lives or dies on attach rate inside a partner's checkout. This copilot turns “attach fell” into a shipped, governed fix — in one sitting.",
         "bullets": []},
        {"icon": "\U0001f504", "title": "One closed loop",
         "body": "The agent runs a stateful loop you can watch live:",
         "bullets": ["Scan — optionally sweep every partner and rank leaks by recovered GWP",
                     "Diagnose — localize the exact funnel stage that broke",
                     "Shadow-test — project the fix against recent traffic",
                     "Guardrail — block anything that buys attach with underpriced cover",
                     "Approve → Ship — write the live offer config the checkout reads",
                     "Verify — recovered GWP ticks up, the checkout flips green"]},
        {"icon": "\U0001f9f1", "title": "Four Databricks ingredients, all load-bearing",
         "body": "",
         "bullets": ["Agent — a tool-calling loop on Foundation Model APIs",
                     "AI/BI Genie — governed metric views as the single source of truth",
                     "Lakebase — Postgres serving the offer config + multi-table ACID writes",
                     "Foundation Model APIs — reasoning + free-text abandonment classification"]},
        {"icon": "▶️", "title": "Try a scenario",
         "body": "Pick a demo branch below the chat — three are real anomalies hiding in the book, and the fourth is one the guardrail will refuse to ship (the most important beat for a risk-minded audience). Or type “scan the book” to have the agent find the biggest leak itself. New here? The “About” tab up top has the full architecture and a plain-English glossary.",
         "bullets": []},
    ],
    # The annualized / portfolio extrapolation for the ROI close (Tier 1.3).
    "roi": {"partners_in_book": 12, "anomalies_planted": 3,
            "note": "One recovered anomaly, annualized. Across the book, recurring attach leaks compound."},
    # About page content (re-skinnable description of purpose + architecture).
    "about": {
        "purpose": [
            "When you buy a phone online, the store often asks at checkout whether you'd like to add protection — screen repair, theft, accidental damage. bolttech is the company that powers those insurance offers inside other companies' checkouts — telcos, banks and electronics retailers around the world. Its whole business hinges on one number: the share of shoppers who say “yes” to that add-on. That number is called the attach rate.",
            "When the attach rate quietly slips in one partner's checkout, the lost revenue adds up fast — but the cause is scattered across dozens of dashboards, and fixing it normally takes days and several teams. The Attach War-Room does it in one sitting: it pinpoints exactly where shoppers are dropping off, tests a fix against real recent traffic before anything goes live, checks the fix won't lose money on future claims, and — with one human approval — pushes the change to the live checkout so the offer works again right away. The recovered revenue then ticks up on screen in real time, using the company's own trusted numbers.",
        ],
        "audience": "Built for the teams who own a partner's checkout performance and revenue — and shown to the commercial and data leaders who care about the result.",
        "glossary": [
            {"term": "Attach rate", "def": "The share of checkout shoppers who add the insurance offer. Higher means more policies sold to the same traffic — bolttech's single biggest growth lever."},
            {"term": "The funnel", "def": "The steps a shopper goes through: see the offer → start a quote → finish it → buy → activate. A drop at any one step pulls the attach rate down, and each has a different fix."},
            {"term": "GWP", "def": "Gross Written Premium — the total premium from policies sold. It's the revenue the “Recovered GWP” tile is counting back up."},
            {"term": "Loss ratio", "def": "Claims paid out ÷ premium taken in. Much above ~70% means a policy is losing money, so the guardrail blocks any “fix” that would push past it."},
        ],
        "loop": [
            {"step": "Diagnose", "detail": "Localize the broken funnel stage — recent vs prior, by partner/tier/market."},
            {"step": "Shadow-test", "detail": "Deterministically project the fix on recent traffic (no LLM-guessed numbers)."},
            {"step": "Guardrail", "detail": "Block anything that pushes projected loss ratio over the 0.70 ceiling."},
            {"step": "Approve & ship", "detail": "One approval → a multi-table ACID write to the live offer config."},
            {"step": "Verify", "detail": "The checkout flips, recovered GWP ticks up, reconciled to Genie."},
        ],
        "ingredients": [
            {"name": "Agent", "tech": "tool-calling loop on Foundation Model APIs",
             "role": "14 tools; stateful diagnose → scan → shadow-test → approve → ship → rollback; streamed reasoning; Lakebase chat memory",
             "why": "Turns “attach fell” into a shipped, governed fix in one sitting"},
            {"name": "AI/BI Genie", "tech": "on Unity Catalog metric views",
             "role": "funnel_metrics + profitability_metrics — diagnostic engine, governed sim baseline, and post-ship reconciliation",
             "why": "One source of truth for attach / GWP / loss-ratio / combined-ratio across partners, markets and currencies"},
            {"name": "Lakebase", "tech": "Postgres (OLTP) serving + state",
             "role": "serves the offer_config the checkout reads; the approved change is a multi-table ACID write; also holds scenarios, audit, thresholds, chat memory",
             "why": "The realistic low-latency serving layer a partner checkout reads — a warehouse cannot"},
            {"name": "Foundation Model APIs", "tech": "claude-sonnet-4-6 + claude-haiku-4-5",
             "role": "agent reasoning / orchestration + free-text abandonment-cause classification",
             "why": "LLM intelligence doing real decision work, not just narration"},
        ],
        "data": "9 governed Delta tables (fully synthetic, deterministic): ~300k checkout sessions, ~60k policies, ~7k claims across 12 partners, 6 markets and 6 currencies — with three funnel anomalies planted at distinct stages and a loss-ratio trap tier the guardrail blocks.",
        "stack": "A single Databricks App: a React + Vite SPA talking only to a FastAPI backend over /api (SSE for chat). All Databricks credentials stay server-side.",
    },
}

_PROFILE = None


def _candidate_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    env = os.environ.get("DEMO_PROFILE_PATH")
    return [p for p in (
        env,
        os.path.join(here, "demo_profile.json"),
        os.path.join(here, "..", "config", "demo_profile.json"),
        os.path.join(here, "..", "..", "config", "demo_profile.json"),
    ) if p]


def get_profile():
    """Return the active demo profile (file override merged over DEFAULT, cached)."""
    global _PROFILE
    if _PROFILE is not None:
        return _PROFILE
    prof = dict(DEFAULT)
    for path in _candidate_paths():
        try:
            if path and os.path.isfile(path):
                with open(path) as f:
                    override = json.load(f)
                prof = {**DEFAULT, **override}
                break
        except Exception:
            continue  # a bad profile file must never break the app
    _PROFILE = prof
    return prof


def currency_for_market(market_name):
    """Local-currency rendering spec for a market (Tier 3.4 i18n)."""
    return get_profile().get("currencies", {}).get(
        market_name, {"code": "USD", "symbol": "$", "usd_rate": 1.0})


if __name__ == "__main__":
    print(json.dumps(get_profile(), indent=2, ensure_ascii=False))
