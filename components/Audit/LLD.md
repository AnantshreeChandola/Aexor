# Audit — Low-Level Design

**Component**: `components/Audit/`
**Layer**: Platform Layer
**Type**: Internal service (read-only HTTP query endpoint, DI-injected event recording)
**Spec**: `specs/033-audit-platform-layer/spec.md`
**Status**: Implementation

---

## 1. Purpose & Scope

Audit is the **centralized, immutable, append-only audit log** for all significant system events (GLOBAL_SPEC v3.0 §3, Project_HLD v6.1 §2.15). It consolidates audit data from three upstream components:

- **ExecuteOrchestrator**: execution lifecycle events (started, step completed/failed, execution completed/failed), policy attestations, policy denials
- **ApprovalGate**: approval decisions (granted, expired)
- **ExecutionMonitor**: infrastructure events (stuck, timeout)

Today, audit-relevant data is scattered across `plan_outcomes`, `execution_tracker`, `policy_attestations` (embedded in PlanOutcome), and ephemeral Redis gate state. Audit consolidates these into a single durable `audit_events` table with a query API, enabling end-to-end execution tracing via `plan_id` / `trace_id` correlation.

**Responsibilities:**
- Record audit events from upstream components (fire-and-forget, non-blocking)
- In-memory buffering with batch INSERT for throughput
- PII/secret sanitization before persistence
- Cursor-based paginated query API (read-only HTTP endpoint)
- Configurable retention cleanup (default 90 days)

**Out of scope:**
- External event ingestion via HTTP POST (deferred — DI-only for MVP)
- Real-time streaming / webhooks (deferred)
- Cross-container event replication (single-container deployment)
- Preview/Execute wrappers (internal platform component)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v3.0 | §3 (NFRs — observability, no PII), §8 (Idempotency) |
| Project_HLD.md | v6.1 | §2.15 (Audit), §13 (Platform Services) |
| MODULAR_ARCHITECTURE.md | v2.0 | §1 (Platform Layer), §4 (Audit deps) |

---

## 3. Architecture Overview

### 3.1 Layer Placement

```
Platform Layer
└── Audit
    ├── AuditService        (record + query + buffer + sanitize + retention)
    ├── AuditDatabaseAdapter (append-only INSERT, filtered SELECT, cursor pagination)
    └── GET /audit/events    (read-only query endpoint)
```

### 3.2 Callers (upstream components inject AuditService via DI)

- **ExecuteOrchestrator** → `execution_started`, `step_completed`, `step_failed`, `execution_completed`, `execution_failed`, `policy_attestation`, `policy_denial`
- **ApprovalGate** → `approval_granted`, `approval_expired`
- **ExecutionMonitor** → `execution_stuck`, `execution_timeout`

### 3.3 Blast Radius Analysis

| Failure Mode | Impact | Containment |
|-------------|--------|-------------|
| AuditService database error | Events buffered in-memory | `record()` wrapped in try/except; never raises to caller |
| Buffer overflow (>1000 events) | Oldest events dropped | Metric incremented; WARNING logged |
| PostgreSQL unavailable | No persistence until reconnect | Buffer retains events; flush retried on next interval |
| Query endpoint failure | Client gets 500 | ErrorResponse returned; no impact on recording |
| Retention cleanup failure | Old events persist longer | Logged as ERROR; retried on next schedule |

**Critical invariant**: Audit failure NEVER blocks callers. `record()` is fire-and-forget with try/except.

---

## 4. Interfaces

### 4.1 AuditServiceProtocol

```python
class AuditServiceProtocol(Protocol):
    async def record(self, event: AuditEvent) -> None:
        """Append an audit event. Non-blocking; buffers if DB unavailable."""
        ...

    async def query(
        self,
        plan_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        event_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> AuditQueryResult:
        """Query audit events with filters. Returns paginated results."""
        ...

    async def flush(self) -> None:
        """Force-flush the in-memory buffer to the database."""
        ...
```

### 4.2 HTTP API

