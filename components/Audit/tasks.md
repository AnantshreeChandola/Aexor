# Tasks: Audit (Platform Layer)

**Created**: 2026-04-05
**Branch**: feat/audit-platform-layer
**SPEC**: specs/033-audit-platform-layer/spec.md
**LLD**: components/Audit/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
Audit is an **internal platform component** -- no Preview/Execute wrappers.
Event recording is DI-injected (fire-and-forget); only a read-only HTTP query endpoint is exposed.

### Already Done (DO NOT re-implement)

The following shared infrastructure changes are already merged on this branch:

- `shared/database/models.py` -- AuditEventTable added (lines 586-621)
- `shared/dependencies.py` -- `get_audit_service()` added (lines 105-107)
- `shared/app.py` -- Audit lifespan wiring added (lines 273-290), router registered (lines 347-349)
- `migrations/009_create_audit_events_table.sql` -- DDL migration complete

---

## Phase 0: Setup & Scaffolding

### Acceptance Criterion: Component skeleton follows project structure

- [ ] [T000] Create `components/Audit/__init__.py`
  - File: `components/Audit/__init__.py`
  - Empty or minimal docstring
- [ ] [T001] Create `components/Audit/domain/__init__.py`
  - File: `components/Audit/domain/__init__.py`
  - Empty or minimal docstring
- [ ] [T002] Create `components/Audit/adapters/__init__.py`
  - File: `components/Audit/adapters/__init__.py`
  - Empty or minimal docstring
- [ ] [T003] Create `components/Audit/service/__init__.py`
  - File: `components/Audit/service/__init__.py`
  - Empty or minimal docstring
- [ ] [T004] Create `components/Audit/api/__init__.py`
  - File: `components/Audit/api/__init__.py`
  - Empty or minimal docstring
- [ ] [T005] Create `components/Audit/schemas/` directory (empty init not needed -- JSON files only)
- [ ] [T006] Create `components/Audit/tests/__init__.py`
  - File: `components/Audit/tests/__init__.py`
  - Empty or minimal docstring
- [ ] [T007] Create `components/Audit/tests/conftest.py`
  - File: `components/Audit/tests/conftest.py`
  - Provide shared test fixtures: `FakeAuditDB` (in-memory list), sample constants (`SAMPLE_PLAN_ID`, `SAMPLE_USER_ID`, `SAMPLE_TRACE_ID`), sample `AuditEvent` factory fixture, `AuditService` fixture wired to `FakeAuditDB`
  - Follow the pattern from `components/ExecutionMonitor/tests/conftest.py`

---

## Phase 1: Domain Models (Foundation)

### Acceptance Criterion: AC from SPEC FR-001, FR-002, FR-003, FR-004, FR-005, FR-008

All 11 event types, AuditEvent with ULID, AuditQueryResult, AuditQueryParams, and exceptions.

- [ ] [T100] Create domain models
  - File: `components/Audit/domain/models.py`
  - Implement `AuditEventType(str, Enum)` with all 11 values:
    - `EXECUTION_STARTED`, `STEP_COMPLETED`, `STEP_FAILED`, `EXECUTION_COMPLETED`, `EXECUTION_FAILED`
    - `APPROVAL_GRANTED`, `APPROVAL_EXPIRED`
    - `POLICY_ATTESTATION`, `POLICY_DENIAL`
    - `EXECUTION_STUCK`, `EXECUTION_TIMEOUT`
  - Implement `AuditEvent(BaseModel)`:
    - `event_id: str` (26-char ULID)
    - `event_type: AuditEventType`
    - `plan_id: str | None = None`
    - `user_id: str | None = None`
    - `trace_id: str | None = None`
    - `step_number: int | None = None`
    - `event_data: dict = Field(default_factory=dict)`
    - `created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))`
  - Implement `AuditQueryResult(BaseModel)`:
    - `events: list[AuditEvent]`
    - `next_cursor: str | None = None`
    - `total_count: int`
  - Implement `AuditQueryParams(BaseModel)`:
    - `plan_id: str | None = None`
    - `user_id: str | None = None`
    - `trace_id: str | None = None`
    - `event_type: str | None = None`
    - `start_time: datetime | None = None`
    - `end_time: datetime | None = None`
    - `cursor: str | None = None`
    - `limit: int = Field(default=50, ge=1, le=200)`
  - Implement exceptions:
    - `AuditError(Exception)` -- base
    - `AuditDatabaseError(AuditError)` -- DB failures
    - `AuditBufferOverflowError(AuditError)` -- buffer overflow
  - Reference: LLD.md Section 5

