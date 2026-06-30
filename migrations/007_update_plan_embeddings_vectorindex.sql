-- Migration: 007_update_plan_embeddings_vectorindex
-- Description: Update plan_embeddings table for VectorIndex hybrid search
-- Date: 2026-03-16
-- Reference: components/VectorIndex/LLD.md (Section 6.2)
-- Component: VectorIndex (Memory / Persistence Layer)

-- =============================================================================
-- Prerequisite: pgvector extension
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- Drop obsolete columns
-- =============================================================================

ALTER TABLE plan_embeddings DROP COLUMN IF EXISTS vector_norm;

-- =============================================================================
-- Add new columns
-- =============================================================================

ALTER TABLE plan_embeddings
    ADD COLUMN IF NOT EXISTS intent_type VARCHAR(64) NOT NULL DEFAULT 'unknown';

ALTER TABLE plan_embeddings
    ADD COLUMN IF NOT EXISTS embedding vector(384);

ALTER TABLE plan_embeddings
    ADD COLUMN IF NOT EXISTS search_text TEXT NOT NULL DEFAULT '';

ALTER TABLE plan_embeddings
    ADD COLUMN IF NOT EXISTS tsv tsvector;

-- =============================================================================
-- Update model_version default
-- =============================================================================

ALTER TABLE plan_embeddings
    ALTER COLUMN model_version SET DEFAULT 'all-MiniLM-L6-v2';

-- =============================================================================
-- Indexes
-- =============================================================================

-- B-tree index for intent_type pre-filtering
CREATE INDEX IF NOT EXISTS idx_plan_embeddings_intent_type
    ON plan_embeddings (intent_type);

-- GIN index for BM25 full-text search
CREATE INDEX IF NOT EXISTS idx_plan_embeddings_tsv
    ON plan_embeddings USING gin (tsv);

-- HNSW index for semantic similarity search
CREATE INDEX IF NOT EXISTS idx_plan_embeddings_hnsw
    ON plan_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- tsvector trigger: auto-populate tsv from search_text
-- =============================================================================

CREATE OR REPLACE FUNCTION plan_embeddings_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('english', NEW.search_text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop trigger first if it exists (CREATE TRIGGER has no IF NOT EXISTS)
DROP TRIGGER IF EXISTS trg_plan_embeddings_tsv ON plan_embeddings;

CREATE TRIGGER trg_plan_embeddings_tsv
    BEFORE INSERT OR UPDATE ON plan_embeddings
    FOR EACH ROW EXECUTE FUNCTION plan_embeddings_tsv_trigger();

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE plan_embeddings IS
    'Plan embeddings for hybrid search (BM25 + semantic). Owned by VectorIndex.';

COMMENT ON COLUMN plan_embeddings.embedding IS
    '384-dim vector from all-MiniLM-L6-v2 via ONNX Runtime';

COMMENT ON COLUMN plan_embeddings.tsv IS
    'tsvector auto-generated from search_text for BM25 ranking';

COMMENT ON COLUMN plan_embeddings.search_text IS
    'Structured text: intent_type | actions | constraints | entities';

COMMENT ON COLUMN plan_embeddings.intent_type IS
    'Denormalized intent type for B-tree pre-filtering';
