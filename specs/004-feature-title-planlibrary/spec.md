# Component Specification: PlanLibrary

**Feature Branch**: `004-feature-title-planlibrary`
**Created**: 2025-12-29
**Status**: Draft
**Input**: User description: "PlanLibrary - Memory Layer component that stores past plans with signatures and outcomes, enables plan reuse and learning, supports querying by similarity/success rate/context, integrates with Planner for optimization"

---

## Scope & Non-Goals

### In Scope

* **Plan Storage**: Store all executed plans with their canonical signatures, outcomes, and execution metadata
* **Plan Retrieval**: Query stored plans by similarity (semantic vector search), success rate, intent type, and execution context
* **Learning Integration**: Provide successful plan patterns to Planner for optimization and reuse
* **Outcome Tracking**: Store execution results (success/failure), performance metrics, and error patterns
* **Evidence Integration**: Return plan data in Evidence Item format (type="plan") for ContextRAG integration
* **Plan Versioning**: Track plan evolution and pattern improvements over time
* **Success Analytics**: Calculate success rates and identify high-performing plan patterns
* **Similarity Matching**: Use vector embeddings to find similar past plans for context and reuse
* **Audit Compliance**: Store plan hashes and signatures for deterministic plan verification

### Out of Scope (Non-Goals)

* **Plan Generation**: Creating new plans (owned by Planner component)
* **Plan Execution**: Running plans (owned by ExecuteOrchestrator component)
* **Plan Signatures**: Signing plans (owned by Signer component)
* **Intent Processing**: Understanding user requests (owned by Intake component)
* **Live Plan Status**: Real-time execution monitoring (owned by orchestration layer)
* **User Preferences**: Stable user settings (owned by ProfileStore component)
* **Vector Embeddings Generation**: Creating embeddings (delegated to OpenAI API)
* **Plugin Registry**: Tool definitions and capabilities (separate component)

### Assumptions

* **Plans arrive already signed**: PlanLibrary receives plans with valid Ed25519 signatures from PlanWriter
* **Outcomes are provided**: Execution results and success/failure status provided by PlanWriter after execution
* **Vector embeddings**: OpenAI embedding API available for semantic similarity
* **Database schema**: PostgreSQL tables exist (`plans`, `plan_outcomes`, `plan_embeddings`)
* **Plan ID uniqueness**: Each plan has a unique ULID identifier
* **Deterministic serialization**: Plan JSON can be canonicalized for consistent hashing
* **Performance targets**: Vector similarity search completes within 100ms (GLOBAL_SPEC requirement)

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Store Plan Execution Results (Priority: P1)

As PlanWriter, I need to store executed plans with their outcomes so that the system can learn from past successes and failures.

**Why this priority**: Core functionality - enables all learning and optimization features. This is the minimum viable PlanLibrary.

**Independent Test**: Can be fully tested by storing a plan with success outcome and retrieving it. Delivers immediate value for audit and compliance.

**Acceptance Scenarios**:

1. **Given** a successfully executed plan with outcome, **When** PlanWriter stores the plan and result, **Then** PlanLibrary persists the plan with success status and execution metadata

2. **Given** a failed plan execution with error details, **When** PlanWriter stores the failure outcome, **Then** PlanLibrary records the failure with error type and timing information

3. **Given** a plan with the same intent executed multiple times, **When** retrieving plans by intent type, **Then** PlanLibrary returns all executions with their respective outcomes

4. **Given** an invalid plan signature, **When** attempting to store the plan, **Then** PlanLibrary rejects storage and returns `INVALID_SIGNATURE` error

---

### User Story 2 - Query Plans by Success Rate and Context (Priority: P1)

As Planner, I need to find successful plans for similar intents so that I can optimize new plan generation using proven patterns.

**Why this priority**: Critical for system learning and improvement. Enables Planner to reuse successful patterns.

**Independent Test**: Can be tested by storing multiple plans for "schedule_meeting" intent with different success rates and querying for successful patterns.

**Acceptance Scenarios**:

1. **Given** 10 stored plans for "schedule_meeting" intent with 80% success rate, **When** Planner queries for successful meeting plans, **Then** PlanLibrary returns plans sorted by success rate with metadata

2. **Given** plans for different intent types, **When** querying for plans with specific intent, **Then** PlanLibrary filters results to only matching intent types