---

## Phase 2: Database Adapter

### Acceptance Criterion: AC from SPEC FR-001 (append-only), FR-006 (query), FR-009 (pagination), FR-011 (retention)

- [ ] [T200] Implement `AuditDatabaseAdapter`
  - File: `components/Audit/adapters/db.py`
  - Use `shared.database.adapter.get_database_adapter()` for `SharedDatabaseAdapter`
  - Import `AuditEventTable` from `shared.database.models`
  - Import `with_db_error_handling` from `shared.database.error_handler`
  - Helper: `_row_to_event(row: AuditEventTable) -> AuditEvent` (follow pattern from `components/ExecutionMonitor/adapters/tracker_db.py::_row_to_record`)
  - Methods:
    - `async def append_event(self, event: AuditEvent) -> None` -- single INSERT
    - `async def append_events_batch(self, events: list[AuditEvent]) -> None` -- bulk INSERT via `session.add_all()`
    - `async def query_events(self, params: AuditQueryParams) -> AuditQueryResult` -- dynamic WHERE clauses (plan_id, user_id, trace_id, event_type, start_time, end_time), cursor pagination via `event_id > cursor ORDER BY event_id ASC LIMIT N`, COUNT for total_count, `next_cursor` = last event_id when more rows exist
    - `async def delete_expired(self, before: datetime) -> int` -- `DELETE WHERE created_at < before`, return rowcount
  - All methods decorated with `@with_db_error_handling`
  - No UPDATE method (append-only invariant per LLD Section 6.3)
  - Reference: LLD.md Section 7

---

## Phase 3: Service Layer (Core Business Logic)

### Acceptance Criterion: AC from SPEC FR-002, FR-003, FR-004, FR-005, FR-007, FR-010, FR-011; SC-001, SC-004, SC-005

- [ ] [T300] Implement `AuditService`
  - File: `components/Audit/service/audit_service.py`
  - Constructor: `__init__(self, db_adapter: AuditDatabaseAdapter, max_buffer_size: int = 1000, flush_threshold: int = 10, flush_interval_s: float = 0.1, retention_days: int = 90)`
  - Private state:
    - `_buffer: list[AuditEvent]` (in-memory event buffer)
    - `_lock: asyncio.Lock` (protect buffer access)
    - `_flush_task: asyncio.Task | None` (background flush loop)
  - Public methods:
    - `async def record(self, event: AuditEvent) -> None`:
      - Call `_sanitize(event.event_data)` in-place
      - Append to `_buffer` under lock
      - If `len(_buffer) >= flush_threshold`, trigger `_flush_buffer()`
      - Wrapped in try/except: NEVER raises to caller (fire-and-forget per LLD Section 13)
      - Log `audit_event_recorded` at DEBUG with `extra={"component": "Audit"}`
    - `async def query(self, plan_id, user_id, trace_id, event_type, start_time, end_time, cursor, limit) -> AuditQueryResult`:
      - Construct `AuditQueryParams` from arguments
      - Delegate to `db_adapter.query_events(params)`
      - Log `audit_query_executed` at INFO
    - `async def flush(self) -> None`:
      - Force-flush the buffer to DB (public, for shutdown)
    - `async def start(self) -> None`:
      - Start background flush loop (`_run_flush_loop`)
    - `async def stop(self) -> None`:
      - Cancel flush loop, final flush
  - Private methods:
    - `def _sanitize(self, event_data: dict) -> dict`:
      - Strip keys: `password`, `secret`, `token`, `credential`, `api_key` (case-insensitive matching on key names)
      - Truncate `error_details` to 500 chars max
      - Redact JWT tokens: only keep `token_id`, never full token value
      - Return sanitized dict
    - `async def _flush_buffer(self) -> None`:
      - Under lock, copy buffer, clear buffer
      - Call `db_adapter.append_events_batch(batch)`
      - On DB error: re-add events to buffer, check overflow
      - If buffer > max_buffer_size: drop oldest events, increment counter, log `audit_buffer_overflow` at WARNING
      - Log `audit_buffer_flushed` at INFO with batch size
    - `async def _run_flush_loop(self) -> None`:
      - Loop: `await asyncio.sleep(flush_interval_s)`, then `_flush_buffer()`
    - `async def _cleanup_expired(self) -> None`:
      - Calculate cutoff = now() - retention_days
      - Call `db_adapter.delete_expired(before=cutoff)`
      - Log `audit_retention_cleanup` at INFO with deleted count
  - Reference: LLD.md Sections 8, 10

