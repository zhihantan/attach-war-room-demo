-- ============================================================================
-- Attach War-Room — synthetic data generation (idempotent)
-- Target: {{S}}  (bolttech_workshop_demo.attach_war_room)
-- Engine: Spark SQL on serverless warehouse.
-- Determinism: all randomness via pmod(hash(id, salt), N) so re-runs reproduce
--   the exact dataset and the exact planted anomalies.
-- ALL DATA IS SYNTHETIC — no real PII, carriers, or partners.
-- Anchored to AS_OF = 2026-06-03; history starts 2025-04-04 (~14 months).
-- Statements are separated by a line containing only:  -- @@
-- ============================================================================

-- @@
-- ---------------------------------------------------------------------------
-- markets
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.markets (
  market_id   STRING NOT NULL COMMENT 'Market / country code (synonyms: country code, geo, market)',
  market_name STRING          COMMENT 'Market / country name',
  region      STRING          COMMENT 'Region: APAC, EMEA, AMER, Africa',
  currency    STRING          COMMENT 'ISO currency code for the market (synonyms: ccy, local currency)',
  CONSTRAINT markets_pk PRIMARY KEY (market_id)
) COMMENT 'Markets bolttech operates in (synthetic). One row per country/market.'

-- @@
INSERT INTO {{S}}.markets VALUES
  ('TH','Thailand','APAC','THB'),
  ('SG','Singapore','APAC','SGD'),
  ('IT','Italy','EMEA','EUR'),
  ('GB','United Kingdom','EMEA','GBP'),
  ('US','United States','AMER','USD'),
  ('KE','Kenya','Africa','KES')

-- @@
-- ---------------------------------------------------------------------------
-- partners (distribution partners embedding bolttech protection)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.partners (
  partner_id   STRING NOT NULL COMMENT 'Distribution partner id (synonyms: distributor, channel partner)',
  partner_name STRING          COMMENT 'Partner name (synthetic)',
  partner_type STRING          COMMENT 'Partner type: telco, OEM, retail, bank, ecommerce',
  market_id    STRING          COMMENT 'Market the partner primarily operates in',
  CONSTRAINT partners_pk PRIMARY KEY (partner_id)
) COMMENT 'Distribution partners (telcos, OEMs, retailers, banks) embedding bolttech protection at checkout (synthetic).'

-- @@
-- Partner names are FICTIONAL (compliance: never attach fabricated loss ratios /
-- "broken funnels" to real companies — see COMPLIANCE.md). partner_id P01..P12 are
-- stable, so all planted anomalies, joins, and EVAL ground-truth are unchanged.
INSERT INTO {{S}}.partners VALUES
  ('P01','Velora Telecom','telco','IT'),
  ('P02','Siam Mobile Care','OEM','TH'),
  ('P03','Marina Mobile','telco','SG'),
  ('P04','Savanna Mobile','telco','KE'),
  ('P05','Brightway Electronics','retail','GB'),
  ('P06','MegaTech Stores','retail','US'),
  ('P07','EasyCredit Finance','bank','TH'),
  ('P08','Rift Valley Bank','bank','KE'),
  ('P09','BazaarOne','ecommerce','TH'),
  ('P10','Thanon Telecom','telco','TH'),
  ('P11','Aquila Mobile','telco','IT'),
  ('P12','NovaPay','bank','GB')

-- @@
-- ---------------------------------------------------------------------------
-- products (coverage SKUs). premium_rate_pct = annual premium / device value.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.products (
  product_id       STRING NOT NULL COMMENT 'Product / coverage SKU id',
  product_name     STRING          COMMENT 'Product name',
  product_line     STRING          COMMENT 'Product line: device_protection, embedded_pc, travel',
  premium_rate_pct DOUBLE          COMMENT 'Annual premium as a fraction of insured device value (0.14 = 14%)',
  term_months      INT             COMMENT 'Coverage term in months',
  CONSTRAINT products_pk PRIMARY KEY (product_id)
) COMMENT 'Protection products / coverage SKUs offered across the exchange (synthetic).'

-- @@
INSERT INTO {{S}}.products VALUES
  ('PR01','Screen Repair','device_protection',0.08,12),
  ('PR02','Screen + Theft','device_protection',0.14,12),
  ('PR03','Full Device Protection','device_protection',0.18,12),
  ('PR04','Extended Warranty','device_protection',0.10,24),
  ('PR05','Theft & Loss','device_protection',0.13,12),
  ('PR06','Accidental Damage','device_protection',0.09,12),
  ('PR07','Multi-Device Bundle','device_protection',0.16,12),
  ('PR08','Premium Care','device_protection',0.20,24),
  ('PR09','Travel Insurance','travel',0.04,1),
  ('PR10','Purchase Protection','embedded_pc',0.05,12),
  ('PR11','Battery & Wear','device_protection',0.06,12),
  ('PR12','Liquid Damage','device_protection',0.085,12)