`GET /audit/events` — query params match `query()` signature. Returns paginated `AuditQueryResult`.

### 4.3 Consumer Contracts

| Caller | Method | Input | Output | Error Handling |
|--------|--------|-------|--------|----------------|
| ExecuteOrchestrator | `audit.record(event)` | AuditEvent | None | Silent — exception caught internally |
| ApprovalGate | `audit.record(event)` | AuditEvent | None | Silent — exception caught internally |
| ExecutionMonitor | `audit.record(event)` | AuditEvent | None | Silent — exception caught internally |
| HTTP client | `GET /audit/events?plan_id=...` | Query params | AuditQueryResult JSON | 400/500 via ErrorResponse |

---

## 5. Data Model

### 5.1 AuditEventType (Enum)

```python
class AuditEventType(str, Enum):
    EXECUTION_STARTED = "execution_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_EXPIRED = "approval_expired"
    POLICY_ATTESTATION = "policy_attestation"
    POLICY_DENIAL = "policy_denial"
    EXECUTION_STUCK = "execution_stuck"
    EXECUTION_TIMEOUT = "execution_timeout"
```

### 5.2 AuditEvent (Pydantic v2)

```python
class AuditEvent(BaseModel):
    event_id: str          # 26-char ULID
    event_type: AuditEventType
    plan_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None
    step_number: int | None = None
    event_data: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

### 5.3 AuditQueryResult

```python
class AuditQueryResult(BaseModel):
    events: list[AuditEvent]
    next_cursor: str | None = None
    total_count: int
```

### 5.4 AuditQueryParams

```python
class AuditQueryParams(BaseModel):
    plan_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None
    event_type: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
