# Tasks: PlanWriter

**Created**: 2026-03-19
**Branch**: feat/planwriter
**SPEC**: specs/011-planwriter/spec.md
**LLD**: components/PlanWriter/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
PlanWriter is a Domain Layer library component with no HTTP routes, no owned
database tables, and no new Python dependencies. It orchestrates writes to
three downstream Memory Layer services: PlanLibrary, History, and VectorIndex.

All three downstream services are already implemented and available in
`shared/app.py` as `app.state.plan_service`, `app.state.fact_service`, and
`app.state.vector_index_service`.

---

## Phase 0: Scaffold

Create the directory structure and empty `__init__.py` files so that
subsequent phases can import modules without path errors.

### T000 -- Create directory structure and placeholder files

- **Description**: Create all directories and empty `__init__.py` files for
  the PlanWriter component. This mirrors the existing Signer and VectorIndex
  layout.
- **Files to create**:
  - `components/PlanWriter/__init__.py` (empty placeholder, updated in Phase 6)
  - `components/PlanWriter/domain/__init__.py` (empty)
  - `components/PlanWriter/service/__init__.py` (empty)
  - `components/PlanWriter/adapters/__init__.py` (empty)
  - `components/PlanWriter/tests/__init__.py` (empty)
  - `components/PlanWriter/tests/conftest.py` (empty placeholder, populated in T010)
- **Dependencies**: None
- **Acceptance criteria**: All imports resolve without `ModuleNotFoundError`.
  Running `python -c "import components.PlanWriter"` succeeds.

### T001 -- Create shared test fixtures (conftest.py)

- **Description**: Populate `conftest.py` with pytest fixtures that provide
  mocked downstream services (PlanService, FactService, VectorIndexService)
  and sample data (plan dict, signature dict, outcome dict, metrics dict,
  user_id UUID). Fixtures use `unittest.mock.AsyncMock` for async service
  methods. Follow the Signer conftest.py pattern for structure.
- **Files to create/modify**:
  - `components/PlanWriter/tests/conftest.py`
- **Fixtures required**:
  - `mock_plan_service` -- AsyncMock with `store_plan` returning a
    `StorePlanResponse(plan_id=..., stored_at=...)`
  - `mock_fact_service` -- AsyncMock with `store_fact` returning a
    `StoreFactResponse(status="ok", fact_id=..., stored_at=...)`
  - `mock_vector_index_service` -- AsyncMock with `store_embedding`
    returning `None`
  - `sample_plan` -- dict matching GLOBAL_SPEC Section 2.3 with valid ULID
    `plan_id`, `graph`, `meta` (with `intent_type`), `intent.entities`
  - `sample_signature` -- dict matching GLOBAL_SPEC Section 2.4
  - `sample_outcome_success` -- dict with `success=True`, timestamps,
    `total_steps`, `failed_step=None`
  - `sample_outcome_failure` -- dict with `success=False`, `error_type`,
    `error_details`, `failed_step`
  - `sample_metrics` -- dict with `preview_latency_ms`, `execute_latency_ms`,
    `step_timings`
  - `sample_user_id` -- `UUID`
  - `plan_writer_service` -- `PlanWriterService` constructed with the three
    mock services above
  - `plan_writer_service_no_vectorindex` -- `PlanWriterService` with
    `vector_index_service=None`
- **Dependencies**: T000
- **Acceptance criteria**: `uv run pytest components/PlanWriter/tests/ --collect-only`
  collects fixtures without error.

---

## Phase 1: Domain Models

Define the Pydantic result models and error classes. These are pure data
classes with no business logic and no external dependencies.

### AC Mapping: FR-001 through FR-006 (PersistResult status/error semantics)

### T100 -- Create PersistResult and BulkPersistResult models

- **Description**: Implement `PersistResult` and `BulkPersistResult` Pydantic
  BaseModel classes exactly as specified in LLD Section 5.1. Fields:
  `plan_id` (str, 26 chars), `fact_id` (UUID | None), `embedding_stored`
  (bool), `status` (Literal["ok", "partial", "error"]), `errors` (list[str]).
  BulkPersistResult wraps a list of PersistResult with summary counts.
- **File to create**:
  - `components/PlanWriter/domain/models.py`
