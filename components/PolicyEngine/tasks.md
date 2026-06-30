# Tasks: PolicyEngine

**Created**: 2026-04-02
**Branch**: `feat/v6.1-alignment`
**SPEC**: `specs/023-policyengine-deny-default/spec.md`
**LLD**: `components/PolicyEngine/LLD.md`

## Task Organization

Tasks are organized by implementation phase following the LLD architecture. PolicyEngine is a **library component** (no HTTP routes — consumed internally by ExecuteOrchestrator and Planner). It depends on SharedDatabaseAdapter (PostgreSQL) and Redis (optional cache).

All tasks are **complete** — implementation was delivered in commit `298c4e4` (v6.1 alignment).

---

## Phase 0: Setup & Scaffolding

### T000 -- Verify external packages are available ✅

**Files**: (read-only verification)
- `pyproject.toml`

**Description**: Confirm that `pydantic` (>=2.5.0), `sqlalchemy` (>=2.0), `asyncpg` (>=0.29.0), `redis[hiredis]` (>=5.0.0), and `ulid-py` (>=1.1.0) are listed in `pyproject.toml`. All present.

**Acceptance**: All packages confirmed present.

---

### T001 -- Create component directory skeleton ✅

**Files created**:
- `components/PolicyEngine/__init__.py`
- `components/PolicyEngine/domain/__init__.py`
- `components/PolicyEngine/domain/models.py`
- `components/PolicyEngine/adapters/__init__.py`
- `components/PolicyEngine/adapters/db.py`
- `components/PolicyEngine/adapters/cache.py`
- `components/PolicyEngine/service/__init__.py`
- `components/PolicyEngine/service/policy_service.py`
- `components/PolicyEngine/tests/__init__.py`
- `components/PolicyEngine/tests/conftest.py`

**Acceptance**: All directories and files exist. `ruff check` passes.

---

## Phase 1: Domain Models (Foundation)

### T100 -- Implement domain models ✅

**File**: `components/PolicyEngine/domain/models.py`

**Models**:
- `PolicyDB` — Pydantic model mapping to `policies` table (policy_id, name, version, scope, allowed_tools, allowed_roles, max_spawned_steps, require_approval, data_access, forbidden_actions, token_budget, created_at, updated_at)
- `PolicyAttestationDB` — Pydantic model mapping to `policy_attestations` table (attestation_id ULID, plan_id, plan_revision, spawned_by_step, new_steps, policy_id, policy_version, decision, attested_at)
- `SpawnRequest` — Input model for `evaluate_spawn()` (plan_id, plan_revision, spawning_step, proposed_steps min_length=1, current_step_count, plan_plugins, policy_ref)
- `PolicyEngineError` — Base exception
- `PolicyNotFoundError` — Policy does not exist
- `PolicyEvaluationError` — Internal evaluation error
- `AttestationError` — Attestation storage failure

**Acceptance**: All models validate. `SpawnRequest(proposed_steps=[])` raises ValidationError. `SpawnRequest(plan_id="short")` raises ValidationError.

---

### T101 -- Verify shared schema models ✅

**File**: `shared/schemas/policy.py` (read-only — already exists)

**Models verified**:
- `ReasoningConfig` — §2.3.1 LLM config
- `PolicyRule` — §2.9 policy definition (policy_id, name, version, scope, allowed_tools default=["*"], allowed_roles, max_spawned_steps 0-10, require_approval, data_access, forbidden_actions, token_budget default=8192)
- `PolicyDecision` — §2.9 evaluation result (allowed, reason, requires_approval, violations)
- `PolicyAttestation` — §2.4.1 audit record

**Acceptance**: PolicyEngine imports from `shared/schemas/policy.py` — does NOT duplicate models.

---

## Phase 2: Adapters

### T200 -- Implement PolicyDatabaseAdapter ✅

**File**: `components/PolicyEngine/adapters/db.py`

**Methods**:
- `store_policy(policy: PolicyDB) -> bool` — Upsert semantics (check existing, update or insert)
- `get_policy(policy_id: str, version: int | None) -> PolicyDB | None` — By ID, optionally at specific version
- `list_policies(scope: str | None) -> list[PolicyDB]` — All policies, optionally filtered by scope
- `store_attestation(attestation: PolicyAttestationDB) -> bool` — Insert attestation record
- `get_attestations_for_plan(plan_id: str) -> list[PolicyAttestationDB]` — All attestations for a plan
- `health_check() -> bool` — Database connectivity check