-- @@
-- ---------------------------------------------------------------------------
-- fx_rates (daily USD per 1 unit of local currency)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.fx_rates (
  currency  STRING NOT NULL COMMENT 'ISO currency code',
  rate_date DATE   NOT NULL COMMENT 'Date of the FX rate',
  usd_rate  DOUBLE          COMMENT 'USD per 1 unit of local currency (premium_local * usd_rate = premium_usd)',
  CONSTRAINT fx_pk PRIMARY KEY (currency, rate_date)
) COMMENT 'Daily FX rates to USD for multi-currency blending (synthetic).'

-- @@
INSERT INTO {{S}}.fx_rates
SELECT c.currency, d.rate_date,
  round(c.base * (1 + 0.03*sin(datediff(d.rate_date, DATE'2025-04-04')/45.0)
        + (pmod(hash(c.currency, datediff(d.rate_date, DATE'2025-04-04')), 1000)/1000.0 - 0.5)*0.012), 6) AS usd_rate
FROM (SELECT explode(sequence(DATE'2025-04-04', DATE'2026-06-03', interval 1 day)) AS rate_date) d
CROSS JOIN (
  SELECT col1 AS currency, col2 AS base
  FROM VALUES ('THB',0.029),('SGD',0.74),('EUR',1.08),('GBP',1.27),('USD',1.0),('KES',0.0078)
) c

-- @@
-- ---------------------------------------------------------------------------
-- sessions — the embedded-checkout funnel grain (one row per eligible session)
-- Funnel: offer_shown -> quote_started -> quote_completed -> bound -> activated
-- Planted anomalies:
--   #1 Velora Telecom (P01) IT mid-tier: offer_shown collapses ~45d (impression rule broke)
--   #2 Siam Mobile Care (P02): bind drops ~30d (deductible raised -> price shock)
--   #3 Rift Valley Bank KE (P08): activation drops ~25d (first-payment provider migration)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.sessions (
  session_id            STRING NOT NULL COMMENT 'Unique checkout session id',
  session_ts            TIMESTAMP       COMMENT 'Session timestamp',
  session_date          DATE            COMMENT 'Session date (synonyms: day, date)',
  partner_id            STRING          COMMENT 'Distribution partner whose checkout this is',
  market_id             STRING          COMMENT 'Market / country of the session',
  currency              STRING          COMMENT 'Local currency of the session',
  product_id            STRING          COMMENT 'Protection product offered',
  product_line          STRING          COMMENT 'Product line (device_protection, embedded_pc, travel)',
  device_tier           STRING          COMMENT 'Device tier: premium, mid, budget (synonyms: handset tier, phone tier)',
  device_model          STRING          COMMENT 'Device model name (synthetic)',
  device_value_usd      DOUBLE          COMMENT 'Insured device value in USD',
  customer_segment      STRING          COMMENT 'Customer segment: new, returning, high_value',
  channel               STRING          COMMENT 'Checkout channel: app, web',
  deductible_tier_shown STRING          COMMENT 'Deductible tier shown at quote: low, std, high',
  premium_usd           DOUBLE          COMMENT 'Offered annual premium in USD',
  premium_local         DOUBLE          COMMENT 'Offered annual premium in local currency',
  price_to_value        DOUBLE          COMMENT 'Premium as a fraction of device value',
  offer_shown           BOOLEAN         COMMENT 'TRUE if the protection offer was displayed (synonyms: impression, offer impression)',
  quote_started         BOOLEAN         COMMENT 'TRUE if the customer started a quote',
  quote_completed       BOOLEAN         COMMENT 'TRUE if the customer completed the quote',
  bound                 BOOLEAN         COMMENT 'TRUE if a policy was bound (synonyms: purchased, attached, sold)',
  activated             BOOLEAN         COMMENT 'TRUE if the bound policy was activated / first payment succeeded',
  stage_reached         STRING          COMMENT 'Furthest funnel stage reached',
  abandon_reason_text   STRING          COMMENT 'Free-text reason the customer gave when abandoning (NULL if converted or no offer)',
  offer_shown_ts        TIMESTAMP       COMMENT 'Timestamp the offer was shown',
  bound_lag_minutes     INT             COMMENT 'Minutes from session start to bind (NULL if not bound)',
  CONSTRAINT sessions_pk PRIMARY KEY (session_id)
) COMMENT 'Embedded-checkout sessions and their funnel outcome. The analytical workhorse and the shadow-sim replay source (synthetic).'

