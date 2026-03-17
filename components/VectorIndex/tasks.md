# Tasks: VectorIndex

**Created**: 2026-03-16
**Branch**: `feat/vectorindex`
**SPEC**: `specs/009-vectorindex/spec.md`
**LLD**: `components/VectorIndex/LLD.md`

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
VectorIndex is a **library component** (no HTTP routes, no API handlers) in the
Memory / Persistence Layer. It follows the same DI pattern as Signer: a factory
function called in `shared/app.py` lifespan, stored on `app.state`, with a thin
dependency getter in `shared/dependencies.py`.

---

## Phase 0: Setup and Dependencies

### T000 -- Install Python packages (LLD Section 9.1)

- **Description**: Add `onnxruntime`, `tokenizers`, and `numpy` to project
  dependencies via `uv add`. The `pgvector` Python package is already listed in
  `pyproject.toml`. Verify all versions meet LLD minimums.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/pyproject.toml` (via `uv add`, never edit directly)
- **Dependencies**: None (first task).
- **Acceptance criteria**:
  - `uv add onnxruntime` succeeds (>= 1.17.0).
  - `uv add tokenizers` succeeds (>= 0.15.0).
  - `uv add numpy` succeeds (>= 1.24.0) -- may already be a transitive dep.
  - `pgvector>=0.2.4` already present in pyproject.toml; confirmed.
  - `uv sync` resolves cleanly with no conflicts.
- **FR mapping**: Prerequisite for FR-001 (ONNX Runtime), FR-007 (pgvector).

### T001 -- Scaffold component directory structure

- **Description**: Create the VectorIndex component directory tree with empty
  `__init__.py` files matching the LLD Section 3 structure. No implementation
  code yet -- only package structure.
- **Files to create**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/domain/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/domain/models.py` (empty placeholder)
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/service/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/service/vector_index_service.py` (empty placeholder)
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/embedding_adapter.py` (empty placeholder)
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/pgvector_adapter.py` (empty placeholder)
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/text_builder.py` (empty placeholder)
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/conftest.py` (empty placeholder)
- **Dependencies**: None.
- **Acceptance criteria**:
  - `from components.VectorIndex import ...` does not raise ImportError (package importable).
  - All subdirectories have `__init__.py`.

---

## Phase 1: Domain Models (Foundation)

### T100 -- Implement domain models and error classes

- **Description**: Define `HybridSearchResult` (Pydantic BaseModel) and all
  VectorIndex error classes exactly as specified in LLD Sections 5.1 and 5.2.
  These are the foundational types consumed by all other layers.
- **Files to create/modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/domain/models.py`
- **Dependencies**: T001 (directory exists).
- **Acceptance criteria**:
  - `HybridSearchResult` has fields: `plan_id` (str), `intent_type` (str),
    `rrf_score` (float), `keyword_rank` (int | None), `semantic_rank` (int | None).
  - `VectorIndexError` is the base exception class.
  - `VectorIndexUnavailableError(VectorIndexError)` accepts `reason` string.
  - `EmbeddingModelError(VectorIndexError)` accepts `model_name` and `reason`.
  - Models validate correctly via Pydantic (test: instantiate with sample data).
- **FR mapping**: Foundation for all FRs; domain types used across service and adapters.

### T101 -- Write domain model unit tests

- **Description**: Test-first: write unit tests for HybridSearchResult model
  validation and error class instantiation before T100 implementation is complete.
  Tests should initially fail (red), then pass (green) after T100 is done.
- **Files to create**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_contract.py`
- **Dependencies**: T001 (directory exists).
- **Acceptance criteria**:
  - Test that HybridSearchResult accepts valid data and produces correct field values.
  - Test that keyword_rank and semantic_rank default to None.
  - Test that rrf_score is a float.
  - Test that VectorIndexUnavailableError and EmbeddingModelError carry expected attributes.
  - Test that error inheritance is correct (both subclass VectorIndexError).
  - All tests pass after T100 is implemented.
- **FR mapping**: Schema compliance for all FRs.

---

## Phase 2: Database Schema and Migration

### T200 -- Update PlanEmbeddingTable in shared/database/models.py

- **Description**: Update the existing `PlanEmbeddingTable` SQLAlchemy model to
  match the LLD Section 6.1 schema. Changes: (1) add `embedding` column using
  `pgvector.sqlalchemy.Vector(384)`, (2) add `intent_type` column (String(64),
  default "unknown"), (3) add `search_text` column (String, not nullable),
  (4) add `tsv` column (tsvector type), (5) change `model_version` default from
  "text-embedding-ada-002" to "all-MiniLM-L6-v2", (6) remove `vector_norm`
  column, (7) update ownership comment from PlanLibrary to VectorIndex,
  (8) add indexes: GIN on tsv, HNSW on embedding, B-tree on intent_type.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/shared/database/models.py`
