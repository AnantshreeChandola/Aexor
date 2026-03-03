-- Migration: 004_create_history_tables
-- Description: Create history and fact_patterns tables for History component
-- Date: 2026-02-28
-- Reference: components/History/LLD.md §4 (Data Model)
-- Component: History (Memory Layer - Tier 3 data source)
-- PR: #5

-- =============================================================================
-- Table: history
-- Purpose: Store normalized, PII-light facts from plan execution outcomes
-- =============================================================================

CREATE TABLE history (
    fact_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL
                  REFERENCES users(user_id) ON DELETE CASCADE,
    fact_text     TEXT NOT NULL,
    intent_type   VARCHAR(64) NOT NULL,
    entities      JSONB NOT NULL DEFAULT '{}',
    outcome       BOOLEAN NOT NULL,
    source_plan_id VARCHAR(26),          -- ULID, nullable
    fact_hash     VARCHAR(64) NOT NULL,  -- SHA256 hex
    ttl_days      INTEGER NOT NULL DEFAULT 30,
    created_at    TIMESTAMP WITH TIME ZONE NOT NULL
                  DEFAULT NOW(),
    expires_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    deleted_at    TIMESTAMP WITH TIME ZONE
);

-- Primary query path: user + intent + active + recency
CREATE INDEX idx_history_user_intent_active
    ON history (user_id, intent_type, created_at DESC)
    WHERE deleted_at IS NULL;

-- Deduplication: unique fact_hash per user (active only)
CREATE UNIQUE INDEX idx_history_user_fact_hash
    ON history (user_id, fact_hash)
    WHERE deleted_at IS NULL;

-- TTL cleanup: find expired facts
CREATE INDEX idx_history_expires_at
    ON history (expires_at)
    WHERE deleted_at IS NULL;

-- Pattern detection queries
CREATE INDEX idx_history_user_entities
    ON history USING GIN (entities)
    WHERE deleted_at IS NULL;

-- Source plan correlation
CREATE INDEX idx_history_source_plan
    ON history (source_plan_id)
    WHERE source_plan_id IS NOT NULL;

-- =============================================================================
-- Table: fact_patterns
-- Purpose: Store detected recurring behavioral patterns from facts
-- =============================================================================

CREATE TABLE fact_patterns (
    pattern_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL
                        REFERENCES users(user_id) ON DELETE CASCADE,
    intent_type         VARCHAR(64) NOT NULL,
    pattern_key         VARCHAR(128) NOT NULL,
    pattern_description VARCHAR(512) NOT NULL,
    entity_pattern      JSONB NOT NULL DEFAULT '{}',
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    last_seen           TIMESTAMP WITH TIME ZONE NOT NULL,
    confidence          REAL NOT NULL DEFAULT 0.0,

    -- One pattern per user + intent + key
    CONSTRAINT uq_fact_patterns_user_intent_key
        UNIQUE (user_id, intent_type, pattern_key)
);

-- Query patterns by user and intent
CREATE INDEX idx_fact_patterns_user_intent
    ON fact_patterns (user_id, intent_type, confidence DESC);

-- Stale pattern cleanup
CREATE INDEX idx_fact_patterns_last_seen
    ON fact_patterns (last_seen);

-- =============================================================================
-- Comments for documentation
-- =============================================================================

COMMENT ON TABLE history IS
'Memory Layer component: stores normalized, PII-light facts from plan execution outcomes. Tier 3 data source for ContextRAG. 30-day TTL with soft-delete.';

COMMENT ON COLUMN history.fact_hash IS
'SHA256 hash of (user_id + intent_type + fact_text + date) for idempotent deduplication';

COMMENT ON COLUMN history.ttl_days IS
'Time-to-live in days (default 30). Fact expires at created_at + ttl_days.';

COMMENT ON COLUMN history.deleted_at IS
'Soft-delete timestamp for TTL expiration. NULL = active, NOT NULL = expired.';

COMMENT ON TABLE fact_patterns IS
'Detected recurring behavioral patterns from stored facts. Updated incrementally on fact storage.';

COMMENT ON COLUMN fact_patterns.confidence IS
'Pattern confidence score (0.0-1.0). Formula: min(1.0, occurrence_count / 5)';

COMMENT ON COLUMN fact_patterns.pattern_key IS
'Unique pattern identifier: {intent_type}:{entity_key}:{day_of_week}';