-- @@
INSERT INTO {{S}}.sessions
WITH draws AS (
  SELECT id,
    pmod(hash(id, 1),100000)/100000.0 AS u_partner,
    pmod(hash(id, 2),100000)/100000.0 AS u_tier,
    pmod(hash(id, 3),100000)/100000.0 AS u_val,
    pmod(hash(id, 4),100000)/100000.0 AS u_model,
    pmod(hash(id, 5),100000)/100000.0 AS u_prod,
    pmod(hash(id, 6),100000)/100000.0 AS u_seg,
    pmod(hash(id, 7),100000)/100000.0 AS u_chan,
    pmod(hash(id, 8),100000)/100000.0 AS u_ded,
    pmod(hash(id, 9), 426)             AS off_days,
    pmod(hash(id,10), 86400)           AS sec_of_day,
    pmod(hash(id,21),100000)/100000.0 AS d1,
    pmod(hash(id,22),100000)/100000.0 AS d2,
    pmod(hash(id,23),100000)/100000.0 AS d3,
    pmod(hash(id,24),100000)/100000.0 AS d4,
    pmod(hash(id,25),100000)/100000.0 AS d5,
    pmod(hash(id,31),100000)/100000.0 AS u_phrase,
    pmod(hash(id,32), 58)              AS lag_min
  FROM range(1, 300001)
),
cat AS (
  SELECT id, sec_of_day, d1, d2, d3, d4, d5, u_val, u_model, u_ded, u_phrase, lag_min,
    concat('S', lpad(cast(id AS string), 8, '0')) AS session_id,
    CASE WHEN u_partner<0.14 THEN 'P01' WHEN u_partner<0.28 THEN 'P02' WHEN u_partner<0.36 THEN 'P03'
         WHEN u_partner<0.43 THEN 'P04' WHEN u_partner<0.50 THEN 'P05' WHEN u_partner<0.59 THEN 'P06'
         WHEN u_partner<0.65 THEN 'P07' WHEN u_partner<0.71 THEN 'P08' WHEN u_partner<0.79 THEN 'P09'
         WHEN u_partner<0.86 THEN 'P10' WHEN u_partner<0.90 THEN 'P11' ELSE 'P12' END AS partner_id,
    CASE WHEN u_tier<0.25 THEN 'premium' WHEN u_tier<0.70 THEN 'mid' ELSE 'budget' END AS device_tier,
    CASE WHEN u_prod<0.20 THEN 'PR02' WHEN u_prod<0.36 THEN 'PR01' WHEN u_prod<0.50 THEN 'PR03'
         WHEN u_prod<0.60 THEN 'PR06' WHEN u_prod<0.69 THEN 'PR05' WHEN u_prod<0.77 THEN 'PR04'
         WHEN u_prod<0.84 THEN 'PR08' WHEN u_prod<0.89 THEN 'PR07' WHEN u_prod<0.93 THEN 'PR12'
         WHEN u_prod<0.96 THEN 'PR11' WHEN u_prod<0.98 THEN 'PR09' ELSE 'PR10' END AS product_id,
    CASE WHEN u_seg<0.55 THEN 'new' WHEN u_seg<0.90 THEN 'returning' ELSE 'high_value' END AS customer_segment,
    CASE WHEN u_chan<0.60 THEN 'app' ELSE 'web' END AS channel,
    date_add(DATE'2025-04-04', off_days) AS session_date
  FROM draws
),
enr AS (
  SELECT c.*, p.market_id, m.currency, pr.product_line, pr.premium_rate_pct,
    timestampadd(SECOND, c.sec_of_day, CAST(c.session_date AS timestamp)) AS session_ts,
    round(CASE c.device_tier WHEN 'premium' THEN 800 + c.u_val*600
               WHEN 'mid' THEN 300 + c.u_val*400 ELSE 110 + c.u_val*180 END, 2) AS device_value_usd,
    element_at(
      CASE c.device_tier
        WHEN 'premium' THEN array('iPhone 16 Pro','Galaxy S25 Ultra','Pixel 10 Pro','iPhone 16','Galaxy S25')
        WHEN 'mid'     THEN array('Galaxy A55','Redmi Note 14','Pixel 9a','iPhone SE','Galaxy A35')
        ELSE                array('Galaxy A16','Redmi 14C','Moto G15','Nokia G42','Tecno Spark 30')
      END, 1 + CAST(c.u_model*5 AS int)) AS device_model,
    CASE WHEN c.partner_id='P02' AND c.session_date >= date_add(DATE'2026-06-03',-30) THEN 'high'
         WHEN c.u_ded<0.25 THEN 'low' WHEN c.u_ded<0.80 THEN 'std' ELSE 'high' END AS deductible_tier_shown
  FROM cat c
  JOIN {{S}}.partners p ON c.partner_id = p.partner_id
  JOIN {{S}}.markets  m ON p.market_id  = m.market_id
  JOIN {{S}}.products pr ON c.product_id = pr.product_id
),
prob AS (
  SELECT *,
    (pmod(hash(partner_id, 99), 9) - 4)/100.0 AS q,
    round(device_value_usd * premium_rate_pct, 2) AS premium_usd
  FROM enr
),
prob2 AS (
  SELECT *,
    least(0.99, greatest(0.05,
      (CASE WHEN partner_id='P01' AND device_tier='mid' AND session_date >= date_add(DATE'2026-06-03',-45)
            THEN 0.56 ELSE 0.86 END) + q)) AS p_offer,
    least(0.99, greatest(0.05, 0.62 + q)) AS p_qs,
    least(0.99, greatest(0.05, 0.72 + q)) AS p_qc,
    least(0.99, greatest(0.03,
      (CASE WHEN partner_id='P02' AND session_date >= date_add(DATE'2026-06-03',-30)
            THEN 0.33 ELSE 0.56 END)
      + q + (CASE WHEN deductible_tier_shown='high' THEN -0.06 ELSE 0 END))) AS p_bind,
    least(0.99, greatest(0.05,
      (CASE WHEN partner_id='P08' AND session_date >= date_add(DATE'2026-06-03',-25)
            THEN 0.71 ELSE 0.96 END))) AS p_act
  FROM prob
),
stages AS (
  SELECT *,
    (d1 < p_offer) AS offer_shown,
    (d1 < p_offer) AND (d2 < p_qs) AS quote_started,
    (d1 < p_offer) AND (d2 < p_qs) AND (d3 < p_qc) AS quote_completed,
    (d1 < p_offer) AND (d2 < p_qs) AND (d3 < p_qc) AND (d4 < p_bind) AS bound,
    (d1 < p_offer) AND (d2 < p_qs) AND (d3 < p_qc) AND (d4 < p_bind) AND (d5 < p_act) AS activated
  FROM prob2
)
SELECT
  s.session_id, s.session_ts, s.session_date, s.partner_id, s.market_id, s.currency,
  s.product_id, s.product_line, s.device_tier, s.device_model, s.device_value_usd,
  s.customer_segment, s.channel, s.deductible_tier_shown, s.premium_usd,
  round(s.premium_usd / fx.usd_rate, 2) AS premium_local,
  round(s.premium_rate_pct, 4) AS price_to_value,
  s.offer_shown, s.quote_started, s.quote_completed, s.bound, s.activated,
  CASE WHEN s.activated THEN 'activated' WHEN s.bound THEN 'bound'
       WHEN s.quote_completed THEN 'quote_completed' WHEN s.quote_started THEN 'quote_started'
       WHEN s.offer_shown THEN 'offer_shown' ELSE 'no_offer' END AS stage_reached,
  CASE
    WHEN s.activated THEN NULL
    WHEN NOT s.offer_shown THEN NULL
    WHEN NOT s.quote_started THEN element_at(array('Offer did not seem relevant to my device','Just browsing, not ready to buy','Did not think I needed cover'), 1 + CAST(s.u_phrase*3 AS int))
    WHEN NOT s.quote_completed THEN element_at(array('Checkout asked too many questions','The form was too long','Got distracted, will finish later'), 1 + CAST(s.u_phrase*3 AS int))
    WHEN NOT s.bound THEN
      CASE WHEN (s.partner_id='P02' AND s.session_date >= date_add(DATE'2026-06-03',-30)) OR s.u_phrase < 0.5
        THEN element_at(array('Too expensive once I saw the deductible','Premium was higher than expected','Found cheaper protection elsewhere','The price jumped at checkout','Not worth it at that price'), 1 + CAST(s.u_phrase*5 AS int))
        ELSE element_at(array('Changed my mind','Wanted to compare options first','Decided I did not need it','Will decide later'), 1 + CAST(s.u_phrase*4 AS int)) END
    WHEN NOT s.activated THEN
      CASE WHEN (s.partner_id='P08' AND s.session_date >= date_add(DATE'2026-06-03',-25)) OR s.u_phrase < 0.6
        THEN element_at(array('Card was declined','Payment did not go through','Could not complete the first payment','Bank authorization failed','Card on file had expired'), 1 + CAST(s.u_phrase*5 AS int))
        ELSE element_at(array('Changed my mind after binding','Decided to cancel before activation','Will activate later'), 1 + CAST(s.u_phrase*3 AS int)) END
    ELSE NULL
  END AS abandon_reason_text,
  s.session_ts AS offer_shown_ts,
  CASE WHEN s.bound THEN CAST(3 + s.lag_min AS int) ELSE NULL END AS bound_lag_minutes