- **Dependencies**: T000 (pgvector Python package available).
- **Acceptance criteria**:
  - `PlanEmbeddingTable.embedding` is `Vector(384)`.
  - `PlanEmbeddingTable.intent_type` is `String(64)`, default "unknown".
  - `PlanEmbeddingTable.search_text` is `String`, not nullable.
  - `PlanEmbeddingTable.tsv` column present for tsvector.
  - `PlanEmbeddingTable.model_version` default is "all-MiniLM-L6-v2".
  - `vector_norm` column removed.
  - `__table_args__` includes GIN index on `tsv`, HNSW index on `embedding`
    with `vector_cosine_ops` (m=16, ef_construction=64), B-tree on `intent_type`.
  - Table docstring updated to reference VectorIndex ownership.
  - Import `from pgvector.sqlalchemy import Vector` added.
- **FR mapping**: FR-003 (store embedding + tsvector), FR-007 (HNSW + GIN indexes).

### T201 -- Write migration SQL file

- **Description**: Create migration file
  `007_update_plan_embeddings_vectorindex.sql` following the LLD Section 6.2
  DDL exactly. Must be idempotent (all statements use IF NOT EXISTS / IF EXISTS
  guards). Includes: pgvector extension, column additions, column removals,
  index creation, tsvector trigger, and table/column comments.
- **Files to create**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/migrations/007_update_plan_embeddings_vectorindex.sql`
- **Dependencies**: T200 (SQLAlchemy model matches DDL).
- **Acceptance criteria**:
  - File header matches migration convention (see 006 for format).
  - `CREATE EXTENSION IF NOT EXISTS vector;` present.
  - `ALTER TABLE plan_embeddings DROP COLUMN IF EXISTS vector_norm;` present.
  - `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for intent_type, embedding,
    search_text, tsv.
  - `ALTER TABLE ... ALTER COLUMN model_version SET DEFAULT 'all-MiniLM-L6-v2';`
  - B-tree index `idx_plan_embeddings_intent_type` on intent_type.
  - GIN index `idx_plan_embeddings_tsv` on tsv.
  - HNSW index `idx_plan_embeddings_hnsw` on embedding with vector_cosine_ops
    (m=16, ef_construction=64).
  - Trigger function `plan_embeddings_tsv_trigger()` and trigger
    `trg_plan_embeddings_tsv` for auto-populating tsvector.
  - COMMENT ON TABLE/COLUMN statements present.
  - All statements idempotent.
- **FR mapping**: FR-002 (tsvector), FR-003 (storage), FR-007 (indexes), FR-008 (upsert support).

---

## Phase 3: Adapters (Bottom-Up)

### T300 -- Implement TextBuilder adapter

- **Description**: Implement `build_search_text()` and `extract_intent_type()`
  pure functions as specified in LLD Section 7.3. These are stateless helper
  functions with no external dependencies -- ideal for TDD.
  Format: `"{intent_type} | {action_1} {action_2} ... | {constraint_keys} | {entity_values}"`.
