# Tasks: History

**Created**: 2026-02-20
**Branch**: `feat/history-memory-layer`
**SPEC**: `specs/005-history-memory-layer/spec.md`
**LLD**: `components/History/LLD.md`

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
History is a **Memory Layer** component with direct database operations (no Preview/Execute model).
It stores normalized, PII-light facts derived from plan execution outcomes and surfaces recurring behavioral patterns.
Two database tables: `history` (facts) and `fact_patterns` (patterns).
Consumers: PlanWriter (storage), ContextRAG (queries), Planner (patterns).

### Reference Patterns
- Follow `components/ProfileStore/` conventions for component structure (completed Memory Layer component)
- Reuse shared infrastructure from `shared/database/`, `shared/api/`, `shared/schemas/`
- SQLAlchemy table models must be added to `shared/database/models.py` (HistoryTable, FactPatternTable)
- DI wiring follows existing pattern in `shared/app.py` (lifespan) and `shared/dependencies.py` (Depends)

---

## Phase 0: Setup & Dependencies

### Install Dependencies (from LLD Section: Dependencies & External Integrations)

- [ ] [T000] Verify Python packages are available in `pyproject.toml`
  - File: `/pyproject.toml`
  - Confirm all required packages already present:
    - `sqlalchemy[asyncio]>=2.0` (async ORM for PostgreSQL)
    - `asyncpg>=0.29` (async PostgreSQL driver)
    - `pydantic>=2.0` (data validation, domain models)
    - `fastapi>=0.109.0` (API framework with async support)
    - `redis[hiredis]>=5.0` (query result caching)
  - Confirm dev dependencies:
    - `pytest>=8.0.0`, `pytest-asyncio>=0.23.0` (async testing)
    - `httpx>=0.27` (API testing)
    - `pytest-cov>=4.1.0` (coverage)
    - `pytest-mock>=3.12.0` (mock utilities)
  - Add `pytest-benchmark>=4.0.0` to `[project.optional-dependencies] dev` if missing (needed for performance tests in Phase 6)
  - All packages already present in `pyproject.toml` -- verify no version conflicts

- [ ] [T001] Create component package structure with `__init__.py` files
  - `/components/History/__init__.py`
  - `/components/History/domain/__init__.py`
  - `/components/History/service/__init__.py`
  - `/components/History/adapters/__init__.py`
  - `/components/History/api/__init__.py`
  - `/components/History/schemas/__init__.py`
  - `/components/History/tests/__init__.py`
  - All `__init__.py` files should be empty (or contain only module docstring)

- [ ] [T002] Verify shared infrastructure availability
  - Confirm `shared/database/adapter.py` -- `SharedDatabaseAdapter`, `get_database_adapter()`
  - Confirm `shared/database/error_handler.py` -- `with_db_error_handling`, `with_user_existence_check()`, `execute_with_retry`, `DatabaseError`, `DatabaseConnectionError`, `DatabaseIntegrityError`, `UserNotFoundError`
  - Confirm `shared/database/models.py` -- `Base`, `UserTable` (History FK target)
  - Confirm `shared/api/error_handlers.py` -- `ErrorHandlerMixin`, `APIErrorHandler`, `ErrorResponse`
  - Confirm `shared/api/auth.py` -- `get_auth_context`, `verify_user_access`, `RequireTier3`, `require_context_tier(3)`
  - Confirm `shared/schemas/evidence.py` -- `EvidenceItem` Pydantic model (type="history", tier=3)
  - Note: `shared/database/models.py` currently has a commented-out `HistoryTable` placeholder -- this will be implemented in T100

---

## Phase 1: Schemas & Domain (Foundation)

### Acceptance Criteria: US-1 (Store Execution Facts), US-2 (Query Facts by Intent and User), FR-001 (External Contract), FR-003 (Fact Normalization)

- [ ] [T100] Add SQLAlchemy table models to shared database models
  - File: `/shared/database/models.py`
  - Replace the commented-out `HistoryTable` placeholder with full implementation
  - Add `HistoryTable` matching DDL from LLD:
    - `fact_id` (UUID PK, server_default gen_random_uuid)
    - `user_id` (UUID FK to users.user_id ON DELETE CASCADE, NOT NULL)
    - `fact_text` (String, NOT NULL)
    - `intent_type` (String(64), NOT NULL)
    - `entities` (JSONB, NOT NULL, server_default '{}')
    - `outcome` (Boolean, NOT NULL)
    - `source_plan_id` (String(26), nullable, ULID format)
    - `fact_hash` (String(64), NOT NULL, SHA256 hex)
    - `ttl_days` (Integer, NOT NULL, default=30)
    - `created_at` (DateTime(timezone=True), NOT NULL, server_default NOW())
    - `expires_at` (DateTime(timezone=True), NOT NULL)
    - `deleted_at` (DateTime(timezone=True), nullable)
  - Add indexes matching LLD DDL:
    - `idx_history_user_intent_active` (user_id, intent_type, created_at DESC) WHERE deleted_at IS NULL
    - `idx_history_user_fact_hash` (user_id, fact_hash) UNIQUE WHERE deleted_at IS NULL
    - `idx_history_expires_at` (expires_at) WHERE deleted_at IS NULL
    - `idx_history_user_entities` GIN (entities) WHERE deleted_at IS NULL
    - `idx_history_source_plan` (source_plan_id) WHERE source_plan_id IS NOT NULL
  - Add `FactPatternTable` matching DDL from LLD:
    - `pattern_id` (UUID PK, server_default gen_random_uuid)
    - `user_id` (UUID FK to users.user_id ON DELETE CASCADE, NOT NULL)
    - `intent_type` (String(64), NOT NULL)
    - `pattern_key` (String(128), NOT NULL)
    - `pattern_description` (String(512), NOT NULL)
    - `entity_pattern` (JSONB, NOT NULL, server_default '{}')
    - `occurrence_count` (Integer, NOT NULL, default=1)
    - `last_seen` (DateTime(timezone=True), NOT NULL)
    - `confidence` (Float, NOT NULL, default=0.0)
  - Add constraints:
    - `uq_fact_patterns_user_intent_key` UNIQUE (user_id, intent_type, pattern_key)
  - Add indexes:
    - `idx_fact_patterns_user_intent` (user_id, intent_type, confidence DESC)
    - `idx_fact_patterns_last_seen` (last_seen)