FROM stages s
LEFT JOIN {{S}}.fx_rates fx ON s.currency = fx.currency AND s.session_date = fx.rate_date

-- @@
-- ---------------------------------------------------------------------------
-- policies (one per bound session)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.policies (
  policy_id        STRING NOT NULL COMMENT 'Unique policy id',
  session_id       STRING          COMMENT 'Originating checkout session',
  partner_id       STRING          COMMENT 'Distribution partner',
  market_id        STRING          COMMENT 'Market / country',
  currency         STRING          COMMENT 'Local currency',
  product_id       STRING          COMMENT 'Protection product',
  product_line     STRING          COMMENT 'Product line',
  device_tier      STRING          COMMENT 'Device tier',
  device_value_usd DOUBLE          COMMENT 'Insured device value in USD',
  bind_date        DATE            COMMENT 'Date the policy was bound',
  premium_usd      DOUBLE          COMMENT 'Annual premium in USD',
  premium_local    DOUBLE          COMMENT 'Annual premium in local currency',
  gwp_usd          DOUBLE          COMMENT 'Gross written premium in USD (synonyms: GWP, written premium)',
  commission_usd   DOUBLE          COMMENT 'Acquisition cost / commission paid to the distribution partner in USD (synonyms: acquisition cost, partner commission)',
  ceded_premium_usd DOUBLE         COMMENT 'Premium ceded to the carrier / reinsurer in USD (synonyms: ceded premium, reinsurance premium)',
  expense_usd      DOUBLE          COMMENT 'Internal admin / claims-handling expense allocated to the policy in USD (synonyms: operating expense)',
  deductible_tier  STRING          COMMENT 'Deductible tier on the policy',
  status           STRING          COMMENT 'Policy status: active, cancelled, lapsed',
  activated        BOOLEAN         COMMENT 'Whether the policy was activated (first payment succeeded)',
  CONSTRAINT policies_pk PRIMARY KEY (policy_id)
) COMMENT 'Bound policies. gwp_usd is the annualized gross written premium in USD. commission + expense over GWP give the expense ratio; loss ratio + expense ratio = combined ratio (synthetic).'