- **Files to create/modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/text_builder.py`
- **Dependencies**: T001 (directory exists).
- **Acceptance criteria**:
  - `build_search_text(plan_data)` produces the pipe-separated format from LLD.
  - Actions are extracted from `plan_data["graph"]` (each step's action/call field).
  - Constraints extracted from `plan_data["constraints"]` (keys only).
  - Entities extracted from `plan_data["intent"]["entities"]` or
    `plan_data["entities"]` (values only).
  - Returns empty string parts gracefully (no KeyError on missing keys).
  - `extract_intent_type(plan_data)` checks `plan_data["intent_type"]`,
    then `plan_data["intent"]["intent"]`, then defaults to "unknown".
  - Functions are pure (deterministic, no side effects).
- **FR mapping**: FR-002 (tsvector text source), FR-003 (search_text for embedding).

### T301 -- Write TextBuilder unit tests

- **Description**: Test-first: write comprehensive unit tests for text_builder
  before or alongside T300 implementation. Cover happy path, edge cases
  (empty plan, missing keys, missing intent_type), and the SPEC acceptance
  scenario (actions ["search_flights", "book_flight"] -> tsvector contains
  "search", "flight", "book").
- **Files to create**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_unit.py`
- **Dependencies**: T001 (directory exists).
- **Acceptance criteria**:
  - Test: plan with intent_type, graph, constraints, entities produces correct
    pipe-separated string.
  - Test: plan with actions `["search_flights", "book_flight"]` produces text
    containing "search_flights" and "book_flight".
  - Test: empty plan_data raises ValueError or returns minimal text.
  - Test: missing intent_type defaults to "unknown".
  - Test: missing graph/constraints/entities handled gracefully.
  - Test: extract_intent_type priority order (intent_type > intent.intent > "unknown").
- **FR mapping**: FR-002, FR-003 (text representation correctness).

### T302 -- Implement EmbeddingAdapter (ONNX Runtime)

- **Description**: Implement the EmbeddingAdapter class (LLD Section 7.1) that
  wraps ONNX Runtime for `all-MiniLM-L6-v2` inference. Provides `embed(text)`
  for single strings and `embed_batch(texts)` for batch inference. Handles
  model loading, tokenization (HuggingFace tokenizers), L2 normalization, and
  256-token truncation.
- **Files to create/modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/embedding_adapter.py`
- **Dependencies**: T000 (onnxruntime + tokenizers installed), T100 (EmbeddingModelError class).
- **Acceptance criteria**:
  - Constructor accepts `model_path: str`, loads ONNX InferenceSession.
  - Raises `EmbeddingModelError` if model file not found or corrupt.
  - `embed(text)` returns `list[float]` of exactly 384 dimensions.
  - Output is L2-normalized (unit vector).
  - Tokenizer truncates at 256 tokens.
  - `embed_batch(texts)` returns `list[list[float]]`, one 384-dim vector per input.
  - Same text always produces same embedding (deterministic).
  - Tokenizer loaded from `sentence-transformers/all-MiniLM-L6-v2`.
  - Logger name: "vectorindex".
- **FR mapping**: FR-001 (384-dim ONNX embeddings), FR-010 (local inference, no API calls).

### T303 -- Write EmbeddingAdapter unit tests

- **Description**: Write unit tests for EmbeddingAdapter. Use a mock ONNX
  session (mock `ort.InferenceSession`) to avoid requiring the real model file
  in CI. Test model load errors, embedding dimensions, determinism, and batch.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_unit.py` (append to existing)
- **Dependencies**: T301 (test file exists), T302 (adapter to test).
- **Acceptance criteria**:
  - Test: successful model load (mock ONNX session).
  - Test: model file not found raises EmbeddingModelError.
  - Test: `embed()` returns list of 384 floats.
  - Test: `embed_batch()` returns correct number of embeddings.
  - Test: determinism -- same input produces same output.
  - Tests use mocked ONNX session (no real model file dependency).
- **FR mapping**: FR-001 (embedding generation verification).

### T304 -- Implement PgvectorAdapter

- **Description**: Implement the PgvectorAdapter class (LLD Section 7.2) that
  handles all PostgreSQL operations: pgvector extension check, upsert with
  ON CONFLICT, hybrid RRF search query (the full CTE from SPEC/LLD), delete by
  plan_id, and bulk upsert. Uses `SharedDatabaseAdapter` and
  `@with_db_error_handling` decorator from shared infrastructure.
