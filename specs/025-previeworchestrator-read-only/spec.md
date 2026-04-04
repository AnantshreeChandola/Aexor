# Feature Specification: PreviewOrchestrator

**Feature Branch**: `feat/previeworchestrator-read-only`
**Created**: 2026-04-03
**Status**: Draft
**Spec ID**: 025
**Input**: User description: "PreviewOrchestrator -- read-only plan preview with dry-run execution and preview state caching"

---

## Overview

PreviewOrchestrator is an **Orchestration Layer** library component that executes a plan's previewable steps in **read-only mode** (no external mutations) and returns structured preview results to the user. It resolves the plan DAG, dispatches previewable API steps via dry-run MCP tool invocations, skips non-previewable and LLM reasoning steps, and caches preview state for downstream reuse by ApprovalGate and ExecuteOrchestrator. This is the first user-facing component after Planner and the **core of the preview-first safety model** (GLOBAL_SPEC v3.0 &sect;1).

---

## User Scenarios & Testing

### User Story 1 - Preview a Pure API Plan (Priority: P1)

The user has asked to book a meeting. Planner produced a pure API plan (all `type: "api"` steps). PreviewOrchestrator runs the read-only steps (calendar fetch, slot analysis) and returns time-slot options to the user without creating any calendar events.

**Why this priority**: This is the core value proposition -- show the user what will happen before doing it. Covers the majority of plans.

**Independent Test**: Can be fully tested by passing a Plan with Fetcher/Analyzer steps marked `previewable: true` and verifying the returned Preview wrapper contains normalized results with no side effects.

**Acceptance Scenarios**:

1. **Given** a valid Plan with 3 previewable API steps and 1 non-previewable Booker step, **When** `preview(plan, user_id)` is called, **Then** the 3 previewable steps execute in DAG order, the Booker step is skipped, and a PreviewResult is returned with `source: "preview"` and `can_execute: true`.
2. **Given** a valid Plan, **When** all previewable steps succeed, **Then** preview state (step results, resolved templates) is cached in Redis with a configurable TTL (default 15 minutes).

---

### User Story 2 - Preview a Hybrid Plan with Reasoning Steps (Priority: P2)

The user asked to "find best flights to Tokyo." The plan includes Fetcher steps (previewable), Tier 1/Tier 2 Reasoner steps (not previewable at preview time), and a Notifier step. PreviewOrchestrator runs only the previewable Fetcher steps and returns raw data for user review, marking reasoning steps as "pending at execution."

**Why this priority**: Hybrid plans are the adaptive execution use case. Preview must handle them gracefully by running what it can and clearly marking what will happen at execution time.

**Independent Test**: Pass a Plan with `type: "llm_reasoning"` steps and verify they are skipped with status `"deferred"`, while API steps execute normally.

**Acceptance Scenarios**:

1. **Given** a Plan with `type: "llm_reasoning"` steps, **When** preview runs, **Then** reasoning steps are marked `status: "deferred"` in the result (not executed, not failed).
2. **Given** a Plan with `type: "policy_check"` steps, **When** preview runs, **Then** policy check steps are marked `status: "deferred"`.
3. **Given** a Plan with mixed step types, **When** an API step depends on (`after`) a deferred reasoning step, **Then** the API step is also deferred (cascade).

---

### User Story 3 - Graceful Degradation on Step Failure (Priority: P2)

A previewable Fetcher step fails (e.g., external API timeout). PreviewOrchestrator should continue with other independent branches of the DAG and return partial results with clear failure indicators.

**Why this priority**: Users should see as much as possible even when one data source is unavailable.

**Independent Test**: Mock one MCP invocation to return an error and verify other parallel steps complete normally.

**Acceptance Scenarios**:

1. **Given** a Plan with 2 parallel Fetcher steps, **When** one fails with a timeout, **Then** the other completes successfully, and the PreviewResult includes both the success result and the error with `partial: true`.
2. **Given** a step failure, **When** downstream steps depend on the failed step, **Then** downstream steps are marked `status: "skipped"` with reason `"dependency_failed"`.
3. **Given** all previewable steps fail, **When** preview completes, **Then** PreviewResult has `can_execute: false` and a list of all errors.