-- @@
INSERT INTO {{S}}.policies
SELECT concat('POL', lpad(CAST(row_number() OVER (ORDER BY s.session_id) AS string), 8, '0')) AS policy_id,
  s.session_id, s.partner_id, s.market_id, s.currency, s.product_id, s.product_line, s.device_tier, s.device_value_usd,
  s.session_date AS bind_date, s.premium_usd, s.premium_local, s.premium_usd AS gwp_usd,
  -- acquisition cost: embedded distribution pays the partner a commission (take rate) by channel type
  round(s.premium_usd * (CASE pt.partner_type WHEN 'retail' THEN 0.32 WHEN 'telco' THEN 0.30
        WHEN 'ecommerce' THEN 0.30 WHEN 'OEM' THEN 0.28 WHEN 'bank' THEN 0.22 ELSE 0.27 END), 2) AS commission_usd,
  round(s.premium_usd * 0.40, 2) AS ceded_premium_usd,    -- portion ceded to the carrier
  round(s.premium_usd * 0.08, 2) AS expense_usd,          -- internal admin / claims-handling expense
  s.deductible_tier_shown AS deductible_tier,
  CASE WHEN (pmod(hash(s.session_id, 77),100000)/100000.0) < 0.08 THEN 'cancelled'
       WHEN (pmod(hash(s.session_id, 78),100000)/100000.0) < 0.05 THEN 'lapsed'
       ELSE 'active' END AS status,
  s.activated
FROM {{S}}.sessions s
JOIN {{S}}.partners pt ON s.partner_id = pt.partner_id
WHERE s.bound = true

-- @@
-- ---------------------------------------------------------------------------
-- claims (FNOL -> settlement). Frequency & severity drive loss ratio.
-- Loss-ratio trap: budget tier + KE market -> high theft frequency & severity.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.claims (
  claim_id         STRING NOT NULL COMMENT 'Unique claim id',
  policy_id        STRING          COMMENT 'Policy the claim is on',
  partner_id       STRING          COMMENT 'Distribution partner',
  market_id        STRING          COMMENT 'Market / country',
  currency         STRING          COMMENT 'Local currency',
  product_id       STRING          COMMENT 'Protection product',
  device_tier      STRING          COMMENT 'Device tier',
  fnol_date        DATE            COMMENT 'First-notice-of-loss date (synonyms: FNOL date, claim date)',
  claim_type       STRING          COMMENT 'Claim type: cracked_screen, theft, liquid, malfunction',
  severity         STRING          COMMENT 'Severity: minor, moderate, major',
  claim_amount_usd DOUBLE          COMMENT 'Claim / loss amount paid in USD, net of deductible (synonyms: loss, incurred loss, payout)',
  status           STRING          COMMENT 'Claim status: settled, in_review, denied',
  settlement_days  INT             COMMENT 'Days from FNOL to settlement (synonyms: cycle time, settlement time)',
  fraud_flag       BOOLEAN         COMMENT 'Suspected-fraud flag',
  fnol_text        STRING          COMMENT 'Free-text first-notice-of-loss description (synthetic)',
  CONSTRAINT claims_pk PRIMARY KEY (claim_id)
) COMMENT 'Claims with frequency, severity and settlement time. Net loss (claim_amount_usd) over GWP drives loss ratio (synthetic).'

