# EVAL — Genie space benchmark

Measurable accuracy for the Genie space (built on the `funnel_metrics` and `profitability_metrics` metric views). Each benchmark has a natural-language question, the expected answer, and ground-truth SQL. The SQL versions are also stored as the space's benchmark questions.

## How to run
```bash
# ask the space a question and see Genie's generated SQL + narrative + rows
uv run --with databricks-sdk 01_metric_views_and_genie/ask_genie.py "<question>" 

# verify a ground-truth SQL directly on the warehouse
uv run --with databricks-sdk 01_metric_views_and_genie/run_sql.py <file.sql>
```
Compare Genie's answer/SQL to the expected answer / ground-truth SQL below. Within limits: the space uses **1** instruction block, **8** example SQL, **10** benchmarks, **8** curated objects — well under the per-space (~100 instruction) and knowledge-store (~200) limits.

> Numbers are from the synthetic dataset anchored to 2026-06-03 and reproduce exactly on re-generation. All `current_date()` filters assume "today" = 2026-06-03.

| # | Question | Expected answer | Ground-truth SQL (metric views) |
|---|---|---|---|
| 1 | For Velora Telecom mid-tier devices, how have offer-shown rate, conversion rate, and attach rate changed in the last 45 days vs before? | Offer-shown **86.5% → 53.0%**, attach **22.3% → 15.2%**; conversion-of-shown held **~26–29%**. An impressions problem, not a conversion problem. | `SELECT CASE WHEN session_date>=date_add(current_date(),-45) THEN 'last_45d' ELSE 'prior' END period, MEASURE(offer_show_rate), MEASURE(conversion_rate), MEASURE(attach_rate) FROM funnel_metrics WHERE partner_name='Velora Telecom' AND device_tier='mid' GROUP BY period` |
| 2 | Which partner had the largest quote-to-bind drop in the last 30 days? | **Siam Mobile Care (Thailand)** — complete-to-bind **~52% → ~24%**. | `... MEASURE(complete_to_bind_rate) ... WHERE partner_id='P02' GROUP BY period` |
| 3 | Where is policy activation / first payment leaking in the last 25 days? | **Rift Valley Bank, Kenya** — bind-to-activation **~95% → ~69%**. | `... MEASURE(activation_rate) ... WHERE partner_id='P08' GROUP BY period` |
| 4 | What is the blended loss ratio across the book? | **~33%** (~$1.39M losses / ~$4.16M GWP). | `SELECT MEASURE(loss_ratio), MEASURE(gross_written_premium_usd), MEASURE(incurred_losses_usd) FROM profitability_metrics` |
| 5 | Which partner, market and device tier has the worst loss ratio? | **Budget tier in Kenya** — Savanna Mobile **~95%**, Rift Valley **~79%**; both above the 0.70 guardrail. | `SELECT partner_name, device_tier, MEASURE(loss_ratio) FROM profitability_metrics GROUP BY partner_name, device_tier ORDER BY 3 DESC LIMIT 10` |
| 6 | Top abandonment reasons for Siam Mobile Care at the quote-completed stage, last 30 days? | Price cluster: "Found cheaper protection elsewhere", "Premium was higher than expected", "Too expensive once I saw the deductible", "The price jumped at checkout". | `SELECT abandon_reason_text, COUNT(*) FROM sessions WHERE partner_id='P02' AND stage_reached='quote_completed' AND session_date>=date_add(current_date(),-30) GROUP BY 1 ORDER BY 2 DESC` |
| 7 | Which offer configurations currently have impressions disabled? | **Velora Telecom (P01) mid-tier** across PR01, PR02, PR03, PR05, PR06 (5 configs). | `SELECT partner_id, product_id, device_tier FROM offer_config WHERE impression_enabled = false` |
| 8 | Gross written premium by market in the last 30 days? | Led by **Thailand (THB)**, then **UK (GBP)** and **Italy (EUR)**, then Kenya, Singapore, US. | `SELECT market_name, currency, MEASURE(gross_written_premium_usd) FROM funnel_metrics WHERE session_date>=date_add(current_date(),-30) GROUP BY 1,2 ORDER BY 3 DESC` |
| 9 | Claims frequency and average settlement time for budget devices in Kenya? | Frequency **~28%**, avg settlement **~10.5 days**, loss ratio **~89%**. | `SELECT MEASURE(claims_frequency), MEASURE(avg_settlement_days), MEASURE(loss_ratio) FROM profitability_metrics WHERE device_tier='budget' AND market_name='Kenya'` |
| 10 | Rank partners by attach rate over the last 30 days. | Highest: **EasyCredit Finance (~27%)**, **Brightway Electronics (~26%)**; **Velora Telecom depressed (~19%)** due to the impression issue. | `SELECT partner_name, MEASURE(attach_rate) FROM funnel_metrics WHERE session_date>=date_add(current_date(),-30) GROUP BY 1 ORDER BY 2 DESC` |