- **Dependencies**: T000
- **Acceptance criteria**: Models validate correct data, reject invalid
  `plan_id` lengths, and serialize to JSON matching the SPEC output format.

### T101 -- Create error classes

- **Description**: Implement `PlanWriterError` (base), `PlanLibraryWriteError`,
  and `FactDerivationError` exactly as specified in LLD Section 5.2. Each
  error class stores `plan_id` and `reason` as attributes. Follow the Signer
  error class pattern (see `components/Signer/domain/models.py`).
- **File to modify**:
  - `components/PlanWriter/domain/models.py` (append to same file as T100)
- **Dependencies**: T100
- **Acceptance criteria**: `PlanLibraryWriteError("PLANID", "db down")` has
  `.plan_id == "PLANID"` and `.reason == "db down"` and `str(e)` contains both.
  `FactDerivationError` follows same pattern. Both inherit from
  `PlanWriterError`.

### T102 -- Write domain model unit tests

- **Description**: Test PersistResult validation (valid plan_id, invalid
  plan_id length, default values), BulkPersistResult aggregation, and error
  class instantiation and inheritance. Test that PersistResult serializes to
  dict matching SPEC output format.
- **File to create**:
  - `components/PlanWriter/tests/test_unit.py` (initial section; more tests
    added in later phases)
- **Dependencies**: T100, T101, T001
- **Acceptance criteria**: `uv run pytest components/PlanWriter/tests/test_unit.py -k "TestDomainModels" -v`
  passes. Tests are red before T100/T101 implementation, green after.

---

## Phase 2: Adapters (FactDeriver Pure Function)

### AC Mapping: FR-002, FR-007, FR-008 (fact derivation, PII-light text, source_plan_id linking)

### T200 -- Implement derive_fact() pure function

- **Description**: Implement the `derive_fact(plan, outcome)` function and its
  helper functions in `adapters/fact_deriver.py` exactly as specified in LLD
  Section 7.1. The function:
  - Extracts `intent_type` from plan (checking `plan["meta"]["intent_type"]`,
    `plan["intent"]["intent"]`, `plan["intent_type"]`, fallback "unknown")
  - Extracts `entities` from plan (checking `plan["intent"]["entities"]`,
    `plan["entities"]`, fallback `{}`)
  - Builds `action_summary` from intent_type (e.g. "book_flight" ->
    "Booked flight")
  - Builds `entity_summary` from entities (e.g. `{destination: "NYC"}` ->
    "to NYC")
  - Builds `error_summary` from outcome (e.g. "timeout at step 3")
  - Uses `_SUCCESS_TEMPLATE` or `_FAILURE_TEMPLATE` based on
    `outcome["success"]`
  - Returns a `StoreFactRequest` (imported from
    `components.History.domain.models`) with `fact_text`, `intent_type`,
    `entities`, `outcome` (bool), `source_plan_id`, `ttl_days=30`
  - Raises `FactDerivationError` if plan is missing required fields
  - Is deterministic: same inputs always produce same output
  - Never includes raw API responses, credentials, or full plan JSON in
    `fact_text`
- **Helpers to implement**: `_extract_intent_type()`, `_extract_entities()`,
  `_build_entity_summary()`, `_build_action_summary()`,
  `_build_error_summary()`
- **File to create**:
  - `components/PlanWriter/adapters/fact_deriver.py`
- **Dependencies**: T101 (uses FactDerivationError)
- **Acceptance criteria**: `derive_fact()` is a pure function with no side
  effects. Given a plan with `intent_type="book_flight"` and
  `entities={destination: "NYC", airline: "Delta"}` and `outcome.success=True`,
  returns a StoreFactRequest where `fact_text` reads like
  "Booked flight to NYC with Delta" and `source_plan_id` is set. Given the
  same inputs twice, produces identical output.

### T201 -- Write fact deriver unit tests

- **Description**: Test `derive_fact()` and all helper functions with:
  - Successful plan with entities -> success template
  - Failed plan with error_type and failed_step -> failure template
  - Plan with no entities -> fallback template
  - Plan with intent_type in different locations (meta, intent, top-level)
  - Plan with unknown/missing intent_type -> fallback to "unknown"
  - Determinism: same inputs produce same StoreFactRequest
  - PII safety: fact_text does not contain raw plan JSON
  - Edge case: empty entities dict
  - Edge case: missing outcome fields (graceful defaults)
  - Error case: plan missing plan_id raises FactDerivationError
