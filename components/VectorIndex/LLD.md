# VectorIndex — Low-Level Design

**Component**: `components/VectorIndex/`
**Layer**: Memory / Persistence (Layer 4)
**Spec**: `specs/009-vectorindex/spec.md`
**Created**: 2026-03-14
**Status**: Draft

---

## 1. Purpose & Scope

VectorIndex provides **hybrid search** over stored plans by combining BM25 keyword ranking (PostgreSQL built-in `tsvector`/`tsquery` with `ts_rank_cd`) with semantic similarity ranking (pgvector cosine distance), fused via **Reciprocal Rank Fusion (RRF)** in a single SQL query. Embeddings are generated locally using `all-MiniLM-L6-v2` (384 dimensions) via ONNX Runtime for low-latency CPU inference (~5-15ms).

### Boundaries

- **In scope**: Store plan embeddings + tsvectors, hybrid search (BM25 + semantic via RRF), delete embeddings, bulk store
- **Out of scope**: HTTP API routes (library component), embedding non-plan data (facts, preferences), real-time index updates from PlanLibrary (PlanWriter triggers this), model fine-tuning, external embedding APIs

### Layer Placement

VectorIndex is a **Memory / Persistence Layer** component (Layer 4). It is called by:
- **PlanWriter** (to store embeddings when plans are persisted)
- **ContextRAG** (to search for similar past plans during context assembly)
- **Planner** (to find exemplar plans for few-shot prompting)

It does NOT expose user-facing Preview/Execute wrappers. It operates as a library service via dependency injection, similar to Signer.

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v2.2 (2026-03-05) | §3 NFRs (vector search < 100ms), §7 Context Policy |
| Project_HLD.md | v5.1 | §4 Memory Layer (VectorIndex active) |
| MODULAR_ARCHITECTURE.md | v1.2 | §3 Table Ownership (vectors table), §4 Component Dependency Matrix |
| SHARED_INFRASTRUCTURE.md | v1.0.0 | §1.1 PostgreSQL, §1.2 Users Table |

**Deviations from existing architecture docs**:
- ~~MODULAR_ARCHITECTURE §3 listed `vectors` table with 1536-dim OpenAI embeddings~~ — **Resolved in v1.5**: Updated to `plan_embeddings` table with 384-dim local ONNX embeddings.
- ~~MODULAR_ARCHITECTURE §4 listed OpenAI API as VectorIndex external dependency~~ — **Resolved in v1.5**: Updated to local ONNX Runtime.
- ~~Project_HLD.md §12 marked VectorIndex as deferred~~ — **Resolved in v5.1**: VectorIndex is now active with hybrid BM25 + semantic search.

---

## 3. Architecture Overview

### Component Structure

```
components/VectorIndex/
├── __init__.py
├── domain/
│   ├── __init__.py
│   └── models.py              # HybridSearchResult, error classes
├── service/
│   ├── __init__.py
│   └── vector_index_service.py # VectorIndexService (store, search, delete, bulk_store)
├── adapters/
│   ├── __init__.py
│   ├── embedding_adapter.py   # ONNX Runtime inference (tokenize, embed)
│   ├── pgvector_adapter.py    # pgvector + tsvector storage, hybrid RRF query
│   └── text_builder.py        # plan dict → search_text conversion
├── tests/
│   ├── __init__.py
│   ├── conftest.py            # Test fixtures (mock ONNX, sample plans)
│   ├── test_unit.py           # Service + text_builder unit tests
│   ├── test_contract.py       # Schema compliance tests
│   ├── test_integration.py    # End-to-end hybrid search tests
│   └── test_observability.py  # Log safety tests
├── diagrams/
│   └── flow.md                # Mermaid flow diagrams
└── LLD.md
```

### Blast Radius Analysis

- **Failure mode**: If VectorIndex is unavailable, ContextRAG and Planner cannot retrieve similar plans → they fall back to structured queries (intent_type exact match via PlanLibrary) or proceed without exemplars
- **Containment**: VectorIndex interacts only with PostgreSQL (pgvector). ONNX model is loaded in-process. No Redis, no external APIs.
- **No cascading failures**: VectorIndex uses the shared database adapter (connection pool). A pgvector extension failure does not affect non-vector PostgreSQL queries from other components.
- **Recovery**: Restart application process. ONNX model reloads (~200ms). Embeddings are persisted in PostgreSQL.

### Isolation Strategy

VectorIndex is **stateful** (owns `plan_embeddings` table) but its runtime state is minimal:
- ONNX inference session (loaded once at startup, immutable)
- Database connection via shared adapter (no component-owned connections)
- No Redis, no external API calls, no background tasks, no queues

---

## 4. Interfaces

### 4.1 Service Interface

