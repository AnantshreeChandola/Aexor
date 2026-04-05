# Tasks: PreviewOrchestrator

**Created**: 2026-04-04
**Branch**: feat/previeworchestrator-read-only
**SPEC**: specs/025-previeworchestrator-read-only/spec.md
**LLD**: components/PreviewOrchestrator/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture (domain models, adapters, service, DI wiring, safety, tests). PreviewOrchestrator is a **library component** -- no HTTP routes, no owned database tables, no API handler phase.

---

## Phase 0: Setup & Scaffolding

### Install Dependencies (from LLD.md Section 12)

- [ ] [T000] Create `components/PreviewOrchestrator/__init__.py` with module docstring.
  - File: `components/PreviewOrchestrator/__init__.py`
  - Content: Empty module init with docstring referencing SPEC 025.

- [ ] [T001] Create directory structure for the component.
  - Directories to create (via `__init__.py` files in each):
    - `components/PreviewOrchestrator/domain/__init__.py`
    - `components/PreviewOrchestrator/service/__init__.py`
    - `components/PreviewOrchestrator/adapters/__init__.py`
    - `components/PreviewOrchestrator/tests/__init__.py`
  - Each `__init__.py` is empty or contains a module-level docstring.

- [ ] [T002] Verify Python package dependencies are already available.
  - Packages required (from LLD Section 12.1): `pydantic>=2.0`, `redis[hiredis]>=5.0`.
  - Both already listed in `pyproject.toml` via ExecuteOrchestrator and Intake.
  - Verify: `pip install -e .` succeeds and `import pydantic; import redis` work.

- [ ] [T003] Verify internal component dependencies are importable.
  - Verify these imports resolve without error:
    - `from components.ExecuteOrchestrator.adapters.dag_resolver import DAGResolver`
    - `from components.ExecuteOrchestrator.adapters.template_resolver import TemplateResolver`
    - `from components.ExecuteOrchestrator.adapters.mcp_client import MCPClient`
    - `from components.ExecuteOrchestrator.domain.models import StepResult, CycleDetectedError`
    - `from components.PluginRegistry.service.registry_service import RegistryService`
    - `from components.PluginRegistry.domain.models import ToolModel, OperationModel, ToolNotFoundError`
    - `from shared.schemas.plan import Plan, PlanStep`

---

## Phase 1: Domain Models & Exceptions (Foundation)

### Acceptance Criteria: SPEC FR-008 (PreviewResult conforms to GLOBAL_SPEC v3.0 S2.5), FR-010 (partial success)

- [ ] [T100] Create domain models in `components/PreviewOrchestrator/domain/models.py`.
  - File: `components/PreviewOrchestrator/domain/models.py`
  - Classes to implement:
    - `PreviewRequest(BaseModel)` -- `plan: Plan`, `user_id: str`, `trace_id: str`
    - `PreviewStepResult(BaseModel)` -- `step: int`, `status: Literal["completed","failed","deferred","skipped"]`, `result: dict | None`, `error: dict | None`, `latency_ms: int`, `reason: str | None`
    - `PreviewResult(BaseModel)` -- `plan_id: str` (26-char ULID), `normalized: dict`, `source: Literal["preview"]`, `can_execute: bool`, `partial: bool`, `cached_state_key: str | None`, `evidence: list[dict]`
    - `PreviewError(Exception)` -- base error
    - `PreviewStepError(PreviewError)` -- step-level failure with `step: int` attribute
  - Field constraints: per LLD Section 5.1 (min_length, ge, Literal types).
  - GLOBAL_SPEC S2.5 alignment: `normalized`, `source: "preview"`, `can_execute`, `evidence` fields.

- [ ] [T101] Write domain model tests (contract tests for model shape).
  - File: `components/PreviewOrchestrator/tests/test_contract.py`
  - Test cases (~10):
    - `PreviewResult` validates with all required fields.
    - `PreviewResult.source` is always `"preview"`.
    - `PreviewResult.plan_id` rejects strings not exactly 26 chars.
    - `PreviewStepResult` accepts all four status values.
    - `PreviewStepResult` rejects invalid status values.
    - `PreviewRequest` rejects empty `user_id` and empty `trace_id`.
    - `PreviewResult.normalized` must contain `"steps"` key (convention test).
    - `PreviewError` and `PreviewStepError` exception hierarchy.
    - `PreviewStepError` stores `step` attribute.
    - Round-trip: `model_dump()` then `model_validate()` produces identical model.

