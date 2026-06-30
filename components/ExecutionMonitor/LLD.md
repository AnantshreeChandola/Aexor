# ExecutionMonitor — Low-Level Design

**Component**: `components/ExecutionMonitor/`
**Layer**: Orchestration Layer
**Type**: Background service (no HTTP routes, owns `execution_tracker` table)
**Spec**: `specs/031-executionmonitor-infrastructure-watchdog/spec.md`
**Status**: Implementation

---

## 1. Purpose & Scope

ExecutionMonitor is the **infrastructure watchdog** for plan execution (GLOBAL_SPEC v3.0 §3, Project_HLD v6.0 §2.14). It polls `execution_tracker` for running executions and detects two terminal failure modes:

- **Stuck execution**: No progress for 5+ minutes → mark `stuck`, notify user
- **Time budget exceeded**: Total elapsed > 60 minutes → mark `timeout`, notify user

Infrastructure failures are **terminal** — no replay, user must start a new plan. Step-level failures are NOT this component's concern (handled by LLM reasoning in ExecuteOrchestrator).

**Responsibilities:**
- Track execution lifecycle (register → progress → complete/fail)
- Background polling loop detecting stuck/timed-out executions
- Mark terminal status in PostgreSQL
- Notify users via structured log events (webhook/SSE deferred)
- Provide non-fatal write API that never breaks ExecuteOrchestrator

**Out of scope:**
- Step-level failure handling (ExecuteOrchestrator)
- Plan execution itself (ExecuteOrchestrator)
- HTTP route handling (no API layer)
- Task cancellation / async task runtime (deferred to Phase 4 durable mode)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v3.0 | §3 (NFRs — observability, no PII), §8 (Idempotency) |
| Project_HLD.md | v6.0 | §2.14 (ExecutionMonitor), §13 (Background Services) |
| MODULAR_ARCHITECTURE.md | v2.0 | §1 (Orchestration Layer), §4 (ExecutionMonitor deps) |

---

## 3. Architecture Overview

### 3.1 Layer Placement

```
Orchestration Layer
├── ExecuteOrchestrator  (upstream — calls TrackerService)
├── ExecutionMonitor     ← THIS COMPONENT
│   ├── TrackerService   (write API for ExecuteOrchestrator)
│   └── MonitorService   (background polling loop)
└── PreviewOrchestrator  (no interaction)
```

### 3.2 Blast Radius Analysis

| Failure Mode | Impact | Containment |
|-------------|--------|-------------|
| TrackerService database error | Execution continues without tracking | All methods wrapped in try/except; logged as WARNING |
| MonitorService crash | No stuck/timeout detection | Background task restarts on next app restart; no data loss |
| PostgreSQL unavailable | No tracking or detection | TrackerService silently degrades; MonitorService logs ERROR |
| Notifier failure | User not notified of stuck execution | Terminal status still marked in DB; notification_sent stays false |
| Race condition (concurrent polls) | Double-mark terminal | `UPDATE WHERE status='running'` — only first write wins |

### 3.3 Component Boundaries

**Two distinct services** with different consumers:
- **TrackerService**: Write API called by ExecuteOrchestrator at execution milestones
- **MonitorService**: Read/poll loop that detects anomalies and takes terminal action

**Isolation strategy**: TrackerService never blocks execution. MonitorService runs as an independent asyncio background task. Neither service calls MCP tools, accesses credentials, or modifies plans.

---

## 4. Interfaces

### 4.1 TrackerService (Write API)

```python
class TrackerService:
    async def register(plan_id, user_id, trace_id, total_steps) -> None
    async def report_progress(plan_id, completed_steps) -> None
    async def complete(plan_id, success, error_type?, error_details?) -> None
```

All methods are **non-fatal**: wrapped in try/except, log warnings on failure.

### 4.2 MonitorService (Background Loop)

```python
class MonitorService:
    async def run() -> None          # Background loop
    def stop() -> None               # Signal loop to stop
    async def _check_active_executions() -> None  # Single poll cycle
```

### 4.3 Consumer Contracts

| Consumer | Calls | Input | Output | Error Handling |
|----------|-------|-------|--------|----------------|
| ExecuteOrchestrator | `tracker.register()` | plan_id, user_id, trace_id, step_count | None | Silent — exception caught internally |
| ExecuteOrchestrator | `tracker.report_progress()` | plan_id, completed_count | None | Silent — exception caught internally |
| ExecuteOrchestrator | `tracker.complete()` | plan_id, success, error_type | None | Silent — exception caught internally |
| shared/app.py lifespan | `monitor.run()` | — | Never returns (loop) | asyncio.CancelledError on shutdown |