- **Files to create/modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/pgvector_adapter.py`
- **Dependencies**: T000, T100 (error classes), T200 (SQLAlchemy model).
- **Acceptance criteria**:
  - Constructor accepts `SharedDatabaseAdapter`.
  - `check_pgvector_extension()` queries `pg_extension` for vector extension.
  - `upsert_embedding(plan_id, intent_type, embedding, search_text)` uses
    INSERT ... ON CONFLICT (plan_id) DO UPDATE.
  - `hybrid_search(query_embedding, query_text, intent_type, top_k, rrf_k=60)`
    executes the full RRF CTE query from LLD Section 7.2 with FULL OUTER JOIN.
  - When `intent_type` is None, no WHERE filter applied.
  - When `intent_type` is provided, both BM25 and semantic legs filter by it.
  - `delete_by_plan_id(plan_id)` is idempotent (DELETE WHERE, no error if missing).
  - `bulk_upsert(rows)` handles batch inserts with ON CONFLICT.
  - All public methods decorated with `@with_db_error_handling`.
  - Logger name: "vectorindex".
- **FR mapping**: FR-003 (store), FR-004 (hybrid RRF search), FR-005 (intent_type filter),
  FR-006 (configurable top_k and rrf_k), FR-008 (upsert), FR-009 (delete).

### T305 -- Write PgvectorAdapter unit tests (mocked DB)

- **Description**: Write unit tests for PgvectorAdapter with a mocked
  SharedDatabaseAdapter. Verify SQL query construction, parameter passing,
  error handling translation, and idempotent delete behavior. These tests
  do NOT require a running database.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_unit.py` (append)
- **Dependencies**: T304 (adapter to test).
- **Acceptance criteria**:
  - Test: upsert_embedding calls session.execute with correct SQL/params.
  - Test: hybrid_search constructs CTE query with BM25 and semantic legs.
  - Test: intent_type=None omits WHERE filter.
  - Test: intent_type="book_travel" includes WHERE clause in both legs.
  - Test: delete_by_plan_id calls DELETE with correct plan_id.
  - Test: database errors translated to DatabaseConnectionError.
  - Test: bulk_upsert handles multiple rows.
- **FR mapping**: FR-004 (RRF query correctness), FR-005 (filter), FR-008 (upsert), FR-009 (delete).

---

## Phase 4: Service Layer (Orchestration)

### T400 -- Implement VectorIndexService

- **Description**: Implement the `VectorIndexService` class (LLD Section 4.1)
  that orchestrates TextBuilder, EmbeddingAdapter, and PgvectorAdapter.
  Provides `store_embedding()`, `search()`, `delete_embedding()`, and
  `bulk_store()`. Includes input validation, structured logging (LLD
  Section 10.1), and latency tracking. No HTTP routes -- this is a library
  service consumed via DI.
- **Files to create/modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/service/vector_index_service.py`
- **Dependencies**: T100 (domain models), T300 (TextBuilder), T302 (EmbeddingAdapter), T304 (PgvectorAdapter).
- **Acceptance criteria**:
  - Constructor accepts `EmbeddingAdapter` and `PgvectorAdapter`.
  - `store_embedding(plan_id, plan_data)`:
    - Raises ValueError if plan_data is empty or None.
    - Calls TextBuilder.build_search_text and extract_intent_type.
    - Calls EmbeddingAdapter.embed(search_text).
    - Calls PgvectorAdapter.upsert_embedding.
    - Logs "embedding_stored" with plan_id, intent_type, embedding_latency_ms, total_latency_ms.
  - `search(query_text, intent_type=None, top_k=5)`:
    - Raises ValueError if top_k < 1 or > 50, or query_text is empty.
    - Calls EmbeddingAdapter.embed(query_text).
    - Calls PgvectorAdapter.hybrid_search.
    - Maps raw rows to list[HybridSearchResult].
    - Logs "hybrid_search" with result_count, keyword_hits, semantic_hits, latencies.
    - Returns empty list if no results (not an error).
  - `delete_embedding(plan_id)`: delegates to PgvectorAdapter, logs "embedding_deleted".
  - `bulk_store(plans)`:
    - Raises ValueError if plans list is empty.
    - Calls EmbeddingAdapter.embed_batch for all search texts.
    - Calls PgvectorAdapter.bulk_upsert.
    - Returns count stored.
  - Logger name: "vectorindex".
  - Never logs raw embedding vectors or full plan content.
- **FR mapping**: FR-001 through FR-010 (service orchestrates all FRs).

### T401 -- Implement factory function (create_vector_index_service)

- **Description**: Implement the `create_vector_index_service(db_adapter)`
  factory function (LLD Section 4.3) in the same service module. This function
  creates EmbeddingAdapter (loading ONNX model), creates PgvectorAdapter,
  checks pgvector extension availability, and returns a configured
  VectorIndexService. Reads `ONNX_MODEL_PATH` env var, with fallback to
  `~/.cache/vectorindex/` for model auto-download.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/service/vector_index_service.py` (append factory function)