- **File to modify**:
  - `components/PlanWriter/tests/test_unit.py` (add TestFactDeriver class)
- **Dependencies**: T200, T001
- **Acceptance criteria**: `uv run pytest components/PlanWriter/tests/test_unit.py -k "TestFactDeriver" -v`
  passes with all scenarios green.

---

## Phase 3: Service Layer (PlanWriterService)

### AC Mapping: FR-001 through FR-012 (all functional requirements)

### T300 -- Implement PlanWriterService.persist_outcome()

- **Description**: Implement the core `persist_outcome()` method following the
  exact execution order from LLD Section 8.1:
  1. **Validate inputs**: `plan` must be non-None/non-empty with `plan_id`.
     Raise `ValueError` if not.
  2. **PlanLibrary write** (PRIMARY): Call `plan_service.store_plan(plan,
     signature, outcome, metrics)`. If it raises `DuplicatePlanError`, catch
     and treat as idempotent success (FR-009). If it raises any other
     exception, wrap in `PlanLibraryWriteError` and re-raise (FR-006).
  3. **Fact derivation**: Call `derive_fact(plan, outcome)`. If it raises
     `FactDerivationError`, catch, log warning, add to errors list, skip
     History write.
  4. **History write** (SECONDARY): Call `fact_service.store_fact(user_id,
     fact_request)`. If it raises, catch, log warning, set `fact_id=None`,
     add to errors list (FR-005). If successful and `status="duplicate"`,
     use the returned `fact_id`.
  5. **VectorIndex write** (OPTIONAL): If `vector_index_service is None`,
     log warning, set `embedding_stored=False` (FR-004). If available, call
     `vector_index_service.store_embedding(plan_id, plan)`. If it raises,
     catch, log warning, set `embedding_stored=False`, add to errors list.
  6. **Build PersistResult**: Set `status="ok"` if no errors, `"partial"` if
     any History/VectorIndex errors, `"error"` should not be returned
     (PlanLibrary failures raise exceptions instead).
  7. **Log structured event**: `outcome_persisted` at INFO with `plan_id`,
     `fact_id`, `embedding_stored`, `status`, latency breakdown.
  - Add `time.monotonic()` measurements for PlanLibrary, History, VectorIndex
    and total latency, logged in the structured event.
- **File to create**:
  - `components/PlanWriter/service/plan_writer_service.py`
- **Dependencies**: T200, T101, T100
- **Acceptance criteria**: Given mocked downstream services that all succeed,
  `persist_outcome()` returns `PersistResult(status="ok", embedding_stored=True,
  fact_id=<UUID>)`. PlanLibrary failure raises `PlanLibraryWriteError`.
  History failure returns `PersistResult(status="partial", fact_id=None)`.
  VectorIndex=None returns `PersistResult(status="ok", embedding_stored=False)`.

### T301 -- Implement PlanWriterService.bulk_persist()

- **Description**: Implement `bulk_persist()` as specified in LLD Section 4.1.
  - Validates `outcomes` is not empty (raises `ValueError` if so).
  - Each item in `outcomes` must have keys: `plan`, `signature`, `outcome`,
    `metrics`.
  - Iterates sequentially, calling `persist_outcome()` for each.
  - Collects individual `PersistResult` objects.
  - Counts `succeeded` (status="ok"), `partial`, `failed` (status="error",
    which would happen if persist_outcome raises and is caught).
  - Wraps `PlanLibraryWriteError` per-item: catch it, create a PersistResult
    with `status="error"` and the error message, add to results.
  - Returns `BulkPersistResult` with all results and summary counts.
  - Logs `bulk_persist_completed` at INFO with totals and latency.
- **File to modify**:
  - `components/PlanWriter/service/plan_writer_service.py` (add method to
    existing class)
- **Dependencies**: T300
- **Acceptance criteria**: Given 3 outcomes (2 succeed, 1 PlanLibrary fails),
  `bulk_persist()` returns `BulkPersistResult(total=3, succeeded=2, failed=1)`.
  Empty list raises `ValueError`.

### T302 -- Implement create_plan_writer_service() factory function