---

### User Story 4 - Preview State Caching for Execution Reuse (Priority: P1)

After a successful preview, the step results are cached so ExecuteOrchestrator can skip re-running previewable steps and reuse the cached data.

**Why this priority**: This is the key innovation per Project_HLD -- preview state caching eliminates redundant work during execution.

**Independent Test**: Run preview, then verify cached state is retrievable by plan_id and contains all step results.

**Acceptance Scenarios**:

1. **Given** a completed preview, **When** `get_preview_state(plan_id, user_id)` is called, **Then** cached state is returned containing all step results keyed by step number.
2. **Given** a cached preview state, **When** the TTL expires, **Then** the state is no longer retrievable (returns None).
3. **Given** a preview was run, **When** a new preview is run for the same plan_id, **Then** the old cache entry is replaced.

---

### Edge Cases

- What happens when the plan has zero previewable steps? Return a PreviewResult with empty results and `can_execute: true` (approval still needed).
- What happens when the plan graph has a cycle? DAGResolver raises `CycleDetectedError` which PreviewOrchestrator propagates as a `PreviewError`.
- What happens when Redis is unavailable for caching? Preview still completes; state caching is best-effort with a logged warning. `can_execute` is still true but preview state won't be available for execution reuse.
- What happens when the plan has a DAG cycle? DAGResolver raises `CycleDetectedError` which PreviewOrchestrator propagates as a `PreviewError`.
- What happens when a step has `gate_id` set? Step is marked as `"deferred"` (gates are resolved during approval/execution, not preview).

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST resolve the plan DAG into topological execution levels using the existing DAGResolver pattern.
- **FR-002**: System MUST only dispatch steps where the corresponding PluginRegistry operation has `previewable: true`.
- **FR-003**: System MUST skip `type: "llm_reasoning"` and `type: "policy_check"` steps during preview (mark as `"deferred"`).
- **FR-004**: System MUST dispatch previewable API steps via MCP tool invocations in **read-only mode** (no write credentials, `dry_run: true`).
- **FR-005**: System MUST execute independent steps (same DAG level) in parallel via `asyncio.gather()`.
- **FR-006**: System MUST cascade deferral: if a step depends on a deferred/failed step, it is also deferred/skipped.
- **FR-007**: System MUST cache preview state (step results) in Redis with configurable TTL (default 900 seconds / 15 minutes).
- **FR-008**: System MUST return a PreviewResult conforming to GLOBAL_SPEC v3.0 &sect;2.5 Preview wrapper.
- **FR-009**: System MUST resolve template args (`{{step_N.result.field}}`) using completed preview step results, via the existing TemplateResolver.
- **FR-010**: System MUST support partial success: if some steps fail, return results for successful steps with `partial: true`.
- **FR-011**: System MUST log all preview operations with `plan_id` correlation, step-level latencies, and no PII/secrets.
- **FR-012**: System MUST provide a `get_preview_state(plan_id, user_id)` method for downstream consumers (ApprovalGate, ExecuteOrchestrator).

### Key Entities

- **PreviewRequest**: plan, user_id, trace_id -- input to `preview()`.
- **PreviewResult**: normalized results, step statuses, can_execute flag, partial flag, cached_state_key.
- **PreviewStepResult**: step number, status (completed/failed/deferred/skipped), result data, error data, latency_ms.
- **PreviewState**: Cached map of step_number -> PreviewStepResult, keyed by `preview:{user_id}:{plan_id}`.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Preview of a 5-step pure API plan completes in p95 < 800ms (GLOBAL_SPEC &sect;3 NFR).
- **SC-002**: All previewable steps execute in parallel where DAG allows (verified by latency being max-of-parallel, not sum-of-sequential).
- **SC-003**: Zero external write operations during preview (verified by mock assertions and no Booker-role step execution).
- **SC-004**: Preview state cache hit rate > 95% within TTL window (verified by integration test).
- **SC-005**: 100% of preview operations logged with plan_id correlation.