---

## Phase 2: Adapters (PreviewCacheAdapter & PreviewabilityChecker)

### Acceptance Criteria: SPEC FR-002 (previewable check), FR-007 (Redis cache), FR-012 (get_preview_state), US4 (cache reuse)

- [ ] [T200] Implement `PreviewCacheAdapter` in `components/PreviewOrchestrator/adapters/preview_cache.py`.
  - File: `components/PreviewOrchestrator/adapters/preview_cache.py`
  - Class: `PreviewCacheAdapter`
  - Constructor: `__init__(self, redis_client: object | None, ttl_s: int = 900)`
  - TTL default 900s, overridable via constructor (env var read by factory, not here).
  - Methods:
    - `async def store(self, plan_id: str, user_id: str, state: dict[int, dict]) -> str | None` -- JSON-serialize state, SET to `preview:{user_id}:{plan_id}` with TTL. Returns cache key on success, None on failure.
    - `async def retrieve(self, plan_id: str, user_id: str) -> dict[int, dict] | None` -- GET from Redis, JSON-deserialize. Returns None on miss/error.
  - Graceful degradation: all Redis operations wrapped in `try/except Exception`; failures logged as warnings via `logging.getLogger(__name__)`, never propagated.
  - Serialization note: dict keys are step numbers (int), but JSON keys are strings. Deserialize must convert keys back to int.

- [ ] [T201] Implement `PreviewabilityChecker` in `components/PreviewOrchestrator/adapters/previewability_checker.py`.
  - File: `components/PreviewOrchestrator/adapters/previewability_checker.py`
  - Class: `PreviewabilityChecker`
  - Constructor: `__init__(self, registry_service: object)`
  - Method: `async def is_previewable(self, tool_id: str, operation_id: str) -> bool`
    - Calls `self._registry.get_tool(tool_id)` to get `ToolModel`.
    - Looks up `tool.operations[operation_id].previewable`.
    - Returns `False` on any exception (`ToolNotFoundError`, `KeyError`, etc.) -- fail-safe per LLD Section 6.3.
    - Logs a warning on lookup failure (no PII -- only tool_id and operation_id).

- [ ] [T202] Write adapter unit tests.
  - File: `components/PreviewOrchestrator/tests/test_service.py` (adapter section, per LLD naming)
  - Test cases for PreviewCacheAdapter (~8):
    - `store()` returns cache key when Redis available.
    - `store()` returns None when Redis is None (no-client mode).
    - `store()` returns None and logs warning when Redis raises `ConnectionError`.
    - `retrieve()` returns deserialized state on cache hit.
    - `retrieve()` returns None on cache miss (key not found).
    - `retrieve()` returns None when Redis is None.
    - `retrieve()` returns None and logs warning on Redis error.
    - `store()` then `retrieve()` round-trip: int keys preserved after serialization.
  - Test cases for PreviewabilityChecker (~4):
    - Returns `True` when tool and operation have `previewable: true`.
    - Returns `False` when operation has `previewable: false`.
    - Returns `False` when `get_tool()` raises `ToolNotFoundError`.
    - Returns `False` when operation_id not found in `tool.operations`.

---

## Phase 3: Test Fixtures & Conftest

### Supporting infrastructure for all test files