```

### 5.5 Exceptions

```python
class AuditError(Exception): ...
class AuditDatabaseError(AuditError): ...
class AuditBufferOverflowError(AuditError): ...
```

---

## 6. Database Schema & Migration

### 6.1 AuditEventTable

```sql
CREATE TABLE audit_events (
    event_id      VARCHAR(26)   PRIMARY KEY,        -- ULID
    event_type    VARCHAR(32)   NOT NULL,
    plan_id       VARCHAR(26),
    user_id       VARCHAR(255),
    trace_id      VARCHAR(255),
    step_number   INTEGER,
    event_data    JSONB         NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
```

### 6.2 Indexes

```sql
-- Filtered partial indexes for common query patterns
CREATE INDEX idx_audit_events_plan_id ON audit_events (plan_id) WHERE plan_id IS NOT NULL;
CREATE INDEX idx_audit_events_user_id ON audit_events (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_audit_events_trace_id ON audit_events (trace_id) WHERE trace_id IS NOT NULL;

-- Full indexes for type and time filtering
CREATE INDEX idx_audit_events_event_type ON audit_events (event_type);
CREATE INDEX idx_audit_events_created_at ON audit_events (created_at);

-- Composite index for plan timeline queries
CREATE INDEX idx_audit_events_plan_created ON audit_events (plan_id, created_at) WHERE plan_id IS NOT NULL;
```

**Migration**: `migrations/009_create_audit_events_table.sql`

### 6.3 Append-Only Invariant

No UPDATE statements on `audit_events` rows. The only DELETE is for retention cleanup (`DELETE WHERE created_at < cutoff`). This is enforced by the adapter API — no `update_event()` method exists.

---

## 7. Adapters

### 7.1 AuditDatabaseAdapter

Uses `SharedDatabaseAdapter` via `get_database_adapter()`. All methods decorated with `@with_db_error_handling`.

| Method | SQL Operation | Description |
|--------|---------------|-------------|
| `append_event(event)` | INSERT | Single row insert |
| `append_events_batch(events)` | Batch INSERT | Bulk insert for buffer flush |
| `query_events(params)` | SELECT + COUNT | Dynamic WHERE clauses + cursor pagination |
| `delete_expired(before)` | DELETE | Retention cleanup, returns deleted count |

**Cursor pagination**: ULID is naturally chronologically sorted, so `event_id > cursor ORDER BY event_id ASC LIMIT N` provides forward-only pagination without offset performance degradation.

**Dynamic WHERE construction**: Each non-None filter in `AuditQueryParams` adds a WHERE clause. Filters are AND-combined. Time range uses `created_at >= start_time AND created_at <= end_time`.

---

## 8. Sequences

### 8.1 Record Event (Happy Path)

```
Caller → AuditService.record(event)
  → _sanitize(event.event_data)     # Strip PII/secrets
  → _buffer.append(event)           # Add to in-memory buffer
  → len(_buffer) >= 10 OR flush_interval elapsed?
    → Yes: AuditDatabaseAdapter.append_events_batch(_buffer)
           → _buffer.clear()
    → No:  return (event stays in buffer)
```

### 8.2 Record Event (DB Unavailable)

```
AuditService.record(event)
  → _sanitize(event.event_data)
  → _buffer.append(event)
  → flush attempt → DB raises exception
    → Caught internally → log audit_db_error
    → Events remain in buffer
  → len(_buffer) > 1000?
    → Yes: drop oldest events, increment audit_buffer_overflow metric
    → No:  wait for next flush interval
  → DB reconnects → next flush succeeds
```

### 8.3 Query Events

```
GET /audit/events?plan_id=X&cursor=Y&limit=50
  → Parse AuditQueryParams from query string
  → AuditService.query(plan_id=X, cursor=Y, limit=50)
    → AuditDatabaseAdapter.query_events(params)
      → SELECT WHERE plan_id=X AND event_id > Y ORDER BY event_id ASC LIMIT 50
      → SELECT COUNT(*) WHERE plan_id=X  (total_count)
    → next_cursor = last event_id if more results exist
  → Return AuditQueryResult{events, next_cursor, total_count}
```

### 8.4 Retention Cleanup (Background)

```
APScheduler / asyncio loop (daily at 02:00 UTC)
  → AuditService._cleanup_expired()
    → cutoff = now() - AUDIT_RETENTION_DAYS (default 90)
    → AuditDatabaseAdapter.delete_expired(before=cutoff)
    → Log audit_retention_cleanup with deleted count
```

---

## 9. Shared Infrastructure Usage

### 9.1 Database

- `SharedDatabaseAdapter` from `shared/database/adapter.py`
- `@with_db_error_handling` from `shared/database/error_handler.py`
- `AuditEventTable` added to `shared/database/models.py`

### 9.2 Error Handling

- `ErrorResponse` from `shared/api/error_handlers.py` for HTTP error responses
- `APIErrorHandler.handle_generic_error()` as fallback for unknown errors
- Local `_handle_domain_error()` in routes for Audit-specific error mapping

### 9.3 DI Wiring

- `shared/app.py`: `create_audit_service()` → `app.state.audit_service`
- `shared/dependencies.py`: `get_audit_service(request) -> AuditService`
- Wire into ExecuteOrchestrator, ApprovalGate, ExecutionMonitor via `app.state` (additive only)

---

## 10. Observability & Safety

### 10.1 Structured Logging

All log events include `extra={"component": "Audit"}`.

| Event | Level | When |
|-------|-------|------|
| `audit_event_recorded` | DEBUG | Event buffered successfully |
| `audit_buffer_flushed` | INFO | Batch INSERT completed |
| `audit_buffer_overflow` | WARNING | Buffer exceeded max capacity (1000) |
| `audit_query_executed` | INFO | Query served to client |
| `audit_retention_cleanup` | INFO | Expired events deleted |
| `audit_db_error` | ERROR | Database operation failed |

### 10.2 PII Sanitization

`AuditService._sanitize(event_data)` enforces:
- **Strip keys**: `password`, `secret`, `token`, `credential`, `api_key`
- **Truncate**: `error_details` to 500 chars max
- **Redact**: JWT tokens stored as `token_id` only, never full token value

### 10.3 No PII in Logs

- `user_id` logged as opaque identifier only
- No passwords, tokens, secrets, or email addresses in log messages
- Verified by `test_observability.py::test_no_pii_*` tests

### 10.4 Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `audit_events_recorded_total` | Counter | event_type |
| `audit_events_queried_total` | Counter | — |
| `audit_buffer_size` | Gauge | — |
| `audit_buffer_overflow_total` | Counter | — |
| `audit_write_seconds` | Histogram | — |
| `audit_query_seconds` | Histogram | — |

---

## 11. Non-Functional Requirements

| Metric | Target |
|--------|--------|
| Write p95 | < 10ms (fire-and-forget, non-blocking) |
| Query p95 (single plan_id) | < 200ms |
| Query p95 (complex multi-filter) | < 500ms |
| Throughput | 100 events/sec sustained (batch inserts) |
| Retention | 90 days default (configurable via `AUDIT_RETENTION_DAYS`) |
| Buffer capacity | 1000 events max in-memory |
| Batch flush | Every 100ms or 10 events (whichever first) |

---

## 12. Testing Strategy

**~80 tests** across 3 test files:

| File | Tests | Coverage |
|------|-------|----------|
| `test_service.py` | ~40 | record, query, buffering, flush, sanitization, retention, error handling |
| `test_contract.py` | ~25 | Schema conformance, table-model alignment, no PII in events, pagination |
| `test_observability.py` | ~15 | Structured logging, component field, no PII in logs |

All tests use `FakeAuditDB` (in-memory list) — no real database or network calls.

---

## 13. Architectural Considerations

- **Fire-and-forget**: `record()` NEVER raises to caller; all errors caught internally
- **Append-only immutability**: No UPDATE on `audit_events` rows (only DELETE for retention)
- **Batch inserts**: In-memory buffer + periodic flush reduces DB round-trips
- **No deduplication**: Caller responsible for not sending duplicate events; append-only model accepts all
- **Background task durability**: In-memory buffer lost on crash — acceptable for MVP single-container
- **Retention cleanup**: Daily background task via asyncio loop or APScheduler (reuse ExecutionMonitor pattern)
- **ULID ordering**: event_id (ULID) provides natural chronological ordering for cursor pagination

---

## 14. ADR References

- ADR-0001 (component-first structure)

---

## 15. Dependencies

- `ulid-py >= 1.1.0` (already in project)
- `SQLAlchemy[asyncio]` (existing)
- `APScheduler` or `asyncio.create_task` loop (already used by ExecutionMonitor)

---

## 16. Risks & Open Questions

| Risk | Mitigation |
|------|-----------|
| Write amplification at high step counts | Batch inserts (flush every 100ms or 10 events) |
| Storage growth without retention | Daily cleanup task, default 90 days |
| Approval expiry detection | Deferred to ExecutionMonitor polling loop |
| In-memory buffer lost on crash | Acceptable for MVP; persistent queue deferred |
| External event ingestion (POST endpoint) | Deferred — DI-only for MVP |

---

## 17. Post-generation Validation Checklist

- [x] event_id uses ULID (26-char) — matches project convention
- [x] user_id present on AuditEvent (nullable — system events may lack user)
- [x] Conformance references current doc versions (v3.0/v6.1/v2.0)
- [x] Table ownership: `audit_events` owned by Audit (new table, requires MODULAR_ARCHITECTURE update)
- [x] Consumer contracts defined for all 3 callers + HTTP
- [x] Append-only storage (no UPDATE on audit rows)
- [x] Migration file: 009 (next after 008_create_execution_tracker)
- [x] Error handling uses ErrorResponse from `shared/api/error_handlers.py`
- [x] Database adapter uses SharedDatabaseAdapter + `@with_db_error_handling`
- [x] No PII/secrets in event_data (sanitization layer)
- [x] All 11 event types from spec covered
- [x] Cursor pagination via ULID natural ordering
- [x] Batch flush: 100ms interval OR 10 events (whichever first)
