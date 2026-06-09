-- ============================================================================
-- Attach War-Room — Lakebase (Postgres OLTP) schema
-- Two roles: (1) low-latency SERVING of offer_config the checkout reads;
--            (2) app/agent STATE with multi-table ACID writes.
-- Applied to database: attach_war_room
-- ============================================================================

-- (1) SERVING + writable by the agent on approval (seeded from the Delta golden copy)
CREATE TABLE IF NOT EXISTS offer_config (
    config_id              TEXT PRIMARY KEY,
    partner_id             TEXT NOT NULL,
    product_id             TEXT NOT NULL,
    device_tier            TEXT NOT NULL,
    market_id              TEXT,
    impression_enabled     BOOLEAN NOT NULL,
    placement              TEXT,
    deductible_tier_shown  TEXT,
    price_band             TEXT,
    eligibility_rule       TEXT,
    version                INT NOT NULL DEFAULT 1,
    is_current             BOOLEAN NOT NULL DEFAULT true,
    updated_by             TEXT,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- the hot path the simulated checkout reads (partner + device tier lookup)
CREATE INDEX IF NOT EXISTS idx_offer_config_lookup ON offer_config (partner_id, device_tier);

-- (2a) audit log — agent appends one row per approved change (ACID with offer_config)
CREATE TABLE IF NOT EXISTS offer_config_audit (
    audit_id               BIGSERIAL PRIMARY KEY,
    config_id              TEXT,
    partner_id             TEXT,
    product_id             TEXT,
    device_tier            TEXT,
    change_ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by             TEXT,
    field_changed          TEXT,
    before_value           TEXT,
    after_value            TEXT,
    rationale              TEXT,
    projected_attach_delta DOUBLE PRECISION,
    projected_loss_ratio   DOUBLE PRECISION,
    scenario_id            BIGINT
);

-- (2b) per-partner alert thresholds the agent checks
CREATE TABLE IF NOT EXISTS alert_thresholds (
    partner_id       TEXT PRIMARY KEY,
    attach_drop_pct  DOUBLE PRECISION NOT NULL DEFAULT 0.05,  -- alert if relative attach drop exceeds this
    loss_ratio_max   DOUBLE PRECISION NOT NULL DEFAULT 0.70,  -- offer-change guardrail
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- (2c) saved shadow-sim scenarios the agent proposes / the user approves
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id             BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by              TEXT,
    partner_id              TEXT,
    market_id               TEXT,
    product_line            TEXT,
    device_tier             TEXT,
    title                   TEXT,
    proposed_change         JSONB,
    baseline_attach         DOUBLE PRECISION,
    projected_attach        DOUBLE PRECISION,
    projected_attach_delta  DOUBLE PRECISION,
    baseline_loss_ratio     DOUBLE PRECISION,
    projected_loss_ratio    DOUBLE PRECISION,
    projected_gwp_delta_usd DOUBLE PRECISION,
    within_guardrail        BOOLEAN,
    status                  TEXT NOT NULL DEFAULT 'proposed'  -- proposed | approved | shipped | rejected
);

-- (2d) conversation memory for continuity across sessions
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id      BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,            -- user | assistant | tool
    content         TEXT,
    tool_name       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_conv ON chat_messages (conversation_id, created_at);

-- (2e) demo self-instrumentation (Tier 3.5) — milestone pings so the SA org can see
-- which demos completed the diagnose->ship loop, by whom, for which account.
CREATE TABLE IF NOT EXISTS demo_events (
    event_id        BIGSERIAL PRIMARY KEY,
    event_name      TEXT NOT NULL,            -- chat | branch | ship | rollback | reset
    detail          JSONB,
    conversation_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_demo_events_ts ON demo_events (created_at);