**Dependencies**: `SharedDatabaseAdapter` via `get_database_adapter()`, `PolicyTable` + `PolicyAttestationTable` from `shared/database/models.py`

**Acceptance**: All methods use `session.begin()` context manager for auto-commit/rollback.

---

### T201 -- Implement PolicyCacheAdapter ✅

**File**: `components/PolicyEngine/adapters/cache.py`

**Constants**: `CACHE_TTL_SECONDS = 300`, `CACHE_KEY_PREFIX = "policy_cache"`

**Methods**:
- `get_policy(policy_id: str, version: int) -> PolicyRule | None` — Cache lookup, None on miss or error
- `set_policy(policy_id: str, version: int, rule: PolicyRule) -> None` — Cache write, silent failure
- `invalidate(policy_id: str, version: int) -> None` — Remove cache entry, silent failure

**Key pattern**: `policy_cache:{policy_id}:{version}`
**Degradation**: All errors caught and logged as warnings — never propagated.

**Acceptance**: Redis unavailability does not raise exceptions.

---

## Phase 3: Service Implementation

### T300 -- Implement PolicyService.evaluate_spawn() ✅

**File**: `components/PolicyEngine/service/policy_service.py`

**Core flow**:
1. Log request (plan_id, spawning_step, policy_ref)
2. Resolve policy: policy_ref → get_policy(); not found → DENIED (deny-by-default)
3. Validate count limits: proposed > max_spawned_steps, proposed > 10 (hard cap), current + proposed > 100 (hard cap)
4. Per-step checks: can_spawn=true → violation; role check; Booker HITL enforcement; tool check (wildcard or explicit); plugin membership; forbidden actions
5. Violations → DENIED; no violations → ALLOWED with requires_approval flag

**Hard caps**: `_MAX_STEPS_PER_SPAWN = 10`, `_MAX_TOTAL_PLAN_STEPS = 100`

**Acceptance**: deny-by-default for None policy_ref. Booker always forces requires_approval=true. Atomic: one violation denies entire request.

---

### T301 -- Implement PolicyService CRUD + cache integration ✅

**File**: `components/PolicyEngine/service/policy_service.py`

**Methods**:
- `get_policy(policy_id, version=None)` — Cache-first when version specified; DB-only for latest; populate cache on DB hit
- `create_policy(rule)` — Store to DB, invalidate cache, return rule
- `list_policies(scope=None)` — Delegate to DB adapter

**Acceptance**: Cache hit skips DB. Cache miss populates cache after DB lookup. Unversioned reads bypass cache.

---

### T302 -- Implement PolicyService.create_attestation() ✅

**File**: `components/PolicyEngine/service/policy_service.py`

**Flow**:
1. Generate ULID for attestation_id
2. Timestamp with `datetime.now(UTC).isoformat()`
3. Build `PolicyAttestation` (shared schema) and `PolicyAttestationDB` (domain model)
4. Store via `db_adapter.store_attestation()`
5. On failure → raise `AttestationError`
6. Return `PolicyAttestation`

**Acceptance**: Each attestation gets unique ULID. DB failure raises AttestationError.

---

### T303 -- Implement factory function ✅

**File**: `components/PolicyEngine/service/policy_service.py`

**Function**: `create_policy_service(db_adapter, redis_client=None) -> PolicyService`

**Acceptance**: Called in `shared/app.py` lifespan. Redis is optional.

---

## Phase 4: DI Wiring

### T400 -- Wire PolicyEngine into shared/app.py ✅

**File**: `shared/app.py`

**Wiring**:
```python
policy_db = PolicyDatabaseAdapter()
app.state.policy_service = create_policy_service(db_adapter=policy_db, redis_client=None)
# After Redis init:
app.state.policy_service._cache = PolicyCacheAdapter(intake_redis)
```

**Acceptance**: `app.state.policy_service` available at startup.

---

### T401 -- Wire get_policy_service into shared/dependencies.py ✅

**File**: `shared/dependencies.py`

**Function**: `get_policy_service(request) -> Any` — returns `request.app.state.policy_service`

**Acceptance**: Function exported and usable by FastAPI `Depends()`.

---

## Phase 5: Database Models

### T500 -- Verify SQLAlchemy models ✅

**File**: `shared/database/models.py` (read-only — already exists)

**Models verified**:
- `PolicyTable` (line 439) — maps to `policies`
- `PolicyAttestationTable` (line 468) — maps to `policy_attestations` with indexes on plan_id, policy_id, attested_at

**Acceptance**: SQLAlchemy models match DDL in LLD §6.

---

## Phase 6: Tests

