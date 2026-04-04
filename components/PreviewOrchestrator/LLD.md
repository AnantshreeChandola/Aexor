# PreviewOrchestrator — Low-Level Design

**Component**: `components/PreviewOrchestrator/`
**Layer**: Orchestration Layer
**Type**: Library component (no HTTP routes, no owned DB tables)
**Spec**: `specs/025-previeworchestrator-read-only/spec.md`
**Status**: Draft

---

## 1. Purpose & Scope

PreviewOrchestrator is the **core of the preview-first safety model** (GLOBAL_SPEC v3.0 §1). It executes a plan's previewable steps in **read-only mode** — no external writes, no mutations — and returns structured preview results for user review before any execution occurs.

**Responsibilities:**
- Resolve plan DAG into topological execution levels
- Filter steps: only dispatch `previewable: true` API steps; defer reasoning, policy, and gated steps
- Dispatch previewable steps via MCP tool invocations in read-only mode (`dry_run: true`)
- Execute independent steps in parallel via `asyncio.gather()`
- Resolve template args (`{{step_N.result.field}}`) from completed preview results
- Cache preview state in Redis for downstream reuse by ApprovalGate and ExecuteOrchestrator
- Return a PreviewResult conforming to GLOBAL_SPEC §2.5

**Out of scope:**
- HTTP route handling (library component — routes added when API gateway layer is built)
- Write operations of any kind
- LLM reasoning execution (deferred to ExecuteOrchestrator)
- Approval token issuance (ApprovalGate's responsibility)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v3.0 | §1 (Safety Model — Preview), §2.3 (Plan), §2.5 (Preview Wrapper), §3 (NFRs) |
| Project_HLD.md | v6.1 | Layer 3 (Orchestration), §2a Step 3 (PreviewOrchestrator flow) |
| MODULAR_ARCHITECTURE.md | v2.1 | §1 (Orchestration Layer), §4 (PreviewOrchestrator deps: PluginRegistry), §8 (Preview State Caching) |

---

## 3. Architecture Overview

### 3.1 Layer Placement

```
Orchestration Layer
├── PreviewOrchestrator  ← this component
├── ApprovalGate         (downstream consumer)
└── ExecuteOrchestrator  (downstream consumer, shares adapters)
```

### 3.2 Blast Radius Analysis

| Failure Mode | Impact | Containment |
|-------------|--------|-------------|
| PreviewOrchestrator crashes | User cannot preview plans | No data loss; retry safe (read-only). Other components unaffected. |
| Redis unavailable | Preview state not cached | Preview still completes; caching is best-effort. ExecuteOrchestrator must re-run previewable steps. |
| MCP step timeout | Partial preview results | Other parallel branches complete normally; `partial: true` returned. |
| PluginRegistry unavailable | Cannot check previewability | Fail-open: treat as non-previewable (defer step). Preview completes with empty results. |

### 3.3 Component Boundaries

PreviewOrchestrator is a **pure library** — no database tables, no HTTP routes. It receives a Plan and returns a PreviewResult. All state is ephemeral (in-memory during preview execution) or cached (Redis, TTL-based).

**Isolation strategy**: PreviewOrchestrator never holds credentials, never writes to external services, never modifies plan state. The only side effect is a Redis SET for caching preview results.

---

## 4. Interfaces

### 4.1 Service Interface

```python
class PreviewService:
    """Read-only plan preview engine."""

    async def preview(self, request: PreviewRequest) -> PreviewResult:
        """Execute plan preview in read-only mode.

        Flow:
            1. Resolve DAG levels via DAGResolver
            2. For each level, dispatch previewable steps in parallel
            3. Cache preview state in Redis (best-effort)
            4. Return PreviewResult

        Args:
            request: PreviewRequest with plan, user_id, trace_id.

        Returns:
            PreviewResult with step statuses, can_execute flag, partial flag.

        Raises:
            PreviewError: If DAG resolution fails (e.g., cycle detected).
        """

    async def get_preview_state(
        self, plan_id: str, user_id: str
    ) -> dict[int, PreviewStepResult] | None:
        """Retrieve cached preview state for downstream consumers.

        Args:
            plan_id: ULID plan identifier.
            user_id: User who ran the preview.

        Returns:
            Cached step results keyed by step number, or None if expired/missing.
        """
```

### 4.2 Factory Function

```python
def create_preview_service(
    mcp_client: MCPClient,
    registry_service: RegistryService,
    redis_client: object | None = None,
) -> PreviewService:
    """Create PreviewService with all dependencies.

    Called once during app lifespan startup in shared/app.py.

    Args:
        mcp_client: MCP client for read-only tool invocations.
        registry_service: PluginRegistry for previewability checks.
        redis_client: Optional Redis client for preview state caching.
    """
```

### 4.3 Consumer Contracts

#### ApprovalGate (downstream)

**Calls**: `get_preview_state(plan_id, user_id)`
**Input**: plan_id (str), user_id (str)
**Output**: `dict[int, PreviewStepResult] | None` — step results keyed by step number
**Error handling**: Returns None if cache miss/expired; ApprovalGate proceeds without cached state.

#### ExecuteOrchestrator (downstream)

**Calls**: `get_preview_state(plan_id, user_id)`
**Input**: plan_id (str), user_id (str)
**Output**: `dict[int, PreviewStepResult] | None` — used to skip `preview_only` steps
**Error handling**: Returns None if cache miss; ExecuteOrchestrator re-runs previewable steps.

#### Intake / API Gateway (upstream caller)

**Calls**: `preview(request)` after Planner produces a Plan
**Input**: `PreviewRequest(plan=Plan, user_id=str, trace_id=str)`
**Output**: `PreviewResult` with normalized step results, can_execute flag
**Error handling**: Must catch `PreviewError` (DAG cycle, unexpected failure).

---

## 5. Data Model

### 5.1 Domain Entities

```python
class PreviewRequest(BaseModel):
    """Input contract for plan preview."""

    plan: Plan
    user_id: str = Field(..., min_length=1, description="User requesting preview")
    trace_id: str = Field(..., min_length=1, description="Distributed tracing ID")


class PreviewStepResult(BaseModel):
    """Result of previewing a single step."""

    step: int = Field(..., ge=1, description="Step number")
    status: Literal["completed", "failed", "deferred", "skipped"] = Field(
        ..., description="Preview outcome for this step"
    )
    result: dict[str, Any] | None = Field(
        default=None, description="Step result data (if completed)"
    )
    error: dict[str, Any] | None = Field(
        default=None, description="Error details (if failed)"
    )
    latency_ms: int = Field(default=0, ge=0, description="Step execution time")
    reason: str | None = Field(
        default=None,
        description="Why step was deferred/skipped (e.g., 'llm_reasoning', 'non_previewable', 'dependency_failed', 'gated')",
    )


class PreviewResult(BaseModel):
    """Preview response wrapper (GLOBAL_SPEC §2.5)."""

    plan_id: str = Field(
        ..., min_length=26, max_length=26, description="ULID plan identifier"
    )
    normalized: dict[str, Any] = Field(
        ..., description="Normalized preview payload with step results"
    )
    source: Literal["preview"] = Field(
        default="preview", description="Always 'preview'"
    )
    can_execute: bool = Field(
        ..., description="Whether execution is possible after this preview"
    )
    partial: bool = Field(
        default=False,
        description="True if some previewable steps failed",
    )
    cached_state_key: str | None = Field(
        default=None,
        description="Redis cache key for preview state (e.g., 'preview:{user_id}:{plan_id}')",
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list, description="Optional supporting evidence"
    )
```

**GLOBAL_SPEC §2.5 alignment**: The `PreviewResult` maps directly to the Preview Wrapper contract — `normalized`, `source: "preview"`, `can_execute`, `evidence`. Extended with `plan_id`, `partial`, and `cached_state_key` per the spec requirements.

### 5.2 Custom Exceptions

```python
class PreviewError(Exception):
    """Base error for PreviewOrchestrator."""


class PreviewStepError(PreviewError):
    """A preview step failed (non-fatal — used for logging)."""

    def __init__(self, step: int, reason: str) -> None:
        self.step = step
        super().__init__(f"Preview step {step} failed: {reason}")
```

---

## 6. Adapters

### 6.1 Reused from ExecuteOrchestrator (imported directly)

| Adapter | Import Path | Usage |
|---------|------------|-------|
| `DAGResolver` | `components.ExecuteOrchestrator.adapters.dag_resolver.DAGResolver` | Resolve plan graph to topological execution levels (Kahn's algorithm) |
| `TemplateResolver` | `components.ExecuteOrchestrator.adapters.template_resolver.TemplateResolver` | Resolve `{{step_N.result.field}}` templates in step args |
| `MCPClient` | `components.ExecuteOrchestrator.adapters.mcp_client.MCPClient` | Protocol interface for MCP tool invocations |
| `CycleDetectedError` | `components.ExecuteOrchestrator.domain.models.CycleDetectedError` | Propagated as PreviewError on DAG cycle |
| `StepResult` | `components.ExecuteOrchestrator.domain.models.StepResult` | Used internally for TemplateResolver compatibility |

**Rationale**: Importing directly from ExecuteOrchestrator avoids code duplication. Both components share the same DAG resolution and template resolution logic. The MCPClient Protocol interface ensures loose coupling — PreviewOrchestrator depends on the Protocol, not the concrete implementation.

**Future consideration** (OQ-1): If more components need these adapters, move to `shared/adapters/`. Current recommendation is to defer this refactor.

### 6.2 PreviewCacheAdapter (new)

```python
class PreviewCacheAdapter:
    """Redis cache adapter for preview state."""

    def __init__(self, redis_client: object | None, ttl_s: int = 900) -> None:
        self._redis = redis_client
        self._ttl_s = ttl_s

    async def store(
        self, plan_id: str, user_id: str, state: dict[int, dict]
    ) -> str | None:
        """Cache preview state in Redis.

        Key pattern: preview:{user_id}:{plan_id}
        TTL: configurable (default 900s / 15min)

        Returns cache key on success, None on failure (graceful degradation).
        """

    async def retrieve(
        self, plan_id: str, user_id: str
    ) -> dict[int, dict] | None:
        """Retrieve cached preview state.

        Returns None if expired, missing, or Redis unavailable.
        """
```

**Cache key**: `preview:{user_id}:{plan_id}` (namespaced, per MODULAR_ARCHITECTURE §3)
**TTL**: 900s (15 minutes), configurable via `PREVIEW_CACHE_TTL_S` env var
**Serialization**: JSON (step results are dicts)
**Graceful degradation**: All Redis operations wrapped in try/except; failures logged as warnings, never propagated.

### 6.3 PreviewabilityChecker (new)

```python
class PreviewabilityChecker:
    """Check PluginRegistry for operation previewability."""

    def __init__(self, registry_service: object) -> None:
        self._registry = registry_service

    async def is_previewable(self, tool_id: str, operation_id: str) -> bool:
        """Check if a tool operation is marked previewable.

        Returns False if tool/operation not found (fail-safe).
        """
```

**Behavior**: Queries PluginRegistry's `get_tool(tool_id)` → `tool.operations[operation_id].previewable`. Returns `False` on any lookup failure (ToolNotFoundError, missing operation, etc.).

---

## 7. Shared Infrastructure Usage

### 7.1 Dependency Injection

1. **`shared/app.py` lifespan**: Initialize `PreviewService` via `create_preview_service()`
2. **`shared/dependencies.py`**: Add `get_preview_service()` Depends accessor
3. **No route registration** (library component — consumed programmatically)

### 7.2 Shared Schemas

| Schema | Location | Usage |
|--------|----------|-------|
| `Plan`, `PlanStep` | `shared/schemas/plan.py` | Plan input model |
| `MCPClient` (Protocol) | `components/ExecuteOrchestrator/adapters/mcp_client.py` | MCP invocation interface |

### 7.3 Error Handling

- Domain errors defined in `components/PreviewOrchestrator/domain/models.py`
- No HTTP routes → no `ErrorResponse` usage (consumers handle their own HTTP mapping)
- Shared error patterns: callers (future API gateway) will use `ErrorResponse` from `shared/api/error_handlers.py`

---

## 8. Sequences

### 8.1 Happy Path — Pure API Plan Preview

```
Caller        PreviewService     DAGResolver   Checker   MCPClient   TemplateResolver   Cache
  │                 │                 │            │          │              │              │
  │ preview(req)    │                 │            │          │              │              │
  │────────────────>│                 │            │          │              │              │
  │                 │ resolve(graph)  │            │          │              │              │
  │                 │────────────────>│            │          │              │              │
  │                 │ levels          │            │          │              │              │
  │                 │<────────────────│            │          │              │              │
  │                 │                 │            │          │              │              │
  │                 │ ── Level 1: Steps 1,2 ──    │          │              │              │
  │                 │ is_previewable(tool,op)      │          │              │              │
  │                 │────────────────────────────>│          │              │              │
  │                 │ true                         │          │              │              │
  │                 │<────────────────────────────│          │              │              │
  │                 │                 │            │          │              │              │
  │                 │ invoke(server, tool, args, dry_run=true)│              │              │
  │                 │───────────────────────────────────────>│              │              │
  │                 │ result = {...}  │            │          │              │              │
  │                 │<───────────────────────────────────────│              │              │
  │                 │                 │            │          │              │              │
  │                 │ ── Level 2: Step 3 ─────    │          │              │              │
  │                 │ is_previewable(tool,op)      │          │              │              │
  │                 │────────────────────────────>│          │              │              │
  │                 │ true                         │          │              │              │
  │                 │<────────────────────────────│          │              │              │
  │                 │ resolve(args, step_results) │          │              │              │
  │                 │──────────────────────────────────────────────────────>│              │
  │                 │ resolved_args   │            │          │              │              │
  │                 │<──────────────────────────────────────────────────────│              │
  │                 │ invoke(...)     │            │          │              │              │
  │                 │───────────────────────────────────────>│              │              │
  │                 │                 │            │          │              │              │
  │                 │ ── Level 3: Steps 4,5 (non-previewable) → deferred ──│              │
  │                 │                 │            │          │              │              │
  │                 │ store(plan_id, user_id, state)          │              │              │
  │                 │─────────────────────────────────────────────────────────────────────>│
  │                 │ cache_key       │            │          │              │              │
  │                 │<─────────────────────────────────────────────────────────────────────│
  │                 │                 │            │          │              │              │
  │ PreviewResult   │                 │            │          │              │              │
  │<────────────────│                 │            │          │              │              │
```

### 8.2 Partial Failure — One Step Fails

```
PreviewService         MCPClient (Step 1)    MCPClient (Step 2)
     │                       │                      │
     │  asyncio.gather()     │                      │
     │──────────────────────>│                      │
     │──────────────────────────────────────────────>│
     │  MCPInvocationError   │                      │
     │<──────────────────────│                      │
     │  result = {...}       │                      │
     │<──────────────────────────────────────────────│
     │                       │                      │
     │  Step 1: status=failed, Step 2: status=completed
     │  Downstream of Step 1: status=skipped (dependency_failed)
     │  partial=true, can_execute=true
```

### 8.3 Deferral Cascade — Dependency on Deferred Step

```
Plan graph:
  Step 1 (api, previewable) → Step 2 (llm_reasoning) → Step 3 (api, previewable, after=[2])

Result:
  Step 1: status=completed (executed)
  Step 2: status=deferred (llm_reasoning — not executed in preview)
  Step 3: status=deferred (dependency on deferred step 2 — cascade)
```

### 8.4 Consumer Query — get_preview_state()

```
ExecuteOrchestrator    PreviewService    PreviewCache (Redis)
     │                      │                  │
     │  get_preview_state() │                  │
     │─────────────────────>│                  │
     │                      │  retrieve()      │
     │                      │─────────────────>│
     │                      │  state or None   │
     │                      │<─────────────────│
     │  dict | None         │                  │
     │<─────────────────────│                  │
```

### 8.5 Graceful Degradation — Redis Unavailable

```
PreviewService    PreviewCache (Redis DOWN)
     │                  │
     │  store(...)      │
     │─────────────────>│
     │  None (logged)   │ ← try/except, warning logged
     │<─────────────────│
     │                  │
     │  Preview completes normally
     │  cached_state_key = None
     │  can_execute = true (preview still valid)
```

---

## 9. Core Algorithm

### 9.1 preview() Flow

```python
async def preview(self, request: PreviewRequest) -> PreviewResult:
    # 1. Resolve DAG levels
    try:
        levels = self._dag_resolver.resolve(request.plan.graph)
    except CycleDetectedError as exc:
        raise PreviewError(f"DAG cycle: {exc}") from exc

    # 2. Build step status tracking
    step_results: dict[int, PreviewStepResult] = {}
    deferred_steps: set[int] = set()
    failed_steps: set[int] = set()

    # 3. Process each level
    for level in levels:
        await self._process_level(
            level, request, step_results, deferred_steps, failed_steps
        )

    # 4. Build normalized result
    has_failures = len(failed_steps) > 0
    all_failed = all(
        sr.status in ("failed", "deferred", "skipped")
        for sr in step_results.values()
    )

    # 5. Cache preview state (best-effort)
    cache_key = await self._cache_state(
        request.plan.plan_id, request.user_id, step_results
    )

    # 6. Return PreviewResult
    return PreviewResult(
        plan_id=request.plan.plan_id,
        normalized={"steps": [sr.model_dump() for sr in sorted(
            step_results.values(), key=lambda s: s.step
        )]},
        source="preview",
        can_execute=not all_failed,
        partial=has_failures,
        cached_state_key=cache_key,
        evidence=[],
    )
```

### 9.2 Step Classification Logic

For each step in a DAG level:

```python
def _classify_step(self, step: PlanStep, deferred: set, failed: set) -> str:
    # 1. Check dependency cascade
    for dep in step.after:
        if dep in deferred:
            return "deferred"  # reason: "dependency_deferred"
        if dep in failed:
            return "skipped"   # reason: "dependency_failed"

    # 2. Non-API step types
    if step.type in ("llm_reasoning", "policy_check"):
        return "deferred"  # reason: step.type

    # 3. Gated steps
    if step.gate_id is not None:
        return "deferred"  # reason: "gated"

    # 4. Check previewability via PluginRegistry
    if not await self._checker.is_previewable(step.uses, step.call):
        return "deferred"  # reason: "non_previewable"

    # 5. Previewable — dispatch via MCP
    return "dispatch"
```

### 9.3 Parallel Dispatch

```python
async def _process_level(self, level, request, results, deferred, failed):
    to_dispatch = []
    for step in level:
        classification = await self._classify_step(step, deferred, failed)
        if classification == "deferred":
            results[step.step] = PreviewStepResult(
                step=step.step, status="deferred", reason=...
            )
            deferred.add(step.step)
        elif classification == "skipped":
            results[step.step] = PreviewStepResult(
                step=step.step, status="skipped", reason="dependency_failed"
            )
            failed.add(step.step)  # propagate to downstream
        else:
            to_dispatch.append(step)

    if not to_dispatch:
        return

    # Parallel dispatch via asyncio.gather (return_exceptions=True)
    outcomes = await asyncio.gather(
        *[self._dispatch_step(step, request, results) for step in to_dispatch],
        return_exceptions=True,
    )

    for step, outcome in zip(to_dispatch, outcomes, strict=True):
        if isinstance(outcome, Exception):
            results[step.step] = PreviewStepResult(
                step=step.step, status="failed",
                error={"error_type": type(outcome).__name__, "message": str(outcome)},
            )
            failed.add(step.step)
        else:
            results[step.step] = outcome
```

### 9.4 MCP Dispatch (read-only)

```python
async def _dispatch_step(self, step, request, results):
    start = time.monotonic()

    # Resolve templates from completed preview results
    exec_results = self._to_exec_step_results(results)
    resolved_args = self._template_resolver.resolve(step.args, exec_results)

    # Add dry_run flag
    resolved_args["dry_run"] = True

    # Resolve tool info from PluginRegistry
    tool = await self._registry.get_tool(step.uses)
    mcp_server = getattr(tool, "mcp_server", step.uses)
    op = tool.operations.get(step.call)
    mcp_tool = getattr(op, "n8n_node", step.call) if op else step.call

    # Invoke MCP (no credentials — read-only)
    result = await self._mcp.invoke(
        server=mcp_server,
        tool=mcp_tool,
        args=resolved_args,
        credentials=None,  # read-only — no write credentials
        timeout_s=step.timeout_s,
    )

    latency_ms = int((time.monotonic() - start) * 1000)
    return PreviewStepResult(
        step=step.step, status="completed",
        result=result, latency_ms=latency_ms,
    )
```

---

## 10. Caching Strategy

### 10.1 Preview State Cache

| Property | Value |
|----------|-------|
| Key pattern | `preview:{user_id}:{plan_id}` |
| TTL | 900s (15 minutes), configurable via `PREVIEW_CACHE_TTL_S` |
| Value format | JSON-serialized `dict[int, PreviewStepResult]` |
| Owner | PreviewOrchestrator |
| Consumers | ApprovalGate, ExecuteOrchestrator (via `get_preview_state()`) |

### 10.2 Cache Invalidation

- **TTL-based expiration**: Redis TTL auto-expires after 15 minutes
- **Replacement on re-preview**: New preview for same plan_id overwrites old cache entry (same key)
- **No explicit invalidation needed**: Read-only data; stale cache is harmless (TTL provides freshness guarantee)

### 10.3 Graceful Degradation

When Redis is unavailable:
- `store()` returns `None` (warning logged)
- `retrieve()` returns `None`
- Preview still completes normally
- `cached_state_key` is `None` in PreviewResult
- ExecuteOrchestrator falls back to re-running previewable steps

---

## 11. Observability & Safety

### 11.1 Structured Logging

All log entries include `plan_id` correlation per GLOBAL_SPEC §3:

| Event | Level | Extra Fields |
|-------|-------|-------------|
| `preview_started` | INFO | plan_id, user_id, trace_id, total_steps |
| `step_dispatched` | INFO | plan_id, step, role, uses, call |
| `step_completed` | INFO | plan_id, step, latency_ms, status |
| `step_deferred` | INFO | plan_id, step, reason |
| `step_failed` | WARNING | plan_id, step, error_type |
| `step_skipped` | INFO | plan_id, step, reason |
| `preview_completed` | INFO | plan_id, total_steps, completed, deferred, failed, partial, duration_ms |
| `cache_stored` | DEBUG | plan_id, user_id, cache_key, ttl_s |
| `cache_store_failed` | WARNING | plan_id, error |
| `cache_retrieved` | DEBUG | plan_id, user_id, hit (bool) |

### 11.2 No PII/Secrets in Logs

- Step args are NOT logged (may contain user data)
- Step results are NOT logged (may contain external API data)
- Only step numbers, statuses, latencies, and error types are logged
- Credential values never reach PreviewOrchestrator (no write credentials used)

### 11.3 Error Classes

| Exception | Domain | When |
|-----------|--------|------|
| `PreviewError` | Base | Unexpected preview failure |
| `PreviewStepError` | `PreviewError` | Step-level failure (non-fatal, logged) |

### 11.4 Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `preview_duration_seconds` | Histogram | plan_id (cardinality note: use only for dev) | Total preview latency |
| `preview_step_duration_seconds` | Histogram | step_type, role, status | Per-step preview latency |
| `preview_step_total` | Counter | status (completed/failed/deferred/skipped) | Step outcome counts |
| `preview_errors_total` | Counter | error_type (cycle/mcp/internal) | Error counts by type |
| `preview_cache_operations_total` | Counter | operation (store/retrieve), result (hit/miss/error) | Cache operation counts |
| `preview_partial_total` | Counter | — | Count of partial previews (some steps failed) |

---

## 12. Dependencies & External Integrations

### 12.1 Python Packages

| Package | Constraint | Justification |
|---------|-----------|---------------|
| `pydantic` | `>=2.0` | Domain models, request/response validation |
| `redis[hiredis]` | `>=5.0` | Preview state caching (async Redis client) |

No new package dependencies — all other packages (httpx, ulid-py, etc.) are already in the project via ExecuteOrchestrator and Planner.

### 12.2 Internal Dependencies

| Component | Type | What's Used |
|-----------|------|-------------|
| ExecuteOrchestrator | Import (adapters) | DAGResolver, TemplateResolver, MCPClient Protocol, StepResult, CycleDetectedError |
| PluginRegistry | Service dependency | `get_tool()` → `operation.previewable` flag |
| Redis | Infrastructure | Preview state cache (best-effort) |

### 12.3 External Dependencies

| Service | SLA | Usage |
|---------|-----|-------|
| MCP servers | Per-server | Read-only tool invocations (`dry_run: true`) |
| Redis | Best-effort | Preview state caching (graceful degradation) |

---

## 13. Non-Functional Requirements

### 13.1 Performance

| Metric | Local Target | Cloud Target | Notes |
|--------|-------------|-------------|-------|
| Preview latency (p95) | < 1200ms | < 800ms | GLOBAL_SPEC §3. Dominated by MCP invocation latency. |
| Preview latency (p99) | < 2000ms | < 1200ms | Long-tail from slow MCP servers |
| Per-step MCP invocation (p95) | < 500ms | < 300ms | Individual read-only tool calls |
| Redis cache store | < 5ms | < 2ms | Single SET with TTL |
| Redis cache retrieve | < 5ms | < 2ms | Single GET |
| DAG resolution | < 1ms | < 1ms | Kahn's algorithm, in-memory |

### 13.2 Availability

| Environment | Target | Notes |
|-------------|--------|-------|
| Cloud | 99.9% | Per GLOBAL_SPEC §3 (Intake/Preview tier) |
| Local | Best-effort | Single-process, no HA |

### 13.3 Throughput

| Scenario | Target |
|----------|--------|
| Single-user local | 10 concurrent previews |
| Multi-user cloud | 100 concurrent previews |

### 13.4 Testing Strategy

| Test File | Scope | Count (est.) |
|-----------|-------|-------------|
| `test_unit.py` | Core preview logic, DAG dispatch, step filtering, deferral cascade | ~25 |
| `test_service.py` | Cache interaction, MCP dispatch, template resolution, factory | ~20 |
| `test_contract.py` | PreviewResult/PreviewStepResult model conformance, GLOBAL_SPEC §2.5 | ~10 |
| `test_observability.py` | Structured logging, no PII, metric events | ~10 |
| **Total** | | **~65** |

---

## 14. Architectural Considerations

### 14.1 Blast Radius Containment

PreviewOrchestrator is stateless (no owned DB tables). The only persistent side effect is a Redis cache entry with auto-expiration. If PreviewOrchestrator fails:
- No data is corrupted
- No external writes occurred
- User simply retries the preview
- Downstream components (ApprovalGate, ExecuteOrchestrator) degrade gracefully (re-run previewable steps)

### 14.2 Fault Isolation

- **MCP step failures**: Caught per-step; other parallel steps complete normally. `partial: true` returned.
- **Redis failure**: Graceful degradation; preview completes without caching.
- **PluginRegistry failure**: Steps treated as non-previewable (deferred). Preview returns with empty completed steps.
- **DAG cycle**: Propagated immediately as PreviewError.

### 14.3 Determinism

Preview results are deterministic for a given plan + MCP server state:
- Same plan → same DAG levels → same step classification
- Same MCP server responses → same step results
- No randomness, no LLM calls, no runtime decisions

### 14.4 State Management

- **Stateless service**: No persistent state between calls
- **Ephemeral state**: Step results accumulated in-memory during a single `preview()` call
- **Cached state**: Redis TTL-based; loss is acceptable (re-run preview)

### 14.5 Cross-Component Adapter Coupling

PreviewOrchestrator imports DAGResolver, TemplateResolver, and MCPClient from ExecuteOrchestrator. This creates a compile-time dependency:

- **Risk**: Changes to ExecuteOrchestrator adapters could break PreviewOrchestrator
- **Mitigation**: Both use the same Protocol interfaces; adapter implementations are stable utility classes
- **Future**: Move shared adapters to `shared/adapters/` if a third consumer emerges

---

## 15. File Structure

```
components/PreviewOrchestrator/
├── __init__.py
├── LLD.md
├── diagrams/
│   └── flow.md
├── domain/
│   └── models.py                    # PreviewRequest, PreviewResult, PreviewStepResult, exceptions
├── service/
│   └── preview_service.py           # PreviewService + create_preview_service()
├── adapters/
│   ├── preview_cache.py             # PreviewCacheAdapter (Redis)
│   └── previewability_checker.py    # PreviewabilityChecker (PluginRegistry)
└── tests/
    ├── conftest.py                  # Fixtures, mock adapters, sample plans
    ├── test_unit.py                 # Core preview logic, DAG dispatch, step filtering
    ├── test_service.py              # Cache, MCP dispatch, template resolution
    ├── test_contract.py             # Model conformance, PreviewResult shape
    └── test_observability.py        # Logging, no PII
```

**Shared files touched:**
- `shared/app.py` — Add `create_preview_service()` in lifespan
- `shared/dependencies.py` — Add `get_preview_service()` accessor

---

## 16. Risks & Open Questions

### 16.1 Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| MCP servers may have side effects even in read-only mode | High | Only dispatch operations with `previewable: true` from PluginRegistry; pass `dry_run: true` in args |
| Stale preview state used during execution | Medium | TTL-based expiration (15min); plan_id match verified at execution time by ExecuteOrchestrator |
| DAGResolver/TemplateResolver imports create coupling to ExecuteOrchestrator | Low | Use Protocol interfaces; move to `shared/` if third consumer emerges |
| Redis unavailable during preview | Low | Graceful degradation: preview completes, caching is best-effort |
| PluginRegistry down during preview | Low | Fail-safe: treat operations as non-previewable (defer all steps) |
| Large plans (100 steps) may exceed p95 latency target | Low | Parallelism via asyncio.gather mitigates; dominated by max-of-parallel, not sum-of-sequential |

### 16.2 Open Questions

- **OQ-1**: Should DAGResolver/TemplateResolver move to `shared/`? **Current decision**: Import directly, refactor later.
- **OQ-2**: Should PreviewOrchestrator expose HTTP routes? **Current decision**: Library component, routes added with API gateway.
- **OQ-3**: Dedicated preview-only credential set? **Current decision**: Use same credentials with read-only scopes from PluginRegistry.
- **OQ-4**: Steps with `gate_id`? **Current decision**: Mark as `"deferred"` (gates resolved during approval/execution).

---

## 17. Post-Generation Validation Checklist

- [x] Data model fields match GLOBAL_SPEC §2.5 Preview Wrapper (normalized, source, can_execute, evidence)
- [x] `user_id` present on PreviewRequest (input) and cache key pattern
- [x] Conformance header references current document versions (GLOBAL_SPEC v3.0, HLD v6.1, MODULAR_ARCHITECTURE v2.1)
- [x] No owned database tables (Redis cache only) — matches MODULAR_ARCHITECTURE §3
- [x] Component dependencies match MODULAR_ARCHITECTURE §4 (PluginRegistry read-only)
- [x] Upstream consumers documented (ApprovalGate, ExecuteOrchestrator, Intake/API Gateway)
- [x] No storage APIs to make idempotent (stateless library)
- [x] No DDL needed (no owned tables)
- [x] Prometheus metrics defined with names and types
- [x] No deprecated library versions
- [x] Error handling follows shared patterns (domain exceptions in domain/models.py)
- [x] Database adapter: N/A (no database access)
- [x] Cache operations use graceful degradation (never fail the preview)

---

**Document Version**: LLD v1.0
**Last Updated**: 2026-04-03
**Author**: Design workflow
