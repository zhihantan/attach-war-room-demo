-- ============================================================================
-- Attach War-Room — metric views (Unity Catalog, governed KPI semantic layer)
-- Genie is built on these first (best practice): business logic lives here once.
-- Target: {{S}}  | Statements separated by a line containing only:  -- @@
-- ============================================================================

-- @@
-- ---------------------------------------------------------------------------
-- funnel_metrics — the embedded-checkout funnel + attach + GWP KPIs.
-- conversion_rate (bound/offers_shown) vs attach_rate (bound/sessions) is the
-- diagnostic crux: when impressions break, attach falls but conversion holds.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{S}}.funnel_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: "Embedded-checkout funnel and attach KPIs for the bolttech exchange (synthetic). Grain: one checkout session."
source: {{S}}.sessions
joins:
  - name: partner
    source: {{S}}.partners
    on: source.partner_id = partner.partner_id
    rely:
      at_most_one_match: true
  - name: market
    source: {{S}}.markets
    on: source.market_id = market.market_id
    rely:
      at_most_one_match: true
  - name: product
    source: {{S}}.products
    on: source.product_id = product.product_id
    rely:
      at_most_one_match: true
dimensions:
  - name: session_date
    expr: session_date
    synonyms: [date, day]
  - name: partner_id
    expr: partner_id
  - name: partner_name
    expr: partner.partner_name
    synonyms: [partner, distributor, channel partner]
  - name: partner_type
    expr: partner.partner_type
  - name: market_id
    expr: market_id
  - name: market_name
    expr: market.market_name
    synonyms: [country, market]
  - name: region
    expr: market.region
  - name: currency
    expr: currency
    synonyms: [ccy]
  - name: product_id
    expr: product_id
  - name: product_name
    expr: product.product_name
    synonyms: [product, cover, coverage]
  - name: product_line
    expr: product_line
  - name: device_tier
    expr: device_tier
    synonyms: [handset tier, phone tier, device segment]
  - name: customer_segment
    expr: customer_segment
  - name: channel
    expr: channel
  - name: deductible_tier_shown
    expr: deductible_tier_shown
  - name: stage_reached
    expr: stage_reached
measures:
  - name: sessions
    expr: COUNT(1)
    synonyms: [eligible sessions, traffic, visits]
  - name: offers_shown
    expr: SUM(CASE WHEN offer_shown THEN 1 ELSE 0 END)
    synonyms: [impressions, offers displayed]
  - name: quotes_started
    expr: SUM(CASE WHEN quote_started THEN 1 ELSE 0 END)
  - name: quotes_completed
    expr: SUM(CASE WHEN quote_completed THEN 1 ELSE 0 END)
  - name: policies_bound
    expr: SUM(CASE WHEN bound THEN 1 ELSE 0 END)
    synonyms: [attaches, sales, policies sold, binds]
  - name: policies_activated
    expr: SUM(CASE WHEN activated THEN 1 ELSE 0 END)
  - name: gross_written_premium_usd
    expr: SUM(CASE WHEN bound THEN premium_usd ELSE 0 END)
    synonyms: [GWP, written premium, premium]
  - name: attach_rate
    expr: MEASURE(policies_bound) / NULLIF(MEASURE(sessions), 0)
    synonyms: [attach, attach rate, take rate, attachment rate]
  - name: conversion_rate
    expr: MEASURE(policies_bound) / NULLIF(MEASURE(offers_shown), 0)
    synonyms: [conversion, conversion rate, conversion of shown offers]
  - name: offer_show_rate
    expr: MEASURE(offers_shown) / NULLIF(MEASURE(sessions), 0)
    synonyms: [impression rate, offer-shown rate]
  - name: quote_start_rate
    expr: MEASURE(quotes_started) / NULLIF(MEASURE(offers_shown), 0)
  - name: quote_complete_rate
    expr: MEASURE(quotes_completed) / NULLIF(MEASURE(quotes_started), 0)
  - name: complete_to_bind_rate
    expr: MEASURE(policies_bound) / NULLIF(MEASURE(quotes_completed), 0)
    synonyms: [bind rate, close rate]
  - name: activation_rate
    expr: MEASURE(policies_activated) / NULLIF(MEASURE(policies_bound), 0)
    synonyms: [activation rate, first-payment success rate]
  - name: gwp_per_1k_sessions
    expr: 1000 * MEASURE(gross_written_premium_usd) / NULLIF(MEASURE(sessions), 0)
    synonyms: [GWP per 1000 sessions]
  - name: avg_premium_usd
    expr: MEASURE(gross_written_premium_usd) / NULLIF(MEASURE(policies_bound), 0)
    synonyms: [average premium]
  - name: avg_device_value_usd
    expr: AVG(device_value_usd)
$$

