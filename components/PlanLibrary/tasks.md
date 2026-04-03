# Tasks: PlanLibrary

**Created**: 2026-02-11
**Branch**: `004-feature-title-planlibrary`
**SPEC**: `specs/004-feature-title-planlibrary/spec.md`
**LLD**: `components/PlanLibrary/LLD.md`

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
PlanLibrary is a **Memory Layer** component with direct database operations (no Preview/Execute model).
All code was previously deleted for a clean rewrite. The directory currently contains only `LLD.md` and `diagrams/flow.md`.

### Reference Patterns
- Follow `components/ProfileStore/` conventions for component structure
- Reuse shared infrastructure from `shared/database/`, `shared/api/`, `shared/schemas/`
- SQLAlchemy table models already exist in `shared/database/models.py` (PlanTable, PlanOutcomeTable, PlanMetricsTable)

---

## Phase 0: Setup & Dependencies

### Install Dependencies (from LLD Section 7)

- [ ] [T000] Verify Python packages are available in `pyproject.toml`
  - Confirm `sqlalchemy>=2.0`, `asyncpg>=0.29`, `pydantic>=2.0`, `fastapi>=0.109.0`, `ulid-py>=1.1.0` are listed
  - All packages already present in `pyproject.toml` -- verify no version conflicts
  - Add `pytest-benchmark>=4.0.0` to `[project.optional-dependencies] dev` if missing

- [ ] [T001] Create component package structure with `__init__.py` files
  - `/components/PlanLibrary/__init__.py`
  - `/components/PlanLibrary/domain/__init__.py`
  - `/components/PlanLibrary/service/__init__.py`
  - `/components/PlanLibrary/adapters/__init__.py`
  - `/components/PlanLibrary/api/__init__.py`
  - `/components/PlanLibrary/schemas/__init__.py`
  - `/components/PlanLibrary/tests/__init__.py`

- [ ] [T002] Verify shared infrastructure availability
  - Confirm `shared/database/adapter.py` -- `SharedDatabaseAdapter`, `get_database_adapter()`
  - Confirm `shared/database/error_handler.py` -- `with_db_error_handling`, `execute_with_retry`, `DatabaseError`, `DatabaseConnectionError`, `DatabaseIntegrityError`
  - Confirm `shared/database/models.py` -- `PlanTable`, `PlanOutcomeTable`, `PlanMetricsTable`
  - Confirm `shared/api/error_handlers.py` -- `ErrorHandlerMixin`, `APIErrorHandler`, `ErrorResponse`
  - Confirm `shared/api/auth.py` -- `get_auth_context`, `get_user_id`
  - Confirm `shared/schemas/evidence.py` -- `EvidenceItem`

---

## Phase 1: Schemas & Domain (Foundation)

### Acceptance Criteria: US-1 (Store Plan Execution Results), FR-001 (External Contract), FR-003 (Plan Storage Format)

- [ ] [T100] Create domain models
  - File: `/components/PlanLibrary/domain/models.py`
  - Pydantic models following ProfileStore pattern:
    - `PlanDB` -- maps to `PlanTable` in `shared/database/models.py` (plan_id, canonical_json, signature_data, intent_type, step_count, plan_hash, size_bytes, created_at, stored_at)
    - `PlanOutcomeDB` -- maps to `PlanOutcomeTable` (outcome_id, plan_id, success, error_type, error_details, execution_start, execution_end, total_steps, failed_step, context_data)
    - `PlanMetricsDB` -- maps to `PlanMetricsTable` (metrics_id, plan_id, preview_latency_ms, execute_latency_ms, step_timings, resource_usage)
  - Request/Response models:
    - `StorePlanRequest` -- plan (dict), outcome (dict), metrics (dict)
    - `StorePlanResponse` -- status, plan_id, stored_at
    - `QueryPlansRequest` -- intent_type, success_threshold, limit, recency_days
    - `PlanPattern` -- intent_type, success_rate, avg_execution_time_ms, steps_count, pattern_summary, plan_id
  - Error classes:
    - `PlanLibraryError(Exception)` -- base exception
    - `DuplicatePlanError(PlanLibraryError)` -- duplicate plan_id
    - `PlanTooLargeError(PlanLibraryError)` -- exceeds 1MB or 100 steps
    - `InvalidQueryError(PlanLibraryError)` -- invalid query parameters
  - Add `SuccessResponse` and `ErrorResponse` models consistent with ProfileStore patterns
  - ULID validation on `plan_id` field (regex pattern `^[0-9A-HJKMNP-TV-Z]{26}$`)