---

## Interfaces & Contracts

### Service Interface

```python
class PreviewService:
    async def preview(self, request: PreviewRequest) -> PreviewResult:
        """Execute plan preview in read-only mode.

        1. Resolve DAG levels
        3. Dispatch previewable steps (parallel per level)
        4. Cache preview state
        5. Return PreviewResult
        """

    async def get_preview_state(
        self, plan_id: str, user_id: str
    ) -> dict[int, PreviewStepResult] | None:
        """Retrieve cached preview state for downstream consumers."""
```

### Factory Function

```python
def create_preview_service(
    mcp_client: MCPClient,
    plugin_registry: PluginRegistryService,
    redis_client: object | None = None,
) -> PreviewService:
    """Create PreviewService with all dependencies."""
```

### Intent (input)

```json
{
  "intent": "schedule_meeting",
  "entities": {"attendee": "alice@company.com", "day": "Tuesday"},
  "constraints": {"scopes": ["calendar.read"]},
  "tz": "America/Chicago",
  "user_id": "user-uuid-123"
}
```

### Preview (wrapper + normalized outline per GLOBAL_SPEC &sect;2.5)

```json
{
  "plan_id": "<ulid>",
  "normalized": {
    "steps": [
      {"step": 1, "status": "completed", "result": {"slots": ["..."]}, "latency_ms": 120},
      {"step": 2, "status": "completed", "result": {"slots": ["..."]}, "latency_ms": 95},
      {"step": 3, "status": "completed", "result": {"overlapping": ["..."]}, "latency_ms": 15},
      {"step": 4, "status": "deferred", "result": null, "reason": "llm_reasoning"},
      {"step": 5, "status": "deferred", "result": null, "reason": "non_previewable"}
    ]
  },
  "source": "preview",
  "can_execute": true,
  "partial": false,
  "cached_state_key": "preview:user-uuid-123:01JXYZ...",
  "evidence": []
}
```

### Execute (wrapper -- consumed by ExecuteOrchestrator via cached state)

```json
{
  "provider": "google.calendar",
  "result": {"id": "gcal_event_123"},
  "status": "created"
}
```

Reference: docs/architecture/GLOBAL_SPEC.md (v3.0)

---

## Component Mapping

- **Target**: `components/PreviewOrchestrator/`
- **Files expected to change**:
  - `components/PreviewOrchestrator/__init__.py`
  - `components/PreviewOrchestrator/domain/models.py` -- PreviewRequest, PreviewResult, PreviewStepResult, PreviewState, custom exceptions
  - `components/PreviewOrchestrator/service/preview_service.py` -- PreviewService with `preview()` and `get_preview_state()`
  - `components/PreviewOrchestrator/adapters/preview_cache.py` -- Redis cache adapter for preview state
  - `components/PreviewOrchestrator/adapters/previewability_checker.py` -- Checks PluginRegistry for `previewable: true`
  - `components/PreviewOrchestrator/tests/conftest.py` -- Fixtures, mock adapters, sample plans
  - `components/PreviewOrchestrator/tests/test_unit.py` -- Core preview logic, DAG dispatch, step filtering
  - `components/PreviewOrchestrator/tests/test_service.py` -- Cache interaction, MCP dispatch, template resolution
  - `components/PreviewOrchestrator/tests/test_contract.py` -- Model conformance, PreviewResult shape
  - `components/PreviewOrchestrator/tests/test_observability.py` -- Logging, no PII
- **Shared files touched**:
  - `shared/app.py` -- DI wiring: `create_preview_service()` in lifespan
  - `shared/dependencies.py` -- `get_preview_service()` accessor

### Reused from existing components (no duplication)

| Adapter | Source | Usage |
|---------|--------|-------|
| DAGResolver | `components/ExecuteOrchestrator/adapters/dag_resolver.py` | Resolve plan graph to execution levels |
| TemplateResolver | `components/ExecuteOrchestrator/adapters/template_resolver.py` | Resolve `{{step_N.result.field}}` templates |
| MCPClient protocol | `components/ExecuteOrchestrator/adapters/mcp_client.py` | Dispatch read-only MCP tool invocations |
| Plan, PlanStep models | `shared/schemas/plan.py` | Plan input model |

