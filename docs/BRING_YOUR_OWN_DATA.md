# Bring Your Own Data — the "now point it at OUR data" contract

> Tier 3.3. This is the contract a customer (or an SA piloting on real data) reads when
> they ask the obvious question after the demo: *"OK, that's synthetic bolttech data —
> what does it take to run this on **our** funnel?"*
>
> The honest answer: the metric views and agent tools depend on a **small, well-defined
> table/column shape**, not on bolttech specifics. If you can land your funnel and claims
> data into that shape (directly, or via the adapter views in §2), the metric layer, the
> shadow-sim math, the loss-ratio guardrail and the agent loop run **unchanged**. What is
> NOT reusable is the demo's *fiction* — the three planted anomalies, the offline fixtures,
> and the seeded scenarios. §3 draws that line explicitly.
>
> ALL DATA IN THE SHIPPED DEMO IS SYNTHETIC. The shapes below are real; the values are not.

The contract has four parts:

1. **The minimal table/column shape** the metric views + agent tools expect (§1).
2. **An adapter pattern** — `CREATE VIEW`s that map your real tables onto that shape so
   `funnel_metrics` / `profitability_metrics` work without edits (§2).
3. **Demo-fiction vs reusable-on-real-data** — what survives the switch and what does not (§3).
4. **A realistic scoping note** — "stand it up on your funnel in N weeks" (§4).

Everything below was derived by reading the actual code, not from memory. The load-bearing
files are:

- `00_setup/generate_data.sql` — the table DDL (the column contract).
- `01_metric_views_and_genie/metric_views.sql` — the two metric views (`funnel_metrics`,
  `profitability_metrics`) + the `v_policy_claims` base view.
- `03_agent/tools.py` — the 14 agent tools and exactly which columns/measures they read.
- `02_lakebase/schema.sql` — the Lakebase OLTP state shape (offer_config + audit + scenarios).

---

## 1. The contract — minimal table/column shape

The agent and metric views read **seven Unity Catalog tables** in one schema
(`SCHEMA_FQN`, e.g. `your_catalog.attach_demo`) plus **a handful of Lakebase OLTP tables**
for live serving + write-back state. You do not need to reproduce every synthetic column —
only the ones below are referenced by the metric views (`MEASURE(...)` / dimensions) or by a
tool's SQL. Anything else in your warehouse can stay where it is.

Legend for **Required?**:

- **REQUIRED** — a metric-view measure/dimension or a tool query reads this column by name.
  Missing it breaks a KPI or a tool.
- **needed-for** — only required if you want that specific feature; otherwise optional.
- types are the Spark SQL types the demo uses; equivalents are fine.

### 1.1 `sessions` — the funnel grain (one row per eligible checkout session)

This is the analytical workhorse: it drives `funnel_metrics` and is the **replay source**
for the deterministic shadow-sim. Grain = one checkout session.

| Column | Type | Required? | Used by |
|---|---|---|---|
| `session_id` | STRING | REQUIRED | PK / `COUNT(1)` = `sessions` measure |
| `session_date` | DATE | REQUIRED | every tool's time window (`date_add(current_date(), -N)`); `funnel_metrics` date dim |
| `partner_id` | STRING | REQUIRED | join to `partners`; per-partner scan/diagnosis/sim |
| `market_id` | STRING | REQUIRED | join to `markets`; `loss_ratio_for(market_name=...)` slice |
| `device_tier` | STRING (`premium`/`mid`/`budget`) | REQUIRED | tier slicing; `run_shadow_sim` device_tier arg. Tools **allow-list** these three values (`tools.TIERS`) — rename or extend that set if your tiers differ |
| `product_id` | STRING | needed-for product slicing / Genie joins | `funnel_metrics` product join |
| `offer_shown` | BOOLEAN | REQUIRED | `offers_shown` measure → `offer_show_rate`, `conversion_rate` |
| `quote_started` | BOOLEAN | REQUIRED | `quotes_started` → `quote_start_rate` |
| `quote_completed` | BOOLEAN | REQUIRED | `quotes_completed` → `quote_complete_rate` |
| `bound` | BOOLEAN | REQUIRED | `policies_bound` → `attach_rate`, `complete_to_bind_rate`, GWP |
| `activated` | BOOLEAN | REQUIRED | `policies_activated` → `activation_rate` |
| `stage_reached` | STRING | REQUIRED | `abandonment_reasons` filters on this (`offer_shown`/`quote_started`/`quote_completed`/`bound`/`activated`) |
| `premium_usd` | DOUBLE | REQUIRED | GWP measure + recovered-GWP math in the sim |
| `deductible_tier_shown` | STRING (`low`/`std`/`high`) | needed-for deductible scenarios | dimension; `restore_deductible` semantics |
| `abandon_reason_text` | STRING (nullable) | needed-for the FMAPI cause-classification beat | `abandonment_reasons` / `classify_abandonment` |
| `currency`, `device_value_usd`, `product_line`, `customer_segment`, `channel` | mixed | optional | extra Genie dimensions / `avg_device_value_usd` |