- **Dependencies**: T400 (service class), T302 (EmbeddingAdapter), T304 (PgvectorAdapter).
- **Acceptance criteria**:
  - Accepts `SharedDatabaseAdapter` as input.
  - Reads `ONNX_MODEL_PATH` env var, defaults to `~/.cache/vectorindex/model.onnx`.
  - Creates EmbeddingAdapter with model path.
  - Creates PgvectorAdapter with db_adapter.
  - Calls `PgvectorAdapter.check_pgvector_extension()`.
  - Raises `VectorIndexUnavailableError` if pgvector not installed.
  - Raises `EmbeddingModelError` if ONNX model cannot load.
  - Returns configured `VectorIndexService`.
  - Logs "vector_index_service_created" on success.
- **FR mapping**: FR-001 (ONNX model loading), FR-010 (local-only).

### T402 -- Write VectorIndexService unit tests

- **Description**: Write comprehensive unit tests for VectorIndexService with
  fully mocked adapters (mock EmbeddingAdapter, mock PgvectorAdapter). Cover
  all four operations, input validation, error propagation, and logging.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_unit.py` (append)
- **Dependencies**: T400 (service to test), T301 (test file exists).
- **Acceptance criteria**:
  - Test: store_embedding happy path -- adapters called in correct order.
  - Test: store_embedding with empty plan_data raises ValueError.
  - Test: store_embedding with same plan_id twice (upsert verified via mock).
  - Test: search happy path -- returns list[HybridSearchResult].
  - Test: search with top_k=0 raises ValueError.
  - Test: search with top_k=-1 raises ValueError.
  - Test: search with top_k=51 raises ValueError.
  - Test: search with empty query_text raises ValueError.
  - Test: search with no results returns empty list.
  - Test: search with intent_type filter passes it to adapter.
  - Test: search with intent_type=None passes None to adapter.
  - Test: delete_embedding delegates to adapter.
  - Test: delete_embedding for non-existent plan_id (no error).
  - Test: bulk_store with empty list raises ValueError.
  - Test: bulk_store happy path returns count.
  - Test: factory function with missing model raises EmbeddingModelError.
  - Test: factory function with missing pgvector raises VectorIndexUnavailableError.
- **FR mapping**: All FRs via service layer (FR-001 through FR-010).

---

## Phase 5: DI Wiring and Public Exports

### T500 -- Add VectorIndex to shared/app.py lifespan

- **Description**: Add VectorIndex initialization to the application lifespan
  in `shared/app.py`, following the Signer pattern. Import the factory function,
  call it with the shared db adapter, and store the service on `app.state`.
  Handle graceful degradation: if VectorIndex fails to initialize (pgvector
  missing, ONNX model missing), log a warning and set
  `app.state.vector_index_service = None` rather than crashing the application.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`
- **Dependencies**: T401 (factory function).
- **Acceptance criteria**:
  - Import `create_vector_index_service` inside lifespan (lazy import pattern).
  - Call `create_vector_index_service(db)` after shared DB is created.
  - Store result on `app.state.vector_index_service`.
  - Wrap initialization in try/except for `VectorIndexUnavailableError` and
    `EmbeddingModelError` -- log warning, set `app.state.vector_index_service = None`.
  - Existing services unaffected (no functional changes to other initialization).
  - Comment follows existing code style.
- **FR mapping**: Infrastructure prerequisite for all FRs.

### T501 -- Add get_vector_index_service to shared/dependencies.py

