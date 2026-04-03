# Feature Specification: PolicyEngine — Deny-by-Default Policy Evaluation

**Feature Branch**: `feat/policyengine-deny-default`
**Created**: 2026-04-02
**Status**: Draft
**Input**: User description: "PolicyEngine: deny-by-default policy evaluation, spawn authorization, and runtime attestations"

## Overview

PolicyEngine is the **safety moat** of the Personal-Agent system. It governs all runtime LLM decisions by evaluating spawn requests against stored policy rules, enforcing a deny-by-default model where any action without an explicit matching policy is rejected. When Reasoner steps propose new child steps at runtime, PolicyEngine atomically validates every constraint (tools, roles, step counts, forbidden actions, plugin membership) and produces either an allow or deny decision. Allowed spawns receive an immutable PolicyAttestation audit record for authorization tracking. Booker-role steps always require human approval regardless of policy configuration.

PolicyEngine is a **Domain/Service Layer library component** — it has no HTTP routes and is consumed internally by ExecuteOrchestrator.

## Goals

- Enforce deny-by-default: no implicit permissions, every spawn must match an explicit PolicyRule
- Provide atomic all-or-nothing constraint evaluation for spawn requests
- Create immutable audit trail (PolicyAttestation) for every approved spawn
- Enforce non-overridable HITL approval for Booker-role steps
- Prevent recursive spawning (spawned steps cannot spawn more steps)
- Support cache-first lookups with graceful degradation when Redis is unavailable
- Provide policy CRUD operations for rule management

## Non-Goals