3. **Given** plans executed in different timeframes, **When** querying with recency preference, **Then** PlanLibrary returns results ordered by execution date

---

### User Story 3 - Semantic Similarity Search (Priority: P2)

As Planner, I need to find plans for similar intents so that I can adapt successful patterns to new scenarios.

**Why this priority**: Enables intelligent plan reuse across similar scenarios, improving system adaptability.

**Independent Test**: Can be tested by storing plans for "book restaurant" and "reserve table" and verifying similarity search finds both for related queries.

**Acceptance Scenarios**:

1. **Given** stored plans for "book_restaurant" and "schedule_meeting" intents, **When** searching for plans similar to "reserve_dinner", **Then** PlanLibrary returns restaurant booking plans with similarity scores

2. **Given** a new intent with vector embedding, **When** performing similarity search, **Then** PlanLibrary returns top 5 most similar plans within 100ms

3. **Given** no similar plans exist (similarity < 0.5 threshold), **When** performing similarity search, **Then** PlanLibrary returns empty results rather than low-quality matches

---

### User Story 4 - Plan Pattern Analytics (Priority: P3)

As system administrators, I want to analyze plan success patterns so that I can identify areas for system improvement.

**Why this priority**: Valuable for system optimization but not critical for core functionality.

**Independent Test**: Can be tested by storing plans with different outcomes and generating success rate analytics.

**Acceptance Scenarios**:

1. **Given** multiple plans for each intent type, **When** requesting success analytics, **Then** PlanLibrary returns success rates grouped by intent with statistical significance

2. **Given** plans with different execution contexts, **When** analyzing performance patterns, **Then** PlanLibrary identifies high-performing contexts and configurations

---

### Edge Cases

* **Duplicate plan hashes**: Same canonical plan executed multiple times (track separate outcomes)
* **Partial plan execution**: Plans that fail mid-execution (store partial outcomes with failure step)
* **Large plan storage**: Plans with many steps or large payloads (implement plan size limits)
* **Concurrent plan storage**: Multiple threads storing different plans simultaneously (ensure thread safety)
* **Vector embedding failures**: OpenAI API unavailable during plan storage (graceful degradation, retry logic)
* **Database capacity**: Large number of stored plans affecting query performance (implement archival strategy)
* **Plan signature verification**: Invalid or tampered signatures (reject storage with detailed error)

---

## Decision Rules (Deterministic Order)

Explicit, ordered rules evaluated **top to bottom**; first match wins:

1. **IF** `plan_id` is null, empty, or not valid ULID format → Return `INVALID_PLAN_ID` error
2. **IF** `plan` object is missing required fields (plan_id, graph, meta) → Return `MALFORMED_PLAN` error  
3. **IF** `signature` is missing or fails Ed25519 verification → Return `INVALID_SIGNATURE` error
4. **IF** plan with same `plan_id` already exists → Return `DUPLICATE_PLAN_ID` error
5. **IF** plan exceeds size limits (>100 steps or >1MB JSON) → Return `PLAN_TOO_LARGE` error
6. **IF** vector embedding generation fails and retry exhausted → Log warning, store plan without embedding (similarity search unavailable)
7. **IF** database connection fails during storage → Return `STORAGE_ERROR` with retry instructions
8. **ELSE** → Proceed with storage (canonicalize plan, generate embedding, persist to database)

For retrieval operations:

1. **IF** query parameters are invalid (negative limit, invalid intent format) → Return `INVALID_QUERY` error
2. **IF** similarity search requested but vector index unavailable → Return `VECTOR_SEARCH_UNAVAILABLE` error
3. **IF** query would return >1000 results → Apply automatic pagination limit
4. **ELSE** → Execute query and return results in Evidence Item format

---

## Requirements *(mandatory)*

### Functional Requirements

* **FR-001: External Contract**
  * Storage Input: `plan` (validated Plan JSON), `signature` (Signature object), `outcome` (execution result), `performance_metrics` (latency, step timing)
  * Query Input: `intent_type` (string), `similarity_vector` (float array), `success_threshold` (0.0-1.0), `limit` (max 1000), `recency_days` (filter)
  * Output (success): Evidence Item array with type="plan", plan data, confidence score, source_ref
  * Output (error): `{"status": "error", "error_code": "...", "message": "...", "details": {...}}`
  * Error codes: `INVALID_PLAN_ID`, `MALFORMED_PLAN`, `INVALID_SIGNATURE`, `DUPLICATE_PLAN_ID`, `PLAN_TOO_LARGE`, `STORAGE_ERROR`, `INVALID_QUERY`