- [ ] [T101] Create JSON schemas for plan storage and query
  - File: `/components/PlanLibrary/schemas/plan_storage.schema.json`
    - JSON Schema Draft 7 for plan storage input (plan object, outcome, metrics)
    - Must pass CI schema-validation job
  - File: `/components/PlanLibrary/schemas/query_request.schema.json`
    - JSON Schema Draft 7 for query parameters (intent_type, similarity_vector, filters)

- [ ] [T102] Write domain model unit tests (TDD -- write tests FIRST)
  - File: `/components/PlanLibrary/tests/test_domain.py`
  - Test all Pydantic model validation:
    - Valid ULID plan_id accepted
    - Invalid plan_id rejected (non-ULID format)
    - Plan size validation (>1MB rejected, >100 steps rejected)
    - PlanOutcome with success=true and success=false
    - Confidence score range (0.0-1.0) in output
    - Error class hierarchy (all inherit from PlanLibraryError)
    - StorePlanRequest/Response serialization roundtrip
    - QueryPlansRequest validation (negative limit rejected, invalid intent rejected)

---

## Phase 2: Service Layer (Business Logic)

### Acceptance Criteria: US-1 (Store Plans), US-2 (Query by Success/Context), FR-002 (Execution Semantics), FR-005 (Evidence Item Integration)

- [ ] [T200] Implement PlanService
  - File: `/components/PlanLibrary/service/plan_service.py`
  - Class: `PlanService`
  - Constructor accepts: `db_adapter`
  - Methods:
    - `async def store_plan(plan, outcome, metrics) -> StorePlanResponse`
      - Decision rules from SPEC (top-to-bottom):
        1. Validate plan_id is valid ULID
        2. Validate required fields (plan_id, graph, meta)
        3. Check for duplicate plan_id
        4. Check size limits (100 steps, 1MB)
        6. Canonicalize plan JSON (sorted keys, no whitespace)
        7. Compute SHA-256 hash
        8. Store plan + outcome + metrics in single DB transaction
      - Returns StorePlanResponse with plan_id and stored_at timestamp
    - `async def get_plans_by_intent(intent_type, success_threshold, limit, recency_days) -> List[EvidenceItem]`
      - Query plans filtered by intent_type
      - Filter by success_threshold (default 0.7)
      - Optional recency filter
      - Return as Evidence Items (type="plan", tier=3)
      - Sort by success_rate DESC, total_executions DESC
    - `async def get_plan_by_id(plan_id) -> Optional[PlanDB]`
      - Direct lookup by plan_id
  - Structured logging with `plan_id`, `intent_type`, `component="PlanLibrary"` correlation
  - No PII in logs (sanitized summaries only)

- [ ] [T201] Implement AnalyticsService
  - File: `/components/PlanLibrary/service/analytics_service.py`
  - Class: `AnalyticsService`
  - Constructor accepts: `db_adapter`
  - Methods:
    - `async def calculate_success_rates(timeframe_days) -> Dict[str, float]`
      - Group plans by intent_type
      - Calculate success rate per intent
    - `async def get_performance_trends(intent_type) -> PerformanceTrends`
      - Aggregate execution latency metrics
      - Return trends over time

- [ ] [T202] Implement EvidenceService (Evidence Item conversion)
  - File: `/components/PlanLibrary/service/evidence_service.py`
  - Class: `EvidenceService`
  - Methods:
    - `def to_evidence_item(plan, outcome_stats) -> EvidenceItem`
      - Convert plan data to Evidence Item format (GLOBAL_SPEC 2.2)
      - type="plan", tier=3, ttl_days=None
      - confidence = success_rate (calculated from outcomes)
      - source_ref = "planlibrary:plans/{plan_id}"
      - value = plan summary (intent, success_rate, avg time, step count, pattern summary)
    - `def to_evidence_items(plans_with_stats) -> List[EvidenceItem]`
      - Batch conversion helper

- [ ] [T203] Write service layer unit tests (TDD -- write tests FIRST)
  - File: `/components/PlanLibrary/tests/test_plan_service.py`
  - Tests for PlanService:
    - Store plan with valid data -- success (US-1 scenario 1)
    - Store plan with failure outcome -- records failure details (US-1 scenario 2)
    - Store plan with duplicate plan_id -- DuplicatePlanError (Decision Rule 4)
    - Store plan exceeding size -- PlanTooLargeError
    - Store plan with null/empty/invalid plan_id -- InvalidPlanIdError (Decision Rule 1)
    - Store plan with missing required fields -- MalformedPlanError (Decision Rule 2)
    - Query by intent with success threshold -- returns filtered results (US-2 scenario 1)
    - Query by intent filters to matching types only (US-2 scenario 2)
    - Query with recency preference -- ordered by date (US-2 scenario 3)
    - Get plan by ID -- found and not found paths
    - Evidence Item output format compliance
  - File: `/components/PlanLibrary/tests/test_analytics_service.py`
  - Tests for AnalyticsService:
    - Success rates calculated correctly (US-4 scenario 1)
    - Performance trends aggregated (US-4 scenario 2)
  - All tests use mocked adapters (MagicMock, AsyncMock patterns from ProfileStore)

