# Feature Specification: ExecutionMonitor

**Feature Branch**: `feat/executionmonitor-infrastructure-watchdog`
**Created**: 2026-04-05
**Status**: Draft
**Spec ID**: 031
**Input**: User description: "ExecutionMonitor -- Infrastructure watchdog: background polling service that detects stuck executions, enforces time budgets, and notifies users of infrastructure failures"

---

## Overview

ExecutionMonitor is an **Orchestration Layer** background polling service (component 14 of 15) that provides infrastructure-level failure detection for plan executions. It polls the `execution_tracker` PostgreSQL table every 30 seconds, detects stuck executions (no progress for 5+ minutes), enforces time budgets (60-minute maximum), marks terminal executions, and notifies users of infrastructure failures. Per the HLD (§13, §C), infrastructure failures are **terminal** -- no replay, user must start a new plan. Step-level failures are NOT its concern (handled by LLM reasoning in ExecuteOrchestrator). The component consists of two services: **TrackerService** (non-fatal write API called by ExecuteOrchestrator to register/update execution progress) and **MonitorService** (background loop that polls for and handles stuck/timed-out executions).

---

## User Scenarios & Testing

### User Story 1 - Execution Progress Tracking (Priority: P1)

ExecuteOrchestrator runs a plan and reports its progress to the execution tracker. TrackerService registers the execution at start, updates completed step count after each level, and marks terminal status on completion or failure. All tracker calls are non-fatal -- tracker failure never breaks execution.

**Why this priority**: This is the foundation -- without progress tracking, the monitor has nothing to poll. TrackerService must be wired into ExecuteOrchestrator to provide the data MonitorService needs.

**Independent Test**: Can be fully tested by calling `TrackerService.register()`, `report_progress()`, and `complete()`, then verifying the tracker record reflects the correct state in the in-memory fake database.

**Acceptance Scenarios**:

1. **Given** a plan with 5 steps starts execution, **When** `TrackerService.register(plan_id, user_id, trace_id, 5)` is called, **Then** a tracker record is created with `status="running"`, `total_steps=5`, `completed_steps=0`, and `started_at` set to now.
2. **Given** a running execution, **When** `TrackerService.report_progress(plan_id, 3)` is called, **Then** the tracker record updates `completed_steps=3` and `last_progress_at` to now.
3. **Given** a running execution, **When** `TrackerService.complete(plan_id, success=True)` is called, **Then** the tracker record updates `status="completed"` and `completed_at` to now.
4. **Given** a running execution, **When** `TrackerService.complete(plan_id, success=False, error_type="step_failure")` is called, **Then** the tracker record updates `status="failed"`, `error_type="step_failure"`, and `completed_at` to now.
5. **Given** the tracker database is unreachable, **When** any TrackerService method is called, **Then** the call returns without raising (non-fatal), and a warning is logged.

---

### User Story 2 - Stuck Execution Detection (Priority: P1)

MonitorService polls every 30 seconds for running executions. If an execution has not reported progress for 5+ minutes, it is marked as "stuck" (terminal), the user is notified, and the notification is recorded.

**Why this priority**: Stuck execution detection is the core value proposition -- without it, hung asyncio tasks silently consume resources indefinitely.

**Independent Test**: Create a tracker record with `last_progress_at` set to 6 minutes ago, run one poll cycle, and verify the record is marked `status="stuck"` and notification was sent.

**Acceptance Scenarios**:

1. **Given** a running execution with `last_progress_at` 6 minutes ago, **When** MonitorService polls, **Then** the execution is marked `status="stuck"`, `error_type="infrastructure_stuck"`, and a notification is sent with `failure_type="stuck"`.
2. **Given** a running execution with `last_progress_at` 3 minutes ago, **When** MonitorService polls, **Then** the execution is NOT marked stuck (within threshold).
3. **Given** a stuck execution that was already notified (`notification_sent=True`), **When** MonitorService polls again, **Then** no duplicate notification is sent.

---

### User Story 3 - Time Budget Enforcement (Priority: P1)

MonitorService detects executions that have exceeded the 60-minute time budget. These are marked as "timeout" (terminal), and the user is notified.

**Why this priority**: Time budget enforcement prevents resource leaks from runaway tasks. Without it, a single hung plan could consume resources indefinitely.

