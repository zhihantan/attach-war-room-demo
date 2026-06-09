#!/usr/bin/env python3
"""Build the Attach War-Room Genie space `serialized_space` JSON.

Emits the serialized_space to stdout. The shell wrapper (create_genie_space.sh)
wraps it with title/description/parent_path/warehouse_id and POSTs to
/api/2.0/genie/spaces.

Best practice followed: build on metric views first; curated tables only for
detail/state; minimal non-conflicting text instructions; verified example SQL
(the highest-leverage instruction type); benchmark questions with ground truth.

Run:
    uv run --with databricks-sdk 01_metric_views_and_genie/build_genie_space.py > /tmp/awr_serialized_space.json
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from genie_space_builder import GenieSpaceBuilder  # noqa: E402

S = os.environ.get("SCHEMA_FQN", "bolttech_workshop_demo.attach_war_room")
WID = os.environ.get("WAREHOUSE_ID", "")

b = GenieSpaceBuilder(
    title="Acme Attach War-Room — Conversion & Profitability",
    description=("Diagnose embedded-checkout attach and conversion across partners, markets, and products, "
                 "and watch the loss-ratio guardrail. Built on governed metric views (synthetic data)."),
    warehouse_id=WID,
)

# --- curated objects: metric views first, then detail/state tables ----------
b.add_metric_view(f"{S}.funnel_metrics")
b.add_metric_view(f"{S}.profitability_metrics")

ent = lambda c: {"column_name": c, "enable_format_assistance": True, "enable_entity_matching": True}
fmt = lambda c: {"column_name": c, "enable_format_assistance": True}

b.add_table(f"{S}.sessions", column_configs=[
    ent("device_tier"), ent("stage_reached"), ent("customer_segment"), ent("channel"),
    ent("partner_id"), ent("market_id"), fmt("abandon_reason_text")])
b.add_table(f"{S}.claims", column_configs=[ent("claim_type"), ent("severity"), ent("status")])
b.add_table(f"{S}.offer_config", column_configs=[ent("device_tier"), ent("placement"), fmt("impression_enabled")])
b.add_table(f"{S}.partners", column_configs=[ent("partner_name"), ent("partner_type")])
b.add_table(f"{S}.markets", column_configs=[ent("market_name"), ent("region")])
b.add_table(f"{S}.products", column_configs=[ent("product_name"), ent("product_line")])

# --- text instructions (one entry, concise non-conflicting rules) -----------
b.set_instructions([
    "All monetary values are in USD unless a single market or currency is specified. Use gross_written_premium_usd for premium and round monetary values to 2 decimals.",
    "If the user does not specify a time range, default to the last 30 days. The data ends on 2026-06-03; treat current_date() as today.",
    "Prefer the metric views funnel_metrics and profitability_metrics for any KPI (attach rate, conversion, GWP, loss ratio, claims frequency, settlement time). Use the sessions table only for funnel-stage or abandonment-reason detail, offer_config for offer-serving state, and claims for individual claim detail.",
    "Funnel order is offer_shown -> quote_started -> quote_completed -> bound -> activated. attach_rate = bound / sessions; conversion_rate = bound / offers_shown. When attach changes, compare offer_show_rate and conversion_rate to localize whether it is an impressions problem or a conversion problem.",
    "If a user asks about attach rate without naming both a partner and a product line, ask them to specify both before answering.",
    "loss_ratio = incurred_losses_usd / gross_written_premium_usd. Treat any loss ratio above 0.70 as breaching the offer-change guardrail and call it out explicitly.",
], replace=True)

# --- verified example SQL (highest-leverage instruction) --------------------
examples = [
    ("What is the attach rate by partner in the last 30 days?",
     f"SELECT partner_name, MEASURE(attach_rate) AS attach_rate\nFROM {S}.funnel_metrics\nWHERE session_date >= date_add(current_date(), -30)\nGROUP BY partner_name\nORDER BY attach_rate DESC"),
    ("Why did attach rate drop for Velora Telecom mid-tier devices in Italy?",
     f"SELECT CASE WHEN session_date >= date_add(current_date(), -45) THEN 'last_45d' ELSE 'prior' END AS period,\n       MEASURE(offer_show_rate) AS offer_show_rate,\n       MEASURE(conversion_rate) AS conversion_of_shown_offers,\n       MEASURE(attach_rate) AS attach_rate\nFROM {S}.funnel_metrics\nWHERE partner_name = 'Velora Telecom' AND device_tier = 'mid'\nGROUP BY period\nORDER BY period"),
    ("Which partners and device tiers have the worst loss ratio?",
     f"SELECT partner_name, device_tier, MEASURE(loss_ratio) AS loss_ratio\nFROM {S}.profitability_metrics\nGROUP BY partner_name, device_tier\nORDER BY loss_ratio DESC\nLIMIT 10"),
    ("Show gross written premium by market and currency in the last 30 days.",
     f"SELECT market_name, currency, MEASURE(gross_written_premium_usd) AS gwp_usd\nFROM {S}.funnel_metrics\nWHERE session_date >= date_add(current_date(), -30)\nGROUP BY market_name, currency\nORDER BY gwp_usd DESC"),
    ("What are the top abandonment reasons for Siam Mobile Care at the quote-completed stage in the last 30 days?",
     f"SELECT abandon_reason_text, COUNT(*) AS sessions\nFROM {S}.sessions\nWHERE partner_id = 'P02' AND stage_reached = 'quote_completed' AND session_date >= date_add(current_date(), -30)\nGROUP BY abandon_reason_text\nORDER BY sessions DESC"),
    ("Which offer configurations currently have impressions disabled?",
     f"SELECT partner_id, product_id, device_tier, market_id\nFROM {S}.offer_config\nWHERE impression_enabled = false"),
    ("What are the claims frequency, loss ratio, and average settlement time for budget devices in Kenya?",
     f"SELECT MEASURE(claims_frequency) AS claims_frequency, MEASURE(loss_ratio) AS loss_ratio, MEASURE(avg_settlement_days) AS avg_settlement_days\nFROM {S}.profitability_metrics\nWHERE device_tier = 'budget' AND market_name = 'Kenya'"),
    ("Which partners have the lowest quote-to-bind rate in the last 30 days?",
     f"SELECT partner_name, MEASURE(complete_to_bind_rate) AS complete_to_bind_rate\nFROM {S}.funnel_metrics\nWHERE session_date >= date_add(current_date(), -30)\nGROUP BY partner_name\nORDER BY complete_to_bind_rate ASC"),
]
for q, sql in examples:
    b.add_example_sql(title=q, sql=sql)

# --- benchmark questions with ground-truth SQL (NL expected answers live in EVAL.md) ---
benchmarks = [
    ("For Velora Telecom mid-tier devices, how have offer-shown rate, conversion rate, and attach rate changed in the last 45 days versus before?",
     f"SELECT CASE WHEN session_date >= date_add(current_date(), -45) THEN 'last_45d' ELSE 'prior' END AS period,\n       MEASURE(offer_show_rate) AS offer_show_rate,\n       MEASURE(conversion_rate) AS conversion_of_shown_offers,\n       MEASURE(attach_rate) AS attach_rate\nFROM {S}.funnel_metrics\nWHERE partner_name = 'Velora Telecom' AND device_tier = 'mid'\nGROUP BY period ORDER BY period"),
    ("Which partner had the largest quote-to-bind drop in the last 30 days, and by how much?",
     f"SELECT CASE WHEN session_date >= date_add(current_date(), -30) THEN 'last_30d' ELSE 'prior' END AS period,\n       MEASURE(complete_to_bind_rate) AS complete_to_bind_rate\nFROM {S}.funnel_metrics\nWHERE partner_id = 'P02'\nGROUP BY period ORDER BY period"),
    ("Where is policy activation / first payment leaking in the last 25 days?",
     f"SELECT CASE WHEN session_date >= date_add(current_date(), -25) THEN 'last_25d' ELSE 'prior' END AS period,\n       MEASURE(activation_rate) AS activation_rate\nFROM {S}.funnel_metrics\nWHERE partner_id = 'P08'\nGROUP BY period ORDER BY period"),
    ("What is the blended loss ratio across the book?",
     f"SELECT MEASURE(loss_ratio) AS loss_ratio, MEASURE(gross_written_premium_usd) AS gwp_usd, MEASURE(incurred_losses_usd) AS losses_usd\nFROM {S}.profitability_metrics"),
    ("Which partner, market and device tier has the worst loss ratio?",
     f"SELECT partner_name, device_tier, MEASURE(loss_ratio) AS loss_ratio\nFROM {S}.profitability_metrics\nGROUP BY partner_name, device_tier\nORDER BY loss_ratio DESC\nLIMIT 10"),
    ("What are the top abandonment reasons for Siam Mobile Care at the quote-completed stage in the last 30 days?",
     f"SELECT abandon_reason_text, COUNT(*) AS sessions\nFROM {S}.sessions\nWHERE partner_id = 'P02' AND stage_reached = 'quote_completed' AND session_date >= date_add(current_date(), -30)\nGROUP BY abandon_reason_text ORDER BY sessions DESC"),
    ("Which offer configurations currently have impressions disabled?",
     f"SELECT partner_id, product_id, device_tier, market_id FROM {S}.offer_config WHERE impression_enabled = false"),
    ("What is gross written premium by market in the last 30 days?",
     f"SELECT market_name, currency, MEASURE(gross_written_premium_usd) AS gwp_usd\nFROM {S}.funnel_metrics\nWHERE session_date >= date_add(current_date(), -30)\nGROUP BY market_name, currency ORDER BY gwp_usd DESC"),
    ("What are the claims frequency and average settlement time for budget devices in Kenya?",
     f"SELECT MEASURE(claims_frequency) AS claims_frequency, MEASURE(loss_ratio) AS loss_ratio, MEASURE(avg_settlement_days) AS avg_settlement_days\nFROM {S}.profitability_metrics\nWHERE device_tier = 'budget' AND market_name = 'Kenya'"),
    ("Rank partners by attach rate over the last 30 days.",
     f"SELECT partner_name, MEASURE(attach_rate) AS attach_rate\nFROM {S}.funnel_metrics\nWHERE session_date >= date_add(current_date(), -30)\nGROUP BY partner_name ORDER BY attach_rate DESC"),
]
for q, sql in benchmarks:
    b.add_benchmark(name=q, expected_response=sql, response_format="SQL")

# --- sample starter questions ----------------------------------------------
for q in [
    "Why did attach rate fall for Velora Telecom in Italy?",
    "Which partners and device tiers have a loss ratio above the 0.70 guardrail?",
    "Where is checkout conversion dropping in the last 30 days?",
    "Show gross written premium by market and partner this month.",
]:
    b._ensure_list(("config", "sample_questions")).append({"id": uuid.uuid4().hex, "question": [q]})

# Export invariants: column_configs sorted by column_name; ID-lists sorted by id.
for _t in b._space.get("data_sources", {}).get("tables", []):
    if isinstance(_t.get("column_configs"), list):
        _t["column_configs"].sort(key=lambda c: c.get("column_name", ""))


def _sort_list_by_id(*path):
    cur = b._space
    for k in path[:-1]:
        if not isinstance(cur, dict):
            return
        cur = cur.get(k)
    if isinstance(cur, dict) and isinstance(cur.get(path[-1]), list):
        cur[path[-1]].sort(key=lambda x: x.get("id", "") if isinstance(x, dict) else "")


for _p in [("config", "sample_questions"), ("instructions", "text_instructions"),
           ("instructions", "example_question_sqls"), ("benchmarks", "questions")]:
    _sort_list_by_id(*_p)

b.validate()
print(b.to_json())
