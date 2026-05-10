-- 015: Create tool_embeddings table for Tool Discovery hybrid search
-- Requires: pgvector extension (already enabled by migration 007)

CREATE TABLE IF NOT EXISTS tool_embeddings (
    embedding_id  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name     VARCHAR(256) NOT NULL UNIQUE,
    provider_name VARCHAR(64)  NOT NULL,
    embedding     vector(384)  NOT NULL,
    search_text   TEXT         NOT NULL,
    tsv           TSVECTOR,
    model_version VARCHAR(32)  NOT NULL DEFAULT 'all-MiniLM-L6-v2',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- HNSW index for cosine similarity
CREATE INDEX IF NOT EXISTS idx_tool_embeddings_hnsw
  ON tool_embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- GIN index for BM25 full-text search
CREATE INDEX IF NOT EXISTS idx_tool_embeddings_tsv
  ON tool_embeddings USING gin (tsv);

-- B-tree indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_embeddings_tool_name
  ON tool_embeddings (tool_name);

CREATE INDEX IF NOT EXISTS idx_tool_embeddings_provider
  ON tool_embeddings (provider_name);

-- Auto-generate tsvector trigger
CREATE OR REPLACE FUNCTION tool_embeddings_tsv_trigger()
RETURNS TRIGGER AS $$
BEGIN
    NEW.tsv := to_tsvector('english', COALESCE(NEW.search_text, ''));
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tool_embeddings_tsv
    BEFORE INSERT OR UPDATE OF search_text
    ON tool_embeddings
    FOR EACH ROW
    EXECUTE FUNCTION tool_embeddings_tsv_trigger();