**Independent Test**: Create a tracker record with `started_at` 65 minutes ago and recent `last_progress_at`, run one poll cycle, and verify it is marked `status="timeout"`.

**Acceptance Scenarios**:

1. **Given** a running execution with `started_at` 65 minutes ago (but `last_progress_at` recent), **When** MonitorService polls, **Then** the execution is marked `status="timeout"`, `error_type="time_budget_exceeded"`, and a notification is sent with `failure_type="timeout"`.
2. **Given** a running execution with `started_at` 45 minutes ago, **When** MonitorService polls, **Then** the execution is NOT marked timed-out (within budget).
3. **Given** a timed-out execution already notified, **When** MonitorService polls again, **Then** no duplicate notification is sent.

---

### User Story 4 - Background Monitor Lifecycle (Priority: P2)

MonitorService runs as an asyncio background task started during application lifespan startup and stopped during shutdown. The monitor gracefully handles exceptions during polling without crashing.

**Why this priority**: The lifecycle management ensures the monitor runs reliably across the application's lifetime.

**Independent Test**: Start the monitor, verify it polls, call `stop()`, and verify the loop exits cleanly.

**Acceptance Scenarios**:

1. **Given** MonitorService is created, **When** `run()` is called, **Then** it begins polling every `poll_interval_s` seconds.
2. **Given** MonitorService is running, **When** `stop()` is called, **Then** `_running` is set to False and the loop exits after the current sleep.
3. **Given** an exception occurs in `_check_active_executions()`, **When** the loop catches it, **Then** the error is logged and the loop continues (does not crash).

---

### User Story 5 - User Notification on Infrastructure Failure (Priority: P2)

When an execution is detected as stuck or timed-out, the user is notified via the Notifier protocol. The default `LogNotifier` emits structured log events. Future implementations may use webhooks, SSE, or email.

**Why this priority**: User awareness of infrastructure failures is important but secondary to detection -- detection without notification still provides value via the `execution_tracker` table (queryable by other components).

**Independent Test**: Trigger a stuck detection, verify `LogNotifier.notify()` emits a structured log with `plan_id`, `user_id`, `failure_type`.

**Acceptance Scenarios**:

1. **Given** a stuck execution for user "user-123", **When** the notification is sent, **Then** a structured log event is emitted with `component="ExecutionMonitor"`, `plan_id`, `user_id`, `failure_type="stuck"`.
2. **Given** a timed-out execution, **When** the notification is sent, **Then** a structured log event is emitted with `failure_type="timeout"`.
3. **Given** the notifier raises an exception, **When** MonitorService handles the stuck execution, **Then** the error is logged but the execution is still marked terminal (notification failure does not block state update).

---

### Edge Cases

- What happens when the same plan_id is re-executed after a timeout? A new tracker record is created (UUID primary key). `get_tracker_by_plan()` returns the most recent record.
- What happens when `report_progress()` is called for a plan that has no tracker? The database UPDATE matches zero rows and returns `False`. No error is raised.
- What happens when MonitorService polls but the database is unreachable? The exception is caught and logged; the monitor continues polling on the next interval.
- What happens when `complete()` races with MonitorService marking the same execution as stuck? The UPDATE uses `WHERE status = 'running'` -- only the first write wins; the second is a no-op.
- What happens when `last_progress_at == started_at` (no progress ever reported)? The stuck threshold (5 minutes since `last_progress_at`) applies -- if the execution has been running for 5+ minutes with no progress, it is stuck.

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST provide a `TrackerService` with `register()`, `report_progress()`, and `complete()` methods for ExecuteOrchestrator to report execution state.
- **FR-002**: System MUST persist execution state in a PostgreSQL `execution_tracker` table with columns: tracker_id (UUID PK), plan_id, user_id, trace_id, status, total_steps, completed_steps, error_type, error_details, notification_sent, started_at, last_progress_at, completed_at.
- **FR-003**: All TrackerService methods MUST be non-fatal -- exceptions are caught and logged, never propagated to ExecuteOrchestrator.
- **FR-004**: System MUST provide a `MonitorService` background polling loop that runs every 30 seconds (configurable).
- **FR-005**: MonitorService MUST detect stuck executions: `status="running"` AND `now - last_progress_at > 5 minutes` (configurable).
- **FR-006**: MonitorService MUST detect timed-out executions: `status="running"` AND `now - started_at > 60 minutes` (configurable).
- **FR-007**: MonitorService MUST mark detected stuck/timed-out executions as terminal (`status="stuck"` or `status="timeout"`).
- **FR-008**: MonitorService MUST send a user notification via the Notifier protocol when marking an execution terminal.
- **FR-009**: MonitorService MUST NOT send duplicate notifications -- skip if `notification_sent=True`.
- **FR-010**: System MUST provide a `Notifier` protocol with `LogNotifier` as the default implementation (structured log output).
- **FR-011**: System MUST log all operations with `component="ExecutionMonitor"`, `plan_id`, and `trace_id` correlation. No PII/secrets in logs.
- **FR-012**: ExecuteOrchestrator MUST accept an optional `tracker` parameter. When present, it calls `register()` at start, `report_progress()` after each DAG level, and `complete()` at the end.
- **FR-013**: MonitorService MUST handle exceptions during polling gracefully -- log and continue, never crash the background task.