```python
from components.VectorIndex.domain.models import HybridSearchResult


class VectorIndexService:
    """Hybrid search over plan embeddings (BM25 + semantic via RRF)."""

    def __init__(
        self,
        embedding_adapter: EmbeddingAdapter,
        pgvector_adapter: PgvectorAdapter,
    ) -> None:
        """Initialize with adapters for embedding generation and DB storage."""

    async def store_embedding(self, plan_id: str, plan_data: dict) -> None:
        """
        Generate embedding + tsvector from plan_data and store in plan_embeddings.

        Extracts intent_type from plan_data. Upserts on conflict (plan_id).

        Args:
            plan_id: ULID plan identifier.
            plan_data: Plan dictionary (must contain intent_type or default to "unknown").

        Raises:
            ValueError: If plan_data is empty or None.
            DatabaseConnectionError: If database is unavailable.
        """

    async def search(
        self,
        query_text: str,
        intent_type: str | None = None,
        top_k: int = 5,
    ) -> list[HybridSearchResult]:
        """
        Hybrid search: BM25 + semantic via RRF, optionally filtered by intent_type.

        Args:
            query_text: Natural-language query string.
            intent_type: Optional B-tree pre-filter (exact match).
            top_k: Maximum results (default 5, max 50).

        Returns:
            List of HybridSearchResult sorted by rrf_score descending.

        Raises:
            ValueError: If top_k < 1 or > 50, or query_text is empty.
            DatabaseConnectionError: If database is unavailable.
        """

    async def delete_embedding(self, plan_id: str) -> None:
        """
        Delete embedding for a plan. Idempotent — no error if not found.

        Args:
            plan_id: ULID plan identifier.

        Raises:
            DatabaseConnectionError: If database is unavailable.
        """

    async def bulk_store(self, plans: list[dict]) -> int:
        """
        Batch generate embeddings + tsvectors and store. Upserts duplicates.

        Args:
            plans: List of plan dicts, each with "plan_id" key.

        Returns:
            Count of rows stored.

        Raises:
            ValueError: If plans list is empty.
            DatabaseConnectionError: If database is unavailable.
        """
```

### 4.2 Consumer Contracts

#### PlanWriter → VectorIndex (store on plan persist)

```python
# PlanWriter calls after storing plan in PlanLibrary:
await vector_index.store_embedding(
    plan_id=plan.plan_id,
    plan_data=plan.canonical_json,
)
```

**Input**: `plan_id` (ULID string), `plan_data` (dict with `intent_type`, `graph`, `constraints`, `intent`).
**Output**: None (void — embedding stored).
**Errors to handle**: `ValueError` (empty plan), `DatabaseConnectionError`.

#### ContextRAG → VectorIndex (search for context assembly)

```python
# ContextRAG calls during context assembly:
results: list[HybridSearchResult] = await vector_index.search(
    query_text="schedule meeting with Alice next week",
    intent_type="schedule_meeting",  # from parsed Intent
    top_k=3,
)
# ContextRAG uses plan_ids to fetch full plans from PlanLibrary
for result in results:
    plan = await plan_library.get_plan(result.plan_id)
    evidence_items.append(build_exemplar_evidence(plan, result.rrf_score))
```

**Input**: Query text (from intent description), optional intent_type, top_k.
**Output**: `list[HybridSearchResult]` with `plan_id`, `intent_type`, `rrf_score`, `keyword_rank`, `semantic_rank`.
**Errors to handle**: `ValueError` (bad top_k), `DatabaseConnectionError`. Empty results are not errors.

#### Planner → VectorIndex (find exemplar plans)

```python
# Planner calls for few-shot exemplars:
exemplars = await vector_index.search(
    query_text=intent.description,
    intent_type=intent.intent_type,
    top_k=2,
)
```

**Input**: Same as ContextRAG consumer contract.
**Output**: Same as ContextRAG consumer contract.
**Errors to handle**: Same. Planner proceeds without exemplars if search returns empty.

### 4.3 Factory Function

```python
def create_vector_index_service(db_adapter: SharedDatabaseAdapter) -> VectorIndexService:
    """
    Create VectorIndexService with ONNX embedding adapter and pgvector adapter.

    Reads:
        ONNX_MODEL_PATH: Path to all-MiniLM-L6-v2 ONNX model (optional, auto-downloads if missing).

    Args:
        db_adapter: Shared database adapter for PostgreSQL connections.

    Returns:
        Configured VectorIndexService.

    Raises:
        EmbeddingModelError: If ONNX model cannot be loaded.
        VectorIndexUnavailableError: If pgvector extension is not installed.
    """
```

This function is called once during application lifespan startup in `shared/app.py` and stored on `app.state.vector_index_service`.

---

## 5. Data Model

### 5.1 Domain Entities

#### HybridSearchResult