---

## Phase 3: Adapters (External Integrations)

### Acceptance Criteria: FR-002 (Execution Semantics), FR-006 (Performance), FR-008 (Security), FR-009 (Fault Tolerance)

- [ ] [T300] Implement DatabaseAdapter
  - File: `/components/PlanLibrary/adapters/db.py`
  - Class: `DatabaseAdapter`
  - Follow ProfileStore `adapters/db.py` pattern:
    - Constructor: `self.shared_db = get_database_adapter()` from `shared/database/adapter.py`
    - Use `@with_db_error_handling` decorator from `shared/database/error_handler.py`
    - Import `PlanTable`, `PlanOutcomeTable`, `PlanMetricsTable` from `shared/database/models.py`
  - Methods:
    - `async def store_plan_transaction(plan, outcome, metrics) -> bool`
      - Single atomic transaction for plan + outcome + metrics
      - Uses `async with self.shared_db.get_session() as session`
      - Handles IntegrityError for duplicate plan_id
    - `async def get_plan_by_id(plan_id) -> Optional[PlanDB]`
    - `async def get_plans_by_intent(intent_type, success_threshold, limit, recency_days) -> List[PlanDB]`
      - JOIN with plan_outcomes for success rate filtering
      - ORDER BY success_rate DESC, execution_start DESC
    - `async def get_plan_outcomes(plan_id) -> List[PlanOutcomeDB]`
    - `async def get_success_rates(timeframe_days) -> Dict[str, float]`
      - Aggregate query: GROUP BY intent_type, COUNT success/total
    - `async def health_check() -> bool`

- [ ] [T302] Write adapter unit tests (TDD -- write tests FIRST)
  - File: `/components/PlanLibrary/tests/test_adapters.py`
  - Tests for DatabaseAdapter:
    - Store plan transaction -- success path
    - Store plan transaction -- duplicate plan_id raises DatabaseIntegrityError
    - Get plan by ID -- found and not found
    - Query plans by intent with success threshold
    - Health check passes/fails
  - All adapter tests use mocks (MagicMock for database sessions, mock OpenAI responses)

---

## Phase 4: API Handlers (Thin Wrappers)

### Acceptance Criteria: FR-001 (External Contract), Interfaces & Contracts section of SPEC

- [ ] [T400] Create API routes (thin wrappers)
  - File: `/components/PlanLibrary/api/routes.py`
  - Follow ProfileStore `api/routes.py` pattern exactly:
    - `router = APIRouter(prefix="/plans", tags=["plans"])`
    - `error_handler = ErrorHandlerMixin()`
    - Dependency injection: `get_plan_service()`, `get_analytics_service()`
  - Endpoints:
    - `POST /plans` -- `store_plan_endpoint(request: StorePlanRequest)`
      - Thin wrapper: delegates to `PlanService.store_plan()`
      - Returns `StorePlanResponse`
      - Error handling: DuplicatePlanError -> 409, PlanTooLargeError -> 413
    - `GET /plans/by-intent/{intent_type}` -- `get_plans_by_intent_endpoint(intent_type, success_threshold, limit, recency_days)`
      - Thin wrapper: delegates to `PlanService.get_plans_by_intent()`
      - Returns `List[EvidenceItem]` wrapped in SuccessResponse
    - `GET /plans/{plan_id}` -- `get_plan_endpoint(plan_id)`
      - Thin wrapper: delegates to `PlanService.get_plan_by_id()`
      - Returns plan data or 404
    - `GET /plans/analytics/success-rates` -- `get_success_rates_endpoint(timeframe_days)`
      - Thin wrapper: delegates to `AnalyticsService.calculate_success_rates()`
    - `GET /plans/health` -- `health_check()`
      - No authentication required
      - Checks database health
  - All endpoints use `X-Plan-ID` header for correlation logging
  - Error responses use `shared/api/error_handlers.py` patterns

