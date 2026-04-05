# Feature Specification: Audit — Platform Layer Audit Logging

**Feature Branch**: `feat/audit-platform-layer`
**Created**: 2026-04-05
**Status**: Draft
**Target Component**: `components/Audit/`
**Input**: Immutable append-only audit logging of all plan executions, approval decisions, policy attestations, and system events for debugging and analytics

## Overview

The Audit component is the final platform-layer component (15 of 15). It provides a centralized, immutable, append-only audit log that captures all significant system events — plan executions, step results, approval decisions, policy attestations, and infrastructure alerts. Today, audit-relevant data is scattered across `plan_outcomes`, `execution_tracker`, `policy_attestations` (embedded in PlanOutcome), and ephemeral Redis gate state. Audit consolidates these into a single durable table with a query API, enabling end-to-end execution tracing via `plan_id` / `trace_id` correlation. It is an internal platform component — no Preview/Execute wrappers; consumed directly by other components that emit events.

## User Scenarios & Testing

### User Story 1 — Record execution lifecycle events (Priority: P1)

When a plan executes via ExecuteOrchestrator, every lifecycle transition (start, step completion, step failure, plan completion/failure) is durably recorded in the audit log with plan_id correlation.

**Why this priority**: Without durable execution event capture, there is no debugging or analytics capability — this is the core value of Audit.

**Independent Test**: Execute a mocked plan, query audit log, verify all lifecycle events are present with correct timestamps and plan_id.

**Acceptance Scenarios**:

1. **Given** a plan execution starts, **When** ExecuteOrchestrator calls `audit.record()`, **Then** an `execution_started` event is appended with plan_id, user_id, trace_id, total_steps, and timestamp.
2. **Given** a plan step completes, **When** the step result is recorded, **Then** a `step_completed` event is appended with plan_id, step number, role, status, latency_ms, and no PII/secrets.
3. **Given** a plan step fails, **When** the error is recorded, **Then** a `step_failed` event is appended with plan_id, step number, error_type, and sanitized error_details (no raw API responses or credentials).
4. **Given** a plan execution completes, **When** the final outcome is recorded, **Then** an `execution_completed` event is appended with plan_id, success boolean, total latency, and step count.

---

### User Story 2 — Record approval decisions durably (Priority: P1)

ApprovalGate tokens are Redis-only with TTL (default 900s). Audit must capture approval decisions before they expire, providing a durable record of who approved what and when.

**Why this priority**: Approval decisions are the core HITL safety mechanism; losing them after Redis TTL defeats auditability.

**Independent Test**: Issue a mock approval token, verify audit log contains the approval event with gate_id, user_id, plan_id, scopes, and decision timestamp.

**Acceptance Scenarios**:

1. **Given** a user approves a gate, **When** ApprovalGate issues a token, **Then** an `approval_granted` event is appended with plan_id, gate_id, user_id, scopes, token_id, and approved_at timestamp.
2. **Given** a gate expires without approval, **When** the TTL elapses, **Then** an `approval_expired` event is appended (if the system detects expiry via ExecutionMonitor polling or gate state check).
3. **Given** an approval event is recorded, **Then** the JWT token value itself is NOT stored in the audit log (only token_id and claims metadata).

---

### User Story 3 — Record policy attestations as first-class events (Priority: P2)

PolicyAttestations are currently embedded as a JSON array inside PlanOutcome. Audit denormalizes these into individual audit events for querying.

**Why this priority**: Attestations are the provenance chain for all runtime LLM decisions; making them independently queryable enables policy compliance analysis.

**Independent Test**: Record a mock attestation, query audit by policy_id, verify the attestation event is returned with spawned step details.

**Acceptance Scenarios**:

1. **Given** a Reasoner step spawns a child step and PolicyEngine issues an attestation, **When** the attestation is recorded, **Then** a `policy_attestation` event is appended with attestation_id, plan_id, plan_revision, spawned_by_step, new_steps summary, policy_id, and decision (allowed/denied/requires_approval).
2. **Given** PolicyEngine denies a spawn request, **When** the denial is recorded, **Then** a `policy_denial` event is appended with plan_id, parent_step, reason, and violations.

---

### User Story 4 — Query audit trail with filters (Priority: P2)

Developers and operators can query the audit log filtered by plan_id, user_id, trace_id, event_type, and time range to debug execution issues or analyze patterns.

**Why this priority**: Capture without query capability has limited value; filtering enables targeted debugging.

**Independent Test**: Insert 10 audit events across 2 plans, query by plan_id, verify only events for that plan are returned in chronological order.

**Acceptance Scenarios**:

1. **Given** multiple audit events exist, **When** querying by `plan_id`, **Then** only events for that plan are returned in chronological order.
2. **Given** audit events across users, **When** querying by `user_id`, **Then** only that user's events are returned.
3. **Given** a `trace_id` that spans multiple components, **When** querying by `trace_id`, **Then** all correlated events are returned.
4. **Given** a time range filter, **When** querying with `start_time` and `end_time`, **Then** only events within the range are returned.
5. **Given** an `event_type` filter (e.g., `approval_granted`), **When** querying, **Then** only matching event types are returned.
6. **Given** a query, **Then** results are paginated with cursor-based pagination (default page size 50, max 200).