---

## Dependencies & Risks

### Dependencies

| Dependency | Type | Risk |
|-----------|------|------|
| PluginRegistry | Internal (read-only) | Must check `previewable` flag per operation. PluginRegistry is complete. |
| ExecuteOrchestrator adapters (DAGResolver, TemplateResolver, MCPClient) | Internal (import) | Must import from ExecuteOrchestrator or move to shared. Risk: tight coupling. Mitigation: use Protocol interfaces. |
| Redis | External | Preview state caching. Graceful degradation if unavailable. |
| MCP servers | External | Read-only tool invocations. Failure handled per-step (partial results). |

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| MCP tool invocations may have side effects even in "read-only" mode | High | Only dispatch operations with `previewable: true` from PluginRegistry; pass `dry_run: true` |
| Stale preview state used during execution | Medium | TTL-based expiration (15min); plan_id match verified at execution time |
| DAGResolver/TemplateResolver imports create coupling to ExecuteOrchestrator | Low | Use Protocol-based interfaces; consider moving shared adapters to `shared/` in future refactor |
| Redis unavailable during preview | Low | Graceful degradation: preview still runs, caching is best-effort |

---

## Non-Functional Requirements

Inherit baseline from GLOBAL_SPEC v3.0 &sect;3, with these specifics:

| Requirement | Target | Notes |
|------------|--------|-------|
| Preview latency (p95) | < 800ms | GLOBAL_SPEC &sect;3. Dominated by MCP tool invocation latency. |
| Preview state cache TTL | 900s (15min) | Configurable via `PREVIEW_CACHE_TTL_S` env var |
| Redis cache key pattern | `preview:{user_id}:{plan_id}` | Namespaced, TTL-based |
| Zero write operations | Guaranteed | Only `previewable: true` operations dispatched |
| Structured logging | plan_id, step, latency_ms | Correlated by plan_id per GLOBAL_SPEC &sect;3 |
| No PII/secrets in logs | Enforced | Verified by observability tests |
| Availability | 99.9% | Same as Intake/Preview per GLOBAL_SPEC &sect;3 |

---

## Open Questions

- **OQ-1**: Should DAGResolver and TemplateResolver be moved to `shared/` to avoid PreviewOrchestrator importing from ExecuteOrchestrator? Current recommendation: import directly, refactor later if needed.
- **OQ-2**: Should PreviewOrchestrator expose HTTP routes (API component) or remain a library component consumed by a future API gateway? Current recommendation: library component (same pattern as PolicyEngine, Planner), with routes added when the API gateway layer is built.
- **OQ-3**: Should preview dispatch use the same MCP credentials (read-only scopes) or a dedicated preview-only credential set? Current recommendation: use same credentials with read-only scopes enforced by PluginRegistry's scope verification.
- **OQ-4**: How should preview handle steps with `gate_id`? Current recommendation: mark as `"deferred"` (gates are resolved during approval/execution, not preview).

---

## Conformance

This work conforms to `docs/architecture/GLOBAL_SPEC.md v3.0`:
- Preview wrapper per &sect;2.5
- Plan model per &sect;2.3
- Safety model per &sect;1 (Preview: side-effect free)
- NFRs per &sect;3 (p95 < 800ms)
- Observability per &sect;3 (plan_id correlation, no PII)

This work conforms to `docs/architecture/Project_HLD.md v6.1`:
- PreviewOrchestrator described in Layer 3 and &sect;2a Step 3
- Preview state caching per ApprovalGate section (preview_state in token)
- Read-only MCP dispatching per &sect;2a

This work conforms to `docs/architecture/MODULAR_ARCHITECTURE.md v2.0`:
- Orchestration Layer placement
- Dependencies: PluginRegistry (read-only)
- No owned database tables (Redis cache only)