- [ ] [T401] Extend shared API error handlers for PlanLibrary errors
  - File: `/components/PlanLibrary/api/error_handlers.py`
  - Create PlanLibrary-specific error handler methods:
    - `handle_duplicate_plan(error) -> JSONResponse` (409)
    - `handle_plan_too_large(error) -> JSONResponse` (413)
    - `handle_invalid_query(error) -> JSONResponse` (400)
  - Extends `ErrorHandlerMixin` pattern from shared infrastructure

- [ ] [T402] Write API handler tests (TDD -- write tests FIRST)
  - File: `/components/PlanLibrary/tests/test_api.py`
  - Tests:
    - POST /plans with valid data -- 200 success
    - POST /plans with duplicate plan_id -- 409 DUPLICATE_PLAN_ID
    - POST /plans with oversized plan -- 413 PLAN_TOO_LARGE
    - GET /plans/by-intent/{intent_type} -- returns Evidence Items
    - GET /plans/{plan_id} -- found returns plan data
    - GET /plans/{plan_id} -- not found returns 404
    - GET /plans/health -- returns health status
    - All error responses match ErrorResponse schema
  - Use mocked services (same pattern as ProfileStore tests)

---

## Phase 5: Fault Isolation & Safety (Architectural)

### From MODULAR_ARCHITECTURE.md, LLD Architectural Considerations, Constitution VII

- [ ] [T500] Validate determinism: plan canonicalization
  - Ensure canonical JSON serialization is deterministic:
    - Sorted keys
    - No whitespace
    - Consistent float formatting
    - Same inputs always produce same SHA-256 hash
  - Add determinism assertion in PlanService.store_plan()
  - File: `/components/PlanLibrary/service/plan_service.py` (enhance from T200)

- [ ] [T501] Add structured logging (correlation: plan_id/step/component)
  - All service and adapter methods include structured log metadata:
    - `plan_id`, `intent_type`, `component="PlanLibrary"`, `operation`
    - Latency timing for performance tracking
    - Error classification for failure analysis
  - File: All service and adapter files (enhance existing implementations)

- [ ] [T502] Verify no PII in logs
  - Review all log statements to ensure:
    - Plan content logged as sanitized summaries only (intent_type, step_count)
    - User IDs referenced by hash when needed
    - Error details do not contain sensitive context data
    - No raw plan JSON in logs (only plan_id and intent_type)
  - File: All files (verification pass)

---

## Phase 6: Contract Tests & Integration

### Acceptance Criteria: SC-001 through SC-007, Invariants 1-10

- [ ] [T600] Write contract tests (GLOBAL_SPEC compliance)
  - File: `/components/PlanLibrary/tests/test_contract.py`
  - Follow ProfileStore `tests/test_contract.py` pattern:
  - TestGlobalSpecCompliance:
    - Evidence Item format compliance (type="plan", tier=3, source_ref="planlibrary:plans/{id}")
    - Evidence Item JSON serialization roundtrip
    - Confidence score range (0.0-1.0)
    - Tier 3 data source compliance (GLOBAL_SPEC section 7)
  - TestErrorCodeContract:
    - All error codes match SPEC FR-001 (INVALID_PLAN_ID, MALFORMED_PLAN, DUPLICATE_PLAN_ID, PLAN_TOO_LARGE, STORAGE_ERROR, INVALID_QUERY)
    - Error classes have required attributes for API error responses
  - TestInvariantCompliance:
    - Plan uniqueness (plan_id is primary key)
    - Outcome consistency (outcome references valid plan_id)
    - Canonical serialization (sorted keys, deterministic)
    - Immutable storage (plans never modified, append-only outcomes)
  - TestPreviewExecuteModelCompliance:
    - PlanLibrary does NOT use Preview/Execute wrappers (internal component)
    - Service methods execute directly (no preview_/execute_ methods)

- [ ] [T601] Write integration tests
  - File: `/components/PlanLibrary/tests/test_integration.py`
  - End-to-end flow tests with mocked database:
    - Store plan -> query by intent -> verify evidence items returned
    - Store multiple plans -> analytics -> verify success rates
    - Store plan with outcome failure -> query filters it below threshold
    - Full lifecycle: store -> query -> analytics
  - Service layer integration:
    - PlanService + EvidenceService integration (Evidence Items formatted correctly)

- [ ] [T602] Write performance benchmark tests
  - File: `/components/PlanLibrary/tests/test_performance.py`
  - Performance targets from SPEC SC-001 through SC-003:
    - Plan storage: p95 < 200ms (SC-001)
    - Intent-based queries: p95 < 150ms (SC-003)
  - Use pytest-benchmark for measurement
  - Tests with mocked database (measure service/adapter overhead, not actual DB)

