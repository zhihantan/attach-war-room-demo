# 01 — Metric views + Genie space

Governed semantic layer (metric views) and the AI/BI Genie space built on top of it. **Genie is built on metric views first** so KPI definitions live in one place and Genie inherits them consistently.

## Files
| File | Purpose |
|---|---|
| `metric_views.sql` | DDL for `funnel_metrics`, `profitability_metrics` (+ `v_policy_claims` base view) |
| `run_sql.py` | Generic `{{S}}`-templated SQL runner (used for the DDL and ad-hoc validation) |
| `build_genie_space.py` | Builds the `serialized_space` (objects, instructions, example SQL, benchmarks, sample qs) |
| `genie_space_builder.py` | Helper that models `serialized_space` (vendored from the genie-rooms skill) |
| `ask_genie.py` | Ask the space a question (validation / EVAL harness) |

## Setup steps
```bash
# 1. Create the metric views
uv run --with databricks-sdk 01_metric_views_and_genie/run_sql.py 01_metric_views_and_genie/metric_views.sql

# 2. Build serialized_space and create the Genie space
uv run --with databricks-sdk 01_metric_views_and_genie/build_genie_space.py > /tmp/awr_serialized_space.json
jq -n --arg title "bolttech Attach War-Room — Conversion & Profitability" \
  --arg description "Diagnose embedded-checkout attach and conversion; watch the loss-ratio guardrail." \
  --arg parent_path "/Workspace/Users/<you>@databricks.com" --arg warehouse_id "" \
  --rawfile serialized_space /tmp/awr_serialized_space.json \
  '{title:$title,description:$description,parent_path:$parent_path,warehouse_id:$warehouse_id,serialized_space:$serialized_space}' \
  > /tmp/create_genie_space.json
databricks api post /api/2.0/genie/spaces --profile DEFAULT --json @/tmp/create_genie_space.json

# 3. Validate
uv run --with databricks-sdk 01_metric_views_and_genie/ask_genie.py "Why did attach rate drop for Velora Telecom mid-tier devices in Italy?" <space_id>
```

**Genie space:** created per-install by `install.py` (prints the id) → `https://YOUR-WORKSPACE.cloud.databricks.com/genie/rooms/<SPACE_ID>`

## `funnel_metrics` (grain: one checkout session; source `sessions` + joins to partners/markets/products)

**Dimensions:** session_date, partner_id, partner_name, partner_type, market_id, market_name, region, currency, product_id, product_name, product_line, device_tier, customer_segment, channel, deductible_tier_shown, stage_reached

| Measure | Definition (expr) | Synonyms |
|---|---|---|
| sessions | `COUNT(1)` | eligible sessions, traffic |
| offers_shown | `SUM(IF(offer_shown))` | impressions |
| quotes_started / quotes_completed | `SUM(IF(...))` | |
| policies_bound | `SUM(IF(bound))` | attaches, sales, binds |
| policies_activated | `SUM(IF(activated))` | |
| gross_written_premium_usd | `SUM(IF(bound, premium_usd))` | GWP, written premium |
| **attach_rate** | `policies_bound / sessions` | attach, take rate |
| **conversion_rate** | `policies_bound / offers_shown` | conversion of shown offers |
| offer_show_rate | `offers_shown / sessions` | impression rate |
| quote_start_rate / quote_complete_rate / complete_to_bind_rate / activation_rate | stage-to-stage ratios | bind rate, close rate |
| gwp_per_1k_sessions, avg_premium_usd, avg_device_value_usd | derived | |

> `attach_rate` vs `conversion_rate` is the diagnostic crux: when impressions break, attach falls but conversion-of-shown holds.

## `profitability_metrics` (grain: one policy; source `v_policy_claims` + joins)

**Dimensions:** bind_date, partner_id/name/type, market_id/name, region, currency, product_name, product_line, device_tier, status

| Measure | Definition | Synonyms |
|---|---|---|
| policy_count | `COUNT(1)` | policies |
| gross_written_premium_usd | `SUM(gwp_usd)` | GWP |
| incurred_losses_usd | `SUM(loss_usd)` | losses, claims cost |
| **loss_ratio** | `incurred_losses_usd / gross_written_premium_usd` | loss ratio |
| total_claims, policies_with_claim | counts | |
| **claims_frequency** | `policies_with_claim / policy_count` | frequency |
| avg_settlement_days | `AVG(...)` | cycle time |
| fraud_rate | `fraud_count / claim_count` | |

## Genie instructions (1 block, 6 rules)
USD default + round money; default last-30-days (data ends 2026-06-03); prefer metric views, sessions for funnel/abandonment text, offer_config for state, claims for detail; funnel order + attach-vs-conversion diagnosis rule; ask for partner+product line if attach asked without them; loss_ratio formula + 0.70 guardrail call-out.

## Example SQL (8, verified) & benchmarks (10, SQL ground truth)
See `build_genie_space.py`. Benchmark NL expected answers are in [`../EVAL.md`](../EVAL.md).