---

## 5. Data Model

### 5.1 TrackerRecord (Pydantic)

```python
class TrackerRecord(BaseModel):
    tracker_id: str          # UUID PK (not plan_id — supports re-execution)
    plan_id: str             # 26-char ULID
    user_id: str             # FK to users.user_id
    trace_id: str            # Correlation ID for distributed tracing
    status: str              # running | completed | failed | stuck | timeout
    total_steps: int         # Total steps in plan
    completed_steps: int     # Steps completed so far
    error_type: str | None   # e.g., "infrastructure_stuck", "time_budget_exceeded"
    error_details: dict | None
    notification_sent: bool  # Dedup flag for user notifications
    started_at: datetime | None
    last_progress_at: datetime | None
    completed_at: datetime | None
```

### 5.2 Request Models

- `RegisterExecutionRequest` — plan_id, user_id, trace_id, total_steps
- `ProgressUpdate` — plan_id, completed_steps
- `CompleteExecutionRequest` — plan_id, success, error_type?, error_details?
- `UserNotification` — failure_type: Literal["stuck", "timeout"] + record metadata

### 5.3 Exceptions

- `MonitorError` — base
- `TrackerNotFoundError(MonitorError)` — plan_id not found
- `TrackerDatabaseError(MonitorError)` — database operation failed

---

## 6. Database Schema

### 6.1 ExecutionTrackerTable

```sql
CREATE TABLE execution_tracker (
    tracker_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id       VARCHAR(26)   NOT NULL,
    user_id       VARCHAR(255)  NOT NULL,
    trace_id      VARCHAR(255)  NOT NULL,
    status        VARCHAR(32)   NOT NULL DEFAULT 'running',
    total_steps   INTEGER       NOT NULL DEFAULT 0,
    completed_steps INTEGER     NOT NULL DEFAULT 0,
    error_type    VARCHAR(64),
    error_details JSONB,
    notification_sent BOOLEAN   NOT NULL DEFAULT FALSE,
    started_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_progress_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);
```

### 6.2 Indexes

```sql
-- Partial index: only active (running) executions for poll queries
CREATE INDEX idx_execution_tracker_active
    ON execution_tracker (status, started_at)
    WHERE status = 'running';

-- Lookup by plan_id (TrackerService queries)
CREATE INDEX idx_execution_tracker_plan_id ON execution_tracker (plan_id);

-- Lookup by user_id (future: user dashboard queries)
CREATE INDEX idx_execution_tracker_user_id ON execution_tracker (user_id);
```

**Migration**: `migrations/008_create_execution_tracker_table.sql`

---

## 7. Adapters

### 7.1 TrackerDatabaseAdapter

Uses `SharedDatabaseAdapter` via `get_database_adapter()` (same pattern as PolicyDatabaseAdapter).

Methods:
- `create_tracker()` — INSERT with gen_random_uuid()
- `update_progress()` — UPDATE WHERE plan_id AND status='running'
- `mark_terminal()` — UPDATE status + error fields WHERE status='running'
- `mark_notified()` — UPDATE notification_sent = true
- `get_active_executions()` — SELECT WHERE status='running' ORDER BY started_at LIMIT 100
- `get_tracker_by_plan()` — SELECT WHERE plan_id

Race safety: All UPDATE queries include `WHERE status = 'running'` so only the first write wins.

### 7.2 Notifier (Protocol + LogNotifier)

```python
@runtime_checkable
class Notifier(Protocol):
    async def notify(notification: UserNotification) -> bool: ...
```

`LogNotifier` emits structured WARNING log events with extra fields (component, plan_id, failure_type). Real push notifications (webhook/SSE/email) deferred to future.

---

## 8. Sequences

### 8.1 Happy Path (Execution Completes)

```
ExecuteOrchestrator → TrackerService.register(plan_id, user_id, trace_id, 5)
  → INSERT execution_tracker (status=running)

ExecuteOrchestrator → TrackerService.report_progress(plan_id, 1)
  → UPDATE completed_steps=1, last_progress_at=NOW()

... (steps 2-5) ...

ExecuteOrchestrator → TrackerService.complete(plan_id, success=True)
  → UPDATE status=completed, completed_at=NOW()
```

### 8.2 Stuck Detection