- **Description**: Add a `get_vector_index_service()` dependency function to
  `shared/dependencies.py` following the existing pattern. This allows
  future consuming components (ContextRAG, Planner, PlanWriter) to inject
  VectorIndexService via FastAPI Depends().
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py`
- **Dependencies**: T500 (app.state attribute exists).
- **Acceptance criteria**:
  - Function signature: `def get_vector_index_service(request: Request) -> Any:`.
  - Returns `request.app.state.vector_index_service`.
  - Docstring follows existing convention.
  - Import `Any` and `Request` already present (no new imports needed).
- **FR mapping**: Infrastructure prerequisite for consumer integration.

### T502 -- Populate component __init__.py with public exports

- **Description**: Update the top-level `components/VectorIndex/__init__.py`
  to export public symbols, following the Signer pattern. Export the service
  class, factory function, domain models, and error classes.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/domain/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/service/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/adapters/__init__.py`
- **Dependencies**: T100 (domain models), T400 (service), T300/T302/T304 (adapters).
- **Acceptance criteria**:
  - `from components.VectorIndex import VectorIndexService, create_vector_index_service` works.
  - `from components.VectorIndex import HybridSearchResult` works.
  - `from components.VectorIndex import VectorIndexError, VectorIndexUnavailableError, EmbeddingModelError` works.
  - `from components.VectorIndex.adapters import TextBuilder, EmbeddingAdapter, PgvectorAdapter` works.
  - `__all__` lists defined in each `__init__.py`.
- **FR mapping**: Clean API surface for consumer components.

---

## Phase 6: Observability and Safety

### T600 -- Implement structured logging in service layer

- **Description**: Verify and finalize structured logging in VectorIndexService
  per LLD Section 10.1. Each operation should emit a structured log event with
  the specified fields. Ensure no embedding vectors, full plan content, or full
  search_text appear in log output.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/service/vector_index_service.py` (verify logging, if not done in T400)
- **Dependencies**: T400 (service implemented).
- **Acceptance criteria**:
  - `store_embedding` logs `"embedding_stored"` with `plan_id`, `intent_type`,
    `embedding_latency_ms`, `total_latency_ms`.
  - `search` logs `"hybrid_search"` with `intent_type`, `top_k`, `result_count`,
    `keyword_hits`, `semantic_hits`, `embedding_latency_ms`, `search_latency_ms`,
    `total_latency_ms`.
  - `delete_embedding` logs `"embedding_deleted"` with `plan_id`.
  - Error paths log `"embedding_store_failed"` or `"hybrid_search_failed"` with
    `plan_id` (where applicable) and `error`.
  - No raw embedding vectors (384 floats) in logs.
  - No full plan_data dicts in logs.
  - Logger name is "vectorindex".
- **FR mapping**: GLOBAL_SPEC Section 3 (observability), LLD Section 10.1.

### T601 -- Write observability and log safety tests

- **Description**: Write tests that capture log output and verify (1) expected
  structured log fields are present and (2) no forbidden data (embedding vectors,
  raw plan content) appears in logs.
- **Files to create**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_observability.py`
- **Dependencies**: T400 (service), T600 (logging finalized).
- **Acceptance criteria**:
  - Test: store_embedding produces log record with expected fields.
  - Test: search produces log record with expected fields.
  - Test: no 384-element list (embedding vector) appears in any log record.
  - Test: no raw plan_data dict appears in any log record.
  - Tests use `caplog` or mock logger to capture output.
- **FR mapping**: GLOBAL_SPEC Section 3 (no secrets/PII), LLD Section 10.1.

---

## Phase 7: Test Fixtures and Integration Tests

### T700 -- Implement test fixtures (conftest.py)

