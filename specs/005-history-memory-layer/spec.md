# Component Specification: History

**Feature Branch**: `feat/history-memory-layer`
**Created**: 2026-02-10
**Status**: Implemented ✅ (PR #5)
**Input**: User description: "History - Memory Layer component that stores normalized, PII-light facts about past user actions. Enables system learning from execution outcomes by recording structured facts like meeting bookings, task completions, and usage patterns. Integrates with ContextRAG as a Tier 3 evidence source."

---

## Scope & Non-Goals

### In Scope

* **Tier 3 Data Source**: History provides recent interaction facts for ContextRAG (as defined in GLOBAL_SPEC §7, Tier 3: recent history with 30-day TTL)
* **Fact Storage**: Store normalized, PII-light facts derived from plan execution outcomes (e.g., "Booked 30min meeting with Alice at 10 AM on Tuesday")
* **Fact Retrieval**: Query stored facts by user_id, intent_type, entity references, and time range
* **Evidence Integration**: Return fact data in Evidence Item format (type="history") for ContextRAG integration
* **TTL Enforcement**: Automatically expire facts older than 30 days (configurable per fact category)
* **PII-Light Normalization**: Store only derived, normalized facts — never raw emails, messages, or provider responses
* **Pattern Detection**: Identify recurring patterns from stored facts (e.g., "Usually meets Alice on Tuesdays")
* **Consent Enforcement**: Require Tier 3+ consent before storing or returning history facts
* **Audit Compliance**: Log all fact storage and retrieval operations with correlation IDs

### Out of Scope (Non-Goals)

* **Session Data**: Temporary context from current conversation (owned by Intake via Redis, Tier 1)
* **User Preferences**: Stable settings like meeting duration or work hours (owned by ProfileStore, Tier 2)
* **Plan Storage**: Full plan graphs, signatures, and execution outcomes (owned by PlanLibrary)
* **Vector Embeddings**: Semantic similarity search over facts (owned by VectorIndex — hybrid BM25 + semantic via pgvector, ONNX Runtime)
* **Raw Data Storage**: Storing raw emails, API responses, or unprocessed provider data
* **Plan Execution**: Running or orchestrating plans (owned by ExecuteOrchestrator)
* **Fact Generation**: Creating derived facts from raw execution data (owned by PlanWriter, which calls History to persist)
* **Live Signals**: Real-time external data (Tier 4, fetched on-demand during planning)

### Assumptions

* **PlanWriter provides facts**: History receives pre-normalized facts from PlanWriter after plan execution
* **Facts are PII-light**: Upstream components (PlanWriter) ensure facts contain only derived entities, not PII
* **User accounts exist**: User IDs are valid and users are registered in the system
* **Database schema exists**: PostgreSQL `history` table with `user_id` index is migrated and available
* **Consent is cumulative**: Tier 3 consent includes access to Tiers 1 and 2 (GLOBAL_SPEC §7)
* **30-day default TTL**: Facts expire after 30 days unless a different TTL is specified per category
* **ContextRAG queries by intent**: ContextRAG requests history facts by intent_type and user_id during context assembly

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Store Execution Facts (Priority: P1)

As PlanWriter, I need to store derived facts from plan execution outcomes so that the system can learn from past actions and provide relevant context for future planning.

**Why this priority**: Core functionality — enables all learning and context features. This is the minimum viable History component.

**Independent Test**: Can be fully tested by storing a fact from a completed plan execution and retrieving it. Delivers immediate value for context assembly.

**Acceptance Scenarios**:

1. **Given** a successfully executed "schedule_meeting" plan, **When** PlanWriter stores the derived fact "Booked 30min meeting with Alice at 10 AM on Tuesday", **Then** History persists the fact with user_id, intent_type, timestamp, and 30-day TTL

2. **Given** a failed plan execution, **When** PlanWriter stores the failure fact "Failed to book meeting: calendar conflict", **Then** History records the fact with success=false and error category

3. **Given** a fact with explicit TTL override (e.g., 90 days for important events), **When** PlanWriter stores the fact with custom TTL, **Then** History respects the custom TTL instead of the 30-day default

4. **Given** a user without Tier 3 consent, **When** PlanWriter attempts to store a fact, **Then** History rejects storage and returns `CONSENT_REQUIRED` error

---

### User Story 2 - Query Facts by Intent and User (Priority: P1)

As ContextRAG, I need to retrieve relevant history facts for a user's current intent so that the Planner has context about past actions when generating new plans.

**Why this priority**: Critical for system learning — enables ContextRAG to assemble historical context for plan generation.

**Independent Test**: Can be tested by storing multiple facts for different intents and querying for a specific intent type. Verifies correct filtering and Evidence Item format.

**Acceptance Scenarios**:

1. **Given** 5 stored facts for user_id="u1" with intent_type="schedule_meeting", **When** ContextRAG queries for meeting history, **Then** History returns facts as Evidence Items sorted by recency

2. **Given** facts for multiple intent types, **When** querying with intent_type filter, **Then** History returns only matching facts (no cross-intent leakage)

3. **Given** facts older than 30 days (expired TTL), **When** querying for recent history, **Then** expired facts are excluded from results

4. **Given** a query with limit=5, **When** more than 5 matching facts exist, **Then** History returns the 5 most recent facts with proper pagination metadata

---

### User Story 3 - Detect Recurring Patterns (Priority: P2)

As Planner, I need to identify recurring patterns from stored facts so that plan generation can leverage established user habits and preferences.

**Why this priority**: Enhances plan quality by surfacing behavioral patterns. Not required for basic functionality but adds significant value.

**Independent Test**: Can be tested by storing 10 facts showing "meets Alice on Tuesday" pattern and verifying pattern detection returns the pattern with confidence score.

**Acceptance Scenarios**:

1. **Given** 5+ facts showing the same entity-action pattern on the same day of week, **When** requesting patterns for a user, **Then** History returns the pattern with frequency count and confidence score

2. **Given** a pattern that was broken (user stopped meeting on Tuesdays), **When** the pattern hasn't occurred in 30 days, **Then** History removes or reduces confidence of the stale pattern

3. **Given** patterns across different intent types, **When** querying patterns for a specific intent, **Then** History filters patterns to the requested intent type

---

### User Story 4 - TTL Expiration and Cleanup (Priority: P2)

As a system administrator, I need expired facts to be automatically cleaned up so that the database remains performant and only relevant history is retained.

**Why this priority**: Important for long-term system health but not critical for initial functionality.

**Independent Test**: Can be tested by storing facts with short TTL and verifying they become inaccessible after expiration.

**Acceptance Scenarios**:

1. **Given** a fact stored 31 days ago with default 30-day TTL, **When** the cleanup job runs, **Then** the expired fact is soft-deleted (marked as expired, not hard-deleted)

2. **Given** a fact with custom 90-day TTL stored 60 days ago, **When** the cleanup job runs, **Then** the fact remains accessible (not yet expired)

---

### Edge Cases

* **Duplicate facts**: Same derived fact from retried plan execution (deduplicate by fact_hash)
* **Concurrent writes**: Multiple PlanWriter instances storing facts for the same user simultaneously (ensure thread safety via database constraints)
* **Empty history**: New user with no stored facts (return empty Evidence list, not error)
* **Large fact payloads**: Facts with long descriptions or many entities (enforce 4KB max per fact)
* **Consent revocation**: User downgrades from Tier 3 to Tier 2 (existing facts remain but become inaccessible until re-consent)
* **Clock skew**: Facts with timestamps in the future (reject, return `INVALID_TIMESTAMP`)
* **TTL edge cases**: Facts exactly at TTL boundary (use >= comparison, exclusive of expiry moment)

---

## Decision Rules (Deterministic Order)

Explicit, ordered rules evaluated **top to bottom**; first match wins:

1. **IF** `user_id` is null, empty, or not valid UUID format → Return `INVALID_USER_ID` error
2. **IF** `fact_text` is null or empty → Return `INVALID_FACT` error
3. **IF** `fact_text` exceeds 4KB → Return `FACT_TOO_LARGE` error
4. **IF** user's `context_tier` < 3 → Return `CONSENT_REQUIRED` error
5. **IF** `timestamp` is in the future (> now + 5min tolerance) → Return `INVALID_TIMESTAMP` error
6. **IF** fact with same `fact_hash` already exists for this user → Return existing fact (idempotent, no duplicate)
7. **IF** database connection fails during storage → Return `STORAGE_ERROR` with retry instructions
8. **ELSE** → Proceed with storage (normalize fact, compute hash, persist to database)

For retrieval operations:

1. **IF** `user_id` is missing or invalid → Return `INVALID_USER_ID` error
2. **IF** user's `context_tier` < 3 → Return `CONSENT_REQUIRED` error
3. **IF** query parameters are invalid (negative limit, invalid date range) → Return `INVALID_QUERY` error
4. **IF** query would return >500 results → Apply automatic pagination (default limit=50, max=500)
5. **ELSE** → Execute query, exclude expired facts, return results as Evidence Items

---

## Requirements *(mandatory)*

### Functional Requirements

* **FR-001: External Contract**
  * Storage Input: `user_id` (UUID), `fact_text` (string, max 4KB), `intent_type` (string), `entities` (JSON object), `outcome` (success/failure), `source_plan_id` (ULID, optional), `ttl_days` (integer, default 30)
  * Query Input: `user_id` (UUID), `intent_type` (string, optional), `entity_filter` (JSON, optional), `limit` (max 500, default 50), `recency_days` (filter, optional)
  * Output (success): Evidence Item array with type="history", fact data, confidence score, source_ref
  * Output (error): `{"status": "error", "error_code": "...", "message": "...", "details": {...}}`
  * Error codes: `INVALID_USER_ID`, `INVALID_FACT`, `FACT_TOO_LARGE`, `CONSENT_REQUIRED`, `INVALID_TIMESTAMP`, `STORAGE_ERROR`, `INVALID_QUERY`

* **FR-002: Execution Semantics**
  * All operations execute directly (no Preview/Execute distinction — internal Memory Layer component per GLOBAL_SPEC §1)
  * Storage operations persist immediately to PostgreSQL with async transaction
  * Query operations read from database with optional Redis caching
  * Consent verification synchronous before all operations

* **FR-003: Fact Normalization**
  * Store PII-light derived facts only (no raw provider data, no email content, no full names unless publicly available)
  * Compute SHA256 hash of `user_id + intent_type + fact_text + date` for deduplication
  * Store creation timestamp and expiry timestamp (created_at + ttl_days)
  * Entities stored as structured JSON (e.g., `{"person": "Alice", "day": "Tuesday", "time": "10:00"}`)

* **FR-004: Evidence Item Integration**
  * Return fact data as Evidence Items with type="history"
  * Include confidence score based on fact recency (newer = higher confidence, linear decay over TTL)
  * Set source_ref as "history:facts/{fact_id}"
  * Set ttl_days matching the fact's remaining TTL
  * Set tier=3 (historical data context tier)

* **FR-005: TTL and Expiration**
  * Default TTL: 30 days (configurable per fact category)
  * Expired facts excluded from all query results
  * Background cleanup job soft-deletes expired facts
  * Hard deletion after 90 days post-expiry (for audit compliance)

* **FR-006: Pattern Detection**
  * Detect recurring patterns from stored facts (same intent + entity + day-of-week)
  * Return patterns with frequency count, last occurrence, and confidence score
  * Pattern confidence: `min(1.0, occurrence_count / 5)` (5+ occurrences = full confidence)
  * Stale patterns (no occurrence in 30 days) have confidence reduced to 0

* **FR-007: Performance Requirements**
  * Fact storage: p95 < 100ms
  * Fact query by intent: p95 < 80ms
  * Pattern detection: p95 < 150ms
  * Support 1000 concurrent queries without degradation

* **FR-008: Security and Privacy**
  * Verify context_tier >= 3 before all storage and retrieval operations
  * No PII in stored facts (enforced by upstream PlanWriter, validated by History)
  * Audit log all storage and retrieval operations with correlation IDs
  * No sensitive data in logs (fact_text logged as truncated summary only)

### Key Entities

* **Fact**: A normalized, PII-light record of a past action. Fields: `fact_id` (UUID), `user_id` (UUID), `fact_text` (string), `intent_type` (string), `entities` (JSON), `outcome` (boolean), `source_plan_id` (ULID, optional), `fact_hash` (SHA256), `created_at` (timestamp), `expires_at` (timestamp), `ttl_days` (integer). Immutable once stored. One user has many facts.

* **FactPattern**: A detected recurring pattern from multiple facts. Fields: `pattern_id` (UUID), `user_id` (UUID), `intent_type` (string), `pattern_description` (string), `entity_pattern` (JSON), `occurrence_count` (integer), `last_seen` (timestamp), `confidence` (float 0-1). Updated as new facts arrive. Derived from Facts, not independently stored by callers.

---

## Invariants & Guarantees

Statements that must **always** hold true:

1. **Consent gate**: No facts stored or returned without verified context_tier >= 3
2. **PII-light**: All stored facts contain only derived, normalized information — never raw provider data or PII
3. **Fact immutability**: Facts are never modified after storage (append-only with soft-delete on expiry)
4. **Deduplication**: Same fact_hash for same user never stored twice (idempotent storage)
5. **TTL enforcement**: Expired facts never included in query results
6. **Audit completeness**: All storage and retrieval operations logged with user_id and correlation_id
7. **Deterministic queries**: Same query parameters always produce same result set (for given database state)
8. **Evidence format**: All returned data conforms to GLOBAL_SPEC §2.2 Evidence Item format
9. **Fact size limit**: No fact exceeds 4KB in size
10. **Temporal ordering**: Facts always returned in reverse chronological order (newest first)

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

* **SC-001**: Fact storage operations complete in p95 < 100ms (measured via distributed tracing)
* **SC-002**: Fact query by intent completes in p95 < 80ms
* **SC-003**: Pattern detection completes in p95 < 150ms
* **SC-004**: 99.5% availability for all History operations
* **SC-005**: Zero consent violations (100% enforcement, verified via contract tests)
* **SC-006**: Support storage of 100,000 facts per user with linear query performance
* **SC-007**: Deduplication success rate 100% (no duplicate fact_hash entries per user)

---

## Interfaces & Contracts

### Internal Component - No Preview/Execute Model

History is an internal backend component invoked by PlanWriter (storage) and ContextRAG (queries). It does **not** use the Preview/Execute model from GLOBAL_SPEC — that model applies to user-facing **plans**, not internal component operations.

### Storage Interface (for STORE_FACT)

```python
async def store_fact(
    user_id: str,
    fact_text: str,
    intent_type: str,
    entities: dict,
    outcome: bool,
    source_plan_id: str | None = None,
    ttl_days: int = 30
) -> StoreFactResult:
    """
    Store a derived fact from plan execution.

    Returns: {"status": "ok", "fact_id": "...", "stored_at": "2026-02-10T10:30:00Z"}
    """
```

### Query Interface (for GET_FACTS_BY_INTENT)

```python
async def get_facts_by_intent(
    user_id: str,
    intent_type: str | None = None,
    limit: int = 50,
    recency_days: int | None = None
) -> List[EvidenceItem]:
    """
    Query facts for a user, optionally filtered by intent type.

    Returns: List of Evidence Items with type="history"
    """
```

### Pattern Interface (for GET_PATTERNS)

```python
async def get_patterns(
    user_id: str,
    intent_type: str | None = None,
    min_confidence: float = 0.5
) -> List[FactPattern]:
    """
    Get detected recurring patterns for a user.

    Returns: List of FactPattern objects with confidence scores
    """
```

### Evidence Item Output

Fact data returned in Evidence Item format (GLOBAL_SPEC §2.2):

```json
{
  "type": "history",
  "key": "schedule_meeting_2026-02-10",
  "value": {
    "fact": "Booked 30min meeting with Alice at 10 AM on Tuesday",
    "intent_type": "schedule_meeting",
    "outcome": true,
    "entities": {"person": "Alice", "day": "Tuesday", "time": "10:00"},
    "age_days": 3
  },
  "confidence": 0.9,
  "source_ref": "history:facts/a1b2c3d4",
  "ttl_days": 27,
  "tier": 3
}
```

Reference: Evidence Item schema from `shared/schemas/evidence.py`

---

## Component Mapping

* **Target**: `components/History/`
* **Files expected to change**:
  * `api/routes.py` — FastAPI endpoints for fact storage, querying, and patterns
  * `service/fact_service.py` — Business logic for fact storage, deduplication, consent verification
  * `service/pattern_service.py` — Pattern detection and confidence scoring
  * `service/evidence_service.py` — Evidence Item format conversion
  * `domain/models.py` — Pydantic models for Fact, FactPattern, request/response schemas
  * `adapters/db.py` — SQLAlchemy database adapter (history table operations)
  * `adapters/cache.py` — Redis caching adapter (query result caching, graceful degradation)
  * `schemas/fact_storage.schema.json` — JSON schema for fact storage
  * `schemas/query_request.schema.json` — JSON schema for query parameters
  * `tests/test_domain.py` — Unit tests for domain models and validation
  * `tests/test_fact_service.py` — Unit tests for fact storage and retrieval logic
  * `tests/test_pattern_service.py` — Unit tests for pattern detection
  * `tests/test_adapters.py` — Unit tests for database adapter with mocks
  * `tests/test_api.py` — API endpoint tests with mocked services
  * `tests/test_contract.py` — Contract tests for Evidence Item compliance and consent enforcement
  * `tests/test_integration.py` — Integration tests for full storage-query-pattern flow

---

## Dependencies & Risks

### Dependencies

* **PostgreSQL 16**: Primary data store for fact persistence (`history` table with user_id index)
* **SQLAlchemy 2.0**: Async ORM for database operations
* **Pydantic v2**: Data validation for incoming facts and queries
* **FastAPI**: API framework with async support
* **Redis 7**: Optional caching layer for query results (graceful degradation if unavailable)
* **Shared Infrastructure**: Database adapter (`shared/database/adapter.py`), error handling (`shared/database/error_handler.py`), auth (`shared/api/auth.py`), Evidence Item schema (`shared/schemas/evidence.py`)

### Risks

* **Risk 1: PII leakage** — Facts could contain PII if upstream normalization fails
  * *Mitigation*: Validate facts against PII patterns before storage; reject facts containing detected PII; structured logging without fact content

* **Risk 2: TTL cleanup performance** — Large volume of expired facts could slow cleanup jobs
  * *Mitigation*: Background async cleanup; batch deletion; database index on `expires_at`; soft-delete with deferred hard-delete

* **Risk 3: Pattern detection accuracy** — False patterns from coincidental data
  * *Mitigation*: Minimum 5 occurrences for pattern confidence; 30-day staleness decay; configurable thresholds

* **Risk 4: Concurrent write conflicts** — Multiple PlanWriter instances storing facts simultaneously
  * *Mitigation*: fact_hash uniqueness constraint prevents duplicates; database-level conflict resolution; idempotent storage

* **Risk 5: Consent state changes** — User revokes Tier 3 consent after facts are stored
  * *Mitigation*: Check consent on every read; existing facts remain but become inaccessible; no retroactive deletion required

---

## Non-Functional Requirements

* **Inherit baseline** (from constitution.md):
  * Structured logs with no secrets/PII
  * 99.9% availability target

* **Deltas** (History-specific):
  * **Stricter latency targets**: Fact storage <100ms p95, fact query <80ms p95 (tighter than general component targets because ContextRAG has <150ms budget and History is one of multiple sources queried)
  * **TTL enforcement**: 30-day default with configurable overrides
  * **Storage retention**: 30-day active + 90-day soft-delete for audit
  * **PII validation**: Reject facts with detected PII patterns (upstream responsibility, but History validates)

---

## Open Questions

* **Q1**: Should pattern detection run on-write (compute patterns incrementally as facts arrive) or on-read (compute patterns at query time)?
  * **Proposed answer**: On-write for performance — update pattern aggregates incrementally when new facts are stored

* **Q2**: Should History support bulk fact import for backfilling historical data?
  * **Proposed answer**: Not in v1. Add bulk import endpoint in future iteration if needed.

* **Q3**: What PII patterns should History validate against? (Email, phone, SSN, etc.)
  * **Proposed answer**: Start with regex patterns for email, phone, SSN. Make pattern list configurable.

* **Q4**: Should expired facts be fully deleted or archived to cold storage?
  * **Proposed answer**: Soft-delete at TTL, hard-delete 90 days after expiry. No cold storage in v1.

* **Q5**: Should fact_text support structured format or remain free-text?
  * **Proposed answer**: Free-text with structured `entities` JSON alongside. Entities enable structured queries; fact_text provides human-readable context.

---

## Conformance

This work conforms to:

* `docs/architecture/GLOBAL_SPEC.md` v2 — Evidence Item format, context tiers, NFR requirements
* `docs/architecture/Project_HLD.md` v4.0 — Memory Layer component responsibilities
* `.specify/memory/constitution.md` v1.0.0 — Component-first architecture, test-first development
