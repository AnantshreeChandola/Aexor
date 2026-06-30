# Feature Specification: VectorIndex

**Feature Branch**: `feat/vectorindex`
**Created**: 2026-03-14
**Status**: Draft
**Input**: Hybrid search (BM25 lexical + semantic similarity via RRF) over plan embeddings using pgvector, PostgreSQL tsvector, and ONNX Runtime

## Overview

VectorIndex provides **hybrid search** over stored plans by combining BM25 keyword ranking (PostgreSQL built-in `tsvector`/`tsquery` with `ts_rank_cd`) with semantic similarity ranking (pgvector cosine distance), fused via **Reciprocal Rank Fusion (RRF)** in a single SQL query. This follows the [Tiger Data hybrid search pattern](https://www.tigerdata.com/search) but uses only free, self-hosted PostgreSQL components — no paid cloud extensions, no external rerankers, no API calls.

Embeddings are generated locally using `all-MiniLM-L6-v2` (384 dimensions) via **ONNX Runtime** for low-latency CPU inference (~5-15ms). Both the embedding vector and a `tsvector` are pre-computed on the write path, so the search path only needs to embed the query string + execute one hybrid SQL query. An optional `intent_type` B-tree filter can further narrow candidates before RRF.

**Cost**: $0 in API/cloud fees. All inference and search is local.
**Total search latency**: p95 < 50ms, well within ContextRAG's 150ms budget.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Store Plan Embedding + Text Index (Priority: P1)

When a plan is finalized (signed), VectorIndex generates a 384-dimensional embedding and a `tsvector` from the plan's text representation and stores both alongside the plan ID and intent_type in the `plan_embeddings` table.

**Why this priority**: Without stored embeddings and text indexes, no search is possible. This is the foundational write path. Both are pre-computed on write so the search path is fast.

**Independent Test**: Insert a plan embedding via the service, then query the database directly to confirm the row exists with correct 384-dim vector, populated `tsvector`, correct `plan_id`, and `intent_type`.

**Acceptance Scenarios**:

1. **Given** a valid plan dict with `intent_type`, **When** `store_embedding(plan_id, plan_data)` is called, **Then** a row is inserted into `plan_embeddings` with a 384-dim vector, a non-empty `tsvector`, the correct `plan_id`, and the plan's `intent_type`.
2. **Given** the same plan is stored twice, **When** `store_embedding` is called with the same `plan_id`, **Then** the embedding is upserted (updated, not duplicated).
3. **Given** an empty plan dict, **When** `store_embedding` is called, **Then** a `ValueError` is raised.
4. **Given** a plan dict, **When** `store_embedding` completes, **Then** embedding generation took < 20ms (ONNX inference).
5. **Given** a plan with actions `["search_flights", "book_flight"]`, **When** stored, **Then** the `tsvector` contains lexemes for "search", "flight", "book".

---

### User Story 2 — Hybrid Search: BM25 + Semantic via RRF (Priority: P1)

ContextRAG or Planner queries VectorIndex with a query string. The service runs two parallel searches — BM25 keyword ranking via `tsvector` and cosine similarity via pgvector — then merges results using Reciprocal Rank Fusion (RRF). An optional `intent_type` parameter adds a B-tree pre-filter to both searches.

**Why this priority**: This is the primary read path — the reason VectorIndex exists. RRF combines the precision of keyword matching (exact action names, entities) with the recall of semantic search (paraphrases, related concepts). Co-P1 with storage.

**Independent Test**: Store 10 plan embeddings across 3 intent types with varied action names. Search with a query that matches one plan by keywords and another by meaning. Verify that RRF ranks both highly and that keyword-only or semantic-only matches appear lower.

**Acceptance Scenarios**:

1. **Given** 10 stored plans, **When** `search(query_text, top_k=5)` is called, **Then** at most 5 results are returned, each with `plan_id`, `intent_type`, `rrf_score`, `keyword_rank`, and `semantic_rank`.
2. **Given** a query matching one plan by exact keywords and another by semantic meaning, **When** `search` is called, **Then** both plans appear in results, with RRF ranking higher than either alone.
3. **Given** `intent_type="schedule_meeting"`, **When** `search` is called, **Then** only plans with that intent_type are considered in both BM25 and semantic legs.
4. **Given** `intent_type=None` (no filter), **When** `search` is called, **Then** all plans are considered across both search legs.
5. **Given** a query with no BM25 matches but good semantic matches, **When** `search` is called, **Then** semantic-only results are still returned (RRF degrades gracefully).
6. **Given** a query with no semantic matches but good BM25 matches, **When** `search` is called, **Then** keyword-only results are still returned.
7. **Given** `top_k=0` or negative, **When** `search` is called, **Then** a `ValueError` is raised.
8. **Given** no stored embeddings, **When** `search` is called, **Then** an empty list is returned.

---

### User Story 3 — Delete Plan Embedding (Priority: P2)

When a plan is deleted or archived, its embedding and text index are removed so it no longer appears in search results.

**Why this priority**: Necessary for data hygiene but not required for MVP search functionality.

**Independent Test**: Store an embedding, delete it, then search and confirm it no longer appears in results.

**Acceptance Scenarios**:

1. **Given** a stored embedding for `plan_id`, **When** `delete_embedding(plan_id)` is called, **Then** the row is removed from `plan_embeddings`.
2. **Given** a non-existent `plan_id`, **When** `delete_embedding` is called, **Then** no error is raised (idempotent).

---

### User Story 4 — Bulk Store Embeddings (Priority: P3)

For backfill or migration, VectorIndex can generate and store embeddings + tsvectors for a batch of plans in a single call.

**Why this priority**: Useful for bootstrapping the index from existing plans but not needed for steady-state operation.

**Independent Test**: Pass a list of 10 plan dicts, confirm all 10 rows appear in the database with correct vectors, tsvectors, and intent_type values.

**Acceptance Scenarios**:

1. **Given** a list of 10 plan dicts with IDs and intent_types, **When** `bulk_store(plans)` is called, **Then** all 10 rows are inserted into `plan_embeddings`.
2. **Given** a list containing a duplicate plan_id, **When** `bulk_store` is called, **Then** duplicates are upserted, not rejected.

---

### Edge Cases

- What happens when pgvector extension is not installed? → Service raises `VectorIndexUnavailableError` at init.
- What happens when the ONNX model file is missing? → Service raises `EmbeddingModelError` at init with the expected model path.
- How does the system handle very long plan text? → Tokenizer truncates at 256 tokens; the service passes a structured text representation (intent + action names + constraints summary) rather than raw JSON.
- What happens on concurrent upserts for the same plan_id? → PostgreSQL ON CONFLICT handles this; no application-level locking needed.
- What if intent_type is not present in the plan data? → Store with `intent_type = "unknown"` so it's still searchable via BM25 and semantic.
- What if a query matches only on BM25 or only on semantic? → RRF handles this gracefully via FULL OUTER JOIN — results from either leg are included with a partial score.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST generate 384-dimensional embeddings using `all-MiniLM-L6-v2` model via ONNX Runtime (not PyTorch, not external APIs).
- **FR-002**: System MUST generate a PostgreSQL `tsvector` from the plan's text representation (intent type + action names + constraint keywords) for BM25 ranking.
- **FR-003**: System MUST store both the embedding vector and `tsvector` in the `plan_embeddings` table, pre-computed on the write path.
- **FR-004**: System MUST support **hybrid search** using Reciprocal Rank Fusion (RRF) to merge BM25 keyword ranking (`ts_rank_cd`) and cosine similarity ranking (`<=>` operator) in a single SQL query.
- **FR-005**: System MUST support an optional `intent_type` B-tree pre-filter applied to both BM25 and semantic search legs before RRF.
- **FR-006**: System MUST support configurable `top_k` (default 5, max 50) and RRF constant `k` (default 60).
- **FR-007**: System MUST use HNSW index on the embedding column and GIN index on the `tsvector` column.
- **FR-008**: System MUST upsert on store (ON CONFLICT DO UPDATE) to prevent duplicates.
- **FR-009**: System MUST support deletion of individual embeddings by `plan_id`.
- **FR-010**: System MUST run entirely locally — no external API calls for embedding generation, keyword search, or reranking. $0 operational cost.

### Key Entities

- **PlanEmbedding**: A stored plan representation for hybrid search. Attributes: `plan_id` (FK to plans), `intent_type` (str, denormalized), `embedding` (vector(384)), `tsv` (tsvector), `search_text` (str, the text fed to both embedder and tsvector), `model_name` (str), `created_at` (timestamp).
- **HybridSearchResult**: A search result with RRF score. Attributes: `plan_id` (str), `intent_type` (str), `rrf_score` (float), `keyword_rank` (int | None), `semantic_rank` (int | None).

## Interfaces & Contracts (conform to GLOBAL_SPEC v1)

### Service Interface (library component — no HTTP routes)

VectorIndex is a **library component** (like Signer), not an HTTP service. It exposes a Python service class consumed by other components (ContextRAG, Planner).

```python
class VectorIndexService:
    async def store_embedding(self, plan_id: str, plan_data: dict) -> None:
        """Generate embedding + tsvector and store. Extracts intent_type from plan_data."""
        ...

    async def search(
        self,
        query_text: str,
        intent_type: str | None = None,  # optional B-tree pre-filter
        top_k: int = 5,
    ) -> list[HybridSearchResult]:
        """Hybrid search: BM25 + semantic via RRF. Optionally filtered by intent_type."""
        ...

    async def delete_embedding(self, plan_id: str) -> None: ...

    async def bulk_store(self, plans: list[dict]) -> int:
        """Batch store embeddings + tsvectors. Returns count stored."""
        ...
```

### Hybrid Search Query Flow (RRF)

```
Caller provides: query_text="book flight to SFO", intent_type="book_travel" (optional)

1. Embed query_text → 384-dim vector via ONNX Runtime (~5-15ms)
2. Convert query_text → tsquery via plainto_tsquery('english', query_text)
3. Execute single hybrid SQL query:

   WITH keyword AS (
     SELECT plan_id, intent_type,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, query) DESC) AS rank_kw
     FROM plan_embeddings
     WHERE tsv @@ plainto_tsquery('english', $query_text)
       AND ($intent_type IS NULL OR intent_type = $intent_type)
     LIMIT 20
   ),
   semantic AS (
     SELECT plan_id, intent_type,
            ROW_NUMBER() OVER (ORDER BY embedding <=> $query_vec) AS rank_vec
     FROM plan_embeddings
     WHERE ($intent_type IS NULL OR intent_type = $intent_type)
     ORDER BY embedding <=> $query_vec
     LIMIT 20
   )
   SELECT COALESCE(k.plan_id, s.plan_id) AS plan_id,
          COALESCE(k.intent_type, s.intent_type) AS intent_type,
          COALESCE(1.0/(60 + k.rank_kw), 0.0)
            + COALESCE(1.0/(60 + s.rank_vec), 0.0) AS rrf_score,
          k.rank_kw AS keyword_rank,
          s.rank_vec AS semantic_rank
   FROM keyword k
   FULL OUTER JOIN semantic s USING (plan_id)
   ORDER BY rrf_score DESC
   LIMIT $top_k;

4. Return list[HybridSearchResult]
```

**Why RRF?** Reciprocal Rank Fusion is score-agnostic — it doesn't require normalizing BM25 scores and cosine distances onto the same scale. It simply uses rank positions from each leg, making it robust and simple. The constant `k=60` (from the original RRF paper) dampens the influence of high-rank positions.

### Domain Models

```python
class HybridSearchResult(BaseModel):
    plan_id: str
    intent_type: str
    rrf_score: float          # combined RRF score (higher = more relevant)
    keyword_rank: int | None  # BM25 rank (None if not in keyword results)
    semantic_rank: int | None # cosine rank (None if not in semantic results)

class VectorIndexUnavailableError(Exception): ...
class EmbeddingModelError(Exception): ...
```

### Database Schema (extends existing PlanEmbeddingTable)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- plan_embeddings table updates:
--   Add: embedding vector(384)       (was commented-out 1536)
--   Add: intent_type VARCHAR(64)     (denormalized from plans table)
--   Add: tsv tsvector                (pre-computed BM25 text index)
--   Add: search_text TEXT            (the text used for embedding + tsvector)
--   Change: model_version default    ('all-MiniLM-L6-v2')
--   Remove: vector_norm              (unused)

-- Indexes:

-- B-tree index for intent_type pre-filtering:
CREATE INDEX idx_plan_embeddings_intent_type ON plan_embeddings (intent_type);

-- GIN index for BM25 full-text search:
CREATE INDEX idx_plan_embeddings_tsv ON plan_embeddings USING gin (tsv);

-- HNSW index for semantic similarity search:
CREATE INDEX idx_plan_embeddings_hnsw
  ON plan_embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

### Text Representation for Indexing

Both the embedding and `tsvector` are generated from the same `search_text` string, built from the plan:

```
search_text = "{intent_type} | {action_1} {action_2} ... | {constraint_keys} | {entity_values}"

Example:
"schedule_meeting | search_calendar check_availability send_invite | max_duration flexible_time | Alice SFO"
```

This structured representation gives BM25 good lexemes to match on (action names, entities) and gives the embedding model enough semantic signal for similarity.

Reference: docs/architecture/GLOBAL_SPEC.md (v1)

## Component Mapping

- Target: `components/VectorIndex/`
- Files expected to change:
  - `components/VectorIndex/__init__.py` — public exports
  - `components/VectorIndex/domain/__init__.py` — domain model exports
  - `components/VectorIndex/domain/models.py` — `HybridSearchResult`, error classes
  - `components/VectorIndex/service/__init__.py` — service exports
  - `components/VectorIndex/service/vector_index_service.py` — core service class, text builder, RRF query
  - `components/VectorIndex/adapters/__init__.py` — adapter exports
  - `components/VectorIndex/adapters/embedding_adapter.py` — ONNX Runtime inference wrapper (load model, tokenize, embed)
  - `components/VectorIndex/adapters/pgvector_adapter.py` — pgvector + tsvector storage, hybrid RRF query builder
  - `components/VectorIndex/adapters/text_builder.py` — plan dict → search_text conversion
  - `components/VectorIndex/tests/conftest.py` — test fixtures
  - `components/VectorIndex/tests/test_unit.py` — unit tests for service, text builder
  - `components/VectorIndex/tests/test_contract.py` — schema validation tests
  - `components/VectorIndex/tests/test_integration.py` — end-to-end hybrid search tests (RRF ranking verification)
  - `shared/database/models.py` — update `PlanEmbeddingTable` (add vector, tsv, search_text, intent_type; remove vector_norm)
  - `shared/app.py` — add VectorIndex DI wiring in lifespan
  - `shared/dependencies.py` — add `get_vector_index_service()`

## Dependencies & Risks

- **pgvector extension**: Must be installed in PostgreSQL. Docker Compose should use `pgvector/pgvector:pg16` image. Risk: local dev environments may not have it.
- **onnxruntime** (~60 MB): CPU inference runtime. Much lighter than PyTorch (~2 GB). $0 cost.
- **tokenizers** (HuggingFace): Needed for tokenizing input text before ONNX inference. Lightweight dependency.
- **ONNX model file**: The `all-MiniLM-L6-v2` ONNX export (~80 MB) fetched from HuggingFace Hub at first startup or bundled in Docker image. Risk: first-run download time.
- **No paid services**: No OpenAI API, no Cohere reranker, no Tiger Data cloud, no pgvectorscale/pg_textsearch. All components are free and self-hosted.
- **PlanEmbeddingTable migration**: Adding vector, tsv, search_text, intent_type columns; removing vector_norm. Risk: low since no production data exists yet.
- **tsvector vs BM25**: PostgreSQL's `ts_rank_cd` is not true BM25 (it's TF-IDF with cover density). For our use case (structured plan text, hundreds-thousands of documents) this is more than sufficient. Upgrade path: ParadeDB `pg_search` extension (open-source) for true BM25 if needed.
- **intent_type denormalization**: Copied from `plans` table. Low risk — plans are immutable after signing.