- [ ] [T101] Create domain models (Pydantic v2)
  - File: `/components/History/domain/models.py`
  - Follow ProfileStore `domain/models.py` pattern
  - **Entity models**:
    - `Fact(BaseModel)` -- immutable record of a past action
      - `fact_id: UUID` (default_factory=uuid4)
      - `user_id: UUID`
      - `fact_text: str` (Field max_length=4096)
      - `intent_type: str` (Field max_length=64)
      - `entities: dict` (Field default_factory=dict)
      - `outcome: bool`
      - `source_plan_id: str | None` (Field pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
      - `fact_hash: str` (Field max_length=64, SHA256 hex)
      - `ttl_days: int` (Field default=30, ge=1)
      - `created_at: datetime` (default_factory=lambda: datetime.now(timezone.utc))
      - `expires_at: datetime`
      - `deleted_at: datetime | None = None`
    - `FactPattern(BaseModel)` -- detected recurring behavioral pattern
      - `pattern_id: UUID` (default_factory=uuid4)
      - `user_id: UUID`
      - `intent_type: str` (Field max_length=64)
      - `pattern_key: str` (Field max_length=128)
      - `pattern_description: str` (Field max_length=512)
      - `entity_pattern: dict` (Field default_factory=dict)
      - `occurrence_count: int` (Field default=1, ge=1)
      - `last_seen: datetime`
      - `confidence: float` (Field default=0.0, ge=0.0, le=1.0)
  - **Request/Response models**:
    - `StoreFactRequest(BaseModel)` -- request body for storing a fact
      - `fact_text: str` (Field min_length=1, max_length=4096)
      - `intent_type: str` (Field min_length=1, max_length=64)
      - `entities: dict` (Field default_factory=dict)
      - `outcome: bool`
      - `source_plan_id: str | None = None`
      - `ttl_days: int` (Field default=30, ge=1, le=365)
    - `StoreFactResponse(BaseModel)` -- response from storing a fact
      - `status: str` ("ok" or "duplicate")
      - `fact_id: UUID`
      - `stored_at: datetime`
    - `QueryFactsResponse(BaseModel)` -- response containing queried facts
      - `evidence: list` (List of EvidenceItem dicts)
      - `total_count: int`
      - `returned_count: int`
    - `PatternsResponse(BaseModel)` -- response containing patterns
      - `patterns: list` (List of FactPattern dicts)
      - `total_count: int`
  - **Error classes** (from LLD Observability & Safety section):
    - `HistoryError(Exception)` -- base exception for History component
    - `FactTooLargeError(HistoryError)` -- fact_text exceeds 4KB limit
    - `InvalidTimestampError(HistoryError)` -- timestamp is in the future beyond tolerance
    - `ConsentRequiredError(HistoryError)` -- user context_tier < 3
    - `InvalidFactError(HistoryError)` -- fact_text is empty or invalid
    - `StorageError(HistoryError)` -- database operation failed after retries
    - `InvalidQueryError(HistoryError)` -- invalid query parameters
  - **Hash computation helper**:
    - `compute_fact_hash(user_id: UUID, intent_type: str, fact_text: str, date: date) -> str`
    - SHA256 of `str(user_id) + intent_type + fact_text + date.isoformat()`
    - Date granularity is calendar day (not timestamp) per LLD Design Decisions

- [ ] [T102] Create JSON schemas for fact storage and query
  - File: `/components/History/schemas/fact_storage.schema.json`
    - JSON Schema Draft 7 for fact storage input
    - Required fields: fact_text, intent_type, entities, outcome
    - Optional fields: source_plan_id, ttl_days
    - Constraints: fact_text max 4096 chars, intent_type max 64 chars, ttl_days 1-365
  - File: `/components/History/schemas/query_request.schema.json`
    - JSON Schema Draft 7 for query parameters
    - Optional fields: intent_type, limit (1-500), recency_days (>=1)
  - File: `/components/History/schemas/evidence_output.schema.json`
    - JSON Schema Draft 7 for Evidence Item output (type="history", tier=3)
    - Validates GLOBAL_SPEC section 2.2 compliance

- [ ] [T103] Write domain model unit tests (TDD -- write tests FIRST)
  - File: `/components/History/tests/test_domain.py`
  - Test all Pydantic model validation:
    - `Fact` model: valid fact creation, fact_text max_length enforcement (>4096 rejected), intent_type max_length enforcement, source_plan_id ULID pattern validation, ttl_days range validation
    - `FactPattern` model: valid pattern creation, confidence range (0.0-1.0), occurrence_count minimum (>=1)
    - `StoreFactRequest` model: valid request creation, empty fact_text rejected (min_length=1), ttl_days range (1-365)
    - `StoreFactResponse` model: status must be "ok" or "duplicate", serialization roundtrip
    - `QueryFactsResponse` model: evidence list can be empty, counts non-negative
    - `PatternsResponse` model: patterns list can be empty
    - `compute_fact_hash` helper: deterministic output, same inputs produce same hash, different dates produce different hashes, same fact_text on different days produces different hashes
  - Test error class hierarchy:
    - All error classes inherit from `HistoryError`
    - Error messages are descriptive
    - Error codes map to SPEC FR-001 (INVALID_USER_ID, INVALID_FACT, FACT_TOO_LARGE, CONSENT_REQUIRED, INVALID_TIMESTAMP, STORAGE_ERROR, INVALID_QUERY)

---

## Phase 2: Service Layer (Business Logic)

### Acceptance Criteria: US-1 (Store Facts), US-2 (Query Facts), US-3 (Detect Patterns), US-4 (TTL Expiration), FR-002 (Execution Semantics), FR-004 (Evidence Item Integration), FR-006 (Pattern Detection)

- [ ] [T200] Implement FactService
  - File: `/components/History/service/fact_service.py`
  - Class: `FactService`
  - Constructor accepts: `db_adapter: DatabaseAdapter`, `evidence_service: EvidenceService`, `pattern_service: PatternService`
  - Methods:
    - `async def store_fact(user_id: UUID, request: StoreFactRequest) -> StoreFactResponse`
      - Decision rules from SPEC (top-to-bottom, first match wins):
        1. Validate fact_text is not empty (InvalidFactError)
        2. Validate fact_text <= 4KB (FactTooLargeError)
        3. Validate timestamp not in future (>now + 5min tolerance) (InvalidTimestampError)
        4. Compute fact_hash = SHA256(user_id + intent_type + fact_text + date)
        5. Calculate expires_at = now + ttl_days
        6. Call db_adapter.insert_fact(fact)
        7. If duplicate fact_hash: return existing fact with status="duplicate" (idempotent, US-1 scenario 6)
        8. If new: call pattern_service.update_patterns_on_store(user_id, fact)
        9. Return StoreFactResponse(status="ok", fact_id, stored_at)
      - Maps to: US-1 scenarios 1-4, FR-003
    - `async def get_facts_by_intent(user_id: UUID, intent_type: str | None, limit: int, recency_days: int | None) -> QueryFactsResponse`
      - Validate query parameters (InvalidQueryError for negative limit, invalid date range)
      - Calculate recency_cutoff from recency_days if provided
      - Call db_adapter.query_facts(user_id, intent_type, limit, recency_cutoff)
      - Exclude expired facts (expires_at > now, enforced in query)
      - Convert each Fact to EvidenceItem via evidence_service.fact_to_evidence()
      - Sort by created_at DESC (newest first -- SPEC Invariant 10)
      - Apply automatic pagination: default limit=50, max=500 (Decision Rule 4 for retrieval)
      - Return QueryFactsResponse with evidence list, total_count, returned_count
      - Maps to: US-2 scenarios 1-4, FR-004
  - Structured logging:
    - `component="History"`, `op="store_fact"` or `op="query_facts"`
    - Log `user_id`, `fact_id`, `intent_type`, `outcome`, `storage_latency_ms` or `query_latency_ms`
    - NEVER log `fact_text` or `entities` (may contain derived personal info -- LLD No PII in Logs)

- [ ] [T201] Implement PatternService
  - File: `/components/History/service/pattern_service.py`
  - Class: `PatternService`
  - Constructor accepts: `db_adapter: DatabaseAdapter`
  - Methods:
    - `async def get_patterns(user_id: UUID, intent_type: str | None, min_confidence: float) -> PatternsResponse`
      - Call db_adapter.query_patterns(user_id, intent_type, min_confidence)
      - Filter out stale patterns (last_seen > 30 days ago -> confidence = 0, excluded if below min_confidence)
      - Return PatternsResponse with patterns list and total_count
      - Maps to: US-3 scenarios 1-3, FR-006
    - `async def update_patterns_on_store(user_id: UUID, fact: Fact) -> None`
      - Extract pattern_key from fact: `{intent_type}:{entity_key}:{day_of_week}`
        - Example: `schedule_meeting:person:Alice:Tuesday`
      - Generate pattern_description from fact context
      - Compute confidence: `min(1.0, occurrence_count / 5)` (FR-006)
      - Upsert pattern via db_adapter.upsert_pattern()
      - On-write incremental update (LLD Design Decision: O(1) database upsert per store_fact)
      - Maps to: US-3, FR-006, SPEC Q1 (on-write for performance)

- [ ] [T202] Implement EvidenceService
  - File: `/components/History/service/evidence_service.py`
  - Class: `EvidenceService`
  - Methods:
    - `def fact_to_evidence(fact: Fact) -> EvidenceItem`
      - Convert Fact to Evidence Item (GLOBAL_SPEC section 2.2)
      - `type="history"` (always)
      - `key=f"{fact.intent_type}_{fact.created_at.date().isoformat()}"` (SPEC Evidence Item Output example)
      - `value` = dict with fact, intent_type, outcome, entities, age_days
      - `confidence` = linear decay: `max(0.0, 1.0 - (age_days / ttl_days))` (LLD Confidence Decay Formula)
      - `source_ref=f"history:facts/{fact.fact_id}"` (FR-004)
      - `ttl_days` = remaining TTL (fact.ttl_days - age_days, minimum 1)
      - `tier=3` (always -- GLOBAL_SPEC section 7 Tier 3)
      - Maps to: FR-004, SPEC Evidence Item Output format

- [ ] [T203] Write service layer unit tests (TDD -- write tests FIRST)
  - File: `/components/History/tests/test_fact_service.py`
  - Tests for FactService:
    - Store fact with valid data -- success, status="ok" (US-1 scenario 1)
    - Store fact with failure outcome -- records fact with outcome=false (US-1 scenario 2)
    - Store fact with custom TTL override -- respects custom TTL (US-1 scenario 3)
    - Store fact with empty fact_text -- InvalidFactError (Decision Rule 2)
    - Store fact exceeding 4KB -- FactTooLargeError (Decision Rule 3)
    - Store fact with future timestamp -- InvalidTimestampError (Decision Rule 5)
    - Store duplicate fact_hash -- returns existing fact with status="duplicate" (Decision Rule 6, Invariant 4)
    - Store fact triggers pattern update (verify pattern_service.update_patterns_on_store called)
    - Query facts by intent -- returns matching Evidence Items sorted by recency (US-2 scenario 1)
    - Query facts by intent_type filter -- no cross-intent leakage (US-2 scenario 2)
    - Query facts excludes expired facts (US-2 scenario 3, Invariant 5)
    - Query facts with limit -- returns correct number (US-2 scenario 4)
    - Query facts for new user -- returns empty list, not error (Edge Case: empty history)
  - File: `/components/History/tests/test_pattern_service.py`
  - Tests for PatternService:
    - Get patterns with confidence above threshold (US-3 scenario 1)
    - Get patterns with stale pattern (>30 days) excluded (US-3 scenario 2)
    - Get patterns filtered by intent_type (US-3 scenario 3)
    - Update patterns on store -- new pattern created
    - Update patterns on store -- existing pattern incremented (occurrence_count + 1)
    - Pattern confidence formula: `min(1.0, count / 5)` verified for counts 1-10 (FR-006)
  - File: `/components/History/tests/test_evidence_service.py`
  - Tests for EvidenceService:
    - fact_to_evidence returns correct Evidence Item format
    - Evidence Item type is always "history"
    - Evidence Item tier is always 3
    - Confidence decay: new fact (age=0) has confidence ~1.0
    - Confidence decay: fact at 50% of TTL has confidence ~0.5
    - Confidence decay: expired fact has confidence 0.0
    - source_ref follows "history:facts/{fact_id}" format
    - ttl_days reflects remaining TTL
  - All tests use mocked adapters (MagicMock, AsyncMock patterns from ProfileStore)

---

## Phase 3: Adapters (External Integrations)

### Acceptance Criteria: FR-002 (Execution Semantics), FR-005 (TTL and Expiration), FR-007 (Performance), FR-008 (Security), SPEC Invariant 4 (Deduplication)

- [ ] [T300] Implement DatabaseAdapter
  - File: `/components/History/adapters/db.py`
  - Class: `DatabaseAdapter`
  - Follow ProfileStore `adapters/db.py` pattern exactly:
    - Constructor: `self.shared_db = get_database_adapter()` from `shared/database/adapter.py`
    - Use `@with_db_error_handling` decorator from `shared/database/error_handler.py`
    - Import `HistoryTable`, `FactPatternTable` from `shared/database/models.py`
  - Methods (matching LLD Adapters section):
    - `@with_db_error_handling async def insert_fact(self, fact: Fact) -> tuple[Fact, bool]`
      - `INSERT INTO history ... ON CONFLICT (user_id, fact_hash) WHERE deleted_at IS NULL DO NOTHING`
      - Returns `(fact, is_new)`: inserted fact + boolean indicating new vs existing
      - Uses `async with self.shared_db.get_session() as session`
      - Handles idempotent deduplication via unique index (Invariant 4, SPEC Decision Rule 6)
    - `@with_db_error_handling async def query_facts(self, user_id: UUID, intent_type: str | None, limit: int, recency_cutoff: datetime | None) -> list[Fact]`
      - `SELECT ... WHERE user_id = :uid AND deleted_at IS NULL AND expires_at > NOW()`
      - Optional: `AND intent_type = :it` (if intent_type provided)
      - Optional: `AND created_at >= :cutoff` (if recency_cutoff provided)
      - `ORDER BY created_at DESC LIMIT :limit`
      - Uses index `idx_history_user_intent_active`
    - `@with_db_error_handling async def count_facts(self, user_id: UUID, intent_type: str | None) -> int`
      - Returns total count matching filters (for QueryFactsResponse.total_count)
    - `@with_db_error_handling async def upsert_pattern(self, pattern: FactPattern) -> None`
      - `INSERT INTO fact_patterns ... ON CONFLICT (user_id, intent_type, pattern_key) DO UPDATE SET occurrence_count = occurrence_count + 1, last_seen = EXCLUDED.last_seen, confidence = EXCLUDED.confidence`
      - Uses constraint `uq_fact_patterns_user_intent_key`
    - `@with_db_error_handling async def query_patterns(self, user_id: UUID, intent_type: str | None, min_confidence: float) -> list[FactPattern]`
      - `SELECT ... WHERE user_id = :uid AND confidence >= :min_conf`
      - Optional: `AND intent_type = :it`
      - `ORDER BY confidence DESC, last_seen DESC`
      - Uses index `idx_fact_patterns_user_intent`
    - `@with_db_error_handling async def cleanup_expired_facts(self, batch_size: int = 500) -> int`
      - `UPDATE history SET deleted_at = NOW() WHERE expires_at < NOW() AND deleted_at IS NULL LIMIT :batch_size`
      - Returns count of soft-deleted rows
      - Maps to: US-4, FR-005
    - `@with_db_error_handling async def hard_delete_old_facts(self, days_after_expiry: int = 90, batch_size: int = 500) -> int`
      - `DELETE FROM history WHERE deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL ':days days' LIMIT :batch_size`
      - Returns count of hard-deleted rows
      - Maps to: FR-005 (90-day post-expiry hard delete)
    - `async def health_check(self) -> bool`
      - Delegates to `self.shared_db.health_check()`

- [ ] [T301] Implement CacheAdapter (Redis, optional graceful degradation)
  - File: `/components/History/adapters/cache.py`
  - Class: `CacheAdapter`
  - Redis caching for query results (SPEC FR-002: "Query operations read from database with optional Redis caching")
  - Constructor: accepts optional Redis URL from environment
  - Methods:
    - `async def get_cached_facts(self, cache_key: str) -> list[dict] | None`
      - Returns cached query results or None on miss/error
    - `async def set_cached_facts(self, cache_key: str, facts: list[dict], ttl_seconds: int = 300) -> None`
      - Caches query results with 5-minute TTL
    - `async def invalidate_user_cache(self, user_id: UUID) -> None`
      - Invalidate all cached queries for a user (called on store_fact)
    - `def build_cache_key(self, user_id: UUID, intent_type: str | None, limit: int) -> str`
      - Deterministic cache key: `history:facts:{user_id}:{intent_type or 'all'}:{limit}`
  - **Graceful degradation**: All methods catch Redis exceptions and log warnings; never raise to caller
  - Redis key pattern: `history:facts:{user_id}:*` with 5-minute TTL
  - Maps to: SPEC Dependencies ("Redis 7: Optional caching layer for query results (graceful degradation if unavailable)")

- [ ] [T302] Write adapter unit tests (TDD -- write tests FIRST)
  - File: `/components/History/tests/test_adapters.py`
  - Tests for DatabaseAdapter:
    - insert_fact -- success path, new fact returns (fact, True)
    - insert_fact -- duplicate fact_hash returns (existing_fact, False) (idempotent)
    - query_facts -- returns facts sorted by created_at DESC
    - query_facts -- excludes expired facts (expires_at <= now filtered out)
    - query_facts -- filters by intent_type when provided
    - query_facts -- respects limit parameter
    - query_facts -- applies recency_cutoff filter
    - upsert_pattern -- creates new pattern
    - upsert_pattern -- increments existing pattern occurrence_count
    - query_patterns -- returns patterns above confidence threshold
    - query_patterns -- filters by intent_type when provided
    - cleanup_expired_facts -- soft-deletes expired rows, returns count
    - hard_delete_old_facts -- hard-deletes old soft-deleted rows, returns count
    - health_check -- passes and fails
  - Tests for CacheAdapter:
    - get_cached_facts -- cache hit returns data
    - get_cached_facts -- cache miss returns None
    - get_cached_facts -- Redis error returns None (graceful degradation)
    - set_cached_facts -- stores with TTL
    - set_cached_facts -- Redis error silently ignored
    - invalidate_user_cache -- removes cached entries
    - build_cache_key -- deterministic key generation
  - All adapter tests use mocks (MagicMock for database sessions, MagicMock for Redis client)

---

## Phase 4: API Handlers (Thin Wrappers)

### Acceptance Criteria: FR-001 (External Contract), SPEC Interfaces & Contracts section

- [ ] [T400] Create API routes (thin wrappers)
  - File: `/components/History/api/routes.py`
  - Follow ProfileStore `api/routes.py` pattern exactly:
    - `router = APIRouter(prefix="/history", tags=["history"])`
    - `error_handler = ErrorHandlerMixin()`
    - Dependency injection: `get_fact_service()`, `get_pattern_service()` from `shared/dependencies.py`
  - Endpoints (matching LLD API Handlers section):
    - `POST /history/{user_id}/facts` -- `store_fact_endpoint`
      - Thin wrapper: delegates to `FactService.store_fact()`
      - Auth: `Depends(get_auth_context)`, `Depends(RequireTier3)`, `verify_user_access(user_id, auth_context)`
      - Input: `user_id: UUID` (path), `request: StoreFactRequest` (body)
      - Output: `StoreFactResponse`
      - Error handling: FactTooLargeError -> 400, InvalidFactError -> 400, InvalidTimestampError -> 400, StorageError -> 500
      - Maps to: SPEC Storage Interface, LLD Store Fact Endpoint
    - `GET /history/{user_id}/facts` -- `query_facts_endpoint`
      - Thin wrapper: delegates to `FactService.get_facts_by_intent()`
      - Auth: `Depends(get_auth_context)`, `Depends(RequireTier3)`, `verify_user_access(user_id, auth_context)`
      - Input: `user_id: UUID` (path), `intent_type: str | None` (query), `limit: int = Query(default=50, le=500, ge=1)` (query), `recency_days: int | None = Query(default=None, ge=1)` (query)
      - Output: `QueryFactsResponse`
      - Error handling: InvalidQueryError -> 400
      - Maps to: SPEC Query Interface, LLD Query Facts Endpoint
    - `GET /history/{user_id}/patterns` -- `query_patterns_endpoint`
      - Thin wrapper: delegates to `PatternService.get_patterns()`
      - Auth: `Depends(get_auth_context)`, `Depends(RequireTier3)`, `verify_user_access(user_id, auth_context)`
      - Input: `user_id: UUID` (path), `intent_type: str | None` (query), `min_confidence: float = Query(default=0.5, ge=0.0, le=1.0)` (query)
      - Output: `PatternsResponse`
      - Maps to: SPEC Pattern Interface, LLD Query Patterns Endpoint
    - `GET /history/health` -- `health_check`
      - No authentication required
      - Checks database health via adapter
  - All endpoints use `X-Plan-ID` header for correlation logging (following ProfileStore pattern)
  - Error responses use `shared/api/error_handlers.py` patterns (`ErrorHandlerMixin.handle_service_errors`)

- [ ] [T401] Wire History into shared DI (dependencies.py + app.py)
  - File: `/shared/dependencies.py`
  - Add two new Depends() functions:
    ```python
    def get_fact_service(request: Request) -> Any:
        """Get FactService singleton from app state."""
        return request.app.state.fact_service

    def get_pattern_service(request: Request) -> Any:
        """Get PatternService singleton from app state."""
        return request.app.state.pattern_service
    ```
  - File: `/shared/app.py`
  - In `lifespan()` function, add History service initialization (after ProfileStore block):
    ```python
    # History services
    from components.History.adapters.db import DatabaseAdapter as HistoryDBAdapter
    from components.History.service.evidence_service import EvidenceService
    from components.History.service.pattern_service import PatternService
    from components.History.service.fact_service import FactService

    history_db = HistoryDBAdapter()
    evidence_service = EvidenceService()
    pattern_service = PatternService(db_adapter=history_db)
    app.state.fact_service = FactService(
        db_adapter=history_db,
        evidence_service=evidence_service,
        pattern_service=pattern_service,
    )
    app.state.pattern_service = pattern_service
    ```
  - In `create_app()` function, register History router:
    ```python
    from components.History.api.routes import router as history_router
    app.include_router(history_router)
    ```

- [ ] [T402] Write API handler tests (TDD -- write tests FIRST)
  - File: `/components/History/tests/test_api.py`
  - Tests (following ProfileStore `tests/test_preferences.py` pattern):
    - POST /history/{user_id}/facts with valid data -- 200 success, StoreFactResponse returned (US-1 scenario 1)
    - POST /history/{user_id}/facts with empty fact_text -- 400 INVALID_FACT
    - POST /history/{user_id}/facts with oversized fact_text -- 400 FACT_TOO_LARGE
    - POST /history/{user_id}/facts without Tier 3 consent -- 403 CONSENT_REQUIRED (US-1 scenario 4)
    - POST /history/{user_id}/facts accessing other user's data -- 403 Forbidden
    - GET /history/{user_id}/facts -- returns QueryFactsResponse with Evidence Items
    - GET /history/{user_id}/facts?intent_type=schedule_meeting -- filters by intent
    - GET /history/{user_id}/facts?limit=5 -- respects limit parameter
    - GET /history/{user_id}/facts without Tier 3 consent -- 403 CONSENT_REQUIRED
    - GET /history/{user_id}/patterns -- returns PatternsResponse
    - GET /history/{user_id}/patterns?min_confidence=0.8 -- filters by confidence
    - GET /history/{user_id}/patterns without Tier 3 consent -- 403 CONSENT_REQUIRED
    - GET /history/health -- returns health status (no auth required)
    - All error responses match ErrorResponse schema from `shared/api/error_handlers.py`
  - Use mocked services via `app.dependency_overrides` (same pattern as ProfileStore tests)

---

## Phase 5: Fault Isolation & Safety (Architectural)

### From MODULAR_ARCHITECTURE.md, LLD Architectural Considerations, Constitution VII, SPEC FR-008

- [ ] [T500] Implement PII validation on fact storage
  - File: `/components/History/service/fact_service.py` (enhance store_fact from T200)
  - Add PII detection before storage (SPEC FR-008, Risk 1):
    - Regex patterns for email addresses (`[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}`)
    - Regex patterns for phone numbers (US format, international format)
    - Regex patterns for SSN (`\d{3}-\d{2}-\d{4}`)
  - If PII detected in fact_text: reject with `InvalidFactError("PII detected in fact text")`
  - Log PII rejection without logging the fact content (no PII in logs)
  - Make PII pattern list configurable (SPEC Q3: "Make pattern list configurable")
  - Defense in depth: History validates even though PlanWriter is responsible for normalization

- [ ] [T501] Implement consent enforcement verification
  - Verify consent enforcement is complete across all paths:
  - File: `/components/History/api/routes.py` -- all 3 data endpoints use `Depends(RequireTier3)`
  - File: `/components/History/service/fact_service.py` -- no service-level consent bypass possible
  - Invariant 1: "No facts stored or returned without verified context_tier >= 3"
  - Test: attempt to access facts with context_tier=1 and context_tier=2 -- both must be rejected
  - Maps to: SPEC Invariant 1, US-1 scenario 4, Decision Rules

- [ ] [T502] Add structured logging (correlation: plan_id/user_id/component)
  - Files: All service and adapter files
  - All log entries include structured metadata (LLD Observability section):
    - `component="History"`, `op=<operation_name>`
    - `user_id`, `fact_id`, `intent_type`, `outcome` (fact storage)
    - `user_id`, `intent_type`, `result_count`, `query_latency_ms` (fact query)
    - `plan_id` from request headers when available (X-Plan-ID)
    - Latency timing for performance tracking
    - Error classification for failure analysis
  - Maps to: Constitution VI, SPEC FR-008

- [ ] [T503] Verify no PII in logs
  - Files: All files in `/components/History/` (verification pass)
  - Review all log statements to ensure (LLD No PII in Logs section):
    - `fact_text` NEVER logged (may contain derived personal info)
    - `entities` NEVER logged (may contain names, times)
    - Only `fact_id`, `intent_type`, `outcome`, and counts are logged
    - `user_id` logged as-is (UUID, not PII)
    - Error details do not contain fact content
  - Maps to: Constitution VI, SPEC FR-008, LLD Observability

- [ ] [T504] Validate determinism guarantees
  - Files: `/components/History/service/fact_service.py`, `/components/History/domain/models.py`
  - Verify determinism properties (LLD Determinism Guarantees section):
    - **Fact storage**: Same `fact_hash` always produces same idempotent result
    - **Hash computation**: SHA256 of `user_id + intent_type + fact_text + date` is deterministic
    - **Query results**: Deterministic ordering by `created_at DESC` for given database state (Invariant 7)
    - **Pattern detection**: Deterministic confidence formula: `min(1.0, occurrence_count / 5)`
  - Add assertions in FactService.store_fact() verifying hash determinism
  - Maps to: Constitution V, SPEC Invariant 7, LLD Determinism Guarantees

---

## Phase 6: Contract Tests & Integration

### Acceptance Criteria: SC-001 through SC-007, Invariants 1-10

- [ ] [T600] Write contract tests (GLOBAL_SPEC compliance)
  - File: `/components/History/tests/test_contract.py`
  - Follow ProfileStore `tests/test_contract.py` pattern exactly
  - **TestGlobalSpecCompliance**:
    - Evidence Item format compliance (type="history", tier=3, source_ref="history:facts/{id}")
    - Evidence Item JSON serialization roundtrip
    - Confidence score range (0.0-1.0) and linear decay validation
    - Tier 3 data source compliance (GLOBAL_SPEC section 7)
    - Evidence Item key format: `{intent_type}_{date}`
    - Value structure includes fact, intent_type, outcome, entities, age_days
  - **TestConsentEnforcement**:
    - Tier 1 consent denied for fact storage and query (context_tier < 3)
    - Tier 2 consent denied for fact storage and query (context_tier < 3)
    - Tier 3 consent allowed (exact minimum tier)
    - Tier 4 consent allowed (cumulative consent, GLOBAL_SPEC section 7)
    - Consent revocation: facts remain but become inaccessible (Edge Case)
  - **TestErrorCodeContract**:
    - All error codes match SPEC FR-001 (INVALID_USER_ID, INVALID_FACT, FACT_TOO_LARGE, CONSENT_REQUIRED, INVALID_TIMESTAMP, STORAGE_ERROR, INVALID_QUERY)
    - Error classes have required attributes for API error responses
    - HistoryError is base class for all History-specific exceptions
  - **TestInvariantCompliance**:
    - Invariant 1: Consent gate (no facts stored/returned without tier >= 3)
    - Invariant 2: PII-light (PII detected in fact_text causes rejection)
    - Invariant 3: Fact immutability (facts never modified after storage, append-only)
    - Invariant 4: Deduplication (same fact_hash never stored twice per user)
    - Invariant 5: TTL enforcement (expired facts excluded from query results)
    - Invariant 7: Deterministic queries (same parameters produce same result set)
    - Invariant 8: Evidence format (GLOBAL_SPEC section 2.2 compliance)
    - Invariant 9: Fact size limit (no fact exceeds 4KB)
    - Invariant 10: Temporal ordering (facts returned newest first)
  - **TestPreviewExecuteModelCompliance**:
    - History does NOT use Preview/Execute wrappers (internal component)
    - Service methods execute directly (no preview_/execute_ methods)
    - Verify FactService and PatternService have no preview/execute method prefixes

- [ ] [T601] Write integration tests
  - File: `/components/History/tests/test_integration.py`
  - End-to-end flow tests with mocked database:
    - **Full storage-query flow**: store_fact -> query_facts -> verify Evidence Items returned correctly
    - **Pattern accumulation flow**: store 5 facts with same intent+entity pattern -> query_patterns -> verify pattern detected with confidence = 1.0
    - **Deduplication flow**: store same fact twice -> verify single storage, second returns status="duplicate"
    - **TTL flow**: store fact with short TTL -> simulate time passage -> verify excluded from query results
    - **Cross-intent isolation**: store facts for different intents -> query by specific intent -> verify no cross-intent leakage
    - **Empty user flow**: query for user with no facts -> verify empty evidence list returned (not error)
    - **Confidence decay flow**: store fact -> verify confidence decreases over time
  - Service layer integration:
    - FactService + EvidenceService integration (Evidence Items formatted correctly)
    - FactService + PatternService integration (patterns updated on store)

- [ ] [T602] Write performance benchmark tests
  - File: `/components/History/tests/test_performance.py`
  - Performance targets from SPEC SC-001 through SC-003:
    - Fact storage: p95 < 100ms (SC-001)
    - Fact query by intent: p95 < 80ms (SC-002)
    - Pattern detection: p95 < 150ms (SC-003)
  - Use pytest-benchmark for measurement
  - Tests with mocked database (measure service/adapter overhead, not actual DB)
  - Verify 4KB fact size limit enforcement does not add significant latency

- [ ] [T603] Validate CI pipeline compatibility
  - Ensure all test files discovered by pytest configuration in `pyproject.toml`
    - `testpaths = ["tests", "components"]` already covers `/components/History/tests/`
    - `python_files = ["test_*.py"]` matches all test file names
  - Verify `ruff check` passes on all new files (line length 100, Python 3.11+ target)
  - Verify `ruff format` passes (double quotes, space indent)
  - Verify `mypy --strict` passes on all new files
  - Verify JSON schemas pass any schema-validation CI job
  - Run full test suite locally before PR:
    - `pytest components/History/tests/ -v --tb=short`
    - `ruff check components/History/ shared/database/models.py shared/dependencies.py shared/app.py`
    - `mypy components/History/ --strict`

---

## Task Summary

- **Total Tasks**: 22
- **Phase 0 (Setup)**: T000-T002 (3 tasks)
- **Phase 1 (Schemas/Domain)**: T100-T103 (4 tasks)
- **Phase 2 (Service Layer)**: T200-T203 (4 tasks)
- **Phase 3 (Adapters)**: T300-T302 (3 tasks)
- **Phase 4 (API/DI)**: T400-T402 (3 tasks)
- **Phase 5 (Safety)**: T500-T504 (5 tasks)
- **Phase 6 (Tests/Integration)**: T600-T603 (4 tasks)

---

## Dependencies

### External (from LLD Section: Dependencies & External Integrations)

| Package | Version | Purpose |
|---------|---------|---------|
| `sqlalchemy[asyncio]` | `>=2.0,<3.0` | Async ORM for PostgreSQL |
| `asyncpg` | `>=0.29` | High-performance async PostgreSQL driver |
| `pydantic` | `>=2.0,<3.0` | Data validation, domain models |
| `fastapi` | `>=0.109.0` | API framework with async support |
| `redis[hiredis]` | `>=5.0` | Optional query result caching (graceful degradation) |

### Development/Testing

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | `>=8.0.0` | Test framework |
| `pytest-asyncio` | `>=0.23.0` | Async test support |
| `pytest-cov` | `>=4.1.0` | Coverage reporting |
| `pytest-mock` | `>=3.12.0` | Mock utilities |
| `httpx` | `>=0.27` | API testing |
| `pytest-benchmark` | `>=4.0.0` | Performance testing (may need to be added to pyproject.toml) |

### Internal (Shared Infrastructure)

| Component | File | What it provides |
|-----------|------|------------------|
| Shared Database | `/shared/database/adapter.py` | `SharedDatabaseAdapter`, `get_database_adapter()` |
| Shared DB Errors | `/shared/database/error_handler.py` | `@with_db_error_handling`, `@with_user_existence_check()`, `execute_with_retry()`, error classes |
| Shared DB Models | `/shared/database/models.py` | `Base`, `UserTable` (FK target); `HistoryTable`, `FactPatternTable` (added in T100) |
| Shared API Errors | `/shared/api/error_handlers.py` | `ErrorHandlerMixin`, `APIErrorHandler`, `ErrorResponse` |
| Shared Auth | `/shared/api/auth.py` | `get_auth_context()`, `verify_user_access()`, `RequireTier3` |
| Shared Evidence | `/shared/schemas/evidence.py` | `EvidenceItem` Pydantic model |
| Shared Dependencies | `/shared/dependencies.py` | `get_fact_service()`, `get_pattern_service()` (added in T401) |
| Shared App Factory | `/shared/app.py` | `lifespan()` DI wiring, `create_app()` router registration (updated in T401) |

### Component Dependencies

**None** -- History is a foundation Memory Layer component with no upstream component dependencies. It is called by PlanWriter (storage), ContextRAG (queries), and Planner (patterns), but does not depend on any other components.

---

## Architectural Considerations

### Blast Radius (from LLD)

- **If History fails**:
  - ContextRAG receives fewer Evidence Items (degrades plan quality, does not block planning)
  - PlanWriter fact storage fails (facts lost until recovery, plan execution unaffected)
  - Pattern queries return empty (Planner uses other signals from ProfileStore and PlanLibrary)
  - No impact on ProfileStore, PlanLibrary, or any orchestration component
- **Containment**: Database connection pooling with `pool_pre_ping=True` for stale connection detection. Retry with exponential backoff (1s, 2s, 4s) on transient database errors via `execute_with_retry`. No cascading failures since History does not call other components.

### Determinism (from LLD)

- **Fact storage**: Same `fact_hash` always produces same idempotent result (SHA256 deterministic)
- **Hash computation**: `SHA256(user_id + intent_type + fact_text + date)` -- date granularity is calendar day, not timestamp. Same fact on same day from retried plan execution deduplicates. Different days producing same fact_text are distinct facts.
- **Query results**: Deterministic ordering by `created_at DESC` for given database state
- **Pattern detection**: Deterministic confidence formula: `min(1.0, occurrence_count / 5)`

### Preview/Execute Model

- **Not applicable**: History is an internal Memory Layer component. GLOBAL_SPEC section 1 explicitly states the Preview/Execute model applies to user-facing plans, not internal component operations. All operations execute directly without Preview/Execute wrappers.

### Performance Targets (from SPEC)

| Operation | Target p95 | SPEC Reference | GLOBAL_SPEC Constraint |
|-----------|-----------|----------------|----------------------|
| Fact storage | < 100ms | SC-001 | History is one of multiple ContextRAG sources |
| Fact query (by intent) | < 80ms | SC-002 | ContextRAG has < 150ms p95 budget |
| Pattern query | < 150ms | SC-003 | Planner latency budget |

---

## Implementation Order (Recommended)

The recommended execution order respects dependencies between tasks:

1. **Phase 0** (T000, T001, T002) -- setup, can be done in parallel
2. **Phase 1** (T103 first for TDD, then T100, T101, T102) -- domain foundation; T100 (shared models) must come before adapters
3. **Phase 3** (T302 first for TDD, then T300, T301) -- adapters before services need them
4. **Phase 2** (T203 first for TDD, then T200, T201, T202) -- services depend on adapters and domain models
5. **Phase 4** (T402 first for TDD, then T400, T401) -- API depends on services; T401 wires DI
6. **Phase 5** (T500-T504) -- safety enhancements on top of working code
7. **Phase 6** (T600-T603) -- contract and integration tests validate everything

Within each phase, write tests first (TDD) per constitution mandate.

---

## SPEC Acceptance Criterion Traceability

| SPEC Criterion | Task(s) | Phase |
|---------------|---------|-------|
| US-1 Scenario 1 (store success fact) | T200, T203, T300, T400 | 2, 3, 4 |
| US-1 Scenario 2 (store failure fact) | T200, T203 | 2 |
| US-1 Scenario 3 (custom TTL override) | T200, T203 | 2 |
| US-1 Scenario 4 (consent required) | T400, T501, T600 | 4, 5, 6 |
| US-2 Scenario 1 (query by intent, sorted by recency) | T200, T203, T300, T400 | 2, 3, 4 |
| US-2 Scenario 2 (intent_type filter, no cross-intent) | T200, T203, T300, T601 | 2, 3, 6 |
| US-2 Scenario 3 (expired facts excluded) | T200, T300, T601 | 2, 3, 6 |
| US-2 Scenario 4 (limit and pagination) | T200, T300, T400 | 2, 3, 4 |
| US-3 Scenario 1 (pattern detection with confidence) | T201, T203, T300 | 2, 3 |
| US-3 Scenario 2 (stale pattern decay) | T201, T203 | 2 |
| US-3 Scenario 3 (patterns filtered by intent) | T201, T300, T400 | 2, 3, 4 |
| US-4 Scenario 1 (TTL cleanup soft-delete) | T300, T601 | 3, 6 |
| US-4 Scenario 2 (custom TTL respected) | T200, T300 | 2, 3 |
| FR-001 (External Contract) | T101, T102, T400 | 1, 4 |
| FR-002 (Execution Semantics) | T200, T300 | 2, 3 |
| FR-003 (Fact Normalization) | T101, T200 | 1, 2 |
| FR-004 (Evidence Item Integration) | T202, T203, T600 | 2, 6 |
| FR-005 (TTL and Expiration) | T300, T601 | 3, 6 |
| FR-006 (Pattern Detection) | T201, T203 | 2 |
| FR-007 (Performance) | T602 | 6 |
| FR-008 (Security and Privacy) | T500, T502, T503 | 5 |
| SC-001 (storage p95 < 100ms) | T602 | 6 |
| SC-002 (query p95 < 80ms) | T602 | 6 |
| SC-003 (pattern p95 < 150ms) | T602 | 6 |
| SC-005 (zero consent violations) | T501, T600 | 5, 6 |
| SC-007 (deduplication 100%) | T300, T600, T601 | 3, 6 |
| Edge Case: Duplicate facts | T200, T300, T601 | 2, 3, 6 |
| Edge Case: Empty history | T200, T601 | 2, 6 |
| Edge Case: Large fact payloads | T200, T500 | 2, 5 |
| Edge Case: Consent revocation | T501, T600 | 5, 6 |
| Edge Case: Clock skew | T200, T203 | 2 |
| Invariant 1 (consent gate) | T501, T600 | 5, 6 |
| Invariant 4 (deduplication) | T300, T504, T600 | 3, 5, 6 |
| Invariant 5 (TTL enforcement) | T300, T600 | 3, 6 |
| Invariant 7 (deterministic queries) | T504, T600 | 5, 6 |
| Invariant 8 (Evidence format) | T202, T600 | 2, 6 |
| Invariant 10 (temporal ordering) | T300, T600 | 3, 6 |