## Live validation (run during the build)
Q1 asked against the live space returned the correct SQL **and** narrative: *"attach dropped from 0.22 to 0.15 … due to a decrease in offer show rate (0.86 → 0.53), even though conversion of shown offers increased (0.26 → 0.29). The main driver is fewer offers being shown, not a decline in conversion."* ✓

## Text-instruction guardrail check
Ask **"What's the attach rate?"** with no partner/product line → the space should **ask you to specify both** before answering (per the instruction), rather than returning a blended number. This verifies the clarifying-question instruction is active.

## Measured results — 2026-06-04 (automated harnesses, live workspace)

Both harnesses now run automatically and score the space/agent (not eyeballed).

**Genie accuracy** — `uv run --with databricks-sdk 01_metric_views_and_genie/eval_genie.py`
(narrative-aware scoring, 5% tolerance for whole-percent rounding, 1 retry on transient Genie failures):

> **11 / 11 = 100%.** Numeric questions match the ground truth **exactly** (0.00% relerr on attach, loss ratio, claims frequency — the harness reads Genie's result rows); name questions surface the right entity (Siam Mobile Care #2, Rift Valley #3, Savanna Mobile/Kenya/budget #5, the 5 disabled Velora Telecom configs #7); top-label questions correct (Thailand #8, EasyCredit Finance #10); and the bare "What's the attach rate?" **#11 correctly clarifies** instead of answering.
>
> Root-cause note: earlier runs scored 45% then 73% — both were **harness/integration fidelity, not Genie**. The unlock was fixing `dbx.genie_ask`, which fetched the query manifest (columns) but used the message-level `get_message_query_result` that returns an **empty `data_array`**; switching to the per-attachment `get_message_attachment_query_result(...attachment_id)` captures the executed rows. This also fixes the app's live **"Verify in Genie"** panel (it now shows real rows). The Genie space itself needed no tuning.
>
> Re-run **2026-06-05** (after the fictional-name activation): **10/11 = 91%**, still over the 80% gate. The only miss was **#2** ("which partner had the largest quote-to-bind drop?"), which is **non-deterministic** — Genie sometimes names the partner in prose and sometimes doesn't. All direct-answer, numeric, and guardrail questions pass consistently across runs; #2 is answer-variance on an open superlative question, not a data/names issue.

**Agent quality** — `uv run --with 'mlflow>=2.21' --with databricks-sdk --with openai --with psycopg2-binary 03_agent/eval_agent.py`
(5 hero cases; falls back to in-process scoring — the `mlflow.genai.evaluate` path needs an expectations-format fix):

> | metric | score |
> |---|---|
> | cites the right broken funnel stage | **5/5 = 1.00** |
> | refuses to ship a guardrail breach | **5/5 = 1.00** |
> | no fabricated numbers (strict: every quoted number must trace to a tool result) | **0.87 avg** |
>
> The agent localized the correct stage in every scenario and chose the right diagnostic tools per case (Siam Mobile Care/Rift Valley used `abandonment_reasons` + `classify_abandonment`). `DEMO_OFFLINE=1` runs the same harness on fixtures for a no-Databricks smoke test.