- [ ] [T603] Validate CI pipeline compatibility
  - Ensure all test files discovered by pytest configuration in `pyproject.toml`
  - Verify `ruff check` passes on all new files (line length 100, Python 3.11+)
  - Verify `ruff format` passes
  - Verify `mypy --strict` passes on all new files
  - Verify JSON schemas pass the schema-validation CI job
  - Run full test suite locally before PR

---

## Task Summary

- **Total Tasks**: 18
- **Phase 0 (Setup)**: T000-T002 (3 tasks)
- **Phase 1 (Schemas/Domain)**: T100-T102 (3 tasks)
- **Phase 2 (Service Layer)**: T200-T203 (4 tasks)
- **Phase 3 (Adapters)**: T300, T302 (2 tasks)
- **Phase 4 (API)**: T400-T402 (3 tasks)
- **Phase 5 (Safety)**: T500-T502 (3 tasks)
- **Phase 6 (Tests/Integration)**: T600-T603 (4 tasks)

---

## Dependencies

### External (from LLD Section 7)

| Package | Version | Purpose |
|---------|---------|---------|
| `sqlalchemy` | `>=2.0,<3.0` | Async ORM for PostgreSQL |
| `asyncpg` | `>=0.29` | PostgreSQL async driver |
| `pydantic` | `>=2.0` | Data validation |
| `fastapi` | `>=0.109.0` | API framework |
| `ulid-py` | `>=1.1.0` | ULID validation |

### Development/Testing

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | `>=8.0.0` | Test framework |
| `pytest-asyncio` | `>=0.23.0` | Async test support |
| `pytest-cov` | `>=4.1.0` | Coverage reporting |
| `pytest-mock` | `>=3.12.0` | Mock utilities |
| `httpx` | `>=0.27` | API testing |
| `pytest-benchmark` | `>=4.0.0` | Performance testing |

### Internal (Shared Infrastructure)

| Component | File | What it provides |
|-----------|------|------------------|
| Shared Database | `/shared/database/adapter.py` | `SharedDatabaseAdapter`, `get_database_adapter()` |
| Shared DB Errors | `/shared/database/error_handler.py` | `@with_db_error_handling`, `execute_with_retry()`, error classes |
| Shared DB Models | `/shared/database/models.py` | `PlanTable`, `PlanOutcomeTable`, `PlanMetricsTable` |
| Shared API Errors | `/shared/api/error_handlers.py` | `ErrorHandlerMixin`, `APIErrorHandler`, `ErrorResponse` |
| Shared Auth | `/shared/api/auth.py` | `get_auth_context()`, `get_user_id()` |
| Shared Evidence | `/shared/schemas/evidence.py` | `EvidenceItem` Pydantic model |

### Component Dependencies

**None** -- PlanLibrary is a foundation Memory Layer component. It is called by PlanWriter, ContextRAG, and Planner, but does not depend on any other components.

---

## Architectural Considerations

### Blast Radius (from LLD)

- **If PlanLibrary fails**: Plan execution continues normally (component is internal/audit-only). New plans execute without historical context. ContextRAG falls back to other Evidence sources. System learning temporarily disabled.
- **Containment**: Database connection pooling with retry logic. No external API dependencies.

### Determinism (from LLD)

- **Plan Storage**: Deterministic canonicalization ensures same inputs produce same SHA-256 hash and storage format. Sorted keys, no whitespace, consistent serialization.
- **Query Results**: Consistent ordering by success_rate DESC, total_executions DESC.

### Preview/Execute Model

- **Not applicable**: PlanLibrary is an internal Memory Layer component. GLOBAL_SPEC section 1 explicitly states the Preview/Execute model applies to user-facing plans, not internal component operations. All operations execute directly without Preview/Execute wrappers.

### Performance Targets (from SPEC)

| Operation | Target p95 | GLOBAL_SPEC Reference |
|-----------|-----------|----------------------|
| Plan Storage | < 200ms | Plan Retrieval target |
| Intent-based Queries | < 150ms | ContextRAG target |

---

## Implementation Order (Recommended)

The recommended execution order respects dependencies between tasks:

1. **Phase 0** (T000, T001, T002) -- setup, can be done in parallel
2. **Phase 1** (T102 first for TDD, then T100, T101) -- domain foundation
3. **Phase 3** (T302 first for TDD, then T300) -- adapters before services need them
4. **Phase 2** (T203 first for TDD, then T200-T202) -- services depend on adapters
5. **Phase 4** (T402 first for TDD, then T400, T401) -- API depends on services
6. **Phase 5** (T500-T502) -- safety enhancements on top of working code
7. **Phase 6** (T600-T603) -- contract and integration tests validate everything

Within each phase, write tests first (TDD) per constitution mandate.