* **FR-002: Execution Semantics**
  * All operations execute directly (no Preview/Execute distinction at component level)
  * Storage operations (`STORE_PLAN`) persist immediately to PostgreSQL with async transaction
  * Query operations (`GET_PLANS_BY_INTENT`, `SIMILARITY_SEARCH`) read from database with caching
  * Vector embedding generation asynchronous with retry logic (3 attempts, exponential backoff)

* **FR-003: Plan Storage Format**
  * Store canonical plan JSON (deterministic serialization, sorted keys)
  * Store Ed25519 signature and verification metadata
  * Store execution outcome: success/failure, error details, execution time
  * Store performance metrics: preview latency, execute latency, step-by-step timing
  * Store creation timestamp and execution timestamp
  * Store vector embedding for semantic similarity (1536-dimension from OpenAI text-embedding-ada-002)

* **FR-004: Query Capabilities**
  * Query by exact intent match with success rate filtering
  * Query by similarity using vector embeddings (cosine similarity, threshold 0.5+)
  * Query by execution recency (plans from last N days)
  * Query by success rate (only plans with >X% success rate)
  * Support pagination and result limits (default 50, max 1000 results)
  * Return results sorted by relevance score (success rate * similarity * recency factor)

* **FR-005: Evidence Item Integration**
  * Return plan data as Evidence Items with type="plan"
  * Include confidence score calculated from success rate and similarity
  * Set source_ref as "planlibrary:plans/{plan_id}"
  * Include ttl_days=null for permanent storage
  * Set tier=3 (historical data context tier)
  * Include plan summary in Evidence value field (not full plan)

* **FR-006: Performance Requirements**
  * Vector similarity search: p95 < 100ms (GLOBAL_SPEC requirement)
  * Plan storage: p95 < 200ms (Plan Retrieval GLOBAL_SPEC requirement)  
  * Intent-based queries: p95 < 150ms
  * Support 1000 concurrent queries without degradation
  * Vector index optimization for sub-100ms search

* **FR-007: Data Retention and Archival**
  * Store plans indefinitely for learning (no automatic deletion)
  * Implement archival for plans older than 2 years (compress, move to cold storage)
  * Maintain vector index for active plans (last 1 year) for performance
  * Archive maintains queryability but with higher latency (p95 < 500ms)

* **FR-008: Security and Integrity**
  * Verify plan signatures before storage (Ed25519 verification)
  * Store plan hashes for integrity verification
  * No PII in stored plans (plans should contain only derived entities)
  * Audit log all plan storage and retrieval operations with correlation IDs
  * Access control: component-level access, no user-specific filtering

* **FR-009: Fault Tolerance**
  * Graceful degradation: store plans even if vector embedding fails
  * Retry logic for vector embedding generation (3 attempts, exponential backoff)
  * Circuit breaker for OpenAI embedding API calls
  * Database transaction rollback on partial failures
  * Monitoring and alerting for storage failures and vector index health

### Key Entities

* **Plan**: Complete execution graph with `plan_id` (ULID), `intent` object, `graph` array, `constraints` object, `meta` metadata. Stored as canonical JSON with deterministic serialization. Immutable once stored. Includes plan signature for verification.

* **PlanOutcome**: Execution result with `outcome_id` (UUID), `plan_id` (FK), `success` (boolean), `error_type` (if failed), `error_details` (JSON), `execution_start` (timestamp), `execution_end` (timestamp), `total_steps` (integer), `failed_step` (integer if applicable).

* **PlanEmbedding**: Vector representation with `embedding_id` (UUID), `plan_id` (FK), `vector` (1536-dimension float array), `model_version` (embedding model used), `created_at` (timestamp). Used for semantic similarity search.

* **PlanMetrics**: Performance data with `metrics_id` (UUID), `plan_id` (FK), `preview_latency_ms` (integer), `execute_latency_ms` (integer), `step_timings` (JSON array), `resource_usage` (JSON object).