- HTTP API endpoints for policy management (library component, consumed internally)
- Policy versioning strategies (rollback, canary, A/B testing)
- Dynamic hot-reload of policies without restart
- Rate limiting or throttling (handled by API gateway)
- Credential management (CredentialVault's responsibility)
- Plan-time validation (Planner's plan_validator handles that)

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Deny-by-Default Spawn Evaluation (Priority: P1)

ExecuteOrchestrator runs a hybrid plan where a Tier 2 Reasoner step proposes spawning new child steps. PolicyEngine evaluates the spawn request against stored rules and denies it if no matching policy exists.

**Why this priority**: This is the core safety guarantee — without deny-by-default, arbitrary runtime modifications could bypass all planning-time constraints.

**Independent Test**: Can be tested by creating a SpawnRequest with no `policy_ref` or a non-existent policy_ref and verifying the decision is `allowed=False`.

**Acceptance Scenarios**:

1. **Given** a SpawnRequest with `policy_ref=None`, **When** `evaluate_spawn()` is called, **Then** the decision is `allowed=False` with reason containing "deny-by-default".
2. **Given** a SpawnRequest with `policy_ref="nonexistent-policy"`, **When** `evaluate_spawn()` is called, **Then** the decision is `allowed=False` because no matching rule exists.
3. **Given** a SpawnRequest with a valid `policy_ref` matching a stored PolicyRule, **When** all constraints pass, **Then** the decision is `allowed=True` with the matched policy_id in the reason.

---

### User Story 2 — Atomic Constraint Validation (Priority: P1)

When PolicyEngine evaluates a spawn request, it checks ALL constraints atomically. If any single proposed step violates any rule, the entire spawn is denied with a list of all violations.

**Why this priority**: Partial enforcement would create security gaps where some constraints are checked but others bypassed.

**Independent Test**: Submit a SpawnRequest with multiple proposed steps where one violates a constraint, verify all are denied.

**Acceptance Scenarios**:

1. **Given** a PolicyRule with `allowed_tools=["google.calendar"]`, **When** a proposed step uses `"slack.messaging"`, **Then** the decision is denied with violation "tool not in allowed_tools".
2. **Given** a PolicyRule with `allowed_roles=["Fetcher", "Analyzer"]`, **When** a proposed step has `role="Booker"` and Booker is not in the list, **Then** the decision is denied with violation "role not in allowed_roles".
3. **Given** a PolicyRule with `forbidden_actions=["delete_all_events"]`, **When** a proposed step calls `"delete_all_events"`, **Then** the decision is denied with violation "forbidden action".
4. **Given** a PolicyRule with `max_spawned_steps=3`, **When** 4 steps are proposed, **Then** the decision is denied with violation "exceeds max_spawned_steps".
5. **Given** `current_step_count=95` and 6 proposed steps, **When** evaluated, **Then** denied because total would exceed the hard cap of 100 steps.
6. **Given** a proposed step with `can_spawn=true`, **When** evaluated, **Then** denied with violation "recursive spawning not allowed".
7. **Given** a proposed step using a tool not in `plan_plugins`, **When** evaluated, **Then** denied with violation "tool not in plan_plugins".

---

### User Story 3 — Booker HITL Enforcement (Priority: P1)

Any spawn that includes a Booker-role step must flag `requires_approval=True` in the PolicyDecision, regardless of the policy's `require_approval` setting. This is a non-overridable safety constraint.

**Why this priority**: Booker steps perform write operations (create, update, delete). Silently spawning Booker steps would bypass the human-in-the-loop safety model.

**Independent Test**: Submit a SpawnRequest with a Booker-role proposed step under a policy with `require_approval=False`, verify `requires_approval=True` in decision.

**Acceptance Scenarios**:

1. **Given** a PolicyRule with `require_approval=False`, **When** a proposed step has `role="Booker"`, **Then** the decision has `requires_approval=True` (overridden).
2. **Given** a PolicyRule with `require_approval=True`, **When** a proposed step has `role="Fetcher"`, **Then** `requires_approval=True` (policy setting honored).
3. **Given** a PolicyRule with `require_approval=False`, **When** all proposed steps are non-Booker roles, **Then** `requires_approval=False`.

---

### User Story 4 — Runtime Attestation Creation (Priority: P1)

When PolicyEngine approves a spawn, it creates an immutable PolicyAttestation audit record stored in PostgreSQL. This record provides the authorization trail for runtime plan modifications.

**Why this priority**: Attestations provide the audit trail proving that runtime plan modifications were authorized by policy. Without them, spawned steps would be unaccountable.

**Independent Test**: Approve a spawn, verify a PolicyAttestation is created with correct fields and stored in the database.

**Acceptance Scenarios**:

1. **Given** an approved spawn decision, **When** `create_attestation()` is called, **Then** a PolicyAttestation is created with a unique 26-char ULID `attestation_id`.
2. **Given** an attestation request, **When** stored, **Then** the attestation contains `plan_id`, `plan_revision`, `spawned_by_step`, `new_steps`, `policy_id`, `policy_version`, `decision`, and `attested_at` (ISO 8601).
3. **Given** a database failure during attestation creation, **When** the insert fails, **Then** `AttestationError` is raised.
4. **Given** multiple spawn events for the same plan, **When** attestations are queried, **Then** all are returned ordered by `attested_at`.

---

### User Story 5 — Cache-First Policy Lookups (Priority: P2)

PolicyEngine uses Redis caching for hot-path policy lookups. When a versioned policy is requested, it checks Redis first (5-minute TTL). On cache miss, it reads from PostgreSQL and populates the cache. If Redis is unavailable, it gracefully degrades to DB-only lookups.

**Why this priority**: Performance optimization for the execution hot path — PolicyEngine is called for every spawn evaluation during plan execution.

**Independent Test**: Mock Redis as unavailable, verify PolicyEngine still functions correctly using DB-only lookups.

**Acceptance Scenarios**:

1. **Given** a policy cached in Redis, **When** `get_policy(policy_id, version)` is called, **Then** the cached value is returned without hitting the database.
2. **Given** a cache miss, **When** the policy exists in PostgreSQL, **Then** the policy is fetched from DB and written to cache with 5m TTL.
3. **Given** Redis is down (ConnectionError), **When** `get_policy()` is called, **Then** it falls through to DB without raising an exception.
4. **Given** a policy is created or updated, **When** `create_policy()` succeeds, **Then** the corresponding cache entry is invalidated.

---

### User Story 6 — Policy CRUD Operations (Priority: P2)

PolicyEngine provides methods to create, retrieve, and list policy rules. These are consumed by system administrators or setup scripts to configure the policy ruleset.

**Why this priority**: Policies must be manageable — creating, updating, and querying rules is necessary for system configuration.

**Independent Test**: Create a policy, retrieve it by ID, list all policies filtered by scope.

**Acceptance Scenarios**:

1. **Given** a valid PolicyRule, **When** `create_policy()` is called, **Then** the rule is stored in PostgreSQL and the cache entry is invalidated.
2. **Given** a stored policy, **When** `get_policy(policy_id)` is called without version, **Then** the latest version is returned.
3. **Given** a stored policy, **When** `get_policy(policy_id, version=2)` is called, **Then** exactly version 2 is returned (or None if not found).
4. **Given** multiple policies with different scopes, **When** `list_policies(scope="step")` is called, **Then** only step-scoped policies are returned.

---

### User Story 7 — Structured Observability (Priority: P3)

All PolicyEngine operations produce structured log output correlated by `plan_id` and `policy_id`. Logs never contain PII or secrets.

**Why this priority**: Observability is essential for debugging and auditing but is not a functional requirement.

**Independent Test**: Run `evaluate_spawn()`, verify log output contains `plan_id` and no PII.

**Acceptance Scenarios**:

1. **Given** an `evaluate_spawn()` call, **When** the decision is logged, **Then** the log includes `plan_id` and `policy_id`.
2. **Given** a denied decision, **When** logged, **Then** the log includes the violations list.
3. **Given** any PolicyEngine operation, **When** log output is inspected, **Then** no PII (emails, names, credentials) appears.

---

### Edge Cases

- What happens when `proposed_steps` is empty? → `SpawnRequest` validation rejects it (`min_length=1`).
- What happens when `plan_plugins` is empty but `allowed_tools=["*"]`? → Denied, because tool is not in the empty `plan_plugins` list (plugin membership checked independently of tool allowlist).
- What happens when the same policy_id is stored twice? → Upsert semantics — the new version replaces the old.
- What happens when Redis cache contains stale data? → 5m TTL ensures staleness is bounded; cache invalidation on writes provides consistency for the common case.
- What happens when `max_spawned_steps=0`? → Any spawn is denied (0 steps allowed).
- What happens when multiple proposed steps have violations? → All violations are collected and returned in the `violations` list.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: PolicyEngine MUST deny any spawn request with no matching policy rule (deny-by-default).
- **FR-002**: PolicyEngine MUST evaluate ALL constraints atomically — if any proposed step violates any rule, the entire spawn is denied.
- **FR-003**: PolicyEngine MUST check tool membership against `allowed_tools` (wildcard `"*"` permits all).
- **FR-004**: PolicyEngine MUST check role membership against `allowed_roles` (empty list permits all roles).
- **FR-005**: PolicyEngine MUST reject spawn requests exceeding `max_spawned_steps` per spawn operation.
- **FR-006**: PolicyEngine MUST enforce a hard cap of 10 steps per spawn (`_MAX_STEPS_PER_SPAWN`) and 100 total plan steps (`_MAX_TOTAL_PLAN_STEPS`).
- **FR-007**: PolicyEngine MUST reject any proposed step that has `can_spawn=true` (no recursive spawning).
- **FR-008**: PolicyEngine MUST reject any proposed step whose tool is not in the plan's `plugins` list.
- **FR-009**: PolicyEngine MUST reject any proposed step whose `call` matches a `forbidden_actions` entry.
- **FR-010**: PolicyEngine MUST force `requires_approval=True` for any spawn containing a Booker-role step, regardless of the policy's `require_approval` setting.
- **FR-011**: PolicyEngine MUST create an immutable PolicyAttestation with a unique ULID for every approved spawn.
- **FR-012**: PolicyEngine MUST store attestations in the `policy_attestations` PostgreSQL table.
- **FR-013**: PolicyEngine MUST support cache-first policy lookups via Redis with 5-minute TTL.
- **FR-014**: PolicyEngine MUST gracefully degrade to DB-only lookups when Redis is unavailable.
- **FR-015**: PolicyEngine MUST invalidate the Redis cache entry when a policy is created or updated.
- **FR-016**: PolicyEngine MUST provide CRUD operations: `create_policy()`, `get_policy()`, `list_policies()`.
- **FR-017**: PolicyEngine MUST produce structured log output with `plan_id` and `policy_id` correlation.
- **FR-018**: PolicyEngine MUST NOT log PII or secrets.

### Key Entities

- **PolicyRule**: Defines constraints for spawn evaluation — scope (step/role/system), allowed tools, allowed roles, max steps, forbidden actions, token budget, approval requirements.
- **PolicyDecision**: Evaluation result — allowed/denied, requires_approval flag, human-readable reason, list of violations.
- **PolicyAttestation**: Immutable audit record for approved spawns — links plan_id, plan_revision, spawning step, new steps, policy used, and the decision. 26-char ULID identifier.
- **SpawnRequest**: Input for evaluation — plan context (id, revision, step count, plugins) plus the proposed steps and optional policy_ref.
- **PolicyDB / PolicyAttestationDB**: Database mapping models for the `policies` and `policy_attestations` tables.

### Internal Protocols

- **PolicyDatabaseAdapter**: Async SQLAlchemy 2.0 adapter for policy and attestation CRUD against PostgreSQL.
- **PolicyCacheAdapter**: Redis adapter with graceful degradation for hot-path policy lookups.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `evaluate_spawn()` p95 latency < 50ms for cached policy lookups (Redis available).
- **SC-002**: `evaluate_spawn()` p95 latency < 200ms for DB-only lookups (Redis unavailable).
- **SC-003**: 100% of spawn requests without a matching policy are denied (deny-by-default coverage).
- **SC-004**: 100% of Booker-role spawns have `requires_approval=True` regardless of policy setting.
- **SC-005**: All PolicyAttestation records are queryable by `plan_id` for audit review.
- **SC-006**: Zero PII or credential values in structured log output.
- **SC-007**: Test coverage ≥ 80% for all PolicyEngine code paths.

## Interfaces & Contracts

### Service Interface (consumed by ExecuteOrchestrator)

```python
class PolicyService:
    async def evaluate_spawn(self, request: SpawnRequest) -> PolicyDecision
    async def create_attestation(
        self, plan_id, plan_revision, spawned_by_step,
        new_steps, policy_id, policy_version, decision,
    ) -> PolicyAttestation
    async def get_policy(self, policy_id, version=None) -> PolicyRule | None
    async def create_policy(self, rule: PolicyRule) -> PolicyRule
    async def list_policies(self, scope=None) -> list[PolicyRule]
```

### Factory Function

```python
def create_policy_service(
    db_adapter: PolicyDatabaseAdapter,
    redis_client: object | None = None,
) -> PolicyService
```

### SpawnRequest (input)

```python
{
    "plan_id": "01HXYZ...",           # 26-char ULID
    "plan_revision": 1,               # Current revision
    "spawning_step": 3,               # Step proposing spawn
    "proposed_steps": [{"role": "Fetcher", "uses": "google.calendar", ...}],
    "current_step_count": 5,
    "plan_plugins": ["google.calendar", "slack.messaging"],
    "policy_ref": "default-reasoning"  # Optional, null → deny
}
```

### PolicyDecision (output)

```python
{
    "allowed": true,
    "requires_approval": false,
    "reason": "Spawn approved under policy default-reasoning v1",
    "violations": []
}
```

### PolicyAttestation (audit record)

```python
{
    "attestation_id": "01HXYZ...",     # 26-char ULID
    "plan_id": "01HXYZ...",
    "plan_revision": 2,
    "spawned_by_step": 3,
    "new_steps": [{"step": 6, "role": "Fetcher", ...}],
    "policy_id": "default-reasoning",
    "policy_version": 1,
    "decision": {"allowed": true, ...},
    "attested_at": "2026-04-02T12:00:00+00:00"
}
```

Reference: `docs/architecture/GLOBAL_SPEC.md` v3.0 §2.9, §2.4.1, §8

## Component Mapping

- **Target**: `components/PolicyEngine/`
- Files:
  - `domain/models.py` — PolicyDB, PolicyAttestationDB, SpawnRequest, exceptions
  - `service/policy_service.py` — PolicyService (evaluate_spawn, create_attestation, CRUD)
  - `adapters/db.py` — PolicyDatabaseAdapter (SQLAlchemy 2.0 async)
  - `adapters/cache.py` — PolicyCacheAdapter (Redis with graceful degradation)
  - `tests/conftest.py` — Fixtures, mock adapters, sample policies
  - `tests/test_unit.py` — 24 unit tests (constraint checks)
  - `tests/test_service.py` — 17 service tests (cache/CRUD/attestation)
  - `tests/test_contract.py` — 13 contract tests (model conformance)
  - `tests/test_observability.py` — 5 observability tests (logging, PII)
- Shared schemas (already exist, not modified):
  - `shared/schemas/policy.py` — PolicyRule, PolicyDecision, PolicyAttestation, ReasoningConfig
  - `shared/database/models.py` — PolicyTable, PolicyAttestationTable

## Dependencies & Risks

### Dependencies

- **Internal**: SharedDatabaseAdapter (PostgreSQL sessions), Redis client (optional)
- **External**: PostgreSQL 16 (policies + policy_attestations tables), Redis 7 (optional cache)
- **Consumed by**: ExecuteOrchestrator (evaluate_spawn + create_attestation during plan execution)
- **Python packages**: sqlalchemy, asyncpg, redis[hiredis], pydantic, ulid-py

### Risks

- **Stale cache**: Redis cache TTL of 5 minutes means policy updates take up to 5m to propagate. Mitigation: cache invalidation on write + bounded TTL.
- **DB unavailable**: If PostgreSQL is down, PolicyEngine cannot evaluate spawns. Mitigation: fail-closed (deny all) — consistent with deny-by-default model.
- **Redis unavailable**: Graceful degradation to DB-only lookups. Mitigation: all Redis errors caught and logged as warnings.

## Non-Functional Requirements

- Inherit baseline (Preview p95 < 800ms; Execute p95 < 2s; structured logs; no secrets/PII)
- **Deltas**:
  - `evaluate_spawn()` p95 < 50ms with cache hit, < 200ms with DB fallback
  - `create_attestation()` p95 < 100ms (single DB insert)
  - Zero secrets/PII in logs (verified by test)
  - Graceful degradation: Redis failure does not block execution

## Open Questions

- **OQ-1**: Should PolicyEngine expose HTTP endpoints for policy CRUD (admin API), or remain a library component consumed only internally? Current: library-only.
- **OQ-2**: Should policy versioning support rollback (reverting to a previous version)? Current: simple upsert, no rollback.
- **OQ-3**: Should there be a system-wide default policy that applies when no `policy_ref` is provided? Current: deny-by-default (no fallback policy).

## Conformance

This work conforms to `docs/architecture/GLOBAL_SPEC.md` v3.0:
- §2.9 PolicyEngine Contract (deny-by-default, spawn evaluation, constraint checks)
- §2.4.1 PolicyAttestation (immutable audit records for spawned steps)
- §8 Safety & Governance (Booker HITL enforcement, no recursive spawning)
- §1 Adaptive execution (deterministic plans with PolicyEngine-bounded runtime modifications)