-- @@
INSERT INTO {{S}}.claims
WITH base AS (
  SELECT p.*,
    pmod(hash(p.policy_id, 1),100000)/100000.0 AS u_freq,
    pmod(hash(p.policy_id, 2),100000)/100000.0 AS u_type,
    pmod(hash(p.policy_id, 3),100000)/100000.0 AS u_sev,
    pmod(hash(p.policy_id, 4),100000)/100000.0 AS u_amt,
    pmod(hash(p.policy_id, 5),100000)/100000.0 AS u_fraud,
    pmod(hash(p.policy_id, 6), 320)            AS off_days,
    pmod(hash(p.policy_id, 7),100000)/100000.0 AS u_set,
    pmod(hash(p.policy_id, 8),100000)/100000.0 AS u_txt
  FROM {{S}}.policies p
  WHERE p.status <> 'cancelled'
),
freq AS (
  SELECT *,
    (0.075
     + CASE device_tier WHEN 'budget' THEN 0.06 WHEN 'mid' THEN 0.02 ELSE 0.0 END
     + CASE WHEN market_id='KE' THEN 0.06 ELSE 0.0 END
     + CASE WHEN device_tier='budget' AND market_id='KE' THEN 0.09 ELSE 0.0 END  -- loss-ratio trap interaction
     + CASE WHEN product_line='device_protection' THEN 0.015 ELSE -0.03 END) AS p_claim
  FROM base
),
hit AS (
  SELECT * FROM freq WHERE u_freq < p_claim
),
typed AS (
  SELECT *,
    CASE WHEN u_type < (CASE WHEN market_id='KE' AND device_tier='budget' THEN 0.58
                             WHEN market_id='KE' OR device_tier='budget' THEN 0.38 ELSE 0.18 END) THEN 'theft'
         WHEN u_type < 0.62 THEN 'cracked_screen'
         WHEN u_type < 0.80 THEN 'liquid'
         ELSE 'malfunction' END AS claim_type
  FROM hit
),
sev AS (
  SELECT *,
    CASE WHEN claim_type='theft' THEN 'major'
         WHEN u_sev < 0.45 THEN 'minor'
         WHEN u_sev < 0.85 THEN 'moderate'
         ELSE 'major' END AS severity
  FROM typed
)
SELECT
  concat('CLM', lpad(CAST(row_number() OVER (ORDER BY policy_id) AS string), 8, '0')) AS claim_id,
  policy_id, partner_id, market_id, currency, product_id, device_tier,
  date_add(bind_date, 10 + off_days) AS fnol_date,
  claim_type, severity,
  round(greatest(20,
    (CASE severity WHEN 'minor' THEN 0.22 WHEN 'moderate' THEN 0.50 ELSE 0.92 END) * device_value_usd
    - (CASE deductible_tier WHEN 'low' THEN 30 WHEN 'high' THEN 150 ELSE 80 END)
  ) * (1 + (u_amt-0.5)*0.15), 2) AS claim_amount_usd,
  CASE WHEN u_amt < 0.04 THEN 'denied' WHEN u_set < 0.10 THEN 'in_review' ELSE 'settled' END AS status,
  CASE WHEN severity='minor' THEN 1 + CAST(u_set*2 AS int)
       WHEN severity='moderate' THEN 2 + CAST(u_set*6 AS int)
       ELSE 5 + CAST(u_set*20 AS int) END AS settlement_days,
  ((claim_type IN ('theft','cracked_screen') AND device_tier='budget' AND u_fraud < 0.10) OR (u_fraud < 0.02)) AS fraud_flag,
  CASE claim_type
    WHEN 'cracked_screen' THEN element_at(array('Dropped my phone and the screen shattered','Screen cracked after a fall','Cracked the display, touch not working'), 1 + CAST(u_txt*3 AS int))
    WHEN 'theft'          THEN element_at(array('Phone was stolen','Lost my phone, suspected theft on the bus','Device taken from my bag'), 1 + CAST(u_txt*3 AS int))
    WHEN 'liquid'         THEN element_at(array('Spilled water, phone will not turn on','Dropped it in the sink','Liquid damage, screen flickering'), 1 + CAST(u_txt*3 AS int))
    ELSE                       element_at(array('Phone stopped charging','Device will not power on','Battery drains in minutes'), 1 + CAST(u_txt*3 AS int))
  END AS fnol_text