### Key Entities

- **TrackerRecord**: Represents a row in `execution_tracker` -- plan_id, user_id, trace_id, status, total/completed steps, error info, timestamps.
- **RegisterExecutionRequest**: Input to `register()` -- plan_id, user_id, trace_id, total_steps.
- **ProgressUpdate**: Input to `report_progress()` -- plan_id, completed_steps.
- **CompleteExecutionRequest**: Input to `complete()` -- plan_id, success, error_type, error_details.
- **UserNotification**: Notification payload -- plan_id, user_id, trace_id, failure_type (stuck/timeout), step counts, timestamps, message.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: MonitorService detects and marks stuck executions within one poll interval (30s) of the 5-minute stuck threshold.
- **SC-002**: MonitorService detects and marks timed-out executions within one poll interval of the 60-minute budget.
- **SC-003**: TrackerService failure never causes an ExecuteOrchestrator request to fail (100% non-fatal).
- **SC-004**: 100% of stuck/timeout events generate exactly one user notification (no duplicates, no misses).
- **SC-005**: All structured log events include `component="ExecutionMonitor"` and `plan_id` correlation.
- **SC-006**: ~95 tests pass with high coverage across unit, service, contract, and observability test files.

---

## Interfaces & Contracts

### TrackerService Interface (Write API for ExecuteOrchestrator)

```python
class TrackerService:
    async def register(
        self, plan_id: str, user_id: str, trace_id: str, total_steps: int
    ) -> None:
        """Register a new execution. Non-fatal: exceptions are caught and logged."""

    async def report_progress(self, plan_id: str, completed_steps: int) -> None:
        """Update completed step count and last_progress_at. Non-fatal."""

    async def complete(
        self, plan_id: str, success: bool,
        error_type: str | None = None, error_details: dict | None = None
    ) -> None:
        """Mark execution as completed/failed. Non-fatal."""
```

### MonitorService Interface (Background Watchdog)

```python
class MonitorService:
    def __init__(
        self,
        tracker_db: TrackerDatabaseAdapter,
        notifier: Notifier,
        poll_interval_s: int = 30,
        stuck_timeout_minutes: int = 5,
        max_execution_minutes: int = 60,
    ) -> None: ...

    async def run(self) -> None:
        """Background polling loop. Runs until stop() is called."""

    def stop(self) -> None:
        """Signal the loop to stop."""
```

### Notifier Protocol

```python
@runtime_checkable
class Notifier(Protocol):
    async def notify(self, notification: UserNotification) -> bool:
        """Send a notification. Returns True on success."""
        ...
```

### TrackerDatabaseAdapter Interface

```python
class TrackerDatabaseAdapter:
    async def create_tracker(
        self, plan_id: str, user_id: str, trace_id: str, total_steps: int
    ) -> TrackerRecord: ...

    async def update_progress(self, plan_id: str, completed_steps: int) -> bool: ...

    async def mark_terminal(
        self, plan_id: str, status: str,
        error_type: str | None = None, error_details: dict | None = None
    ) -> bool: ...

    async def mark_notified(self, plan_id: str) -> bool: ...

    async def get_active_executions(self, limit: int = 100) -> list[TrackerRecord]: ...

    async def get_tracker_by_plan(self, plan_id: str) -> TrackerRecord | None: ...
```