- [ ] [T300] Create `conftest.py` with fixtures, mock adapters, and sample plans.
  - File: `components/PreviewOrchestrator/tests/conftest.py`
  - Fixtures to implement:
    - `sample_plan()` -- Returns a `Plan` with 5 steps: 3 previewable API steps (Fetcher, Fetcher, Analyzer), 1 non-previewable Booker step, 1 Notifier step. DAG: steps 1,2 parallel, step 3 after [1,2], step 4 after [3] (Booker, gate_id="gate-A"), step 5 after [4] (Notifier).
    - `hybrid_plan()` -- Returns a `Plan` with mixed types: 2 API steps (previewable), 1 `llm_reasoning` step (Reasoner), 1 `policy_check` step, 1 API step depending on reasoning step.
    - `parallel_plan()` -- Returns a `Plan` with 4 steps, all at the same DAG level (no dependencies), all previewable.
    - `empty_previewable_plan()` -- Returns a `Plan` where zero steps are previewable (all Booker with gate_id).
    - `mock_mcp_client()` -- Mock implementing the `MCPClient` Protocol. `invoke()` returns configurable dict results. Tracks calls for assertion.
    - `mock_registry_service()` -- Mock for `RegistryService`. `get_tool()` returns `ToolModel` with configurable operations and previewable flags. Supports raising `ToolNotFoundError`.
    - `mock_redis_client()` -- Fake async Redis client with in-memory dict storage. Supports `set()`, `get()`, `delete()` with TTL tracking.
    - `preview_request(sample_plan)` -- Returns `PreviewRequest` with the sample plan, a test user_id, and a test trace_id.
  - Use `ulid` or hardcoded 26-char test plan_id values.
  - Import `Plan`, `PlanStep` from `shared/schemas/plan.py`.
  - Import `ToolModel`, `OperationModel` from `components/PluginRegistry/domain/models.py`.

---

## Phase 4: Service Layer (Core Preview Logic)

### Acceptance Criteria: SPEC US1 (pure API preview), US2 (hybrid plan), US3 (graceful degradation), US4 (cache reuse), FR-001 (DAG resolve), FR-003 (skip reasoning), FR-004 (dry_run MCP), FR-005 (parallel), FR-006 (cascade deferral), FR-009 (template resolution)

- [ ] [T400] Implement `PreviewService` in `components/PreviewOrchestrator/service/preview_service.py`.
  - File: `components/PreviewOrchestrator/service/preview_service.py`
  - Class: `PreviewService`
  - Constructor dependencies (injected):
    - `_dag_resolver: DAGResolver` (from `components.ExecuteOrchestrator.adapters.dag_resolver`)
    - `_template_resolver: TemplateResolver` (from `components.ExecuteOrchestrator.adapters.template_resolver`)
    - `_mcp_client: MCPClient` (Protocol from `components.ExecuteOrchestrator.adapters.mcp_client`)
    - `_checker: PreviewabilityChecker` (new, from adapters)
    - `_cache: PreviewCacheAdapter` (new, from adapters)
    - `_registry: RegistryService` (from PluginRegistry, for tool resolution in dispatch)
  - Public methods:
    - `async def preview(self, request: PreviewRequest) -> PreviewResult`
    - `async def get_preview_state(self, plan_id: str, user_id: str) -> dict[int, PreviewStepResult] | None`

- [ ] [T401] Implement `preview()` core algorithm per LLD Section 9.1.
  - File: `components/PreviewOrchestrator/service/preview_service.py` (within `PreviewService`)
  - Steps:
    1. Resolve DAG levels via `self._dag_resolver.resolve(request.plan.graph)`. Catch `CycleDetectedError` and wrap in `PreviewError`.
    2. Initialize tracking: `step_results: dict[int, PreviewStepResult]`, `deferred_steps: set[int]`, `failed_steps: set[int]`.
    3. For each level, call `_process_level()`.
    4. Build result: determine `partial` (any failures), `can_execute` (not all failed/deferred/skipped).
    5. Cache state via `_cache.store()` (best-effort).
    6. Return `PreviewResult`.

- [ ] [T402] Implement `_classify_step()` per LLD Section 9.2.
  - File: `components/PreviewOrchestrator/service/preview_service.py` (private method)
  - Classification order (LLD Section 9.2):
    1. Dependency cascade: if any step in `after` is in `deferred_steps` -> "deferred" (reason: "dependency_deferred").
    2. Dependency cascade: if any step in `after` is in `failed_steps` -> "skipped" (reason: "dependency_failed").
    3. Non-API type: if `step.type in ("llm_reasoning", "policy_check")` -> "deferred" (reason: step.type).
    4. Gated: if `step.gate_id is not None` -> "deferred" (reason: "gated").
    5. Previewability: if `not await self._checker.is_previewable(step.uses, step.call)` -> "deferred" (reason: "non_previewable").
    6. Otherwise -> "dispatch".
  - Note: This method is `async` because it calls `is_previewable()`.