- **Description**: Implement the factory function as specified in LLD
  Section 4.3. Simple constructor that accepts `plan_service`, `fact_service`,
  and `vector_index_service` (may be None) and returns a configured
  `PlanWriterService`. Log service creation at INFO level.
- **File to modify**:
  - `components/PlanWriter/service/plan_writer_service.py` (add function
    below class)
- **Dependencies**: T300
- **Acceptance criteria**: `create_plan_writer_service(mock_ps, mock_fs, None)`
  returns a `PlanWriterService` instance with `vector_index_service is None`.

### T303 -- Write persist_outcome unit tests

- **Description**: Test all `persist_outcome()` paths using mocked downstream
  services from conftest.py fixtures. Test classes:
  - `TestPersistOutcomeHappyPath`: All three writes succeed ->
    `PersistResult(status="ok", embedding_stored=True, fact_id=<UUID>)`
    (maps to SPEC User Story 1, Acceptance Scenario 1-3)
  - `TestPersistOutcomeFailedExecution`: outcome.success=False, all writes
    succeed -> PersistResult status="ok", fact_text describes failure
    (maps to SPEC User Story 2, Acceptance Scenario 1-2)
  - `TestPersistOutcomeVectorIndexNone`: VectorIndex=None ->
    `PersistResult(status="ok", embedding_stored=False)`, warning logged
    (maps to SPEC User Story 3, Acceptance Scenario 1)
  - `TestPersistOutcomeVectorIndexError`: VectorIndex raises ->
    `PersistResult(status="ok" or "partial", embedding_stored=False)`,
    error logged (maps to SPEC User Story 3, Acceptance Scenario 2)
  - `TestPersistOutcomeHistoryFails`: History raises ->
    `PersistResult(status="partial", fact_id=None)`, VectorIndex still
    attempted (maps to SPEC Edge Case: History fails after PlanLibrary)
  - `TestPersistOutcomePlanLibraryFails`: PlanLibrary raises ->
    `PlanLibraryWriteError` raised, History and VectorIndex NOT called
    (maps to SPEC Edge Case: PlanLibrary fails)
  - `TestPersistOutcomeDuplicatePlan`: PlanLibrary raises
    `DuplicatePlanError` -> treated as success, History and VectorIndex
    still called (maps to SPEC Edge Case: same plan_id twice, FR-009)
  - `TestPersistOutcomeFactDerivationFails`: derive_fact raises
    FactDerivationError -> partial result, History skipped, VectorIndex
    still attempted
  - `TestPersistOutcomeValidation`: Empty plan raises ValueError, None
    plan raises ValueError, plan missing plan_id raises ValueError
  - Verify that `plan_service.store_plan()` receives the exact plan,
    signature, outcome, metrics args without transformation (FR-001,
    SPEC US1 Acceptance Scenario 3)
- **File to modify**:
  - `components/PlanWriter/tests/test_unit.py` (add test classes)
- **Dependencies**: T300, T001
- **Acceptance criteria**: `uv run pytest components/PlanWriter/tests/test_unit.py -k "TestPersistOutcome" -v`
  passes with all scenarios green.

### T304 -- Write bulk_persist unit tests

- **Description**: Test `bulk_persist()` with:
  - 3 successful outcomes -> `BulkPersistResult(total=3, succeeded=3)`
  - Mix of success and failure -> correct counts
  - Empty list -> `ValueError`
  - Single item -> works correctly
  - PlanLibrary error on one item -> that item gets `status="error"`,
    others still processed
  (Maps to SPEC User Story 5, Acceptance Scenarios 1-2)
- **File to modify**:
  - `components/PlanWriter/tests/test_unit.py` (add TestBulkPersist class)
- **Dependencies**: T301, T001
- **Acceptance criteria**: `uv run pytest components/PlanWriter/tests/test_unit.py -k "TestBulkPersist" -v`
  passes.

---

## Phase 4: DI Wiring

### T400 -- Add PlanWriterService to shared/app.py lifespan