FROM sev

-- @@
-- ---------------------------------------------------------------------------
-- offer_config — the LIVE offer-serving rules the partner checkout reads.
-- Golden Delta copy; synced to Lakebase for low-latency serving.
-- Velora Telecom (P01) mid-tier impression_enabled=false reflects the broken state.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.offer_config (
  config_id             STRING NOT NULL COMMENT 'Unique offer-config id (partner-product-tier)',
  partner_id            STRING          COMMENT 'Distribution partner',
  product_id            STRING          COMMENT 'Protection product',
  device_tier           STRING          COMMENT 'Device tier the rule applies to',
  market_id             STRING          COMMENT 'Market / country',
  impression_enabled    BOOLEAN         COMMENT 'Whether the offer is shown at checkout (the impression switch)',
  placement             STRING          COMMENT 'Placement: checkout_confirmation, cart, post_purchase',
  deductible_tier_shown STRING          COMMENT 'Deductible tier shown: low, std, high',
  price_band            STRING          COMMENT 'Price band: standard, promo, premium',
  eligibility_rule      STRING          COMMENT 'Human-readable eligibility rule',
  version               INT             COMMENT 'Config version',
  effective_date        DATE            COMMENT 'Date this version became effective',
  is_current            BOOLEAN         COMMENT 'Whether this is the current live version',
  updated_by            STRING          COMMENT 'Who last updated this config',
  CONSTRAINT offer_config_pk PRIMARY KEY (config_id)
) COMMENT 'Live offer-serving rules read by the partner checkout (golden copy; synced to Lakebase) (synthetic).'

-- @@
INSERT INTO {{S}}.offer_config
SELECT
  concat('CFG-', partner_id, '-', product_id, '-', substr(device_tier,1,3)) AS config_id,
  partner_id, product_id, device_tier, market_id,
  CASE WHEN partner_id='P01' AND device_tier='mid' THEN false ELSE true END AS impression_enabled,
  CASE WHEN u<0.5 THEN 'checkout_confirmation' WHEN u<0.8 THEN 'cart' ELSE 'post_purchase' END AS placement,
  CASE WHEN partner_id='P02' THEN 'high' ELSE 'std' END AS deductible_tier_shown,
  'standard' AS price_band,
  'device_value 80-2000 USD; exclude refurbished older than 24 months' AS eligibility_rule,
  1 AS version,
  date_add(DATE'2026-06-03', -90) AS effective_date,
  true AS is_current,
  'init_load' AS updated_by
FROM (
  SELECT p.partner_id, p.market_id, pr.product_id, t.device_tier,
    pmod(hash(p.partner_id, pr.product_id, t.device_tier, 1),100000)/100000.0 AS u
  FROM {{S}}.partners p
  CROSS JOIN (SELECT product_id FROM {{S}}.products WHERE product_id IN ('PR01','PR02','PR03','PR05','PR06')) pr
  CROSS JOIN (SELECT explode(array('premium','mid','budget')) AS device_tier) t
)

-- @@
-- ---------------------------------------------------------------------------
-- offer_config_audit — who/what/when/why for offer-config changes.
-- Seeded with the three anomaly-causing changes + benign history.
-- The agent appends future changes here (and to Lakebase) on approval.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE {{S}}.offer_config_audit (
  audit_id               STRING NOT NULL COMMENT 'Unique audit id',
  config_id              STRING          COMMENT 'Offer-config affected',
  partner_id             STRING          COMMENT 'Distribution partner',
  product_id             STRING          COMMENT 'Protection product',
  device_tier            STRING          COMMENT 'Device tier',
  change_ts              TIMESTAMP       COMMENT 'When the change happened',
  changed_by             STRING          COMMENT 'Who/what made the change (user or system job)',
  field_changed          STRING          COMMENT 'Field that changed',
  before_value           STRING          COMMENT 'Value before the change',
  after_value            STRING          COMMENT 'Value after the change',
  rationale              STRING          COMMENT 'Why the change was made',
  projected_attach_delta DOUBLE          COMMENT 'Projected attach-rate delta (for agent-proposed changes)',
  projected_loss_ratio   DOUBLE          COMMENT 'Projected loss ratio after the change (for agent-proposed changes)',
  CONSTRAINT offer_config_audit_pk PRIMARY KEY (audit_id)
) COMMENT 'Audit log of offer-config changes. Seeded with the anomaly-causing changes (synthetic).'