- [ ] [T403] Implement `_process_level()` with parallel dispatch per LLD Section 9.3.
  - File: `components/PreviewOrchestrator/service/preview_service.py` (private method)
  - Logic:
    1. Classify each step in the level.
    2. Record deferred/skipped steps immediately in `step_results`.
    3. Add deferred steps to `deferred_steps` set. Add skipped steps to `failed_steps` set (to propagate downstream).
    4. Dispatch remaining steps in parallel via `asyncio.gather(*tasks, return_exceptions=True)`.
    5. For each outcome: if Exception, record as `status="failed"` with error dict containing `error_type` and `message`. Add to `failed_steps`. If success, record the returned `PreviewStepResult`.

- [ ] [T404] Implement `_dispatch_step()` (MCP invocation) per LLD Section 9.4.
  - File: `components/PreviewOrchestrator/service/preview_service.py` (private method)
  - Logic:
    1. Measure start time via `time.monotonic()`.
    2. Convert completed `PreviewStepResult`s to `StepResult` format for `TemplateResolver` compatibility.
    3. Resolve template args via `self._template_resolver.resolve(step.args, exec_results)`.
    4. Add `dry_run: True` to resolved args.
    5. Resolve tool info: call `self._registry.get_tool(step.uses)` to get `mcp_server` and `mcp_tool` (via `tool.operations[step.call].n8n_node` or fallback to `step.call`).
    6. Invoke MCP: `self._mcp_client.invoke(server=mcp_server, tool=mcp_tool, args=resolved_args, credentials=None, timeout_s=step.timeout_s)`.
    7. Calculate `latency_ms`.
    8. Return `PreviewStepResult(step=step.step, status="completed", result=result, latency_ms=latency_ms)`.

- [ ] [T405] Implement `get_preview_state()` per LLD Section 4.3.
  - File: `components/PreviewOrchestrator/service/preview_service.py` (within `PreviewService`)
  - Delegates to `self._cache.retrieve(plan_id, user_id)`.
  - Returns `dict[int, PreviewStepResult] | None` (deserialize from cache, reconstruct PreviewStepResult models if needed, or return raw dict).

- [ ] [T406] Implement `create_preview_service()` factory function.
  - File: `components/PreviewOrchestrator/service/preview_service.py` (module-level function)
  - Signature:
    ```python
    def create_preview_service(
        mcp_client: MCPClient,
        registry_service: RegistryService,
        redis_client: object | None = None,
    ) -> PreviewService:
    ```
  - Logic:
    1. Read `PREVIEW_CACHE_TTL_S` from `os.environ` (default `"900"`), parse to int.
    2. Create `DAGResolver()`.
    3. Create `TemplateResolver()`.
    4. Create `PreviewCacheAdapter(redis_client, ttl_s)`.
    5. Create `PreviewabilityChecker(registry_service)`.
    6. Return `PreviewService(dag_resolver, template_resolver, mcp_client, checker, cache, registry_service)`.

---

## Phase 5: Service Tests (Core Logic)

### Acceptance Criteria: All SPEC user stories (US1-US4) and functional requirements (FR-001 through FR-012)