- **Description**: Add PlanWriterService initialization to the lifespan
  function in `shared/app.py`, after the VectorIndex initialization block
  (since PlanWriter depends on all three downstream services). Follow the
  existing pattern for Signer (lazy import, single factory call, store on
  app.state).
  ```python
  # PlanWriter service (library -- no routes)
  from components.PlanWriter.service.plan_writer_service import (
      create_plan_writer_service,
  )

  app.state.plan_writer_service = create_plan_writer_service(
      plan_service=app.state.plan_service,
      fact_service=app.state.fact_service,
      vector_index_service=app.state.vector_index_service,
  )
  ```
  Place this block after the VectorIndex block and before the
  `logger.info("All services initialized")` line.
- **File to modify**:
  - `shared/app.py`
- **Dependencies**: T302
- **Acceptance criteria**: Application starts without error. PlanWriterService
  is available at `app.state.plan_writer_service`. If VectorIndex is None,
  PlanWriterService still initializes with `vector_index_service=None`.

### T401 -- Add get_plan_writer_service to shared/dependencies.py

- **Description**: Add the DI getter function following the exact pattern of
  `get_signer_service()` and `get_vector_index_service()`:
  ```python
  def get_plan_writer_service(request: Request) -> Any:
      """Get PlanWriterService singleton from app state."""
      return request.app.state.plan_writer_service
  ```
- **File to modify**:
  - `shared/dependencies.py`
- **Dependencies**: T400
- **Acceptance criteria**: `from shared.dependencies import get_plan_writer_service`
  imports without error. Function returns the service from app.state.

---

## Phase 5: Observability and Safety Tests

### AC Mapping: FR-011, FR-012 (structured logging, no PII in logs)

### T500 -- Write observability tests (log safety)

- **Description**: Verify that PlanWriter logs do not contain raw plan
  content, embedding vectors, signature bytes, or credentials. Follow the
  Signer `test_observability.py` pattern. Test cases:
  - `test_persist_does_not_log_plan_json`: After `persist_outcome()`, logs
    do not contain plan graph steps, action names from plan, or raw entities
  - `test_persist_does_not_log_signature_bytes`: Logs do not contain
    signature base64 value
  - `test_persist_does_not_log_metrics_payload`: Logs do not contain raw
    step_timings array
  - `test_persist_logs_plan_id`: Logs contain the `plan_id` string
  - `test_persist_logs_status`: Logs contain the status ("ok", "partial")
  - `test_partial_failure_logs_warning`: When History fails, a WARNING log
    is emitted with `persist_partial_failure` message and `plan_id`
  - `test_vectorindex_unavailable_logs_warning`: When VectorIndex is None,
    a WARNING log is emitted with `vectorindex_unavailable` message
  - `test_planlibrary_failure_logs_error`: When PlanLibrary fails, an ERROR
    log is emitted with `persist_failed` message
  Use `caplog` pytest fixture to capture logs from the "planwriter" logger.
- **File to create**:
  - `components/PlanWriter/tests/test_observability.py`
- **Dependencies**: T300, T001
- **Acceptance criteria**: `uv run pytest components/PlanWriter/tests/test_observability.py -v`
  passes. No raw plan content, signatures, or credential values appear
  in captured log output.

---

## Phase 6: Contract Tests and Public Exports

### AC Mapping: All SPEC acceptance scenarios (User Stories 1-5, Edge Cases)

### T600 -- Write contract tests (SPEC acceptance scenarios)

- **Description**: Write end-to-end contract tests that verify the full
  PlanWriter contract as specified in the SPEC acceptance scenarios. These
  tests use mocked downstream services but exercise the complete
  `persist_outcome()` flow including fact derivation.
  Test classes map 1:1 to SPEC User Stories:
  - `TestUS1_PersistSuccessfulExecution`:
    - Scenario 1: PlanLibrary, History, VectorIndex all called with correct args
    - Scenario 2: Returns PersistResult with plan_id, fact_id, embedding_stored=True, status="ok"
    - Scenario 3: PlanLibrary receives plan, signature, outcome, metrics unmodified
  - `TestUS2_PersistFailedExecution`:
    - Scenario 1: Failed outcome with error_type/failed_step passed to PlanLibrary, History fact has outcome=False
    - Scenario 2: Derived fact_text describes failure
  - `TestUS3_GracefulDegradation`:
    - Scenario 1: VectorIndex=None -> PlanLibrary+History succeed, embedding_stored=False, warning logged
    - Scenario 2: VectorIndex raises -> PlanLibrary+History succeed, embedding_stored=False, error logged
  - `TestUS4_DeriveFactsFromExecution`:
    - Scenario 1: "book_flight" with entities -> fact_text like "Booked flight to NYC with Delta"
    - Scenario 2: fact_text does not contain raw API responses or full plan JSON
    - Scenario 3: No entities -> entities={}, fact_text uses intent_type only
  - `TestUS5_BulkPersist`:
    - Scenario 1: 10 outcomes -> all 10 persisted
    - Scenario 2: Empty list -> ValueError
  - `TestEdgeCases`:
    - PlanLibrary fails -> entire persist_outcome fails, History/VectorIndex not attempted
    - History fails after PlanLibrary -> partial, VectorIndex still attempted
    - Same plan_id twice -> idempotent success
  Verify PersistResult schema matches SPEC output format (JSON serialization).