```python
from pydantic import BaseModel, Field


class HybridSearchResult(BaseModel):
    """Result from hybrid BM25 + semantic search via RRF."""

    plan_id: str = Field(description="ULID plan identifier")
    intent_type: str = Field(description="Plan intent type (denormalized)")
    rrf_score: float = Field(description="Combined RRF score (higher = more relevant)")
    keyword_rank: int | None = Field(
        default=None,
        description="BM25 rank position (None if not in keyword results)",
    )
    semantic_rank: int | None = Field(
        default=None,
        description="Cosine similarity rank position (None if not in semantic results)",
    )
```

### 5.2 Error Classes

```python
class VectorIndexError(Exception):
    """Base error for VectorIndex component."""


class VectorIndexUnavailableError(VectorIndexError):
    """Raised when pgvector extension is not available."""

    def __init__(self, reason: str = "pgvector extension not installed") -> None:
        self.reason = reason
        super().__init__(f"VectorIndex unavailable: {reason}")


class EmbeddingModelError(VectorIndexError):
    """Raised when ONNX embedding model cannot be loaded."""

    def __init__(self, model_name: str, reason: str = "") -> None:
        self.model_name = model_name
        self.reason = reason
        super().__init__(f"Embedding model error ({model_name}): {reason}")
```

### 5.3 Note on user_id

VectorIndex's `plan_embeddings` table references `plans.plan_id` (FK). The `plans` table is owned by PlanLibrary and does not have a direct `user_id` column (plans are system-level, not per-user). If per-user filtering is needed in future, it can be achieved by JOINing through `plans` → plan metadata. For MVP, VectorIndex search is not user-scoped.