- [ ] [T500] Write core preview logic unit tests.
  - File: `components/PreviewOrchestrator/tests/test_unit.py`
  - Test cases (~25):
    - **US1 / FR-001**: `preview()` resolves DAG into correct levels for `sample_plan`.
    - **US1 / FR-002**: Only steps with `previewable: true` operations are dispatched.
    - **US1 / FR-004**: MCP invocations include `dry_run: True` in args.
    - **US1 / FR-004**: MCP invocations pass `credentials=None`.
    - **US1 / FR-005**: Steps at the same DAG level execute in parallel (verify via mock call timestamps or gather usage).
    - **US1 / FR-008**: Returned `PreviewResult.source == "preview"`.
    - **US1 / FR-008**: Returned `PreviewResult.can_execute == True` when some steps succeed.
    - **US1**: Non-previewable Booker step is marked `status="deferred"` with `reason="non_previewable"` or `reason="gated"`.
    - **US2 / FR-003**: `llm_reasoning` steps are marked `status="deferred"`, `reason="llm_reasoning"`.
    - **US2 / FR-003**: `policy_check` steps are marked `status="deferred"`, `reason="policy_check"`.
    - **US2 / FR-006**: API step depending on a deferred reasoning step is cascade-deferred with `reason="dependency_deferred"`.
    - **US2**: Steps with `gate_id` set are deferred with `reason="gated"`.
    - **US3 / FR-010**: One step fails, other parallel steps complete. `partial=True`.
    - **US3 / FR-010**: Downstream steps of a failed step are skipped with `reason="dependency_failed"`.
    - **US3**: All previewable steps fail: `can_execute=False`.
    - **US3**: All steps are non-previewable: `can_execute=True` (approval still needed, per SPEC edge case).
    - **FR-009**: Template args `{{step_1.result.field}}` resolved from prior completed step results.
    - **FR-009**: Template resolution failure (missing reference) causes step to fail, not crash preview.
    - **Edge case**: Plan with zero previewable steps returns PreviewResult with empty completed results and `can_execute=True`.
    - **Edge case**: DAG cycle raises `PreviewError` (wrapping `CycleDetectedError`).
    - **Edge case**: Single-step plan (one previewable step) works correctly.
    - **Edge case**: Step with both `gate_id` and `type="api"` is deferred (gate_id takes priority).
    - **Edge case**: Step depends on multiple predecessors, one deferred and one failed -- should be deferred (deferred check runs before failed check in `_classify_step` per LLD order).
    - **Determinism / SC-003**: Assert zero MCP calls made for non-previewable steps.
    - **Parallel verification / SC-002**: For `parallel_plan`, verify total latency is approximately max-of-steps (not sum).

- [ ] [T501] Write service-level integration tests (cache + MCP + templates).
  - File: `components/PreviewOrchestrator/tests/test_service.py` (service section)
  - Test cases (~8 additional beyond T202 adapter tests):
    - **US4 / FR-007**: Preview caches state in Redis after completion. Verify `cached_state_key` is set.
    - **US4 / FR-012**: `get_preview_state()` returns cached results by plan_id + user_id.
    - **US4**: Cache TTL: verify key is set with correct expiry (mock Redis `setex` args).
    - **US4**: Re-running preview for same plan_id replaces old cache entry.
    - **US4**: `get_preview_state()` returns None for expired/missing cache (mock returns None).
    - **Redis down / FR-007**: Preview completes when Redis unavailable. `cached_state_key=None`. No exception raised.
    - **Factory**: `create_preview_service()` creates valid `PreviewService` with all adapters wired.
    - **Factory**: `create_preview_service()` reads `PREVIEW_CACHE_TTL_S` from env var.

---

## Phase 6: DI Wiring (Shared Infrastructure)

### From LLD Section 7.1

- [ ] [T600] Add `create_preview_service()` call in `shared/app.py` lifespan.
  - File: `shared/app.py`
  - Location: After ExecuteOrchestrator initialization block (PreviewOrchestrator shares its MCP client and PluginRegistry).
  - Logic:
    ```python
    # PreviewOrchestrator service (library -- no routes)
    try:
        from components.PreviewOrchestrator.service.preview_service import (
            create_preview_service,
        )
        app.state.preview_service = create_preview_service(
            mcp_client=MCPClientAdapter(registry_service=app.state.registry_service),
            registry_service=app.state.registry_service,
            redis_client=intake_redis,  # Reuse same Redis client
        )
    except Exception as exc:
        logger.warning("PreviewOrchestrator init failed: %s", exc)
        app.state.preview_service = None
    ```
  - Note: Must import `MCPClientAdapter` (already imported in the ExecuteOrchestrator block above). If the import is scoped to the EO try-block, may need to re-import or restructure. Alternatively, create a new MCPClientAdapter instance.
  - Placement: After the ExecuteOrchestrator block but before `yield`, so `intake_redis` is available.

- [ ] [T601] Add `get_preview_service()` accessor in `shared/dependencies.py`.
  - File: `shared/dependencies.py`
  - Add function:
    ```python
    def get_preview_service(request: Request) -> Any:
        """Get PreviewService singleton from app state."""
        return request.app.state.preview_service
    ```