```
MonitorService._check_active_executions()
  → SELECT WHERE status='running'
  → For each: now - last_progress_at > 5 min?
    → Yes: mark_terminal(status=stuck) + notify(failure_type=stuck) + mark_notified()
```

### 8.3 Timeout Detection

```
MonitorService._check_active_executions()
  → SELECT WHERE status='running'
  → For each: now - started_at > 60 min?
    → Yes: mark_terminal(status=timeout) + notify(failure_type=timeout) + mark_notified()
```

Timeout takes **priority** over stuck (checked first).

### 8.4 Tracker DB Failure

```
ExecuteOrchestrator → TrackerService.register(plan_id, ...)
  → DB raises RuntimeError
  → Caught by try/except → logger.warning("tracker_register_failed")
  → Execution continues unaffected
```

---

## 9. DI Wiring

### 9.1 shared/app.py Lifespan

```python
# After ApprovalGate block
tracker_db = TrackerDatabaseAdapter()
app.state.tracker_service = TrackerService(tracker_db=tracker_db)
app.state.monitor_service = MonitorService(tracker_db=tracker_db, notifier=LogNotifier())

# Wire tracker into ExecuteOrchestrator
if app.state.execute_service is not None:
    app.state.execute_service._tracker = app.state.tracker_service

# Start background task
monitor_task = asyncio.create_task(app.state.monitor_service.run(), name="execution-monitor")

# Shutdown: stop() + cancel() + suppress(CancelledError)
```

### 9.2 shared/dependencies.py

- `get_tracker_service(request) -> TrackerService`
- `get_monitor_service(request) -> MonitorService`

---

## 10. Observability & Safety

### 10.1 Structured Logging

All log events include `extra={"component": "ExecutionMonitor", "plan_id": ...}`.

| Event | Level | When |
|-------|-------|------|
| `tracker_registered` | INFO | Execution registered |
| `tracker_progress` | INFO | Progress updated |
| `tracker_completed` | INFO | Execution completed/failed |
| `tracker_register_failed` | WARNING | DB error on register |
| `tracker_progress_failed` | WARNING | DB error on progress |
| `tracker_complete_failed` | WARNING | DB error on complete |
| `monitor_started` | INFO | Background loop started |
| `monitor_stopped` | INFO | Background loop stopped |
| `monitor_poll_error` | ERROR | Poll cycle failed |
| `execution_stuck_detected` | WARNING | Stuck execution found |
| `execution_timeout_detected` | WARNING | Timed-out execution found |
| `execution_notification` | WARNING | User notified |
| `notification_failed` | ERROR | Notifier raised exception |

### 10.2 No PII in Logs

- `user_id` logged as opaque identifier only
- No passwords, tokens, secrets, or email addresses in log messages
- Verified by `test_observability.py::test_no_pii_*` tests

---

## 11. Non-Functional Requirements

| Metric | Target |
|--------|--------|
| Stuck detection latency | ≤ poll_interval_s (30s default) after threshold |
| Timeout detection latency | ≤ poll_interval_s (30s default) after threshold |
| Poll query p99 | < 50ms (partial index on running status) |
| TrackerService overhead per call | < 10ms |
| Background task memory | < 10MB (stateless per-poll) |
| Notification dedup | 100% (notification_sent flag + single-use check) |

---

## 12. Testing Strategy

**85 tests** across 4 test files:

| File | Tests | Coverage |
|------|-------|----------|
| `test_unit.py` | 25 | Domain model validation, Pydantic constraints, exception hierarchy |
| `test_service.py` | 28 | Async service tests (TrackerService + MonitorService), lifecycle, error suppression |
| `test_contract.py` | 17 | Schema conformance, table-model alignment, serialization round-trips |
| `test_observability.py` | 15 | Structured log verification, no PII leaks, component field checks |

All tests use `FakeTrackerDB` (in-memory dict) and `FakeNotifier` — no real database or network calls.

---

## 13. Risks & Open Questions

| Risk | Mitigation |
|------|-----------|
| In-process polling loop lost on crash | Acceptable for single-container; persistent queue deferred to Phase 4 |
| No real task cancellation | MonitorService marks DB status only; ExecuteOrchestrator runs sync within HTTP request |
| Notification via log only | LogNotifier is placeholder; webhook/SSE/email via Notifier protocol in future |
| Re-execution creates duplicate rows | UUID PK allows multiple tracker rows per plan_id — most recent is authoritative |