---

### User Story 5 — Infrastructure and monitor events (Priority: P3)

ExecutionMonitor detects stuck executions and timeout violations. These infrastructure events are recorded in the audit log for operational visibility.

**Why this priority**: Operational events are important for SLO tracking but secondary to execution and approval capture.

**Independent Test**: Emit a mock `execution_stuck` event, query audit, verify the event with plan_id and detection details.

**Acceptance Scenarios**:

1. **Given** ExecutionMonitor detects a stuck execution, **When** a notification is sent, **Then** an `execution_stuck` event is appended with plan_id, detection_reason, and elapsed_time.
2. **Given** ExecutionMonitor detects a timeout violation, **When** the timeout fires, **Then** an `execution_timeout` event is appended with plan_id and timeout policy details.

---

### Edge Cases

- What happens when the audit table is unavailable (PostgreSQL down)? — Events are buffered in-memory (bounded queue, max 1000) and flushed on reconnection. If buffer overflows, oldest events are dropped with a `audit_buffer_overflow` metric increment.
- What happens when an event contains PII? — The `record()` method strips known PII fields before persisting. Caller is responsible for not passing raw user data; Audit provides a safety net via sanitization.
- What happens on duplicate events? — Events are append-only with unique `event_id` (ULID). No deduplication — idempotency is the caller's responsibility.
- What happens for very long-running plans with many steps? — Each step is an individual event; no single event grows unboundedly.

## Requirements

### Functional Requirements

- **FR-001**: System MUST provide an immutable, append-only audit log table in PostgreSQL.
- **FR-002**: System MUST record execution lifecycle events (started, step_completed, step_failed, execution_completed, execution_failed) with plan_id correlation.
- **FR-003**: System MUST record approval decisions (approval_granted, approval_expired) with gate_id, user_id, scopes, and token_id.
- **FR-004**: System MUST record policy attestation events (policy_attestation, policy_denial) with attestation_id, policy_id, and decision.
- **FR-005**: System MUST record infrastructure events (execution_stuck, execution_timeout) from ExecutionMonitor.
- **FR-006**: System MUST expose a query API for filtered audit trail retrieval (plan_id, user_id, trace_id, event_type, time range).
- **FR-007**: System MUST NOT store secrets, credentials, JWT tokens, or raw PII in audit records.
- **FR-008**: System MUST use ULID for event_id to provide chronological ordering and uniqueness.
- **FR-009**: System MUST support cursor-based pagination for query results (default 50, max 200).
- **FR-010**: System MUST buffer events in-memory when PostgreSQL is unavailable and flush on reconnection (bounded queue, max 1000).
- **FR-011**: System MUST support retention policy (configurable TTL, default 90 days, enforced via background cleanup).

### Key Entities

- **AuditEvent**: Core entity — event_id (ULID), event_type, plan_id, user_id, trace_id, step_number, event_data (JSONB), created_at. Immutable after creation.
- **AuditEventType**: Enum — execution_started, step_completed, step_failed, execution_completed, execution_failed, approval_granted, approval_expired, policy_attestation, policy_denial, execution_stuck, execution_timeout.

## Success Criteria

### Measurable Outcomes

- **SC-001**: All plan executions produce a complete audit trail (start, steps, completion) queryable by plan_id within 1 second.
- **SC-002**: Approval decisions are durably captured before Redis TTL expiry (0% loss of approval events for completed executions).
- **SC-003**: Audit query p95 latency < 200ms for single plan_id lookups.
- **SC-004**: Audit write p95 latency < 10ms (fire-and-forget async, non-blocking to caller).
- **SC-005**: Zero secrets/PII in audit records (validated by contract tests scanning event_data).

## Interfaces & Contracts

**Note**: Audit is an internal platform component. It does NOT use Preview/Execute wrappers. It is consumed directly by other components via a service interface (DI-injected `AuditService`).