---

## Phase 4: API Routes (Read-Only Query Endpoint)

### Acceptance Criterion: AC from SPEC FR-006, FR-009 (cursor pagination, filters)

- [ ] [T400] Implement API routes
  - File: `components/Audit/api/routes.py`
  - FastAPI `APIRouter(prefix="/audit", tags=["audit"])`
  - Single endpoint: `GET /audit/events`
    - Query parameters: `plan_id`, `user_id`, `trace_id`, `event_type`, `start_time`, `end_time`, `cursor`, `limit` (default 50, max 200)
    - Depends on `get_audit_service` from `shared.dependencies`
    - Delegate to `audit_service.query(...)`
    - Return `AuditQueryResult` as JSON
    - Error handling: local `_handle_domain_error()` for `AuditError` subtypes, `APIErrorHandler.handle_generic_error()` as fallback
    - Use `ErrorResponse` from `shared/api/error_handlers.py` for error shapes
  - No POST/PUT/DELETE endpoints (events recorded internally only per SPEC)
  - Reference: LLD.md Section 4.2

---

## Phase 5: JSON Schemas

### Acceptance Criterion: AC from SPEC -- schema validation, no schema drift

- [ ] [T500] Create `audit_event.schema.json`
  - File: `components/Audit/schemas/audit_event.schema.json`
  - JSON Schema (draft-07) for AuditEvent:
    - `event_id`: string, minLength 26, maxLength 26
    - `event_type`: enum of all 11 values
    - `plan_id`: string or null
    - `user_id`: string or null
    - `trace_id`: string or null
    - `step_number`: integer or null
    - `event_data`: object
    - `created_at`: string (ISO 8601 datetime)
  - Required: `event_id`, `event_type`, `event_data`, `created_at`
- [ ] [T501] Create `audit_query.schema.json`
  - File: `components/Audit/schemas/audit_query.schema.json`
  - JSON Schema (draft-07) for AuditQueryResult:
    - `events`: array of AuditEvent
    - `next_cursor`: string or null
    - `total_count`: integer, minimum 0
  - Required: `events`, `total_count`

---

## Phase 6: Tests

### 6A: Unit Tests -- AuditService (~40 tests)

#### Acceptance Criterion: SPEC SC-001, SC-004, SC-005; User Stories 1-5

- [ ] [T600] Write `test_service.py` -- record method tests (~12 tests)
  - File: `components/Audit/tests/test_service.py`
  - Uses `FakeAuditDB` from conftest (no real DB)
  - Tests:
    - `test_record_appends_event_to_buffer` -- event goes to buffer
    - `test_record_auto_flushes_at_threshold` -- flush triggers when buffer reaches flush_threshold (10)
    - `test_record_never_raises_on_db_error` -- fire-and-forget invariant
    - `test_record_sanitizes_password_from_event_data` -- PII stripped
    - `test_record_sanitizes_secret_from_event_data` -- PII stripped
    - `test_record_sanitizes_token_from_event_data` -- PII stripped
    - `test_record_sanitizes_credential_from_event_data` -- PII stripped
    - `test_record_sanitizes_api_key_from_event_data` -- PII stripped
    - `test_record_truncates_error_details` -- 500 char limit
    - `test_record_execution_started_event` -- correct event_type
    - `test_record_step_completed_event` -- includes step_number, role, latency
    - `test_record_step_failed_event` -- sanitized error_details