### ExecuteOrchestrator Factory (Modified)

```python
def create_execute_service(
    ...,
    tracker: Any | None = None,  # NEW: optional TrackerService
) -> ExecuteService: ...
```

### Tracker Record (stored in execution_tracker table)

```json
{
  "tracker_id": "uuid",
  "plan_id": "01JXYZ1234567890ABCDEFGHIJ",
  "user_id": "user-uuid-12345678",
  "trace_id": "trace-abc-123",
  "status": "running|completed|failed|stuck|timeout",
  "total_steps": 5,
  "completed_steps": 3,
  "error_type": null,
  "error_details": null,
  "notification_sent": false,
  "started_at": "2026-04-05T10:00:00Z",
  "last_progress_at": "2026-04-05T10:02:30Z",
  "completed_at": null
}
```

### UserNotification (notification payload)

```json
{
  "plan_id": "01JXYZ1234567890ABCDEFGHIJ",
  "user_id": "user-uuid-12345678",
  "trace_id": "trace-abc-123",
  "failure_type": "stuck|timeout",
  "total_steps": 5,
  "completed_steps": 3,
  "started_at": "2026-04-05T10:00:00Z",
  "last_progress_at": "2026-04-05T10:02:30Z",
  "message": "Execution stuck -- no progress for 5+ minutes. Please start a new plan."
}
```

Reference: docs/architecture/GLOBAL_SPEC.md (v3.0), Project_HLD.md (v6.1 §13, ExecutionMonitor Pattern)

---

## Component Mapping

- **Target**: `components/ExecutionMonitor/`
- **Files expected to change**:
  - `components/ExecutionMonitor/__init__.py` -- Module docstring
  - `components/ExecutionMonitor/domain/__init__.py`
  - `components/ExecutionMonitor/domain/models.py` -- TrackerRecord, RegisterExecutionRequest, ProgressUpdate, CompleteExecutionRequest, UserNotification, MonitorError, TrackerNotFoundError, TrackerDatabaseError
  - `components/ExecutionMonitor/adapters/__init__.py`
  - `components/ExecutionMonitor/adapters/tracker_db.py` -- TrackerDatabaseAdapter (PostgreSQL CRUD via SharedDatabaseAdapter)
  - `components/ExecutionMonitor/adapters/notifier.py` -- Notifier Protocol + LogNotifier
  - `components/ExecutionMonitor/service/__init__.py`
  - `components/ExecutionMonitor/service/tracker_service.py` -- TrackerService (non-fatal write API)
  - `components/ExecutionMonitor/service/monitor_service.py` -- MonitorService (background loop) + factory functions
  - `components/ExecutionMonitor/tests/__init__.py`
  - `components/ExecutionMonitor/tests/conftest.py` -- FakeTrackerDB, fixtures for services and sample records
  - `components/ExecutionMonitor/tests/test_unit.py` -- Domain model validation (~25 tests)
  - `components/ExecutionMonitor/tests/test_service.py` -- Async service tests (~40 tests)
  - `components/ExecutionMonitor/tests/test_contract.py` -- Schema conformance (~15 tests)
  - `components/ExecutionMonitor/tests/test_observability.py` -- Structured logging (~15 tests)
- **Shared files touched**:
  - `shared/database/models.py` -- Add `ExecutionTrackerTable`
  - `shared/app.py` -- DI wiring: create TrackerService + MonitorService, start/stop background task
  - `shared/dependencies.py` -- `get_tracker_service()`, `get_monitor_service()`
  - `components/ExecuteOrchestrator/service/execute_service.py` -- Add optional `tracker` param + 3 non-fatal hooks
- **Database**:
  - `migrations/008_create_execution_tracker_table.sql` -- DDL for execution_tracker table

### Dependencies (import, no duplication)

| Dependency | Source | Usage |
|-----------|--------|-------|
| SharedDatabaseAdapter | `shared/database/adapter.py` | Database session management for TrackerDatabaseAdapter |
| ExecutionTrackerTable | `shared/database/models.py` | SQLAlchemy model for execution_tracker table |
| ExecuteService | `components/ExecuteOrchestrator/service/execute_service.py` | Wires optional TrackerService for progress hooks |