The five funnel booleans must be **monotone** (a session that's `bound` must also be
`quote_completed`, `quote_started`, `offer_shown`) — every funnel rate is a ratio of one stage
count to the prior stage count, so a non-monotone session produces rates > 1.0.

### 1.2 `policies` — bound-policy grain (one row per bound policy)

Feeds the `v_policy_claims` base view → `profitability_metrics`. Grain = one policy.

| Column | Type | Required? | Used by |
|---|---|---|---|
| `policy_id` | STRING | REQUIRED | PK; join key for claims aggregation |
| `partner_id`, `market_id`, `product_id`, `device_tier` | STRING | REQUIRED | profitability dimensions; elasticity slice |
| `bind_date` | DATE | REQUIRED | profitability date dim |
| `gwp_usd` | DOUBLE | REQUIRED | `gross_written_premium_usd` → denominator of **loss ratio**, expense ratio, ceding ratio |
| `commission_usd` | DOUBLE | needed-for expense/combined ratio | `acquisition_cost_usd`, `expense_ratio`, `combined_ratio` |
| `ceded_premium_usd` | DOUBLE | needed-for ceding ratio | `ceded_premium_usd`, `ceding_ratio` |
| `expense_usd` | DOUBLE | needed-for expense/combined ratio | `expense_ratio`, `combined_ratio` |
| `deductible_tier` | STRING (`low`/`std`/`high`) | REQUIRED for the data-derived elasticity | `tools._deductible_loss_elasticity` groups by this (`high` vs `std`) |
| `status` | STRING | REQUIRED | `profitability_metrics.status` dim; elasticity excludes `cancelled` |
| `premium_usd`, `premium_local`, `currency`, `device_value_usd`, `product_line`, `activated` | mixed | optional | extra dimensions / continuity with sessions |

> `commission_usd`, `ceded_premium_usd`, `expense_usd` are `coalesce(..., 0)` in the base view,
> so if you don't have them, the expense/ceding/combined ratios simply read 0 — loss ratio and
> the guardrail still work. If you DO have them, you get a full combined-ratio story for free.

### 1.3 `claims` — claim grain (one row per claim)

Aggregated up to policy grain inside `v_policy_claims`.

| Column | Type | Required? | Used by |
|---|---|---|---|
| `claim_id` | STRING | REQUIRED | PK |
| `policy_id` | STRING | REQUIRED | join to `policies` |
| `claim_amount_usd` | DOUBLE | REQUIRED | net loss → `incurred_losses_usd` → **loss ratio**. Note: this is **net of deductible** in the demo, so the loss ratio already reflects the deductible. Keep your value net, or adjust the elasticity reading |
| `status` | STRING (`settled`/`in_review`/`denied`) | REQUIRED | base view sums losses only `WHERE status <> 'denied'` |
| `settlement_days` | INT | needed-for `avg_settlement_days` | `avg_settlement_days` measure |
| `fraud_flag` | BOOLEAN | needed-for `fraud_rate` | `fraud_rate` measure |
| `partner_id`, `market_id`, `product_id`, `device_tier`, `severity`, `claim_type`, `fnol_date`, `fnol_text` | mixed | optional | Genie dimensions / drill-downs |

### 1.4 Dimension tables — `partners`, `markets`, `products`