- [ ] [T601] Write `test_service.py` -- query method tests (~8 tests)
  - File: `components/Audit/tests/test_service.py` (append to same file)
  - Tests:
    - `test_query_by_plan_id` -- filters correctly
    - `test_query_by_user_id` -- filters correctly
    - `test_query_by_trace_id` -- filters correctly
    - `test_query_by_event_type` -- filters correctly
    - `test_query_with_time_range` -- start_time/end_time filtering
    - `test_query_with_cursor_pagination` -- cursor-based forward paging
    - `test_query_default_limit_50` -- default page size
    - `test_query_max_limit_200` -- limit capped at 200

- [ ] [T602] Write `test_service.py` -- buffer management tests (~10 tests)
  - File: `components/Audit/tests/test_service.py` (append to same file)
  - Tests:
    - `test_flush_sends_buffer_to_db` -- manual flush empties buffer
    - `test_flush_clears_buffer_after_success` -- buffer empty after flush
    - `test_flush_retains_events_on_db_failure` -- events stay in buffer on error
    - `test_buffer_overflow_drops_oldest` -- oldest events dropped at max_buffer_size
    - `test_buffer_overflow_logs_warning` -- WARNING logged on overflow
    - `test_concurrent_record_is_thread_safe` -- asyncio.Lock protects buffer
    - `test_flush_loop_runs_periodically` -- background task triggers
    - `test_stop_flushes_remaining_buffer` -- shutdown drains buffer
    - `test_empty_buffer_flush_is_noop` -- no DB call for empty buffer
    - `test_batch_insert_multiple_events` -- batch of N events sent

- [ ] [T603] Write `test_service.py` -- approval and policy event tests (~5 tests)
  - File: `components/Audit/tests/test_service.py` (append to same file)
  - Tests:
    - `test_record_approval_granted_event` -- gate_id, user_id, scopes, token_id present
    - `test_record_approval_expired_event` -- gate_id and plan_id present
    - `test_record_approval_does_not_store_jwt` -- JWT value absent, only token_id
    - `test_record_policy_attestation_event` -- attestation_id, policy_id, decision present
    - `test_record_policy_denial_event` -- violations, reason present

- [ ] [T604] Write `test_service.py` -- retention and infrastructure event tests (~5 tests)
  - File: `components/Audit/tests/test_service.py` (append to same file)
  - Tests:
    - `test_cleanup_expired_deletes_old_events` -- events older than retention_days removed
    - `test_cleanup_expired_returns_deleted_count` -- count returned
    - `test_cleanup_expired_keeps_recent_events` -- recent events untouched
    - `test_record_execution_stuck_event` -- plan_id, detection_reason, elapsed_time
    - `test_record_execution_timeout_event` -- plan_id, timeout details

### 6B: Contract Tests (~25 tests)

#### Acceptance Criterion: GLOBAL_SPEC conformance, schema validation, no PII

- [ ] [T610] Write `test_contract.py` -- schema conformance tests (~8 tests)
  - File: `components/Audit/tests/test_contract.py`
  - Tests:
    - `test_audit_event_validates_against_json_schema` -- AuditEvent.model_dump() vs audit_event.schema.json
    - `test_audit_query_result_validates_against_json_schema` -- vs audit_query.schema.json
    - `test_event_id_is_26_char_ulid` -- validates ULID format
    - `test_event_type_is_valid_enum` -- all 11 values accepted
    - `test_invalid_event_type_rejected` -- Pydantic validation error
    - `test_limit_capped_at_200` -- AuditQueryParams validation
    - `test_limit_minimum_1` -- AuditQueryParams validation
    - `test_created_at_is_utc_iso8601` -- timezone-aware datetime