---

## Dependencies & Risks

### Dependencies

| Dependency | Type | Risk |
|-----------|------|------|
| PostgreSQL | External | Tracker table storage. If DB is down, TrackerService calls fail silently (non-fatal). MonitorService polling fails and retries next cycle. |
| ExecuteOrchestrator | Internal (integration) | TrackerService is injected as optional param. If TrackerService is None, ExecuteOrchestrator works exactly as before. |
| SharedDatabaseAdapter | Internal (shared) | Database session provider. Same pattern as PolicyEngine, PluginRegistry. |

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| TrackerService failure breaks execution | High | All TrackerService methods wrapped in try/except -- failure is logged, never propagated. |
| MonitorService crashes | Medium | Exception handler around `_check_active_executions()` catches and logs all errors. Loop continues. |
| Race condition: complete() vs MonitorService mark_terminal() | Low | Both use `WHERE status = 'running'` -- only first write succeeds, second is a no-op. |
| Notification sent but mark_notified fails | Low | Next poll cycle will see `notification_sent=False` and re-notify. Acceptable for infrastructure failures (better to double-notify than miss). |
| Clock skew between web server and DB | Low | All time comparisons use `datetime.now(UTC)` in Python, not DB server time. Consistent clock source. |
| Poll interval too coarse (30s) | Low | Stuck detection has 5-minute threshold; 30s granularity is sufficient. Configurable via constructor param. |

---

## Non-Functional Requirements

Inherit baseline from GLOBAL_SPEC v3.0 §3, with these specifics:

| Requirement | Target | Notes |
|------------|--------|-------|
| Poll interval | 30s (configurable) | Background loop frequency |
| Stuck detection threshold | 5 minutes (configurable) | No progress since `last_progress_at` |
| Max execution time | 60 minutes (configurable) | Total wall-clock time since `started_at` |
| TrackerService latency overhead | < 10ms per call | Non-blocking DB INSERT/UPDATE via async |
| Active execution query limit | 100 per poll | Prevents unbounded memory in pathological cases |
| Structured logging | `component`, `plan_id`, `trace_id` | Correlated by plan_id per GLOBAL_SPEC §3 |
| No PII/secrets in logs | Enforced | user_id logged as opaque identifier, no sensitive data |
| No HTTP routes | Correct | Background service; health via /health or structured logs |
| Notification protocol | Pluggable via Protocol | LogNotifier now; webhook/SSE/email in future |

---

## Open Questions

- **OQ-1**: Should MonitorService have its own `/health` sub-endpoint or report via the root `/health`? Current recommendation: report via structured logs + root `/health` checks app.state for monitor running status.
- **OQ-2**: Should `mark_terminal()` also attempt to cancel the running asyncio task? Current recommendation: No -- real task cancellation is deferred to Phase 4 (durable execution mode). For now, the monitor only marks DB status and notifies.
- **OQ-3**: Should we add a Redis-based "last heartbeat" for faster stuck detection? Current recommendation: No -- PostgreSQL `last_progress_at` is sufficient for MVP. Redis heartbeat can be added later for sub-second detection.

---

## Conformance

This work conforms to `docs/architecture/GLOBAL_SPEC.md v3.0`:
- ExecutionMonitor referenced in §1 Execute note and §1 Adaptive
- Infrastructure failure handling per §1 Execute retry safety
- NFRs per §3 (structured logging, no PII, plan_id correlation)
- Observability per §3

This work conforms to `docs/architecture/Project_HLD.md v6.1`:
- ExecutionMonitor described in Layer 3 Orchestration Layer
- ExecutionMonitor Pattern in §13 (background polling, stuck detection, time budget)
- Infrastructure-Level Recovery §C (terminal failures, no replay)
- Component #14 of 15 Active Components

This work conforms to `.specify/memory/constitution.md v1.0.0`:
- Component-first architecture (self-contained under `components/ExecutionMonitor/`)
- Test-first development (tests written alongside implementation)
- Structured logging with plan_id correlation
- Fault isolation (non-fatal TrackerService, resilient MonitorService loop)