Small lookups joined by both metric views (and used by `tools._resolve_partner`).

| Table | REQUIRED columns | Optional columns |
|---|---|---|
| `partners` | `partner_id` (PK), `partner_name` | `partner_type`, `market_id` |
| `markets` | `market_id` (PK), `market_name` | `region`, `currency` |
| `products` | `product_id` (PK), `product_name` | `product_line`, `premium_rate_pct`, `term_months` |

`_resolve_partner` accepts either a `partner_id` like `P01` **or** a fuzzy name match
(`lower(partner_name) LIKE ...`), so your IDs don't need the `Pnn` shape — but `partner_name`
must be populated for the natural-language ("Velora Telecom", "the Kenya bank") path to work.

### 1.5 `offer_config` — the live serving rules (Delta golden copy + Lakebase)

This is the table the agent **writes to** when it ships a change. It exists in **two places**:
a Delta golden copy in UC (`generate_data.sql`) and a synced Lakebase OLTP copy
(`02_lakebase/schema.sql`) that the (simulated) checkout reads on the hot path. The agent's
`ship_offer_change` / `rollback_offer_change` operate on the **Lakebase** copy.

| Column | Type | Required? | Used by |
|---|---|---|---|
| `config_id` | TEXT | REQUIRED | PK |
| `partner_id`, `device_tier` | TEXT | REQUIRED | the `(partner_id, device_tier)` hot-path lookup index; ship/rollback `WHERE` |
| `product_id` | TEXT | REQUIRED | `get_offer_config` select |
| `impression_enabled` | BOOLEAN | REQUIRED | the impression switch `restore_impressions` flips |
| `deductible_tier_shown` | TEXT | REQUIRED | the field `restore_deductible`/`lower_price` flips (`high`→`std`) |
| `placement`, `price_band`, `eligibility_rule`, `version`, `is_current`, `updated_by`, `updated_at`, `market_id` | mixed | needed-for audit/version semantics | `version` is bumped on each ship; `updated_by`/`updated_at` stamp who/when |

### 1.6 Lakebase state tables — `offer_config_audit`, `scenarios`, `alert_thresholds`

These are app/agent **state**, not your data — you create them empty from
`02_lakebase/schema.sql` and the agent populates them. Documented here so the contract is
complete:

- **`offer_config_audit`** — one row per shipped/rolled-back change (`field_changed`,
  `before_value`, `after_value`, `rationale`, `projected_attach_delta`,
  `projected_loss_ratio`, `scenario_id`). The before/after enables `rollback_offer_change`.
- **`scenarios`** — saved shadow-sim proposals (`proposed_change` JSONB, baseline/projected
  attach + loss ratio, `projected_gwp_delta_usd`, `within_guardrail`, `status`:
  `proposed | approved | shipped | rejected | rolled_back`).
- **`alert_thresholds`** — `(partner_id, attach_drop_pct DEFAULT 0.05, loss_ratio_max DEFAULT 0.70)`.
  `scan_for_anomalies` reads this to decide what counts as a breach per partner. Seed one row
  per partner (the setup scripts do this with the defaults); tune `attach_drop_pct` per partner
  to make the scan sensitive to your real volatility.

---

## 2. The adapter pattern — map your tables onto the shape

You almost never want to physically rewrite your warehouse into these exact tables. Instead,
**adapt with views**: point the demo's `SCHEMA_FQN` at a schema that contains *views* named
`sessions` / `policies` / `claims` / `partners` / `markets` / `products`, each selecting from
your real tables and aliasing columns onto the contract. The metric views are then created on
top **verbatim** (`run_sql.py 01_metric_views_and_genie/metric_views.sql`), because they only
ever reference contract column names.

This is the whole point of the metric-view layer: business logic lives in
`metric_views.sql` once; the adapter is the only customer-specific SQL you write.

### 2.1 Funnel adapter — your event/clickstream → `sessions`

Most real funnels are an **event stream** (one row per step) rather than one row per session
with five booleans. Collapse it with a `GROUP BY` and `MAX(...)` per stage:

```sql
-- your_demo_schema.sessions  (a VIEW the demo reads as its `sessions` table)
CREATE OR REPLACE VIEW your_demo_schema.sessions AS
SELECT
    e.checkout_id                                   AS session_id,
    CAST(e.first_seen_ts AS DATE)                   AS session_date,
    e.distributor_code                              AS partner_id,      -- your channel/partner key
    e.country_code                                  AS market_id,
    e.handset_band                                  AS device_tier,     -- map to premium/mid/budget (see note)
    e.sku                                           AS product_id,
    -- monotone funnel booleans rolled up from your event log:
    MAX(e.step = 'offer_impression')                AS offer_shown,
    MAX(e.step IN ('quote_start','quote_complete','bind','activate'))   AS quote_started,
    MAX(e.step IN ('quote_complete','bind','activate'))                 AS quote_completed,
    MAX(e.step IN ('bind','activate'))              AS bound,
    MAX(e.step = 'activate')                        AS activated,
    -- furthest stage reached, as a label the abandonment tool filters on:
    CASE WHEN MAX(e.step='activate')=1        THEN 'activated'
         WHEN MAX(e.step='bind')=1            THEN 'bound'
         WHEN MAX(e.step='quote_complete')=1  THEN 'quote_completed'
         WHEN MAX(e.step='quote_start')=1     THEN 'quote_started'
         WHEN MAX(e.step='offer_impression')=1 THEN 'offer_shown'
         ELSE 'no_offer' END                        AS stage_reached,
    MAX(e.quoted_premium_usd)                       AS premium_usd,
    MAX(e.deductible_band)                          AS deductible_tier_shown,
    MAX(CASE WHEN e.step <> 'activate' THEN e.abandon_note END)  AS abandon_reason_text
FROM your_warehouse.checkout_events e
GROUP BY 1,2,3,4,5,6;
```

Mapping notes that matter for accuracy:

- **`device_tier` allow-list.** The tools enforce `{premium, mid, budget}` (`tools.TIERS`,
  and the `enum` in `TOOL_SCHEMAS`). Either map your bands onto those three labels in the
  view (a `CASE`), or change that one set + the schema enums to your labels. There's no other
  place tiers are hardcoded.
- **Monotonicity.** The `MAX(step IN (...))` pattern guarantees the booleans are monotone even
  if your event log has gaps, because a later stage implies all earlier ones.
- **`abandon_reason_text`** only needs to be populated on non-converted sessions; the demo's
  `abandonment_reasons` already filters `IS NOT NULL`.

### 2.2 Claims adapter — your policy + claims tables → `policies` / `claims`

```sql
-- your_demo_schema.policies
CREATE OR REPLACE VIEW your_demo_schema.policies AS
SELECT
    pol.policy_no                                   AS policy_id,
    pol.distributor_code                            AS partner_id,
    pol.country_code                                AS market_id,
    pol.sku                                          AS product_id,
    pol.handset_band                                AS device_tier,
    pol.inception_date                              AS bind_date,
    pol.written_premium_usd                         AS gwp_usd,
    pol.partner_commission_usd                      AS commission_usd,     -- 0 if you don't track it
    pol.reinsurance_ceded_usd                       AS ceded_premium_usd,  -- 0 if you don't track it
    pol.admin_cost_usd                              AS expense_usd,        -- 0 if you don't track it
    pol.deductible_band                             AS deductible_tier,    -- needed for elasticity (high vs std)
    pol.lifecycle_status                            AS status              -- map your states to active/cancelled/lapsed
FROM your_warehouse.policies pol;

-- your_demo_schema.claims
CREATE OR REPLACE VIEW your_demo_schema.claims AS
SELECT
    clm.claim_no                                    AS claim_id,
    clm.policy_no                                   AS policy_id,
    clm.net_paid_usd                                AS claim_amount_usd,   -- net of deductible (see §1.3)
    clm.claim_status                                AS status,             -- map your denied state to 'denied'
    clm.days_to_settle                             AS settlement_days,
    clm.suspected_fraud                             AS fraud_flag
FROM your_warehouse.claims clm;
```

Then create the dimension views (`partners`, `markets`, `products`) the same way, and run the
metric layer unchanged:

```bash
# point the demo at your adapter schema, then build the metric views verbatim:
export SCHEMA_FQN=your_catalog.your_demo_schema
uv run --with databricks-sdk \
  01_metric_views_and_genie/metric_views.sql   # via run_sql.py; {{S}} is substituted with SCHEMA_FQN
```

If `v_policy_claims` and the two metric views build cleanly over your adapter views,
`partner_funnel_overview`, `funnel_diagnosis`, `loss_ratio_for`, `run_shadow_sim` and the rest
of the read tools work on your data with **no code change** — they only call `MEASURE(...)`
against the metric views.

### 2.3 What still needs a real (small) write target

Read tools work off the adapter views. The **write** tools (`ship_offer_change`,
`rollback_offer_change`, `propose_offer_change`) need a real, writable
`offer_config` + `offer_config_audit` + `scenarios` in Lakebase (a view can't be written to,
and you want true OLTP latency + ACID for the ship/rollback). Seed `offer_config` from your
real offer-serving rules (or a golden Delta copy of them) using the pattern in
`02_lakebase/setup_provisioned.py`, and create the state tables from `02_lakebase/schema.sql`.
This is the one place "bring your own data" also means "bring your own *writable* serving
config" — because the demo's punchline is that the agent changes what the checkout serves.

---

## 3. Demo-fiction vs reusable-on-real-data

The single most important table in this document. Be precise with customers about which is
which — overclaiming here is how a demo loses credibility.

| Capability | Fiction or reusable? | Why / what carries over |
|---|---|---|
| The **3 planted funnel anomalies** (Velora Telecom IT mid-tier impression collapse; Siam Mobile Care deductible/price shock; Rift Valley Bank KE activation drop) | **DEMO-FICTION** | Hand-authored into `generate_data.sql` via `CASE WHEN partner_id=... AND session_date >= ...` so the agent always "finds" something. Your real funnel will have its *own* anomalies — or none today. The **detection mechanism** (§ scan + diagnosis below) is what's reusable, not these specific breaks. |
| The **Kenya budget-tier loss-ratio trap** | **DEMO-FICTION** | A deliberate frequency/severity interaction (`device_tier='budget' AND market_id='KE'`) seeded so a guardrail breach is demonstrable. Your loss ratios come from your real claims. |
| The **offline fixtures** (`DEMO_OFFLINE=1` → `server/fixtures.py`) | **DEMO-FICTION** | Canned tool outputs so the app runs with no warehouse/Lakebase (conference wifi, air-gapped laptop). Never point fixtures at real decisions. |
| The **seeded scenarios / audit history** (`offer_config_audit` AUD0001–AUD0010, demo `scenarios`) | **DEMO-FICTION** | Pre-written so `recent_activity` has a story on first load. Starts empty on your data; the agent writes real rows as you use it. |
| The **synthetic partner/market/product names** (Velora Telecom, Siam Mobile Care, …) | **DEMO-FICTION** | Real company names used for relatability; the re-skin profile (`config/demo_profile.json`) swaps them. Your data brings your own names. |
| The **deterministic funnel-restore shadow-sim math** (`run_shadow_sim`) | **REUSABLE** | Pure arithmetic: restore the broken stage rate to its *own* healthy prior level, replay over recent traffic, multiply stage rates → projected attach → recovered GWP/month. No magic constants, no LLM inventing KPIs. Runs identically on your `sessions`/metric views. |
| The **loss-ratio guardrail (0.70)** | **REUSABLE** | A hard gate in `ship_offer_change` (and `within_guardrail` on every sim/scenario). The *threshold* is config (`config.yaml` / `alert_thresholds.loss_ratio_max`); set it to your underwriting limit. The enforcement logic is yours unchanged. |
| The **data-derived deductible elasticity** (`tools._deductible_loss_elasticity`) | **REUSABLE (and the point)** | The loss-ratio multiplier for lowering a deductible is **computed from your own claims book** (observed loss ratio at `std` vs `high` deductible, `GROUP BY deductible_tier`), not hardcoded. It only falls back to a labelled placeholder (`1.12`, `derived:false`) when your slice is thin (<50 std-deductible policies). On a real book it derives a real number. |
| The **metric-view definitions** (`funnel_metrics`, `profitability_metrics`, `v_policy_claims`) | **REUSABLE** | The governed KPI semantic layer. Built on the contract columns only — create them verbatim over your adapter views (§2). Genie sits on these too, so NL analytics comes along. |
| The **agent loop** (tool-calling orchestration, `scan → diagnose → simulate → propose → approve → ship → rollback`, the system prompt from `profile.py`) | **REUSABLE** | Model-agnostic (models are env-configurable: `MODEL_AGENT`, `MODEL_CLASSIFIER`). The loop, tool schemas, human-in-the-loop approval gate and ACID write/rollback are all data-independent. |
| The **anomaly *detection* mechanism** (`scan_for_anomalies` + `funnel_diagnosis`) | **REUSABLE** | Compares recent-vs-prior stage rates per partner against `alert_thresholds`, ranks by recoverable GWP. The *logic* finds whatever is actually broken in your funnel; only the planted breaks it finds in the demo are fiction. |
| The **FMAPI cause classification + partner-note drafting** (`classify_abandonment`, `draft_partner_note`) | **REUSABLE** | Operates on your free-text `abandon_reason_text` / context. Quality scales with how rich your abandonment text is. |