---

## Phase 7: Observability & Safety

### Acceptance Criteria: SPEC FR-011 (structured logging), SC-005 (plan_id correlation), NFR (no PII/secrets)

- [ ] [T700] Add structured logging throughout `PreviewService`.
  - File: `components/PreviewOrchestrator/service/preview_service.py`
  - Use `logging.getLogger(__name__)` at module level.
  - Log events per LLD Section 11.1:
    - `preview_started` (INFO): plan_id, user_id, trace_id, total_steps.
    - `step_dispatched` (INFO): plan_id, step, role, uses, call.
    - `step_completed` (INFO): plan_id, step, latency_ms, status.
    - `step_deferred` (INFO): plan_id, step, reason.
    - `step_failed` (WARNING): plan_id, step, error_type (no args, no results -- per LLD 11.2).
    - `step_skipped` (INFO): plan_id, step, reason.
    - `preview_completed` (INFO): plan_id, total_steps, completed count, deferred count, failed count, partial, duration_ms.
    - `cache_stored` (DEBUG): plan_id, user_id, cache_key, ttl_s.
    - `cache_store_failed` (WARNING): plan_id, error.
    - `cache_retrieved` (DEBUG): plan_id, user_id, hit (bool).
  - All log calls use `extra={}` dict for structured data.
  - NO step args or results in logs (PII/secrets risk per LLD 11.2).

- [ ] [T701] Add structured logging to `PreviewCacheAdapter`.
  - File: `components/PreviewOrchestrator/adapters/preview_cache.py`
  - Log `cache_store_failed` (WARNING) and `cache_retrieved` (DEBUG) events with plan_id correlation.

- [ ] [T702] Write observability tests.
  - File: `components/PreviewOrchestrator/tests/test_observability.py`
  - Test cases (~10):
    - `preview_started` log emitted with correct plan_id, user_id, trace_id.
    - `step_completed` log emitted for each completed step with latency_ms.
    - `step_deferred` log emitted with reason for deferred steps.
    - `step_failed` log emitted at WARNING level for failed steps.
    - `preview_completed` log emitted with summary counts.
    - `cache_stored` log emitted on successful cache write.
    - `cache_store_failed` log emitted when Redis fails.
    - No PII in logs: assert step args are NOT present in any log record.
    - No PII in logs: assert step results are NOT present in any log record.
    - plan_id correlation: all log records from a single preview call share the same plan_id in extra.
  - Use `caplog` pytest fixture or custom log handler to capture and inspect log records.

---

## Phase 8: Contract Tests & End-to-End Validation

### Acceptance Criteria: SPEC conformance, GLOBAL_SPEC S2.5 envelope

- [ ] [T800] Write GLOBAL_SPEC S2.5 Preview Wrapper conformance tests.
  - File: `components/PreviewOrchestrator/tests/test_contract.py` (extend from T101)
  - Additional test cases (~5, beyond the model-level tests in T101):
    - Full `preview()` call returns a `PreviewResult` that matches GLOBAL_SPEC S2.5 shape: has `normalized`, `source`, `can_execute`, `evidence` keys.
    - `PreviewResult.normalized` contains a `"steps"` list where each entry has `step`, `status`.
    - Preview of a pure API plan returns `can_execute=True`, `partial=False`, non-empty `normalized.steps`.
    - Preview of a plan where all steps fail returns `can_execute=False`, `partial=True`.
    - `PreviewResult.evidence` defaults to empty list.

- [ ] [T801] Write Intent-to-Preview flow integration test.
  - File: `components/PreviewOrchestrator/tests/test_contract.py` (flow section)
  - Test case:
    - Construct a `Plan` from a test `Intent` (manually, since Planner is not invoked in library tests).
    - Call `preview()`.
    - Verify the returned `PreviewResult` can be serialized to JSON and matches the Preview Wrapper schema.
    - Verify `get_preview_state()` returns the cached state.
    - Verify cached state step numbers match the completed steps in `PreviewResult.normalized.steps`.

