# Verification Report: PreviewOrchestrator

**Date**: 2026-04-04
**Branch**: feat/previeworchestrator-read-only
**Status**: PASS

---

## Test Results

```
75 passed in 1.02s
```

- Passed: 75
- Failed: 0
- Skipped: 0

### Breakdown by Test File

| File | Tests | Status |
|------|-------|--------|
| `test_contract.py` | 21 | All passed |
| `test_observability.py` | 10 | All passed |
| `test_service.py` | 20 | All passed |
| `test_unit.py` | 24 | All passed |
| **Total** | **75** | **All passed** |

---

## Lint & Format

| Check | Result |
|-------|--------|
| `ruff check components/PreviewOrchestrator/ shared/app.py shared/dependencies.py` | All checks passed |
| `ruff format --check components/PreviewOrchestrator/` | 14 files already formatted |

---

## Schema Validation Matrix

| Contract | Requirement | Status | Evidence |
|----------|------------|--------|----------|
| **PreviewResult.normalized** | GLOBAL_SPEC S2.5: `normalized` field present | PASS | `PreviewResult(BaseModel)` has `normalized: dict[str, Any]` field; test `test_preview_result_has_required_keys` verifies key present |
| **PreviewResult.source** | GLOBAL_SPEC S2.5: `source: "preview"` | PASS | `source: Literal["preview"]` with default `"preview"`; test `test_source_always_preview` verifies |
| **PreviewResult.can_execute** | GLOBAL_SPEC S2.5: `can_execute` boolean | PASS | `can_execute: bool` field; tested across all scenarios (True on partial, False on all-fail) |
| **PreviewResult.evidence** | GLOBAL_SPEC S2.5: `evidence` list | PASS | `evidence: list[dict[str, Any]]` with `default_factory=list`; test `test_evidence_defaults_to_empty` verifies |
| **PreviewStepResult.status** | Spec: `completed/failed/deferred/skipped` | PASS | `status: Literal["completed", "failed", "deferred", "skipped"]`; test `test_all_valid_statuses` covers all four; `test_invalid_status_rejected` validates rejection |
| **PreviewRequest** | Spec: `plan + user_id + trace_id` | PASS | `plan: Plan`, `user_id: str` (min_length=1), `trace_id: str` (min_length=1); tests `test_rejects_empty_user_id`, `test_rejects_empty_trace_id` |
| **PreviewResult.plan_id** | 26-char ULID | PASS | `plan_id: str` with `min_length=26, max_length=26`; tests `test_plan_id_rejects_short_string`, `test_plan_id_rejects_long_string` |
| **Cache key pattern** | `preview:{user_id}:{plan_id}` | PASS | `preview_cache.py` line 32: `f"preview:{user_id}:{plan_id}"`; test `test_store_returns_cache_key` asserts `"preview:user-456:plan-123"` |
| **Plan schema (S2.3)** | `mode, role, after?, gate_id?` | PASS | Uses `shared.schemas.plan.PlanStep` directly; conftest builds plans with `mode`, `role`, `after`, `gate_id` |
| **Constraints** | `scopes`, `ttl_s` | PASS | Plan constraints passed through; not modified by PreviewOrchestrator |
| **No PlanIntegrityError** | No plan_hash references | PASS | Grep for `PlanIntegrityError\|plan_hash` in component: zero matches |

---

## Preview Evidence

### Preview Safety Scan