**Deviation**: Unlike other Memory Layer components, `plan_embeddings` does not have a `user_id` FK. This is intentional — plans are system resources shared across users. The consent tier enforcement happens at the ContextRAG level (which decides whether to include plan exemplars based on the user's tier).

---

## 6. Database Schema & Migrations

### 6.1 Updated PlanEmbeddingTable (SQLAlchemy model)

Updates to `shared/database/models.py`:

```python
from pgvector.sqlalchemy import Vector

class PlanEmbeddingTable(Base):
    """
    Plan embeddings table - stores vector embeddings and tsvector for hybrid search.

    Owned by VectorIndex component. Requires pgvector extension.
    """

    __tablename__ = "plan_embeddings"

    embedding_id = Column(
        SQLAlchemy_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    plan_id = Column(
        String(26),
        ForeignKey("plans.plan_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # One embedding per plan
    )
    intent_type = Column(String(64), nullable=False, default="unknown")
    embedding = Column(Vector(384), nullable=False)
    search_text = Column(String, nullable=False)
    # tsv is auto-generated from search_text via trigger or application code
    tsv = Column(
        # tsvector column — populated on insert/update
    )
    model_version = Column(String(32), nullable=False, default="all-MiniLM-L6-v2")
    created_at = Column(DateTime, nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_plan_embeddings_plan_id", plan_id),
        Index("idx_plan_embeddings_intent_type", intent_type),
        Index("idx_plan_embeddings_tsv", tsv, postgresql_using="gin"),
        Index(
            "idx_plan_embeddings_hnsw",
            embedding,
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_plan_embeddings_created_at", created_at),
    )
```

### 6.2 DDL (Complete)

```sql
-- Requires pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Drop old columns if migrating (vector_norm no longer needed)
ALTER TABLE plan_embeddings DROP COLUMN IF EXISTS vector_norm;

-- Add new columns
ALTER TABLE plan_embeddings ADD COLUMN IF NOT EXISTS intent_type VARCHAR(64) NOT NULL DEFAULT 'unknown';
ALTER TABLE plan_embeddings ADD COLUMN IF NOT EXISTS embedding vector(384) NOT NULL;
ALTER TABLE plan_embeddings ADD COLUMN IF NOT EXISTS search_text TEXT NOT NULL DEFAULT '';
ALTER TABLE plan_embeddings ADD COLUMN IF NOT EXISTS tsv tsvector;

-- Update model_version default
ALTER TABLE plan_embeddings ALTER COLUMN model_version SET DEFAULT 'all-MiniLM-L6-v2';

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

-- Auto-update tsvector on insert/update
CREATE OR REPLACE FUNCTION plan_embeddings_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.tsv := to_tsvector('english', NEW.search_text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_plan_embeddings_tsv
  BEFORE INSERT OR UPDATE ON plan_embeddings
  FOR EACH ROW EXECUTE FUNCTION plan_embeddings_tsv_trigger();

-- Table comment
COMMENT ON TABLE plan_embeddings IS 'Plan embeddings for hybrid search (BM25 + semantic). Owned by VectorIndex.';
COMMENT ON COLUMN plan_embeddings.embedding IS '384-dim vector from all-MiniLM-L6-v2 via ONNX Runtime';
COMMENT ON COLUMN plan_embeddings.tsv IS 'tsvector auto-generated from search_text for BM25 ranking';
COMMENT ON COLUMN plan_embeddings.search_text IS 'Structured text: intent_type | actions | constraints | entities';
```

### 6.3 Migration File Specification

- **File**: `migrations/007_update_plan_embeddings_vectorindex.sql`
- **Sequence**: Next after `006_create_pluginregistry_tables.sql`
- **DDL**: Must match SQLAlchemy model in §6.1 exactly
- **Dependencies**: pgvector extension must be installed (`CREATE EXTENSION IF NOT EXISTS vector`)
- **Idempotent**: All statements use `IF NOT EXISTS` / `IF EXISTS` guards

---

## 7. Adapters

### 7.1 EmbeddingAdapter (ONNX Runtime)

```python
# adapters/embedding_adapter.py
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


class EmbeddingAdapter:
    """Generate 384-dim embeddings using all-MiniLM-L6-v2 via ONNX Runtime."""

    def __init__(self, model_path: str) -> None:
        """
        Load ONNX model and tokenizer.

        Args:
            model_path: Path to ONNX model file.

        Raises:
            EmbeddingModelError: If model cannot be loaded.
        """
        self._session = ort.InferenceSession(model_path)
        self._tokenizer = Tokenizer.from_pretrained(
            "sentence-transformers/all-MiniLM-L6-v2"
        )

    def embed(self, text: str) -> list[float]:
        """
        Generate 384-dim embedding for a text string.

        Args:
            text: Input text (will be truncated at 256 tokens).

        Returns:
            List of 384 floats (L2-normalized embedding).
        """

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts in a single ONNX session call.

        Args:
            texts: List of input strings.

        Returns:
            List of 384-dim embeddings.
        """
```

**Model source**: `sentence-transformers/all-MiniLM-L6-v2` ONNX export from HuggingFace Hub.
**Download strategy**: Auto-download on first startup if not found at `ONNX_MODEL_PATH`. Cache in `~/.cache/vectorindex/`.

### 7.2 PgvectorAdapter

```python
# adapters/pgvector_adapter.py
from shared.database.adapter import SharedDatabaseAdapter
from shared.database.error_handler import with_db_error_handling


class PgvectorAdapter:
    """PostgreSQL pgvector + tsvector storage and hybrid RRF search."""

    def __init__(self, db_adapter: SharedDatabaseAdapter) -> None:
        self._db = db_adapter

    @with_db_error_handling
    async def check_pgvector_extension(self) -> bool:
        """Check if pgvector extension is installed."""

    @with_db_error_handling
    async def upsert_embedding(
        self,
        plan_id: str,
        intent_type: str,
        embedding: list[float],
        search_text: str,
    ) -> None:
        """
        Insert or update a plan embedding row.

        Uses INSERT ... ON CONFLICT (plan_id) DO UPDATE for idempotent upserts.
        The tsvector is auto-generated by the database trigger.
        """

    @with_db_error_handling
    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        intent_type: str | None,
        top_k: int,
        rrf_k: int = 60,
    ) -> list[dict]:
        """
        Execute hybrid RRF query combining BM25 and cosine similarity.

        SQL pattern:
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
        """

    @with_db_error_handling
    async def delete_by_plan_id(self, plan_id: str) -> None:
        """Delete embedding row by plan_id. Idempotent."""

    @with_db_error_handling
    async def bulk_upsert(
        self,
        rows: list[dict],
    ) -> int:
        """Batch upsert embedding rows. Returns count."""
```

### 7.3 TextBuilder

```python
# adapters/text_builder.py

def build_search_text(plan_data: dict) -> str:
    """
    Build a structured text representation of a plan for embedding + tsvector.

    Format: "{intent_type} | {action_1} {action_2} ... | {constraint_keys} | {entity_values}"

    Example:
        "schedule_meeting | search_calendar check_availability send_invite | max_duration flexible_time | Alice SFO"

    Args:
        plan_data: Plan dictionary with 'intent_type', 'graph', 'constraints', 'intent'.

    Returns:
        Structured search text string.
    """

def extract_intent_type(plan_data: dict) -> str:
    """
    Extract intent_type from plan_data.

    Looks in plan_data["intent_type"], plan_data["intent"]["intent"],
    or defaults to "unknown".

    Returns:
        Intent type string.
    """
```

### 7.4 Shared Infrastructure Usage

| Shared utility | Usage in VectorIndex |
|---------------|----------------------|
| `shared/database/adapter.py` | `SharedDatabaseAdapter` for PostgreSQL connections |
| `shared/database/error_handler.py` | `@with_db_error_handling` on all adapter methods |
| `shared/database/models.py` | `PlanEmbeddingTable` (updated for VectorIndex ownership) |
| `shared/api/error_handlers.py` | Not used (no API routes — library component) |
| `shared/dependencies.py` | `get_vector_index_service()` added for DI |
| `shared/app.py` | `create_vector_index_service()` called in lifespan |

### 7.5 Dependency Injection Integration

```python
# shared/app.py — add to lifespan:
from components.VectorIndex.service.vector_index_service import create_vector_index_service
app.state.vector_index_service = create_vector_index_service(app.state.db_adapter)

# shared/dependencies.py — add:
def get_vector_index_service(request: Request) -> Any:
    """Get VectorIndexService singleton from app state."""
    return request.app.state.vector_index_service
```

### 7.6 Idempotency

- **store_embedding**: Idempotent via `INSERT ... ON CONFLICT (plan_id) DO UPDATE`. Duplicate calls update the existing row.
- **delete_embedding**: Idempotent — `DELETE WHERE plan_id = ?` is a no-op if row doesn't exist.
- **search**: Read-only, inherently idempotent.
- **bulk_store**: Idempotent via upsert for each row.

---

## 8. Sequences

### 8.1 Store Embedding (Happy Path)

```
PlanWriter            VectorIndexService       TextBuilder       EmbeddingAdapter     PgvectorAdapter
   │                        │                       │                  │                    │
   │  store_embedding(      │                       │                  │                    │
   │    plan_id, plan_data) │                       │                  │                    │
   │───────────────────────>│                       │                  │                    │
   │                        │  build_search_text()  │                  │                    │
   │                        │──────────────────────>│                  │                    │
   │                        │  "schedule_meeting |  │                  │                    │
   │                        │   search_calendar..." │                  │                    │
   │                        │<──────────────────────│                  │                    │
   │                        │                       │                  │                    │
   │                        │  extract_intent_type()│                  │                    │
   │                        │──────────────────────>│                  │                    │
   │                        │  "schedule_meeting"   │                  │                    │
   │                        │<──────────────────────│                  │                    │
   │                        │                       │                  │                    │
   │                        │  embed(search_text)                      │                    │
   │                        │─────────────────────────────────────────>│                    │
   │                        │  [0.12, -0.34, ...]  (384 floats)       │                    │
   │                        │<─────────────────────────────────────────│                    │
   │                        │                                          │                    │
   │                        │  upsert_embedding(plan_id, intent_type, embedding, text)     │
   │                        │─────────────────────────────────────────────────────────────>│
   │                        │  OK (row upserted, tsvector auto-generated by trigger)       │
   │                        │<─────────────────────────────────────────────────────────────│
   │                        │                                                               │
   │  None (success)        │                                                               │
   │<───────────────────────│                                                               │
```

### 8.2 Hybrid Search (Happy Path)

```
ContextRAG            VectorIndexService       EmbeddingAdapter     PgvectorAdapter
   │                        │                       │                    │
   │  search(query_text,    │                       │                    │
   │    intent_type, top_k) │                       │                    │
   │───────────────────────>│                       │                    │
   │                        │  validate(top_k, query_text)              │
   │                        │                       │                    │
   │                        │  embed(query_text)    │                    │
   │                        │──────────────────────>│                    │
   │                        │  query_vec (384 floats)                   │
   │                        │<──────────────────────│                    │
   │                        │                       │                    │
   │                        │  hybrid_search(query_vec, query_text,     │
   │                        │    intent_type, top_k)                    │
   │                        │─────────────────────────────────────────>│
   │                        │    [CTE: keyword BM25 + semantic cosine] │
   │                        │    [FULL OUTER JOIN + RRF score]         │
   │                        │  rows [{plan_id, intent_type,            │
   │                        │         rrf_score, rank_kw, rank_vec}]   │
   │                        │<─────────────────────────────────────────│
   │                        │                                           │
   │                        │  → list[HybridSearchResult]               │
   │  results               │                                           │
   │<───────────────────────│                                           │
```

### 8.3 Search with No Results

```
ContextRAG            VectorIndexService       PgvectorAdapter
   │                        │                       │
   │  search("novel query", │                       │
   │    "unknown_intent")   │                       │
   │───────────────────────>│                       │
   │                        │  embed + hybrid_search│
   │                        │──────────────────────>│
   │                        │  [] (empty)           │
   │                        │<──────────────────────│
   │                        │                       │
   │  [] (empty list)       │                       │
   │<───────────────────────│                       │
```

### 8.4 Database Connection Error

```
PlanWriter            VectorIndexService       PgvectorAdapter
   │                        │                       │
   │  store_embedding(...)  │                       │
   │───────────────────────>│                       │
   │                        │  upsert_embedding()   │
   │                        │──────────────────────>│
   │                        │  @with_db_error_handling catches SQLAlchemyError
   │                        │  DatabaseConnectionError
   │                        │<──────────────────────│
   │                        │                       │
   │  DatabaseConnectionError                       │
   │<───────────────────────│                       │
```

### 8.5 ONNX Model Not Found at Startup

```
Lifespan              create_vector_index_service()   EmbeddingAdapter
   │                        │                              │
   │  (app startup)         │                              │
   │───────────────────────>│                              │
   │                        │  EmbeddingAdapter(model_path)│
   │                        │─────────────────────────────>│
   │                        │  model file not found        │
   │                        │  EmbeddingModelError         │
   │                        │<─────────────────────────────│
   │                        │                              │
   │  EmbeddingModelError   │                              │
   │  (application fails to start)                         │
   │<───────────────────────│                              │
```

### 8.6 Graceful Degradation

VectorIndex is an **optional enhancement** — unlike Signer (which is required for safety), VectorIndex failing should not prevent the system from operating.

- **ContextRAG**: If VectorIndex is unavailable, ContextRAG falls back to structured queries via PlanLibrary (intent_type exact match, ordered by success rate). Context quality degrades but the system continues.
- **Planner**: If VectorIndex is unavailable, Planner proceeds without exemplar plans. Plan quality may be lower but is still functional.
- **PlanWriter**: If VectorIndex is unavailable, PlanWriter logs a warning and skips embedding storage. The plan is still stored in PlanLibrary. Embedding can be backfilled later via `bulk_store`.

### 8.7 Retry / Idempotency Path

If a caller's process crashes between calling `store_embedding` and confirming success:
1. The database may or may not have persisted the row (depending on transaction state)
2. The caller retries `store_embedding` with the same `plan_id`
3. The upsert (`ON CONFLICT DO UPDATE`) safely handles both cases — it either inserts a new row or updates the existing one
4. No duplicate rows, no errors

---

## 9. Dependencies & External Integrations

### 9.1 Python Packages

| Package | Version | Justification |
|---------|---------|---------------|
| `onnxruntime` | `>=1.17.0` | CPU inference for all-MiniLM-L6-v2 ONNX model (~60 MB) |
| `tokenizers` | `>=0.15.0` | HuggingFace tokenizer for text preprocessing before ONNX |
| `numpy` | `>=1.24.0` | Array operations for embedding normalization (already in deps) |
| `pgvector` | `>=0.2.0` | SQLAlchemy Vector type + pgvector operator support |
| `pydantic` | `>=2.0` | HybridSearchResult model (already in deps) |
| `sqlalchemy` | `>=2.0` | Database ORM (already in deps) |

**New dependencies to add**: `onnxruntime`, `tokenizers`, `pgvector` (Python package for SQLAlchemy integration).

### 9.2 Internal Component Dependencies

| Component | Dependency Type | Direction |
|-----------|----------------|-----------|
| PlanWriter | Consumer | PlanWriter → VectorIndex (store embedding on plan persist) |
| ContextRAG | Consumer | ContextRAG → VectorIndex (search for similar plans) |
| Planner | Consumer | Planner → VectorIndex (find exemplar plans) |
| PlanLibrary | Data source | VectorIndex → PlanLibrary (FK: plan_embeddings.plan_id → plans.plan_id) |

This matches MODULAR_ARCHITECTURE §4 Memory Layer dependency graph (VectorIndex is a foundation component with no component dependencies).

### 9.3 External Services

None. VectorIndex is fully self-contained:
- Embedding generation: local ONNX Runtime (no OpenAI API)
- Keyword search: PostgreSQL built-in tsvector (no pg_textsearch)
- Score fusion: SQL CTE (no Cohere reranker)

### 9.4 Infrastructure Dependencies

| Resource | Description |
|----------|-------------|
| PostgreSQL 16 | With pgvector extension installed |
| ONNX model file | `all-MiniLM-L6-v2` (~80 MB), auto-downloaded on first run |

---

## 10. Observability & Safety

### 10.1 Structured Logging

```python
import logging

logger = logging.getLogger("vectorindex")

# Store operation
logger.info("embedding_stored", extra={
    "plan_id": plan_id,
    "intent_type": intent_type,
    "embedding_latency_ms": embedding_ms,
    "total_latency_ms": total_ms,
})

# Search operation
logger.info("hybrid_search", extra={
    "intent_type": intent_type or "all",
    "top_k": top_k,
    "result_count": len(results),
    "keyword_hits": sum(1 for r in results if r.keyword_rank is not None),
    "semantic_hits": sum(1 for r in results if r.semantic_rank is not None),
    "embedding_latency_ms": embed_ms,
    "search_latency_ms": search_ms,
    "total_latency_ms": total_ms,
})

# Delete operation
logger.info("embedding_deleted", extra={"plan_id": plan_id})

# Error
logger.error("embedding_store_failed", extra={
    "plan_id": plan_id,
    "error": str(e),
})
```

**Never log**: Raw embedding vectors (384 floats), full plan content, full search_text.

### 10.2 Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vectorindex_store_duration_seconds` | Histogram | `status` | Time to store an embedding |
| `vectorindex_search_duration_seconds` | Histogram | `status`, `search_mode` | Time for hybrid search |
| `vectorindex_embed_duration_seconds` | Histogram | `status` | ONNX embedding generation time |
| `vectorindex_search_result_count` | Histogram | `search_mode` | Number of results per search |
| `vectorindex_store_total` | Counter | `status` | Total store operations |
| `vectorindex_search_total` | Counter | `status`, `search_mode` | Total search operations |
| `vectorindex_errors_total` | Counter | `operation`, `error_type` | Errors by type |

Labels:
- `status`: `success`, `error`
- `search_mode`: `hybrid`, `semantic_only`, `keyword_only`
- `operation`: `store`, `search`, `delete`, `bulk_store`
- `error_type`: `db_connection`, `embedding_model`, `validation`

### 10.3 Error Classes Summary

| Error | When | Caller action |
|-------|------|---------------|
| `VectorIndexUnavailableError` | pgvector extension missing at startup | Application fails to start |
| `EmbeddingModelError` | ONNX model file missing/corrupt at startup | Application fails to start |
| `ValueError` | Empty plan_data, empty query_text, invalid top_k | Caller fixes input |
| `DatabaseConnectionError` | PostgreSQL connection failure | Caller retries or degrades gracefully |
| `DatabaseIntegrityError` | FK violation (plan_id doesn't exist in plans) | Caller ensures plan exists first |

Note: VectorIndex has no API routes, so HTTP codes are determined by the calling component. ContextRAG/Planner should catch `DatabaseConnectionError` and degrade gracefully (proceed without vector results).

---

## 11. Caching Strategy

**Not applicable for MVP.** VectorIndex does not use Redis.

**Future consideration**: If search latency becomes a bottleneck, cache frequent query embeddings:
- Key: `vectorindex:embed:{sha256(query_text)}`
- Value: 384-float embedding vector
- TTL: 1 hour
- Invalidation: Not needed (same text always produces same embedding)

This would eliminate the ~10ms ONNX inference for repeated queries. Not needed at current scale.

---

## 12. Non-Functional Requirements

### 12.1 Performance

| Operation | p95 Target | p99 Target | Notes |
|-----------|-----------|-----------|-------|
| `embed()` (ONNX) | < 15 ms | < 25 ms | Single text, CPU inference |
| `embed_batch()` (100 texts) | < 500 ms | < 800 ms | Batched ONNX inference |
| `hybrid_search()` (full) | < 30 ms | < 50 ms | GIN + HNSW + B-tree, single SQL |
| `store_embedding()` (total) | < 25 ms | < 40 ms | embed + upsert |
| `search()` (total) | < 50 ms | < 70 ms | embed query + hybrid SQL |
| ONNX session init | < 300 ms | < 500 ms | One-time at startup |

### 12.2 Availability

- VectorIndex is an **optional** component — system continues without it
- Target: Same as system baseline (99.9% cloud, best-effort local)
- Degradation: ContextRAG/Planner fall back to structured queries if unavailable

### 12.3 Scalability

| Scale | Embeddings | Search latency | Notes |
|-------|-----------|----------------|-------|
| Local/MVP | < 1,000 | < 20 ms | HNSW overkill, but no downside |
| Cloud | < 100,000 | < 50 ms | HNSW index effective at this scale |
| Enterprise | < 1,000,000 | < 100 ms | Consider pgvectorscale DiskANN upgrade |

### 12.4 Testing Strategy

| Test Type | File | Coverage |
|-----------|------|----------|
| Unit — text_builder | `test_unit.py` | build_search_text, extract_intent_type edge cases |
| Unit — service | `test_unit.py` | Input validation, adapter delegation, error mapping |
| Contract | `test_contract.py` | HybridSearchResult schema compliance |
| Integration — store | `test_integration.py` | Store embedding, verify in DB (requires pgvector) |
| Integration — search | `test_integration.py` | Hybrid search with known data, verify RRF ranking |
| Integration — RRF | `test_integration.py` | Verify keyword-only, semantic-only, and hybrid results |
| Observability | `test_observability.py` | No embedding vectors in logs |
| Edge cases | `test_unit.py` | Empty plan, invalid top_k, missing intent_type |

**Test fixtures**: Tests use a mock EmbeddingAdapter (returns deterministic vectors) to avoid ONNX model dependency in CI. Integration tests with real pgvector require the Docker Compose PostgreSQL setup.

---

## 13. Architectural Considerations

### 13.1 Blast Radius Containment

- VectorIndex failure does not prevent plan creation, signing, or execution
- Database connection issues in VectorIndex do not affect other components (shared pool with isolation)
- ONNX model failure is caught at startup (fail-fast)

### 13.2 Fault Isolation

- **pgvector extension missing**: Detected at startup via `check_pgvector_extension()`. Application logs error and starts without VectorIndex (graceful degradation).
- **ONNX model missing**: Detected at startup. Application fails to start (model is required for any VectorIndex operation).
- **Database down**: `@with_db_error_handling` catches and translates errors. Callers handle `DatabaseConnectionError`.

### 13.3 Determinism

- `build_search_text()` is deterministic: same plan dict → same search text
- ONNX embedding is deterministic: same text → same 384-dim vector (model is frozen)
- `tsvector` generation is deterministic: same search_text → same tsvector (PostgreSQL `english` dictionary)
- RRF scores are deterministic: same data → same ranks → same RRF scores

### 13.4 State Management

- **Persistent state**: `plan_embeddings` table in PostgreSQL
- **In-memory state**: ONNX inference session (loaded once, read-only)
- **No ephemeral state**: No Redis, no in-memory caches, no background tasks
- **Data loss risk**: None — all state is in PostgreSQL with WAL

### 13.5 RRF Constant Selection

The RRF constant `k=60` comes from the [original RRF paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf). It dampens the influence of high-rank positions. At our scale (hundreds of plans), this value is robust. The constant is hardcoded for MVP; could be made configurable if tuning is needed.

### 13.6 tsvector vs True BM25

PostgreSQL's `ts_rank_cd` uses cover density ranking (TF-IDF variant), not true BM25 (which includes document length normalization). For our use case — structured search text of similar lengths (plan representations) — the difference is negligible. If true BM25 is needed later, [ParadeDB pg_search](https://github.com/paradedb/paradedb) is a drop-in open-source upgrade.

---

## 14. Architecture Decision Records

### Referenced ADRs

| ADR | Relevance |
|-----|-----------|
| `0001-component-first.md` | VectorIndex follows component-first structure under `components/VectorIndex/` |

### New Decisions (documented here, may need ADR)

- **ONNX Runtime over PyTorch**: 10-20x smaller footprint, 3-5x faster CPU inference. Trade-off: less flexible model loading, requires ONNX export.
- **Local embeddings over OpenAI API**: $0 cost, <15ms vs 200-500ms latency, no network dependency. Trade-off: slightly lower embedding quality (MiniLM-384 vs text-embedding-3-small-1536).
- **PostgreSQL tsvector over pg_textsearch**: Zero additional dependencies, built into PostgreSQL. Trade-off: `ts_rank_cd` is not true BM25 (see §13.6).
- **RRF over learned fusion**: Simple, score-agnostic, no training data needed. Trade-off: fixed weighting (equal rank contribution from both legs).
- **Library component (no HTTP routes)**: Same pattern as Signer. Consumers call via DI. Reduces API surface area.

---

## 15. Risks & Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| ONNX model download on first run (~80MB) | Low | Bundle in Docker image or pre-download in CI |
| pgvector extension not in dev PostgreSQL | Medium | Docker Compose uses `pgvector/pgvector:pg16` image; document setup |
| tsvector insufficient for complex queries | Low | Upgrade path: ParadeDB pg_search (open-source BM25) |
| Embedding quality (MiniLM-384) insufficient | Low | Upgrade path: larger model (e5-base, 768-dim) with same ONNX pattern |
| ~~MODULAR_ARCHITECTURE docs still say "deferred"~~ | ~~Medium~~ | **Resolved**: Project_HLD v5.1 and MODULAR_ARCHITECTURE v1.5 updated — VectorIndex active |

### Open Questions

1. Should we add a minimum RRF score threshold to filter low-quality results?
2. What text representation should we embed — the structured `search_text` format, or just the intent description?
3. Should the RRF constant `k` be configurable per query, or fixed at 60?
4. Should intent_type filtering support prefix/LIKE matching in addition to exact match?

---

## 16. Post-Generation Validation Checklist

- [x] Data model fields — HybridSearchResult uses clear domain naming (no GLOBAL_SPEC §2 contract overlap — VectorIndex doesn't produce Intent/Evidence/Plan/Signature)
- [x] `user_id` — Documented deviation in §5.3 (plan_embeddings references plans, not users; consent enforcement is at ContextRAG level)
- [x] Conformance header references current document versions (GLOBAL_SPEC v2.2, MODULAR_ARCHITECTURE v1.2)
- [x] Table ownership — `plan_embeddings` ownership transferring from PlanLibrary to VectorIndex (documented, requires MODULAR_ARCHITECTURE update)
- [x] Component dependencies match MODULAR_ARCHITECTURE (foundation component, no component deps)
- [x] Every upstream consumer has documented interface contract (§4.2: PlanWriter, ContextRAG, Planner)
- [x] Storage APIs are idempotent (§7.6: upsert via ON CONFLICT)
- [x] DDL included for owned tables with indexes (§6.2)
- [x] Migration file specification documented (§6.3: `007_update_plan_embeddings_vectorindex.sql`)
- [x] Prometheus metrics defined with names and types (§10.2)
- [x] No deprecated library versions (onnxruntime >=1.17, tokenizers >=0.15, pgvector >=0.2)
- [x] Error handling uses shared database error handler (`@with_db_error_handling`, `DatabaseConnectionError`)
- [x] Database adapter uses `SharedDatabaseAdapter` from `shared/database/adapter.py`