- **File to create**:
  - `components/PlanWriter/tests/test_contract.py`
- **Dependencies**: T300, T301, T200, T001
- **Acceptance criteria**: `uv run pytest components/PlanWriter/tests/test_contract.py -v`
  passes. Every SPEC acceptance scenario has a corresponding test.

### T601 -- Populate public exports (__init__.py)

- **Description**: Update `components/PlanWriter/__init__.py` with public
  exports following the Signer and VectorIndex `__init__.py` patterns.
  Export: `PlanWriterService`, `create_plan_writer_service`, `PersistResult`,
  `BulkPersistResult`, `PlanWriterError`, `PlanLibraryWriteError`,
  `FactDerivationError`, `derive_fact`. Define `__all__` list.
- **File to modify**:
  - `components/PlanWriter/__init__.py`
- **Dependencies**: T300, T302, T100, T101, T200
- **Acceptance criteria**: `from components.PlanWriter import PlanWriterService, PersistResult, derive_fact`
  imports successfully. `__all__` lists exactly the public API.

### T602 -- Populate domain and service __init__.py exports

- **Description**: Update the subpackage `__init__.py` files to re-export
  their contents for cleaner imports:
  - `components/PlanWriter/domain/__init__.py`: export PersistResult,
    BulkPersistResult, PlanWriterError, PlanLibraryWriteError,
    FactDerivationError
  - `components/PlanWriter/service/__init__.py`: export PlanWriterService,
    create_plan_writer_service
  - `components/PlanWriter/adapters/__init__.py`: export derive_fact
- **Files to modify**:
  - `components/PlanWriter/domain/__init__.py`
  - `components/PlanWriter/service/__init__.py`
  - `components/PlanWriter/adapters/__init__.py`
- **Dependencies**: T300, T302, T100, T101, T200
- **Acceptance criteria**: `from components.PlanWriter.domain import PersistResult`
  works. `from components.PlanWriter.service import PlanWriterService` works.
  `from components.PlanWriter.adapters import derive_fact` works.

---

## Phase 7: Final Validation

### T700 -- Run full test suite and lint checks

- **Description**: Run all PlanWriter tests, ruff lint, and ruff format to
  verify everything passes CI gates before PR creation.
  Commands:
  ```bash
  uv run pytest components/PlanWriter/tests/ -v --tb=short
  uv run ruff check components/PlanWriter/ shared/app.py shared/dependencies.py
  uv run ruff format --check components/PlanWriter/ shared/app.py shared/dependencies.py
  ```
  Fix any failures before marking complete.
- **Files**: All PlanWriter files, shared/app.py, shared/dependencies.py
- **Dependencies**: All previous tasks
- **Acceptance criteria**: Zero test failures. Zero ruff errors. Zero ruff
  format changes needed. All files under 500 lines. All functions under 50
  lines.

---

## Task Summary

- **Total Tasks**: 17
- **Phase 0 -- Scaffold**: T000, T001 (2 tasks)
- **Phase 1 -- Domain Models**: T100, T101, T102 (3 tasks)
- **Phase 2 -- Adapters**: T200, T201 (2 tasks)
- **Phase 3 -- Service**: T300, T301, T302, T303, T304 (5 tasks)
- **Phase 4 -- DI Wiring**: T400, T401 (2 tasks)
- **Phase 5 -- Observability**: T500 (1 task)
- **Phase 6 -- Contract Tests & Exports**: T600, T601, T602 (3 tasks)
- **Phase 7 -- Validation**: T700 (1 task; non-coding, verification only)

