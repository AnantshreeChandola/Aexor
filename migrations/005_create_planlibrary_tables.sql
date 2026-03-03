-- Migration: 005_create_planlibrary_tables
-- Description: Create plans, plan_outcomes, plan_embeddings, and plan_metrics tables for PlanLibrary component
-- Date: 2026-02-28
-- Reference: components/PlanLibrary/LLD.md (Memory Layer component)
-- Component: PlanLibrary (Memory Layer - plan storage and analytics)

-- =============================================================================
-- Table: plans
-- Purpose: Immutable storage of executed plans with signatures
-- =============================================================================

CREATE TABLE plans (
    plan_id         VARCHAR(26) PRIMARY KEY,  -- ULID format
    canonical_json  JSONB NOT NULL,
    signature_data  JSONB NOT NULL,
    intent_type     VARCHAR(64) NOT NULL,
    step_count      INTEGER NOT NULL,
    plan_hash       VARCHAR(64) NOT NULL,     -- SHA-256 hex
    size_bytes      INTEGER NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    stored_at       TIMESTAMP WITH TIME ZONE NOT NULL
                    DEFAULT NOW()
);

-- Query by intent type for plan pattern analysis
CREATE INDEX idx_plans_intent_type
    ON plans (intent_type);

-- Query recent plans for analytics
CREATE INDEX idx_plans_stored_at
    ON plans (stored_at);

-- Deduplication and lookup by hash
CREATE INDEX idx_plans_hash
    ON plans (plan_hash);

-- Filter by complexity
CREATE INDEX idx_plans_step_count
    ON plans (step_count);

-- =============================================================================
-- Table: plan_outcomes
-- Purpose: Store execution results for success rate analysis
-- =============================================================================

CREATE TABLE plan_outcomes (
    outcome_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id         VARCHAR(26) NOT NULL
                    REFERENCES plans(plan_id) ON DELETE CASCADE,
    success         BOOLEAN NOT NULL,
    error_type      VARCHAR(64),
    error_details   JSONB,
    execution_start TIMESTAMP WITH TIME ZONE NOT NULL,
    execution_end   TIMESTAMP WITH TIME ZONE NOT NULL,
    total_steps     INTEGER NOT NULL,
    failed_step     INTEGER,
    context_data    JSONB
);

-- Query outcomes for a specific plan
CREATE INDEX idx_plan_outcomes_plan_id
    ON plan_outcomes (plan_id);

-- Filter by success/failure for analytics
CREATE INDEX idx_plan_outcomes_success
    ON plan_outcomes (success);

-- Query outcomes by time range
CREATE INDEX idx_plan_outcomes_execution_start
    ON plan_outcomes (execution_start);

-- =============================================================================
-- Table: plan_embeddings
-- Purpose: Store vector embeddings for similarity search (requires pgvector)
-- =============================================================================

CREATE TABLE plan_embeddings (
    embedding_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id         VARCHAR(26) NOT NULL
                    REFERENCES plans(plan_id) ON DELETE CASCADE
                    UNIQUE,  -- One embedding per plan
    -- Note: vector column requires pgvector extension
    -- Uncomment after installing pgvector:
    -- vector          vector(1536) NOT NULL,
    model_version   VARCHAR(32) NOT NULL DEFAULT 'text-embedding-ada-002',
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL
                    DEFAULT NOW(),
    vector_norm     TEXT NOT NULL  -- Temporary: store as JSON until pgvector installed
);

-- Query embeddings by plan_id
CREATE INDEX idx_plan_embeddings_plan_id
    ON plan_embeddings (plan_id);

-- Query embeddings by creation time
CREATE INDEX idx_plan_embeddings_created_at
    ON plan_embeddings (created_at);

-- Uncomment after installing pgvector extension for similarity search:
-- CREATE INDEX idx_plan_embeddings_vector
--     ON plan_embeddings USING ivfflat (vector vector_cosine_ops)
--     WITH (lists = 100);

-- =============================================================================
-- Table: plan_metrics
-- Purpose: Store performance metrics for optimization
-- =============================================================================

CREATE TABLE plan_metrics (
    metrics_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id             VARCHAR(26) NOT NULL
                        REFERENCES plans(plan_id) ON DELETE CASCADE,
    preview_latency_ms  INTEGER,
    execute_latency_ms  INTEGER NOT NULL,
    step_timings        JSONB,
    resource_usage      JSONB
);

-- Query metrics for a specific plan
CREATE INDEX idx_plan_metrics_plan_id
    ON plan_metrics (plan_id);

-- Analyze slow executions
CREATE INDEX idx_plan_metrics_execute_latency
    ON plan_metrics (execute_latency_ms);

-- =============================================================================
-- Comments for documentation
-- =============================================================================

COMMENT ON TABLE plans IS
'Memory Layer component: immutable storage of executed plans with Ed25519 signatures for audit trail and pattern learning.';

COMMENT ON COLUMN plans.plan_id IS
'ULID format (26 characters, lexicographically sortable). Primary key for plan identification.';

COMMENT ON COLUMN plans.plan_hash IS
'SHA-256 hash of canonical JSON for deduplication and integrity verification.';

COMMENT ON TABLE plan_outcomes IS
'Execution results for success rate analysis and failure pattern detection.';

COMMENT ON COLUMN plan_outcomes.failed_step IS
'Step index where execution failed (NULL if success=true). Zero-indexed.';

COMMENT ON TABLE plan_embeddings IS
'Vector embeddings for semantic similarity search. Requires pgvector extension in production.';

COMMENT ON COLUMN plan_embeddings.vector_norm IS
'Temporary storage as JSON. Replace with vector(1536) column after pgvector installation.';

COMMENT ON TABLE plan_metrics IS
'Performance metrics for plan execution optimization and latency analysis.';

COMMENT ON COLUMN plan_metrics.step_timings IS
'Per-step latency breakdown as JSON array: [{"step": 0, "latency_ms": 120}, ...]';

-- =============================================================================
-- Notes
-- =============================================================================

-- pgvector installation required for production similarity search:
-- CREATE EXTENSION IF NOT EXISTS vector;
-- Then alter plan_embeddings to use vector(1536) column and create HNSW/IVFFlat index.
