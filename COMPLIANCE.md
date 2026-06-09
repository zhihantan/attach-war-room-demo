# COMPLIANCE.md — Why the Attach War-Room is safe to show

**Audience:** anyone (legal / compliance / brand reviewers) vetting this demo before it is shown.
**Scope:** this is a Databricks App *demo*. It does not connect to any production system, carrier,
partner, or customer record. Everything it displays is generated locally from a single SQL script.

**TL;DR**
1. The data is **fully synthetic and deterministic** — hash-generated from row indices, re-derivable
   from scratch, with **no PII schema** (no real people, names, emails, or identifiers — even fabricated ones).
2. **Every entity is fictional** — the exchange operator (**Acme Embedded Insurance**) and *all*
   distribution partners (Velora Telecom, Siam Mobile Care, Rift Valley Bank, Savanna Mobile, …) are
   invented. No real company is shown attached to any metric, anomaly, or "broken funnel."
3. Every row is reproducible from `00_setup/generate_data.sql` — no external data source.

---

## 1. The data is fully synthetic and deterministic

All tables are built by a single idempotent script, `00_setup/generate_data.sql`, which runs on a serverless SQL warehouse. There is no ingestion, no upload, no scrape, no external feed.

**Deterministic generation (hash-based, not random).** The script's own header states the contract:

> *Determinism: all randomness via `pmod(hash(id, salt), N)` so re-runs reproduce the exact dataset and the exact planted anomalies.*

Concretely, the 300k-row `sessions` table is generated from `FROM range(1, 300001)` — a pure integer sequence — and every "random" attribute (partner, device tier, premium, funnel outcome, abandonment lag, etc.) is a deterministic hash of the row index and a per-field salt, e.g. `pmod(hash(id, 1),100000)/100000.0`. `policies` and `claims` are derived deterministically from `sessions`/`policies` the same way. There is **no** `rand()`, no wall-clock seed, no machine-specific entropy. The same script produces the same dataset on any warehouse, every time. The dataset is anchored to a fixed analysis date, `AS_OF = 2026-06-03` (history starts `2025-04-04`), so dates do not drift between rebuilds.

This matters for compliance because it means there is nothing to "discover" in the data — every figure on screen is a closed-form function of a row number, auditable line-by-line in the script.

**No PII — not even synthetic PII.** The schema was deliberately designed without a person grain. There are **no** columns for customer names, emails, phone numbers, addresses, dates of birth, government IDs, policy-holder identifiers, or any other natural-person attribute. The grain is the *checkout session* and the *policy*, keyed only by opaque surrogate IDs (`session_id`, `policy_id`, `claim_id`). The only free-text fields are:

- `device_model` — synthetic handset names drawn from a fixed array (e.g. "Galaxy A55", "iPhone SE").
- `abandon_reason_text` and the claim free-text — short generic phrases drawn from fixed arrays (e.g. *"Too expensive once I saw the deductible"*, *"Dropped my phone and the screen shattered"*). These are scripted strings, not transcribed from any real customer, and contain no identifying detail.

So even if the entire dataset leaked, there is no personal data in it to breach — synthetic or otherwise.

---

## 2. Every named entity is fictional

The demo's narrative is that specific named partners are failing (an impressions bug, a price shock at bind, an activation leak, a loss-ratio trap). To avoid attaching a fabricated statement of fact to any **real** company, **every entity is invented**:

- **Exchange operator:** *Acme Embedded Insurance* (fictional).
- **Distribution partners** (`partners` table): *Velora Telecom, Siam Mobile Care, Marina Mobile, Savanna Mobile, Brightway Electronics, MegaTech Stores, EasyCredit Finance, Rift Valley Bank, BazaarOne, Thanon Telecom, Aquila Mobile, NovaPay* — all fictional.
- **Markets** are real countries (Italy, Thailand, Kenya, …) and **device models** reference generic consumer-electronics names; neither is attached to a defamatory claim.

To re-skin for a specific account, edit `config/demo_profile.json` (account name, partner display names, branches, theme) and regenerate the data with matching names — see `docs/BRING_YOUR_OWN_DATA.md`.

---

## 3. Reproducible, no external data

Every table is a closed-form function of a row index in `00_setup/generate_data.sql`; there is no external feed, no scraped data, and no credentials in the repo (auth is OAuth via the running identity / the app's service principal). A reviewer can regenerate the entire dataset from scratch and diff it.

## Pre-demo checklist
- [ ] All entities fictional — confirm the operator + every partner name on screen / in tool output / in any Genie SQL is from the fictional list above. *(Default profile already is.)*
- [ ] No real credentials committed — confirmed (OAuth only).
- [ ] Data regenerated in the target workspace via `install.py` (or `setup_notebook`), so it's local + synthetic.