| Check | Result | Evidence |
|-------|--------|----------|
| **dry_run=True on all MCP invocations** | PASS | `preview_service.py` line 318: `resolved_args["dry_run"] = True`; test `test_mcp_invoked_with_dry_run` asserts `dry_run is True` for every call |
| **credentials=None on all MCP invocations** | PASS | `preview_service.py` line 331: `credentials=None`; test `test_mcp_invoked_with_credentials_none` asserts `credentials is None` for every call |
| **No write operations (HTTP POST/PUT/PATCH/DELETE)** | PASS | Grep for `\.write\|\.delete\|\.put\|\.post\|requests\.\(post\|put\|patch\|delete\)\|httpx\.\(post\|put\|patch\|delete\)`: zero matches |
| **No file mutations (open/os.remove/shutil)** | PASS | Grep for `open(\|os.remove\|os.unlink\|shutil.rmtree\|subprocess`: zero matches |
| **Non-previewable steps never dispatched** | PASS | Test `test_zero_mcp_calls_for_non_previewable`: `mock_mcp_client.invoke.assert_not_called()` for all-gated plan |
| **Booker/gated steps deferred, never executed** | PASS | Test `test_booker_deferred_with_gated_reason`: step 4 (Booker, gate_id) is `status="deferred"` |
| **llm_reasoning steps deferred** | PASS | Test `test_llm_reasoning_deferred`: step 3 (type=llm_reasoning) is `status="deferred"` |
| **policy_check steps deferred** | PASS | Test `test_policy_check_deferred`: step 4 (type=policy_check) is `status="deferred"` |
| **Only Redis SET is the sole side effect** | PASS | Cache adapter `store()` calls `self._redis.set(key, payload, ex=self._ttl_s)` only; all other operations are read-only |

### Backward Compatibility

| Check | Result | Evidence |
|-------|--------|----------|
| **shared/app.py: existing services untouched** | PASS | `git diff master -- shared/app.py` shows purely additive block (lines 210-224) after ExecuteOrchestrator; no existing lines modified |
| **shared/dependencies.py: existing accessors untouched** | PASS | `git diff master -- shared/dependencies.py` shows purely additive `get_preview_service()` function appended; no existing lines modified |
| **shared/schemas/ unchanged by PreviewOrchestrator** | PASS | PreviewOrchestrator has zero imports from or references to `shared/schemas/approval_token.schema.json`; component uses `shared.schemas.plan` and `shared.schemas.intent` which are unchanged |
| **No removed/renamed exports** | PASS | No existing shared module exports were removed or renamed |

### Code Quality

| Check | Result | Evidence |
|-------|--------|----------|
| **No files over 500 lines** | PASS | Largest file: `conftest.py` at 466 lines; `preview_service.py` at 411 lines |
| **Structured logging with plan_id correlation** | PASS | All service-level log calls include `extra={"plan_id": ...}`; test `test_plan_id_correlation_in_all_records` verifies |
| **No PII/secrets in logs** | PASS | Tests `test_no_step_args_in_logs` and `test_no_step_results_in_logs` verify step args and results are absent from log output |
| **Graceful degradation for Redis failures** | PASS | `PreviewCacheAdapter` wraps all Redis operations in try/except; test `test_preview_completes_without_redis` verifies preview succeeds with `redis_client=None` |
| **Graceful degradation for PluginRegistry failures** | PASS | `PreviewabilityChecker.is_previewable()` returns `False` on any exception; tests `test_tool_not_found_returns_false`, `test_operation_not_found_returns_false` |
| **Factory reads PREVIEW_CACHE_TTL_S env var** | PASS | `create_preview_service()` reads `os.environ.get("PREVIEW_CACHE_TTL_S", "900")`; test `test_factory_reads_ttl_from_env` sets env to 300 and asserts `_cache._ttl_s == 300` |

---

## Failures Requiring Implementer Action

None.

---

## Schema Drift

None.

---

## Warnings (Non-blocking)

- [W001] `shared/schemas/approval_token.schema.json` has an uncommitted modification (`plan_hash` renamed to `plan_id`) in the working tree. This change is NOT introduced by PreviewOrchestrator (zero references to `approval_token` in the component) but will be included if the working tree is committed as-is. Recommend committing it separately or verifying it belongs to this branch.
- [W002] Coverage for `preview_service.py` is 99.17% (1 uncovered line: line 281). This is the `_classify_step` fallback branch for `step.uses` being None in the previewability check. Non-blocking given the 99%+ coverage.
- [W003] Test count (75) exceeds LLD estimate (~65). This is a positive deviation indicating thorough test coverage.