- [ ] [T802] Write determinism validation test.
  - File: `components/PreviewOrchestrator/tests/test_contract.py`
  - Test case:
    - Call `preview()` twice with the same Plan and same mock MCP responses.
    - Assert both `PreviewResult` objects are identical (same step statuses, same results, same can_execute).
    - Validates LLD Section 14.3: "Same plan + same MCP responses = same preview results."

---

## Task Summary

- **Total Tasks**: 27
- **Phase 0 (Setup)**: T000-T003 (4 tasks)
- **Phase 1 (Domain)**: T100-T101 (2 tasks)
- **Phase 2 (Adapters)**: T200-T202 (3 tasks)
- **Phase 3 (Fixtures)**: T300 (1 task)
- **Phase 4 (Service)**: T400-T406 (7 tasks)
- **Phase 5 (Service Tests)**: T500-T501 (2 tasks)
- **Phase 6 (DI Wiring)**: T600-T601 (2 tasks)
- **Phase 7 (Observability)**: T700-T702 (3 tasks)
- **Phase 8 (Contract Tests)**: T800-T802 (3 tasks)

---

## Dependencies

### External (from LLD.md Section 12.1)

| Package | Constraint | Status |
|---------|-----------|--------|
| `pydantic` | `>=2.0` | Already in pyproject.toml |
| `redis[hiredis]` | `>=5.0` | Already in pyproject.toml |

No new package dependencies required.

### Internal (from LLD.md Section 12.2)

| Component | Import Path | What's Used |
|-----------|------------|-------------|
| ExecuteOrchestrator | `components.ExecuteOrchestrator.adapters.dag_resolver` | `DAGResolver` |
| ExecuteOrchestrator | `components.ExecuteOrchestrator.adapters.template_resolver` | `TemplateResolver` |
| ExecuteOrchestrator | `components.ExecuteOrchestrator.adapters.mcp_client` | `MCPClient` (Protocol), `MCPClientAdapter` |
| ExecuteOrchestrator | `components.ExecuteOrchestrator.domain.models` | `StepResult`, `CycleDetectedError`, `MCPInvocationError` |
| PluginRegistry | `components.PluginRegistry.service.registry_service` | `RegistryService` |
| PluginRegistry | `components.PluginRegistry.domain.models` | `ToolModel`, `OperationModel`, `ToolNotFoundError` |
| Shared | `shared/schemas/plan.py` | `Plan`, `PlanStep` |
| Shared | `shared/app.py` | DI wiring (lifespan) |
| Shared | `shared/dependencies.py` | Depends accessor |

---

## Architectural Considerations

### Blast Radius (from LLD Section 14.1)

- **If PreviewOrchestrator fails**: User cannot preview plans. No data loss (stateless). No external writes occurred. User retries the preview. Other components unaffected.
- **If Redis unavailable**: Preview still completes; caching is best-effort. ExecuteOrchestrator re-runs previewable steps.
- **If MCP step times out**: Other parallel branches complete normally. `partial: true` returned.
- **Containment**: Stateless library (no DB tables), TTL-based cache auto-expires, per-step error isolation.

### Determinism (from LLD Section 14.3)

- **Preview**: Same plan + same MCP server responses = same preview results. No randomness, no LLM calls, no runtime decisions.
- **No idempotency needed**: Preview is read-only with no side effects beyond a Redis cache SET (TTL-based, overwrite-safe).

### Cross-Component Coupling (from LLD Section 14.5)

- Direct imports from ExecuteOrchestrator adapters (DAGResolver, TemplateResolver, MCPClient). Both components use the same Protocol interfaces.
- Risk: Changes to EO adapters could break PreviewOrchestrator.
- Mitigation: Protocol-based interface for MCPClient; DAGResolver and TemplateResolver are stable utility classes.
- Future: Move to `shared/adapters/` if a third consumer emerges (OQ-1).

### Test Count Alignment (from LLD Section 13.4)

| Test File | Estimated Count | LLD Target |
|-----------|----------------|------------|
| `test_unit.py` | ~25 | ~25 |
| `test_service.py` | ~20 (12 adapter + 8 service) | ~20 |
| `test_contract.py` | ~15 (10 model + 5 flow) | ~10 |
| `test_observability.py` | ~10 | ~10 |
| **Total** | **~70** | **~65** |