One-line summary for the customer: *the storyline is staged; the machinery is not.* The math,
the guardrail, the elasticity, the metric layer and the agent loop are the parts you'd actually
run in production — and they run on your data the moment it lands in the §1 shape.

---

## 4. Scoping note — "stand it up on your funnel in N weeks"

A realistic, honest range for a first working pilot on real data, assuming a Databricks
workspace with Unity Catalog, a SQL warehouse, FMAPI access, and a Lakebase instance available
(the four ingredients this demo already validates end-to-end). Effort is data-mapping, not
platform-building — most of the system is reused as-is.

**Week 1 — land + adapt the data (the only real work).**
Write the adapter views in §2 over your real funnel/claims/policy tables. The risk is almost
entirely here: does your clickstream actually carry the five funnel stages, and is
`abandon_reason_text` (or equivalent) captured? Build `v_policy_claims` + the two metric views
over the adapter and sanity-check a few KPIs against your existing dashboards. *Exit criterion:
`partner_funnel_overview` and `loss_ratio_for` return numbers your analysts recognize.*

**Week 2 — wire serving state + the guardrail.**
Stand up the Lakebase `offer_config` / `offer_config_audit` / `scenarios` / `alert_thresholds`
from `02_lakebase/schema.sql`; seed `offer_config` from your real offer rules and
`alert_thresholds` per partner. Set the guardrail (`loss_ratio_max`) to your underwriting
limit. Confirm the elasticity derives a real multiplier from your claims book (not the
placeholder). *Exit criterion: `run_shadow_sim` → `propose_offer_change` → `ship_offer_change`
→ `rollback_offer_change` round-trips against your serving table, guardrail enforced.*

**Week 3 — agent + access + a real scenario.**
Point the app at `SCHEMA_FQN` + the Lakebase instance, grant the service principal the same
UC `SELECT` / Lakebase role / Genie `CAN_RUN` it uses today, set the FMAPI models. Pick one
real funnel question you care about and run the full diagnose→ship loop. Build the Genie space
on your metric views. *Exit criterion: a stakeholder drives the loop on a real (not planted)
funnel issue.*

**So: roughly 2–4 weeks to a credible internal pilot**, weighted toward week 1. It is **faster**
if your funnel is already one-row-per-session with stage flags (skip most of §2.1) and you
already track commission/ceded/expense (full combined-ratio story out of the box). It is
**slower** if the funnel lives only in raw clickstream that needs sessionization, if
abandonment text isn't captured (the classification beat degrades), or if offer-serving rules
aren't in a queryable table yet (you'll model `offer_config` from scratch). None of those are
platform problems — they're "is the data captured and shaped" problems, which is exactly what
this contract is for.

Hardening beyond the pilot (production, not in scope for the N-week number): switch FMAPI to
provisioned-throughput endpoints for SLAs, add row/column-level UC governance on the metric
views, move `offer_config` writes behind your real change-management/approval system rather
than the demo's single approve gate, and replace the offline fixtures path entirely.