### T600 -- Implement test fixtures (conftest.py) ✅

**File**: `components/PolicyEngine/tests/conftest.py`

**Fixtures**:
- `DEFAULT_POLICY`, `RESTRICTIVE_POLICY`, `SYSTEM_POLICY` — Sample PolicyRule instances
- `DEFAULT_POLICY_DB` — Sample PolicyDB instance
- `SAMPLE_PLAN_ID` — Valid 26-char ULID
- `make_spawn_request()` — Factory with sensible defaults
- `mock_db_adapter` — AsyncMock(spec=PolicyDatabaseAdapter)
- `mock_cache_adapter` — Always-miss cache
- `mock_cache_hit_adapter` — Cache hit for default policy
- `policy_service` — Fully wired PolicyService with mocks
- `policy_service_with_cache` — PolicyService with cache hits

**Acceptance**: All fixtures import cleanly.

---

### T601 -- Implement unit tests (test_unit.py) ✅

**File**: `components/PolicyEngine/tests/test_unit.py`

**Test classes** (24 tests):
- `TestDenyByDefault` — No policy_ref denies, policy not found denies
- `TestAllowedTools` — Wildcard allows any, explicit list check, tool in list permits
- `TestAllowedRoles` — Role not in list denies, role in list permits, empty list permits any
- `TestForbiddenActions` — Forbidden action denies, non-forbidden permits
- `TestMaxSpawnedSteps` — Exceeds denies, within permits
- `TestTotalPlanSize` — Exceeds 100 denies, at limit permits
- `TestRecursiveSpawning` — can_spawn=true denies, can_spawn=false permits
- `TestBookerHITL` — Booker forces approval, non-Booker respects policy
- `TestPluginConstraint` — Tool not in plan_plugins denies, tool in plan_plugins permits
- `TestMultipleViolations` — One invalid denies all, all valid permits
- `TestHappyPath` — Valid request allowed, decision includes policy version

**Acceptance**: All 24 tests pass.

---

### T602 -- Implement service tests (test_service.py) ✅

**File**: `components/PolicyEngine/tests/test_service.py`

**Test classes** (17 tests):
- `TestCacheFirstLookup` — Cache hit skips DB, cache miss falls to DB, DB miss returns None, no version skips cache, Redis unavailable falls to DB
- `TestAttestationCreation` — Success stores to DB, generates unique ULIDs, DB failure raises AttestationError, attestation contains decision
- `TestPolicyCRUD` — Create stores and invalidates cache, list no filter, list with scope, get specific version, get latest version, create returns same rule
- `TestEvaluateSpawnWithCache` — Resolves policy, populates cache on miss

**Acceptance**: All 17 tests pass.

---

### T603 -- Implement contract tests (test_contract.py) ✅

**File**: `components/PolicyEngine/tests/test_contract.py`

**Test classes** (13 tests):
- `TestPolicyRuleConformance` — Default validates, scope values, wildcard tools, default token budget, max_spawned_steps range
- `TestPolicyDecisionConformance` — Allowed, denied with violations, requires_approval
- `TestPolicyAttestationConformance` — ULID format, requires plan_id
- `TestSpawnRequestEdgeCases` — Empty proposed_steps rejected, 0 current valid, plan_id must be 26 chars

**Acceptance**: All 13 tests pass.

---

### T604 -- Implement observability tests (test_observability.py) ✅

**File**: `components/PolicyEngine/tests/test_observability.py`

**Test class** (5 tests):
- `TestEvaluateSpawnLogging` — Logs plan_id, logs policy_id, logs ALLOWED/DENIED, denied logged with violations, no PII in logs

**Acceptance**: All 5 tests pass.

---

## Summary

| Phase | Tasks | Status |
|-------|-------|--------|
| Phase 0: Setup | T000-T001 | ✅ Complete |
| Phase 1: Domain Models | T100-T101 | ✅ Complete |
| Phase 2: Adapters | T200-T201 | ✅ Complete |
| Phase 3: Service | T300-T303 | ✅ Complete |
| Phase 4: DI Wiring | T400-T401 | ✅ Complete |
| Phase 5: Database | T500 | ✅ Complete |
| Phase 6: Tests | T600-T604 | ✅ Complete |
| **Total** | **16 tasks** | **All complete** |

### Test Results

```
59 passed in 1.50s
ruff check: All checks passed
```

### Implementation Order (executed)

```
T000 → T001 → T100 → T101 → T200 → T201 → T300 → T301 → T302 → T303 → T400 → T401 → T500 → T600 → T601 → T602 → T603 → T604
```
