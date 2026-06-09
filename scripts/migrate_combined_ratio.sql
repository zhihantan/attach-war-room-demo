-- ============================================================================
-- Migration (Tier 2.8) — combined-ratio depth WITHOUT regenerating data.
-- Adds commission / ceded-premium / expense columns to policies and backfills
-- them from the existing book, so loss ratio becomes a real COMBINED ratio.
-- Run with run_sql.py (uses the {{S}} placeholder + per-statement separators),
-- then re-apply 01_metric_views_and_genie/metric_views.sql to refresh the views.
--
--   uv run --with databricks-sdk 01_metric_views_and_genie/run_sql.py scripts/migrate_combined_ratio.sql
--   uv run --with databricks-sdk 01_metric_views_and_genie/run_sql.py 01_metric_views_and_genie/metric_views.sql
-- ALL DATA IS SYNTHETIC.
-- ============================================================================

-- @@
ALTER TABLE {{S}}.policies ADD COLUMNS (
  commission_usd    DOUBLE COMMENT 'Acquisition cost / commission paid to the distribution partner in USD (synonyms: acquisition cost, partner commission)',
  ceded_premium_usd DOUBLE COMMENT 'Premium ceded to the carrier / reinsurer in USD (synonyms: ceded premium, reinsurance premium)',
  expense_usd       DOUBLE COMMENT 'Internal admin / claims-handling expense allocated to the policy in USD (synonyms: operating expense)'
)

-- @@
MERGE INTO {{S}}.policies t
USING (
  SELECT p.policy_id,
    round(p.premium_usd * (CASE pt.partner_type WHEN 'retail' THEN 0.32 WHEN 'telco' THEN 0.30
          WHEN 'ecommerce' THEN 0.30 WHEN 'OEM' THEN 0.28 WHEN 'bank' THEN 0.22 ELSE 0.27 END), 2) AS commission_usd,
    round(p.premium_usd * 0.40, 2) AS ceded_premium_usd,
    round(p.premium_usd * 0.08, 2) AS expense_usd
  FROM {{S}}.policies p
  JOIN {{S}}.partners pt ON p.partner_id = pt.partner_id
) s
ON t.policy_id = s.policy_id
WHEN MATCHED THEN UPDATE SET
  t.commission_usd = s.commission_usd,
  t.ceded_premium_usd = s.ceded_premium_usd,
  t.expense_usd = s.expense_usd