---

## Invariants & Guarantees

Statements that must **always** hold true:

1. **Plan uniqueness**: Every `plan_id` exists in exactly one row in the `plans` table
2. **Signature integrity**: All stored plans have valid Ed25519 signatures that verify against canonical plan JSON
3. **Outcome consistency**: Every `plan_outcome` references a valid `plan_id` in the plans table
4. **Vector consistency**: Plan embeddings, if present, always correspond to existing plans
5. **Canonical serialization**: Plan JSON stored in deterministic format (sorted keys, no whitespace)
6. **Immutable storage**: Plans never modified after storage (append-only for outcomes)
7. **Success rate accuracy**: Calculated success rates always reflect actual outcome data
8. **Vector search accuracy**: Similarity scores accurately represent cosine similarity of embeddings
9. **Performance bounds**: Vector searches complete within 100ms for 95% of queries
10. **Audit completeness**: All storage and retrieval operations logged with correlation IDs

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

* **SC-001**: Plan storage operations complete in p95 < 200ms (measured via distributed tracing)
* **SC-002**: Vector similarity search completes in p95 < 100ms (GLOBAL_SPEC requirement)
* **SC-003**: Intent-based queries complete in p95 < 150ms (measured via distributed tracing)
* **SC-004**: 99.5% availability for all PlanLibrary operations (measured via uptime monitoring)
* **SC-005**: Support storage of 100,000 plans with linear query performance (verified via load testing)
* **SC-006**: Zero signature verification failures for valid plans (100% integrity, verified via contract tests)
* **SC-007**: Vector embedding success rate >95% (account for API failures, measured via success metrics)

---

## Interfaces & Contracts

### Internal Component - No Preview/Execute Model

PlanLibrary is an internal backend component invoked by PlanWriter (storage) and ContextRAG/Planner (queries). It does **not** use the Preview/Execute model from GLOBAL_SPEC - that model applies to user-facing **plans**, not internal component operations.

### Storage Interface (for STORE_PLAN)

```python
async def store_plan(
    plan: Plan,
    signature: Signature, 
    outcome: PlanOutcome,
    metrics: PlanMetrics
) -> StoreResult:
    """
    Store executed plan with outcome and metrics.
    
    Returns: {"status": "ok", "plan_id": "01HX...", "stored_at": "2025-12-29T10:30:00Z"}
    """
```

### Query Interface (for GET_PLANS_BY_INTENT)

```python
async def get_plans_by_intent(
    intent_type: str,
    success_threshold: float = 0.7,
    limit: int = 50,
    recency_days: int | None = None
) -> List[EvidenceItem]:
    """
    Query plans by intent type with success filtering.
    
    Returns: List of Evidence Items with type="plan"
    """
```

### Similarity Search Interface (for SIMILARITY_SEARCH)

```python
async def similarity_search(
    query_vector: List[float],
    similarity_threshold: float = 0.5,
    limit: int = 10,
    success_threshold: float = 0.5
) -> List[EvidenceItem]:
    """
    Find similar plans using vector embeddings.
    
    Returns: List of Evidence Items sorted by relevance score
    """
```

### Evidence Item Output

Plan data returned in Evidence Item format (GLOBAL_SPEC §2.2):

```json
{
  "type": "plan",
  "key": "schedule_meeting_pattern_1",
  "value": {
    "intent": "schedule_meeting",
    "success_rate": 0.85,
    "avg_execution_time_ms": 1200,
    "steps_count": 6,
    "pattern_summary": "Fetch calendars → Find overlap → User choice → Book event"
  },
  "confidence": 0.85,
  "source_ref": "planlibrary:plans/01HX123456",
  "ttl_days": null,
  "tier": 3
}
```

Reference: Evidence Item schema from `shared/schemas/evidence.py`

---

## Component Mapping