-- @@
INSERT INTO {{S}}.offer_config_audit VALUES
  ('AUD0001','CFG-P01-PR02-mid','P01','PR02','mid', timestampadd(DAY,-45,CAST(DATE'2026-06-03' AS timestamp)),'catalog_sync_job','impression_enabled','true','false','Automated handset-catalog refresh remapped mid-tier SKUs; impression rule was not re-applied to the new group', NULL, NULL),
  ('AUD0002','CFG-P01-PR01-mid','P01','PR01','mid', timestampadd(DAY,-45,CAST(DATE'2026-06-03' AS timestamp)),'catalog_sync_job','impression_enabled','true','false','Automated handset-catalog refresh remapped mid-tier SKUs; impression rule was not re-applied to the new group', NULL, NULL),
  ('AUD0003','CFG-P01-PR03-mid','P01','PR03','mid', timestampadd(DAY,-45,CAST(DATE'2026-06-03' AS timestamp)),'catalog_sync_job','impression_enabled','true','false','Automated handset-catalog refresh remapped mid-tier SKUs; impression rule was not re-applied to the new group', NULL, NULL),
  ('AUD0004','CFG-P02-PR02-mid','P02','PR02','mid', timestampadd(DAY,-30,CAST(DATE'2026-06-03' AS timestamp)),'pricing_team','deductible_tier_shown','std','high','Raised default deductible tier across Siam Mobile Care to protect margin', NULL, NULL),
  ('AUD0005','CFG-P02-PR03-mid','P02','PR03','mid', timestampadd(DAY,-30,CAST(DATE'2026-06-03' AS timestamp)),'pricing_team','deductible_tier_shown','std','high','Raised default deductible tier across Siam Mobile Care to protect margin', NULL, NULL),
  ('AUD0006','CFG-P08-PR02-mid','P08','PR02','mid', timestampadd(DAY,-25,CAST(DATE'2026-06-03' AS timestamp)),'payments_eng','payment_provider','provider_a','provider_b','Migrated first-payment provider for Rift Valley Bank KE checkout', NULL, NULL),
  ('AUD0007','CFG-P05-PR01-premium','P05','PR01','premium', timestampadd(DAY,-70,CAST(DATE'2026-06-03' AS timestamp)),'growth_team','placement','cart','checkout_confirmation','A/B winner: moved offer to checkout confirmation', 0.018, 0.54),
  ('AUD0008','CFG-P03-PR02-mid','P03','PR02','mid', timestampadd(DAY,-60,CAST(DATE'2026-06-03' AS timestamp)),'growth_team','price_band','standard','promo','Holiday promo pricing for Marina Mobile mid-tier', 0.025, 0.61),
  ('AUD0009','CFG-P06-PR03-premium','P06','PR03','premium', timestampadd(DAY,-52,CAST(DATE'2026-06-03' AS timestamp)),'pricing_team','price_band','promo','standard','Ended promo; returned MegaTech Stores premium to standard pricing', -0.012, 0.49),
  ('AUD0010','CFG-P10-PR05-budget','P10','PR05','budget', timestampadd(DAY,-38,CAST(DATE'2026-06-03' AS timestamp)),'growth_team','impression_enabled','false','true','Enabled theft cover impressions for Thanon Telecom budget tier', 0.031, 0.66)

-- @@
-- ---------------------------------------------------------------------------
-- Foreign key (informational) relationships — help Genie infer joins.
-- ---------------------------------------------------------------------------
ALTER TABLE {{S}}.partners ADD CONSTRAINT partners_market_fk FOREIGN KEY (market_id) REFERENCES {{S}}.markets (market_id)

-- @@
ALTER TABLE {{S}}.sessions ADD CONSTRAINT sessions_partner_fk FOREIGN KEY (partner_id) REFERENCES {{S}}.partners (partner_id)

-- @@
ALTER TABLE {{S}}.sessions ADD CONSTRAINT sessions_product_fk FOREIGN KEY (product_id) REFERENCES {{S}}.products (product_id)

-- @@
ALTER TABLE {{S}}.sessions ADD CONSTRAINT sessions_market_fk FOREIGN KEY (market_id) REFERENCES {{S}}.markets (market_id)

-- @@
ALTER TABLE {{S}}.policies ADD CONSTRAINT policies_session_fk FOREIGN KEY (session_id) REFERENCES {{S}}.sessions (session_id)

-- @@
ALTER TABLE {{S}}.policies ADD CONSTRAINT policies_product_fk FOREIGN KEY (product_id) REFERENCES {{S}}.products (product_id)

-- @@
ALTER TABLE {{S}}.claims ADD CONSTRAINT claims_policy_fk FOREIGN KEY (policy_id) REFERENCES {{S}}.policies (policy_id)

-- @@
ALTER TABLE {{S}}.offer_config ADD CONSTRAINT offer_config_partner_fk FOREIGN KEY (partner_id) REFERENCES {{S}}.partners (partner_id)