## Non-Functional Requirements

- Inherit baseline: Preview p95 < 800 ms; Execute p95 < 2 s; structured logs; no secrets/PII.
- **Embedding generation (ONNX)**: p95 < 15 ms per plan on CPU.
- **Hybrid search (RRF query)**: p95 < 30 ms for top-K with GIN + HNSW + B-tree indexes (up to 100K embeddings).
- **Total search latency** (embed query + hybrid SQL): p95 < 50 ms, within ContextRAG's 150 ms budget.
- **Bulk store**: p95 < 5 s for 100 plans.
- **Model loading**: One-time ONNX session initialization at startup (~200 ms), cached in service singleton.
- **Operational cost**: $0. No external API calls.
- **Observability**: Structured logging for store/search/delete operations. Log `plan_id`, `intent_type`, `top_k`, `result_count`, `keyword_hits`, `semantic_hits`, `latency_ms`, `embedding_latency_ms`. Never log raw embedding vectors.

## Open Questions

1. Should we add a minimum RRF score threshold to filter low-quality results, or leave filtering to the caller?
2. What text representation should we embed — the structured `search_text` format above, or something else?
3. Should the RRF constant `k` be configurable per query, or fixed at 60?
4. Should intent_type filtering support prefix/LIKE matching (e.g., `book_*` matches `book_flight`, `book_hotel`) in addition to exact match?

## Conformance

This work conforms to docs/architecture/GLOBAL_SPEC.md v1.