Note: Tasks T102, T201, T303, T304 are test-first tasks that should be written
before their corresponding implementation tasks per TDD methodology. The
ordering above groups by phase for clarity, but the implementer should write
tests first within each phase:
- Phase 1: T102 (tests) -> T100, T101 (implementation)
- Phase 2: T201 (tests) -> T200 (implementation)
- Phase 3: T303, T304 (tests) -> T300, T301 (implementation)

---

## Dependencies

### External (from LLD Section 9.1)

No new Python packages required. PlanWriter uses only existing dependencies:

| Package | Version | Already in pyproject.toml |
|---------|---------|--------------------------|
| `pydantic` | >=2.0 | Yes |

### Internal (from LLD Section 9.2)

| Component | Interface Used | Status |
|-----------|---------------|--------|
| PlanLibrary | `PlanService.store_plan(plan, signature, outcome, metrics)` -> `StorePlanResponse` | Implemented |
| History | `FactService.store_fact(user_id, StoreFactRequest)` -> `StoreFactResponse` | Implemented |
| VectorIndex | `VectorIndexService.store_embedding(plan_id, plan_data)` -> `None` | Implemented |

### Shared Infrastructure

| Utility | Usage |
|---------|-------|
| `shared/app.py` | Add PlanWriterService to lifespan (T400) |
| `shared/dependencies.py` | Add `get_plan_writer_service()` getter (T401) |

---

## Architectural Considerations

### Blast Radius (from LLD Section 12.1)

- **If PlanWriter fails**: Execution outcomes are not persisted, breaking the
  learning loop. However, plan execution itself is NOT affected (PlanWriter
  is called post-execution).
- **Containment**: PlanWriter introduces no new infrastructure (no DB
  connections, no Redis, no queues). It uses only downstream services'
  existing infrastructure.
- **No cascading failures**: History and VectorIndex failures are isolated
  from PlanLibrary. PlanLibrary is the only fatal dependency.

### Fault Isolation (from LLD Section 12.2)

- **PlanLibrary down**: `persist_outcome()` raises `PlanLibraryWriteError`.
  History and VectorIndex NOT attempted.
- **History down**: Caught, logged, continues to VectorIndex. Returns
  `status="partial"`.
- **VectorIndex down (None)**: Logged, skipped. Returns `status="ok"` with
  `embedding_stored=False`.
- **VectorIndex raises**: Caught, logged. Returns `embedding_stored=False`.
- **Fact derivation fails**: Caught, logged. History skipped, VectorIndex
  still attempted. Returns `status="partial"`.

### Determinism (from LLD Section 12.3)

- `derive_fact()` is deterministic: same (plan, outcome) always produces
  the same StoreFactRequest (template-based, no LLM, no randomness).
- Idempotency guaranteed via downstream upsert semantics (FR-009).

### Write Ordering (from LLD Section 12.5)

1. **PlanLibrary first** (primary store, prevents orphaned facts/embeddings)
2. **History second** (facts reference source_plan_id which must exist)
3. **VectorIndex last** (most optional, can be backfilled)

---

## Files Created/Modified Summary

### New Files (components/PlanWriter/)

| File | Phase | Task |
|------|-------|------|
| `__init__.py` | 0, 6 | T000, T601 |
| `domain/__init__.py` | 0, 6 | T000, T602 |
| `domain/models.py` | 1 | T100, T101 |
| `service/__init__.py` | 0, 6 | T000, T602 |
| `service/plan_writer_service.py` | 3 | T300, T301, T302 |
| `adapters/__init__.py` | 0, 6 | T000, T602 |
| `adapters/fact_deriver.py` | 2 | T200 |
| `tests/__init__.py` | 0 | T000 |
| `tests/conftest.py` | 0 | T001 |
| `tests/test_unit.py` | 1, 2, 3 | T102, T201, T303, T304 |
| `tests/test_contract.py` | 6 | T600 |
| `tests/test_observability.py` | 5 | T500 |

### Modified Files (shared/)

| File | Phase | Task |
|------|-------|------|
| `shared/app.py` | 4 | T400 |
| `shared/dependencies.py` | 4 | T401 |