-- @@
-- ---------------------------------------------------------------------------
-- v_policy_claims — curated policy grain with aggregated claim losses/counts.
-- Base for the profitability metric view (keeps loss ratio at policy grain).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{S}}.v_policy_claims
COMMENT 'Policy grain joined to aggregated claim losses/counts; base for the profitability metric view (synthetic).'
AS
SELECT p.policy_id, p.session_id, p.partner_id, p.market_id, p.currency, p.product_id, p.product_line,
       p.device_tier, p.device_value_usd, p.bind_date, p.gwp_usd, p.premium_usd, p.status,
       coalesce(p.commission_usd, 0)    AS commission_usd,
       coalesce(p.ceded_premium_usd, 0) AS ceded_premium_usd,
       coalesce(p.expense_usd, 0)       AS expense_usd,
       coalesce(c.loss_usd, 0)   AS loss_usd,
       coalesce(c.claim_count, 0) AS claim_count,
       c.avg_settlement_days,
       coalesce(c.fraud_count, 0) AS fraud_count
FROM {{S}}.policies p
LEFT JOIN (
  SELECT policy_id,
         SUM(CASE WHEN status <> 'denied' THEN claim_amount_usd ELSE 0 END) AS loss_usd,
         COUNT(1)                          AS claim_count,
         AVG(settlement_days)              AS avg_settlement_days,
         SUM(CASE WHEN fraud_flag THEN 1 ELSE 0 END) AS fraud_count
  FROM {{S}}.claims
  GROUP BY policy_id
) c ON p.policy_id = c.policy_id

-- @@
-- ---------------------------------------------------------------------------
-- profitability_metrics — GWP, incurred losses, loss ratio, claims frequency,
-- settlement time. The loss-ratio guardrail (0.70) is enforced by the agent.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW {{S}}.profitability_metrics
WITH METRICS
LANGUAGE YAML
AS $$
version: 1.1
comment: "Policy profitability KPIs: GWP, incurred losses, loss ratio, claims frequency and settlement time (synthetic). Grain: one policy."
source: {{S}}.v_policy_claims
joins:
  - name: partner
    source: {{S}}.partners
    on: source.partner_id = partner.partner_id
    rely:
      at_most_one_match: true
  - name: market
    source: {{S}}.markets
    on: source.market_id = market.market_id
    rely:
      at_most_one_match: true
  - name: product
    source: {{S}}.products
    on: source.product_id = product.product_id
    rely:
      at_most_one_match: true
dimensions:
  - name: bind_date
    expr: bind_date
    synonyms: [date, day]
  - name: partner_id
    expr: partner_id
  - name: partner_name
    expr: partner.partner_name
    synonyms: [partner, distributor]
  - name: partner_type
    expr: partner.partner_type
  - name: market_id
    expr: market_id
  - name: market_name
    expr: market.market_name
    synonyms: [country, market]
  - name: region
    expr: market.region
  - name: currency
    expr: currency
  - name: product_name
    expr: product.product_name
    synonyms: [product, cover]
  - name: product_line
    expr: product_line
  - name: device_tier
    expr: device_tier
    synonyms: [handset tier]
  - name: status
    expr: status
measures:
  - name: policy_count
    expr: COUNT(1)
    synonyms: [policies, in-force policies]
  - name: gross_written_premium_usd
    expr: SUM(gwp_usd)
    synonyms: [GWP, written premium, premium]
  - name: incurred_losses_usd
    expr: SUM(loss_usd)
    synonyms: [losses, claims cost, incurred losses, payouts]
  - name: loss_ratio
    expr: MEASURE(incurred_losses_usd) / NULLIF(MEASURE(gross_written_premium_usd), 0)
    synonyms: [loss ratio]
  - name: total_claims
    expr: SUM(claim_count)
    synonyms: [number of claims, claim count]
  - name: policies_with_claim
    expr: SUM(CASE WHEN claim_count > 0 THEN 1 ELSE 0 END)
  - name: claims_frequency
    expr: MEASURE(policies_with_claim) / NULLIF(MEASURE(policy_count), 0)
    synonyms: [claim frequency, frequency]
  - name: avg_settlement_days
    expr: AVG(avg_settlement_days)
    synonyms: [settlement time, cycle time, days to settle]
  - name: fraud_rate
    expr: SUM(fraud_count) / NULLIF(SUM(claim_count), 0)
    synonyms: [suspected fraud rate]
  - name: acquisition_cost_usd
    expr: SUM(commission_usd)
    synonyms: [commission, partner commission, acquisition cost]
  - name: ceded_premium_usd
    expr: SUM(ceded_premium_usd)
    synonyms: [ceded premium, reinsurance premium]
  - name: operating_expense_usd
    expr: SUM(expense_usd)
    synonyms: [operating expense, admin expense]
  - name: expense_ratio
    expr: (SUM(commission_usd) + SUM(expense_usd)) / NULLIF(MEASURE(gross_written_premium_usd), 0)
    synonyms: [expense ratio]
  - name: combined_ratio
    expr: MEASURE(loss_ratio) + MEASURE(expense_ratio)
    synonyms: [combined ratio, COR]
  - name: ceding_ratio
    expr: SUM(ceded_premium_usd) / NULLIF(MEASURE(gross_written_premium_usd), 0)
    synonyms: [ceding ratio, cession rate]
$$