* **Target**: `components/PlanLibrary/`
* **Files expected to change**:
  * `api/routes.py` - FastAPI endpoints for plan storage and querying  
  * `service/plan_service.py` - Business logic for plan storage and retrieval
  * `service/vector_service.py` - Vector embedding generation and similarity search
  * `domain/models.py` - Pydantic models for Plans, Outcomes, Metrics
  * `adapters/db.py` - SQLAlchemy database adapter (plans, outcomes, embeddings tables)
  * `adapters/vector_db.py` - Vector database adapter (pgvector integration)
  * `adapters/embedding_client.py` - OpenAI embedding API client
  * `schemas/plan_storage.schema.json` - JSON schema for plan storage
  * `schemas/query_request.schema.json` - JSON schema for query parameters
  * `tests/test_plan_storage.py` - Unit tests for plan storage logic
  * `tests/test_similarity_search.py` - Unit tests for vector search
  * `tests/test_integration.py` - Integration tests with database and vector index
  * `tests/test_contract.py` - Contract tests for Evidence Item compliance

---

## Dependencies & Risks

### Dependencies

* **PostgreSQL 16 with pgvector**: Database and vector extension for plan storage and similarity search
* **OpenAI Embedding API**: text-embedding-ada-002 model for generating plan vectors
* **PlanWriter**: Provides executed plans with outcomes and metrics
* **Shared schemas**: Evidence Item format and Plan/Signature schemas
* **SQLAlchemy 2.0**: Async ORM for database operations
* **Pydantic v2**: Data validation for incoming plans and queries

### Risks

* **Risk 1: Vector API dependency** - OpenAI embedding API failures could block plan storage
  * *Mitigation*: Store plans without embeddings on API failure; retry embedding generation asynchronously; circuit breaker pattern

* **Risk 2: Vector search performance** - Large vector index could slow similarity queries
  * *Mitigation*: pgvector HNSW index optimization; result caching; index partitioning by recency; archive old embeddings

* **Risk 3: Database growth** - Unlimited plan storage could affect performance
  * *Mitigation*: Implement archival strategy (2-year cutoff); query pagination; index optimization; cold storage for old plans

* **Risk 4: Plan signature verification overhead** - Ed25519 verification on every storage
  * *Mitigation*: Verify signatures asynchronously after storage; batch verification; signature caching for repeated verifications

* **Risk 5: Concurrent write conflicts** - Multiple plan outcomes for same plan_id
  * *Mitigation*: Database constraints ensure plan_id uniqueness; separate table for multiple outcomes; optimistic locking

---

## Non-Functional Requirements

* **Inherit baseline** (from constitution.md):
  * Preview p95 < 800ms (not applicable, internal component)
  * Execute p95 < 2s (PlanLibrary queries target <200ms)  
  * Structured logs with no secrets/PII
  * 99.9% availability

* **Deltas** (PlanLibrary-specific):
  * **Stricter latency targets**: Vector search <100ms (GLOBAL_SPEC), storage <200ms (Plan Retrieval GLOBAL_SPEC)
  * **Storage retention**: Indefinite plan storage with archival strategy (2+ years)
  * **Vector performance**: Support 100,000 plans with linear query time
  * **Embedding availability**: 95% vector embedding success rate (tolerate API failures)

---

## Open Questions

* **Q1**: Should we store full plan JSON or just plan summaries for similarity search?
  * **Proposed answer**: Store full plan for audit/compliance, generate summary fields for efficient querying

* **Q2**: What similarity threshold should be used for plan matching (current: 0.5)?
  * **Proposed answer**: Start with 0.5, make configurable, gather metrics to optimize threshold

* **Q3**: Should plan outcomes support partial success (some steps succeeded, others failed)?
  * **Proposed answer**: Yes, store `failed_step` index and `partial_success` flag for granular analysis

* **Q4**: How should we handle plan versioning when the same intent has evolved patterns?
  * **Proposed answer**: Store all plans with timestamps, use recency weighting in relevance scoring

* **Q5**: Should vector embeddings be generated synchronously or asynchronously during storage?
  * **Proposed answer**: Asynchronous with immediate storage, retry logic, graceful degradation for search

* **Q6**: What archival strategy should be used for old plans (>2 years)?
  * **Proposed answer**: Move to compressed cold storage, maintain queryability with higher latency tolerance

---

## Conformance

This work conforms to:

* `docs/architecture/GLOBAL_SPEC.md` v2 - Evidence Item format, performance targets, context tiers
* `docs/architecture/Project_HLD.md` v4.0 - Memory Layer component responsibilities  
* `.specify/memory/constitution.md` v1.0.0 - Component-first architecture, test-first development