- [ ] [T611] Write `test_contract.py` -- table-model alignment tests (~5 tests)
  - File: `components/Audit/tests/test_contract.py` (append to same file)
  - Tests:
    - `test_audit_event_table_columns_match_model` -- AuditEventTable columns match AuditEvent fields
    - `test_audit_event_table_has_plan_id_index` -- index exists
    - `test_audit_event_table_has_user_id_index` -- index exists
    - `test_audit_event_table_has_trace_id_index` -- index exists
    - `test_audit_event_table_has_event_type_index` -- index exists

- [ ] [T612] Write `test_contract.py` -- no PII/secrets tests (~7 tests)
  - File: `components/Audit/tests/test_contract.py` (append to same file)
  - Tests:
    - `test_sanitize_strips_password_field` -- "password" key removed from event_data
    - `test_sanitize_strips_secret_field` -- "secret" key removed
    - `test_sanitize_strips_token_field` -- "token" key removed
    - `test_sanitize_strips_credential_field` -- "credential" key removed
    - `test_sanitize_strips_api_key_field` -- "api_key" key removed
    - `test_sanitize_case_insensitive` -- "Password", "SECRET", "Token" all stripped
    - `test_sanitize_preserves_non_sensitive_fields` -- "role", "status", "latency_ms" kept

- [ ] [T613] Write `test_contract.py` -- consumer contract tests (~5 tests)
  - File: `components/Audit/tests/test_contract.py` (append to same file)
  - Tests:
    - `test_record_matches_audit_service_protocol` -- AuditService implements AuditServiceProtocol
    - `test_query_matches_audit_service_protocol` -- query method signature matches
    - `test_flush_matches_audit_service_protocol` -- flush method exists
    - `test_record_is_fire_and_forget` -- record() returns None, never raises
    - `test_all_11_event_types_recordable` -- each AuditEventType can be recorded

### 6C: Observability Tests (~15 tests)

#### Acceptance Criterion: Constitution VI (structured logging, no PII in logs)

- [ ] [T620] Write `test_observability.py` -- structured logging tests (~8 tests)
  - File: `components/Audit/tests/test_observability.py`
  - Tests:
    - `test_record_logs_audit_event_recorded` -- DEBUG log emitted
    - `test_flush_logs_audit_buffer_flushed` -- INFO log with batch size
    - `test_overflow_logs_audit_buffer_overflow` -- WARNING log on overflow
    - `test_query_logs_audit_query_executed` -- INFO log on query
    - `test_cleanup_logs_audit_retention_cleanup` -- INFO log with count
    - `test_db_error_logs_audit_db_error` -- ERROR log on DB failure
    - `test_all_logs_include_component_field` -- `extra={"component": "Audit"}` on every log
    - `test_log_levels_appropriate` -- DEBUG for record, INFO for flush/query, WARNING for overflow, ERROR for DB error

- [ ] [T621] Write `test_observability.py` -- no PII in logs tests (~4 tests)
  - File: `components/Audit/tests/test_observability.py` (append to same file)
  - Tests:
    - `test_no_password_in_log_messages` -- scan all log output for "password" values
    - `test_no_jwt_token_in_log_messages` -- scan for "eyJ" (JWT prefix)
    - `test_no_email_in_log_messages` -- scan for email patterns
    - `test_user_id_logged_as_opaque_only` -- user_id appears as UUID/string, not joined with PII

- [ ] [T622] Write `test_observability.py` -- metrics stubs tests (~3 tests)
  - File: `components/Audit/tests/test_observability.py` (append to same file)
  - Tests:
    - `test_metrics_counter_audit_events_recorded` -- counter incremented on record
    - `test_metrics_gauge_audit_buffer_size` -- gauge reflects buffer length
    - `test_metrics_counter_audit_buffer_overflow` -- counter incremented on overflow

---

## Phase 7: Observability Integration (Implementation)

### Acceptance Criterion: SPEC SC-004, SC-005; Constitution VI