- **Description**: Create shared test fixtures for VectorIndex tests. Include
  a mock EmbeddingAdapter (returns deterministic 384-dim vectors), sample plan
  dicts (with varied intent_types and actions), and a mock PgvectorAdapter.
  These fixtures eliminate the need for real ONNX models or pgvector in CI.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/conftest.py`
- **Dependencies**: T100 (domain models), T302 (EmbeddingAdapter interface), T304 (PgvectorAdapter interface).
- **Acceptance criteria**:
  - `mock_embedding_adapter` fixture: returns a mock that produces deterministic
    384-dim vectors (e.g., based on hash of input text).
  - `mock_pgvector_adapter` fixture: returns a mock with upsert, search, delete stubs.
  - `sample_plan_data` fixture: realistic plan dict with intent_type, graph,
    constraints, intent/entities.
  - `sample_plans_batch` fixture: list of 10 plan dicts with 3 different intent_types.
  - `vector_index_service` fixture: VectorIndexService with mocked adapters.
  - Fixtures follow Signer conftest.py pattern (session-scoped where appropriate).
- **FR mapping**: Test infrastructure for all acceptance scenarios.

### T701 -- Write integration test stubs

- **Description**: Create integration test file with test stubs for end-to-end
  flows that require a real PostgreSQL with pgvector extension. These tests
  are marked with `@pytest.mark.integration` (or `@pytest.mark.skipif`) so
  they only run in environments with pgvector available (Docker Compose).
  Covers: store + search round-trip, RRF ranking verification, intent_type
  filtering, delete + re-search, bulk store.
- **Files to create**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_integration.py`
- **Dependencies**: T400 (service), T700 (fixtures).
- **Acceptance criteria**:
  - Test stub: store 10 plans, search with query, verify top_k results returned.
  - Test stub: verify RRF ranking (keyword match + semantic match both appear).
  - Test stub: verify intent_type filter restricts results.
  - Test stub: store, delete, search -- deleted plan not in results.
  - Test stub: bulk_store 10 plans, verify all searchable.
  - Test stub: search with no stored data returns empty list.
  - All integration tests marked for skip when pgvector is unavailable.
  - Tests document expected behavior per SPEC acceptance scenarios.
- **FR mapping**: FR-001 through FR-009 end-to-end verification.

### T702 -- Write contract tests (SPEC acceptance scenarios)

- **Description**: Write contract tests that map directly to SPEC acceptance
  scenarios. These tests use mocked adapters (no DB/ONNX required) and verify
  the service contract: inputs, outputs, error conditions, and idempotency
  behavior as specified in the SPEC user stories.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_contract.py` (append to existing from T101)
- **Dependencies**: T400 (service), T700 (fixtures).
- **Acceptance criteria**:
  - **US1-AC1**: store_embedding with valid plan_data succeeds (mock verifies
    upsert called with 384-dim vector, non-empty tsvector, correct plan_id, intent_type).
  - **US1-AC2**: store_embedding called twice with same plan_id -- upsert called
    twice (no error, second call updates).
  - **US1-AC3**: store_embedding with empty plan_data raises ValueError.
  - **US2-AC1**: search returns at most top_k results, each with plan_id,
    intent_type, rrf_score, keyword_rank, semantic_rank.
  - **US2-AC5**: search with no BM25 matches returns semantic-only results
    (keyword_rank=None, semantic_rank populated).
  - **US2-AC7**: search with top_k=0 raises ValueError.
  - **US2-AC8**: search with no stored embeddings returns empty list.
  - **US3-AC1**: delete_embedding removes the row (mock verified).
  - **US3-AC2**: delete_embedding for non-existent plan_id -- no error.
  - **US4-AC1**: bulk_store with 10 plans returns count 10.
- **FR mapping**: All SPEC acceptance criteria.

---

## Phase 8: Determinism Verification

### T800 -- Verify determinism across the stack

- **Description**: Write specific tests confirming determinism at each layer
  as documented in LLD Section 13.3. Same plan dict must produce same
  search_text, same embedding, same tsvector, and same RRF scores.
- **Files to modify**:
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/VectorIndex/tests/test_unit.py` (append)
- **Dependencies**: T300, T302, T400 (all layers implemented).
- **Acceptance criteria**:
  - Test: `build_search_text(plan)` called twice with same plan produces identical output.
  - Test: `embed(text)` called twice with same text produces identical 384-dim vector
    (mocked ONNX -- determinism of the adapter contract).
  - Test: same search inputs to service.search() produce same result ordering
    (mocked adapter returns consistent data).
  - Tests explicitly document determinism requirement from LLD Section 13.3.
- **FR mapping**: GLOBAL_SPEC Section 2.0 (deterministic inputs).

---

## Task Summary

- **Total Tasks**: 23
- **Phase 0 (Setup)**: T000-T001 (2 tasks)
- **Phase 1 (Domain)**: T100-T101 (2 tasks)
- **Phase 2 (Database)**: T200-T201 (2 tasks)
- **Phase 3 (Adapters)**: T300-T305 (6 tasks)
- **Phase 4 (Service)**: T400-T402 (3 tasks)
- **Phase 5 (DI Wiring)**: T500-T502 (3 tasks)
- **Phase 6 (Observability)**: T600-T601 (2 tasks)
- **Phase 7 (Tests)**: T700-T702 (3 tasks)
- **Phase 8 (Determinism)**: T800 (1 task -- but critical)