### Service Interface (Python Protocol)

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
```

### AuditEvent Schema

```json
{
  "event_id": "<ulid>",
  "event_type": "execution_started|step_completed|step_failed|execution_completed|execution_failed|approval_granted|approval_expired|policy_attestation|policy_denial|execution_stuck|execution_timeout",
  "plan_id": "<ulid|null>",
  "user_id": "<uuid|null>",
  "trace_id": "<string|null>",
  "step_number": "<int|null>",
  "event_data": {
    "role": "<string|null>",
    "status": "<string|null>",
    "latency_ms": "<int|null>",
    "error_type": "<string|null>",
    "gate_id": "<string|null>",
    "token_id": "<string|null>",
    "scopes": "<list|null>",
    "policy_id": "<string|null>",
    "decision": "<object|null>",
    "detection_reason": "<string|null>"
  },
  "created_at": "<iso8601>"
}
```

### AuditQueryResult Schema

```json
{
  "events": ["<AuditEvent[]>"],
  "next_cursor": "<string|null>",
  "total_count": "<int>"
}
```

### Integration Points (callers)

| Caller Component | Event Types Emitted | Integration Method |
|-----------------|--------------------|--------------------|
| ExecuteOrchestrator | execution_started, step_completed, step_failed, execution_completed, execution_failed, policy_attestation, policy_denial | DI-injected AuditService, called after each step/lifecycle event |
| ApprovalGate | approval_granted, approval_expired | DI-injected AuditService, called in approve() and expiry detection |
| ExecutionMonitor | execution_stuck, execution_timeout | DI-injected AuditService, called in monitor detection loop |

Reference: `docs/architecture/GLOBAL_SPEC.md` (v3)

## Component Mapping

- **Target**: `components/Audit/`
- **Files expected to change**:
  - `components/Audit/__init__.py`
  - `components/Audit/domain/__init__.py`
  - `components/Audit/domain/models.py` — AuditEvent, AuditEventType, AuditQueryResult, AuditQueryParams domain models
  - `components/Audit/adapters/__init__.py`
  - `components/Audit/adapters/db.py` — AuditDatabaseAdapter (append-only writes, filtered queries, cursor pagination)
  - `components/Audit/service/__init__.py`
  - `components/Audit/service/audit_service.py` — AuditService (record, query, in-memory buffer, PII sanitization, retention cleanup)
  - `components/Audit/api/routes.py` — GET /audit/events query endpoint (read-only, no POST — events are recorded internally)
  - `components/Audit/schemas/audit_event.schema.json` — JSON schema for AuditEvent
  - `components/Audit/schemas/audit_query.schema.json` — JSON schema for AuditQueryResult
  - `components/Audit/tests/__init__.py`
  - `components/Audit/tests/test_service.py` — Unit tests for AuditService
  - `components/Audit/tests/test_contract.py` — Contract tests (GLOBAL_SPEC compliance, no PII, schema validation)
  - `components/Audit/tests/test_observability.py` — Structured logging, metrics tests
  - `shared/database/models.py` — AuditEventTable (new SQLAlchemy model)
  - `shared/dependencies.py` — `get_audit_service()` DI factory
  - `shared/app.py` — `create_audit_service()` lifespan factory
  - `migrations/009_create_audit_events_table.sql` — Database migration

## Dependencies & Risks

### Dependencies
- **PostgreSQL**: Primary storage for audit events (append-only table with indexes on plan_id, user_id, trace_id, event_type, created_at)
- **shared/database**: Reuses existing DatabaseAdapter patterns for async SQLAlchemy
- **ulid-py**: For event_id generation (already a project dependency)
- **Existing components** (ExecuteOrchestrator, ApprovalGate, ExecutionMonitor): Must add `AuditService` calls at integration points — additive changes only

### Risks
- **Write amplification**: Every step execution generates an audit event; high-throughput plans could stress PostgreSQL. Mitigation: batch inserts (configurable flush interval, default 100ms or 10 events).
- **Integration coupling**: Adding audit calls to 3 existing components introduces coupling. Mitigation: AuditService is injected via DI and uses fire-and-forget async pattern; failure does not propagate to callers.
- **Storage growth**: Audit table grows indefinitely without retention. Mitigation: configurable retention policy with background cleanup (default 90 days).
- **Approval expiry detection**: Redis TTL expiry is passive; Audit may miss `approval_expired` events unless actively polled. Mitigation: defer to ExecutionMonitor's polling loop to detect expired gates and emit events.

## Non-Functional Requirements

Inherit baseline from GLOBAL_SPEC v3, with these specific targets:

- **Write latency**: p95 < 10ms (fire-and-forget async, non-blocking to caller)
- **Query latency**: p95 < 200ms for single plan_id lookups; p95 < 500ms for complex multi-filter queries
- **Availability**: 99.5% (same as Execute tier; audit failure must not block execution)
- **Observability**: Structured logs with `plan_id`, `event_type` correlation; metrics for events_recorded, events_queried, buffer_size, buffer_overflows
- **Safety**: Zero secrets/PII in persisted event_data; sanitization layer in AuditService
- **Retention**: Configurable TTL (default 90 days); background cleanup via APScheduler task
- **Throughput**: Support up to 100 events/second sustained (batch inserts)

## Open Questions

1. Should Audit expose an HTTP POST endpoint for external event ingestion (e.g., from future microservices), or is internal DI-only sufficient for MVP?
2. Should the retention cleanup be a separate APScheduler task in Audit, or reuse ExecutionMonitor's existing polling infrastructure?
3. Should batch insert use a fixed flush interval (100ms) or event count threshold (10 events), or both (whichever comes first)?

## Conformance

This work conforms to `docs/architecture/GLOBAL_SPEC.md v3`.

**Deviations declared**:
- Audit is an internal platform component; it does NOT implement Preview/Execute wrappers (same pattern as ProfileStore, History, ContextRAG, Planner, PolicyEngine, PluginRegistry, PlanWriter, ExecutionMonitor).
- Audit exposes a read-only HTTP query endpoint (GET only); event recording is internal-only via DI-injected service.