- [ ] [T700] Add structured logging to AuditService
  - File: `components/Audit/service/audit_service.py` (already exists from T300)
  - Ensure all log calls use `logging.getLogger(__name__)` with `extra={"component": "Audit"}`
  - Log events per LLD Section 10.1:
    - `audit_event_recorded` at DEBUG
    - `audit_buffer_flushed` at INFO
    - `audit_buffer_overflow` at WARNING
    - `audit_query_executed` at INFO
    - `audit_retention_cleanup` at INFO
    - `audit_db_error` at ERROR
  - Note: This is implemented inline with T300; this task is for verification and refinement

- [ ] [T701] Add metrics stubs
  - File: `components/Audit/service/audit_service.py` (already exists from T300)
  - Add simple counter/gauge attributes (no Prometheus dependency for MVP):
    - `_events_recorded: int = 0` (counter)
    - `_buffer_overflows: int = 0` (counter)
    - `_events_queried: int = 0` (counter)
  - Properties: `buffer_size -> int` (len of buffer for metrics gauge)
  - These enable test assertions without requiring external metrics libraries

- [ ] [T702] Verify no PII in any log output
  - File: `components/Audit/service/audit_service.py` (already exists from T300)
  - Audit all `logger.*()` calls to ensure no event_data content (which could contain PII) is logged
  - Only log metadata: event_type, event_id, plan_id, batch_size, deleted_count
  - This is a review/verification task, not new code

---

## Task Summary

- **Total Tasks**: 27
- **Setup (Phase 0)**: T000-T007 (8 tasks)
- **Domain (Phase 1)**: T100 (1 task)
- **Adapter (Phase 2)**: T200 (1 task)
- **Service (Phase 3)**: T300 (1 task)
- **API (Phase 4)**: T400 (1 task)
- **Schemas (Phase 5)**: T500-T501 (2 tasks)
- **Tests (Phase 6)**: T600-T604, T610-T613, T620-T622 (13 tasks, ~80 tests total)
- **Observability (Phase 7)**: T700-T702 (3 tasks -- verification/refinement)

## Dependencies

**External** (from LLD.md Section 15):
- `ulid-py >= 1.1.0` -- already in project (event_id generation)
- `SQLAlchemy[asyncio]` -- already in project (database adapter)
- `asyncio` -- stdlib (buffer management, background tasks)

**Internal** (from LLD.md Sections 9, 15):
- `shared/database/adapter.py` -- `SharedDatabaseAdapter`, `get_database_adapter()`
- `shared/database/models.py` -- `AuditEventTable` (ALREADY DONE)
- `shared/database/error_handler.py` -- `with_db_error_handling` decorator
- `shared/api/error_handlers.py` -- `ErrorResponse`, `APIErrorHandler`
- `shared/dependencies.py` -- `get_audit_service()` (ALREADY DONE)
- `shared/app.py` -- lifespan wiring (ALREADY DONE)

## Architectural Considerations

**Blast Radius** (from LLD Section 3.3):
- If Audit fails: events buffered in-memory; upstream callers unaffected
- `record()` wrapped in try/except; NEVER raises to caller
- Buffer overflow: oldest events dropped, metric incremented, WARNING logged
- Query endpoint failure returns 500; no impact on recording path
- Containment: all errors caught internally, structured logging for diagnostics

**Determinism** (from LLD Section 13):
- No Preview/Execute envelope -- internal platform component
- `record()` is append-only, no deduplication (caller responsibility)
- `query()` is read-only, deterministic for same data and params
- ULID provides natural chronological ordering for cursor pagination

**Fire-and-Forget Invariant** (from LLD Section 13):
- `AuditService.record()` MUST NOT raise exceptions to callers
- All database errors caught internally and logged
- This is the single most important behavioral contract

**Append-Only Immutability** (from LLD Section 6.3):
- No UPDATE statements on `audit_events` rows
- Only DELETE for retention cleanup (`created_at < cutoff`)
- Enforced by adapter API: no `update_event()` method exists