## Task Dependency Graph

```
T000 (packages) ──┬── T200 (SQLAlchemy model) ── T201 (migration SQL)
                   │
T001 (scaffold) ──┬── T100 (domain models) ── T101 (domain tests)
                   │
                   ├── T300 (TextBuilder) ── T301 (TextBuilder tests)
                   │
                   ├── T302 (EmbeddingAdapter) ── T303 (Embedding tests)
                   │        depends on: T000, T100
                   │
                   ├── T304 (PgvectorAdapter) ── T305 (Pgvector tests)
                   │        depends on: T000, T100, T200
                   │
                   └── T400 (Service) ── T401 (factory) ── T402 (service tests)
                            depends on: T100, T300, T302, T304
                                          │
                                 ┌────────┴────────┐
                                 │                  │
                           T500 (app.py)      T502 (exports)
                           T501 (deps.py)
                                 │
                           T600 (logging)
                           T601 (log tests)
                                 │
                           T700 (fixtures)
                           T701 (integration stubs)
                           T702 (contract tests)
                                 │
                           T800 (determinism)
```

## Dependencies

**External** (from LLD Section 9.1):
- `onnxruntime >= 1.17.0` -- CPU inference for all-MiniLM-L6-v2
- `tokenizers >= 0.15.0` -- HuggingFace tokenizer for text preprocessing
- `numpy >= 1.24.0` -- Array operations for embedding normalization
- `pgvector >= 0.2.4` -- Already in pyproject.toml; SQLAlchemy Vector type
- `pydantic >= 2.0` -- Already in pyproject.toml
- `sqlalchemy >= 2.0` -- Already in pyproject.toml

**Infrastructure**:
- PostgreSQL 16 with pgvector extension installed
- ONNX model file `all-MiniLM-L6-v2` (~80 MB) -- auto-downloaded on first run

**Internal** (from LLD Section 9.2):
- `shared/database/adapter.py` -- `SharedDatabaseAdapter` for PostgreSQL connections
- `shared/database/error_handler.py` -- `@with_db_error_handling` decorator, `DatabaseConnectionError`
- `shared/database/models.py` -- `PlanEmbeddingTable` (updated in T200)
- `shared/app.py` -- Lifespan initialization (updated in T500)
- `shared/dependencies.py` -- DI getter (updated in T501)

## Architectural Considerations

**Blast Radius** (LLD Section 13.1):
- If VectorIndex fails: ContextRAG and Planner fall back to structured queries
  via PlanLibrary (intent_type exact match). System continues operating with
  degraded search quality.
- Containment: VectorIndex only interacts with PostgreSQL (pgvector) and ONNX
  (in-process). No Redis, no external APIs. Database connection issues in
  VectorIndex do not cascade to other components (shared pool with isolation).
- Recovery: Restart application process. ONNX model reloads (~200ms).
  Embeddings persisted in PostgreSQL survive restarts.

**Determinism** (LLD Section 13.3):
- `build_search_text()`: Pure function, same plan dict produces same output.
- ONNX embedding: Same text produces same 384-dim vector (frozen model).
- tsvector: Same search_text produces same tsvector (PostgreSQL 'english' dictionary).
- RRF scores: Same data produces same ranks produces same scores.

**Fault Isolation** (LLD Section 13.2):
- pgvector extension missing: Detected at startup, VectorIndex set to None,
  application continues without vector search (graceful degradation).
- ONNX model missing: Detected at startup, same degradation path.
- Database connection failure: `@with_db_error_handling` catches and translates
  to `DatabaseConnectionError`. Callers (ContextRAG, Planner, PlanWriter)
  catch this and degrade gracefully.

**Idempotency** (LLD Section 7.6):
- store_embedding: Idempotent via INSERT ... ON CONFLICT (plan_id) DO UPDATE.
- delete_embedding: Idempotent -- DELETE WHERE is a no-op if row missing.
- search: Read-only, inherently idempotent.
- bulk_store: Idempotent via upsert for each row.
