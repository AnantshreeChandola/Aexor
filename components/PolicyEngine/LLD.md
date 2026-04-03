# Low-Level Design — PolicyEngine

**Component**: `components/PolicyEngine/`
**Layer**: Domain/Service Layer
**Type**: Library component (no HTTP routes — consumed internally)
**Created**: 2026-04-02
**SPEC**: `specs/023-policyengine-deny-default/spec.md`

---

## 1. Purpose & Scope

PolicyEngine is the **safety boundary** for all runtime LLM decisions. It evaluates whether a Reasoner step's proposed spawned steps are allowed under the governing policy rule. When no policy matches, it falls back to requiring user approval (HITL gate) and learns from approved decisions for future auto-approval.

**Responsibilities:**
- Evaluate spawn requests against stored PolicyRule constraints (fallback to user approval when unmatched)
- Perform atomic all-or-nothing constraint validation (tools, roles, step counts, forbidden actions, plugin membership)
- Force `requires_approval=true` for any Booker-role spawned step (non-overridable HITL)
- Reject recursive spawning (spawned steps cannot have `can_spawn=true`)
- Create immutable PolicyAttestation audit records for approved spawns
- Provide cache-first policy lookups (Redis 5m TTL) with graceful DB fallback
- Manage policy CRUD (create, retrieve, list)

**Out of scope:**
- HTTP API endpoints (library component, consumed internally)
- Plan-time validation (Planner's plan_validator handles that)
- Credential management (CredentialVault's responsibility)
- Approval token issuance (ApprovalGate)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v3.0 | §1 Adaptive (PolicyEngine-bounded reasoning), §2.3.2 Spawned Step Rules, §2.4.1 PolicyAttestation, §2.9 PolicyEngine Contract |
| MODULAR_ARCHITECTURE.md | v2.0 | §1 Domain/Service Layer, §3 Table Ownership (policies, policy_attestations), §3 Redis Keys (policy_cache), §4 Dependency Matrix |
| Project_HLD.md | v6.1 | §2a-§2c Deterministic planning with adaptive execution, PolicyEngine examples |
| SHARED_INFRASTRUCTURE.md | v1.0.0 | §1.1 SharedDatabaseAdapter, §4.1 shared schemas |

---

## 3. Architecture Overview

### Layer Placement

```
Domain / Service Layer
├── ContextRAG       (context assembler)
├── Planner          (plan generation)
├── PluginRegistry   (tool catalog)
├── PlanWriter       (outcome persistence)
├── PolicyEngine     ← THIS COMPONENT
└── Audit            (cross-cutting concern)
```

### Blast Radius Analysis

| Failure | Impact | Containment |
|---------|--------|-------------|
| PostgreSQL down | Cannot evaluate spawns, cannot store attestations | Fail-closed: deny all spawns (consistent with deny-by-default) |
| Redis unavailable | Cache misses, slower lookups | Graceful degradation: fall through to DB-only lookups |
| PolicyEngine bug | Incorrect spawn evaluation | All decisions logged with policy_id + violations for audit |
| Stale cache | Policy updates delayed up to 5m | Bounded by TTL; cache invalidated on writes |

### Component Boundaries

```
                    ┌─────────────────────────┐
                    │     PolicyEngine         │
                    │                          │
 SpawnRequest ────→ │  ┌─── Policy Resolver ──┐│
                    │  │ policy_ref → DB/cache ││
                    │  └────────┬──────────────┘│
                    │           │               │
                    │  ┌────────▼──────────────┐│
                    │  │ Constraint Evaluator  ││──→ Redis (cache)
                    │  │  - tool check         ││──→ PostgreSQL (policies)
                    │  │  - role check         ││
                    │  │  - count limits       ││
                    │  │  - forbidden actions   ││
                    │  │  - plugin membership  ││
                    │  │  - recursive spawn    ││
                    │  │  - Booker HITL        ││
                    │  └────────┬──────────────┘│
                    │           │               │
                    │  ┌────────▼──────────────┐│
                    │  │ Attestation Manager   ││──→ PostgreSQL (policy_attestations)
                    │  │  ULID generation       ││
                    │  │  Audit persistence     ││
                    │  └───────────────────────┘│
                    └─────────────────────────┘
                              │
                    PolicyDecision / PolicyAttestation
```

### Dependency Contract Table

| Dependency | Method | Input | Output | Error Handling |
|-----------|--------|-------|--------|----------------|
| SharedDatabaseAdapter | `get_session()` | — | AsyncSession | DB errors → fail-closed (deny) |
| Redis client | `get()`, `set()`, `delete()` | key, value | bytes or None | Errors → log warning, fall through to DB |
| Consumed by: ExecuteOrchestrator | `evaluate_spawn(request)` | SpawnRequest | PolicyDecision | Fallback to user approval on no match |
| Consumed by: ExecuteOrchestrator | `create_attestation(...)` | plan_id, decision, etc. | PolicyAttestation | AttestationError raised to caller |
| Consumed by: ExecuteOrchestrator | `learn_from_approval(role, tool)` | role, tool | PolicyRule | Delegates to create_policy |
| Consumed by: Planner | `get_policy(policy_id)` | policy_id | PolicyRule or None | None → caller decides |

---

## 4. Interfaces

### 4.1 Service Interface

```python
class PolicyService:
    """Core PolicyEngine service.

    Evaluates spawn requests against policy rules, with fallback to
    user approval when no matching policy is found. Uses cache-first
    lookups with DB fallback.
    """

    async def evaluate_spawn(self, request: SpawnRequest) -> PolicyDecision:
        """Evaluate whether a step may spawn child steps.

        Resolution order: explicit policy_ref → learned policy → user approval.
        If no policy is found, falls back to requiring user approval.

        Returns:
            PolicyDecision with allowed/denied result and reason.
        """

    async def learn_from_approval(
        self, role: str, tool: str, *, max_spawned_steps=3, token_budget=8192
    ) -> PolicyRule:
        """Create a learned policy from a user-approved spawn.

        Called after user approves a gated spawn where decision.policy_matched
        is False. Future spawns with the same role+tool auto-approve.
        """

    async def create_attestation(
        self,
        plan_id: str,
        plan_revision: int,
        spawned_by_step: int,
        new_steps: list[dict],
        policy_id: str,
        policy_version: int,
        decision: PolicyDecision,
    ) -> PolicyAttestation:
        """Create and store an attestation for an approved spawn.

        Raises:
            AttestationError: If storage fails.
        """

    async def get_policy(
        self, policy_id: str, version: int | None = None
    ) -> PolicyRule | None:
        """Cache-first policy lookup. Returns None if not found."""

    async def create_policy(self, rule: PolicyRule) -> PolicyRule:
        """Store a policy. Invalidates cache. Returns the stored rule."""

    async def list_policies(self, scope: str | None = None) -> list[PolicyRule]:
        """List policies, optionally filtered by scope."""
```

### 4.2 Factory Function

```python
def create_policy_service(
    db_adapter: PolicyDatabaseAdapter,
    redis_client: object | None = None,
) -> PolicyService:
    """Create PolicyService with all dependencies.

    Called once during app lifespan startup in shared/app.py.
    """
```

### 4.3 Consumer Contracts

**ExecuteOrchestrator** (primary consumer):
- Calls `evaluate_spawn(SpawnRequest)` during `_handle_spawned_steps()` when a Tier 2 Reasoner proposes new steps
- Calls `create_attestation(...)` when spawn is approved, before inserting steps into graph
- Calls `evaluate_spawn(SpawnRequest)` during `_execute_policy_check()` for `type="policy_check"` steps
- Expects: fast response (<50ms cached), fail-closed on errors

**Planner** (secondary consumer):
- Calls `get_policy(policy_id)` to snapshot `policy_version` at plan creation time
- Calls `list_policies()` for policy_ref assignment during plan generation
- Expects: None on missing policy (uses default), read-only

---

## 5. Data Model

### 5.1 Domain Models (`domain/models.py`)

```python
class PolicyDB(BaseModel):
    """Pydantic model mapping to the policies table."""
    policy_id: str = Field(..., max_length=128)
    name: str = Field(..., max_length=256)
    version: int = Field(default=1, ge=1)
    scope: str = Field(...)  # "step" | "role" | "system"
    allowed_tools: list[str] = Field(default_factory=lambda: ["*"])
    allowed_roles: list[str] = Field(default_factory=list)
    max_spawned_steps: int = Field(default=3, ge=0, le=10)
    require_approval: bool = Field(default=False)
    data_access: list[str] = Field(default_factory=lambda: ["tier1"])
    forbidden_actions: list[str] = Field(default_factory=list)
    token_budget: int = Field(default=8192, ge=256)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PolicyAttestationDB(BaseModel):
    """Pydantic model mapping to the policy_attestations table."""
    attestation_id: str = Field(..., min_length=26, max_length=26)  # ULID
    plan_id: str = Field(..., min_length=26, max_length=26)          # ULID
    plan_revision: int = Field(..., ge=1)
    spawned_by_step: int = Field(..., ge=1)
    new_steps: list[dict[str, Any]] = Field(...)
    policy_id: str = Field(..., max_length=128)
    policy_version: int = Field(..., ge=1)
    decision: dict[str, Any] = Field(...)
    attested_at: datetime | None = None


class SpawnRequest(BaseModel):
    """Input to PolicyService.evaluate_spawn()."""
    plan_id: str = Field(..., min_length=26, max_length=26)
    plan_revision: int = Field(..., ge=1)
    spawning_step: int = Field(..., ge=1)
    proposed_steps: list[dict[str, Any]] = Field(..., min_length=1)
    current_step_count: int = Field(..., ge=0)
    plan_plugins: list[str] = Field(default_factory=list)
    policy_ref: str | None = Field(default=None)
```

### 5.2 Shared Schema Models (`shared/schemas/policy.py`)

These models are the GLOBAL_SPEC §2.9 canonical contracts. PolicyEngine imports them — it does NOT duplicate them.

```python
# shared/schemas/policy.py — already exists, not modified
class ReasoningConfig(BaseModel): ...   # §2.3.1 — LLM config for reasoning steps
class PolicyRule(BaseModel): ...         # §2.9   — policy definition
class PolicyDecision(BaseModel): ...     # §2.9   — evaluation result
class PolicyAttestation(BaseModel): ...  # §2.4.1 — audit record
```

### 5.3 Error Classes (`domain/models.py`)

```python
class PolicyEngineError(Exception):     # Base exception
class PolicyNotFoundError(PolicyEngineError):  # Policy does not exist
class PolicyEvaluationError(PolicyEngineError): # Internal evaluation error
class AttestationError(PolicyEngineError):      # Attestation storage failure
```

---

## 6. Database Schema & Migrations

### 6.1 DDL — `policies` Table

```sql
CREATE TABLE policies (
    policy_id      VARCHAR(128) PRIMARY KEY,
    name           VARCHAR(256) NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    scope          VARCHAR(32) NOT NULL,   -- 'step', 'role', 'system'
    allowed_tools  JSONB NOT NULL DEFAULT '["*"]',
    allowed_roles  JSONB NOT NULL DEFAULT '[]',
    max_spawned_steps INTEGER NOT NULL DEFAULT 3,
    require_approval BOOLEAN NOT NULL DEFAULT false,
    data_access    JSONB NOT NULL DEFAULT '["tier1"]',
    forbidden_actions JSONB NOT NULL DEFAULT '[]',
    token_budget   INTEGER NOT NULL DEFAULT 8192,
    created_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_policies_scope ON policies(scope);
CREATE INDEX idx_policies_version ON policies(policy_id, version);

COMMENT ON TABLE policies IS 'PolicyEngine rules governing LLM reasoning step spawning (GLOBAL_SPEC §2.9)';
```

### 6.2 DDL — `policy_attestations` Table

```sql
CREATE TABLE policy_attestations (
    attestation_id VARCHAR(26) PRIMARY KEY,   -- ULID format
    plan_id        VARCHAR(26) NOT NULL REFERENCES plans(plan_id),
    plan_revision  INTEGER NOT NULL,
    spawned_by_step INTEGER NOT NULL,
    new_steps      JSONB NOT NULL,
    policy_id      VARCHAR(128) NOT NULL REFERENCES policies(policy_id),
    policy_version INTEGER NOT NULL,
    decision       JSONB NOT NULL,
    attested_at    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_policy_attestations_plan_id ON policy_attestations(plan_id);
CREATE INDEX idx_policy_attestations_policy_id ON policy_attestations(policy_id);
CREATE INDEX idx_policy_attestations_attested_at ON policy_attestations(attested_at);

COMMENT ON TABLE policy_attestations IS 'Immutable audit records for runtime spawn authorization (GLOBAL_SPEC §2.4.1)';
```

### 6.3 SQLAlchemy Models

Defined in `shared/database/models.py`:
- `PolicyTable` — maps to `policies`
- `PolicyAttestationTable` — maps to `policy_attestations`

### 6.4 Migration Specification

- **File**: `migrations/007_create_policy_tables.sql` (check existing sequence)
- **Contents**: DDL from §6.1 and §6.2 above
- Must match SQLAlchemy models exactly

---

## 7. Adapters

### 7.1 PolicyDatabaseAdapter (`adapters/db.py`)

**Responsibility**: Async SQLAlchemy 2.0 operations on `policies` and `policy_attestations` tables.

```python
class PolicyDatabaseAdapter:
    def __init__(self) -> None:
        self.shared_db = get_database_adapter()  # SharedDatabaseAdapter

    async def store_policy(self, policy: PolicyDB) -> bool:
        """Insert or update a policy (upsert). Returns True on success."""

    async def get_policy(self, policy_id: str, version: int | None = None) -> PolicyDB | None:
        """Retrieve by ID, optionally at specific version."""

    async def list_policies(self, scope: str | None = None) -> list[PolicyDB]:
        """List all policies, optionally filtered by scope."""

    async def store_attestation(self, attestation: PolicyAttestationDB) -> bool:
        """Insert attestation record. Returns True on success."""

    async def get_attestations_for_plan(self, plan_id: str) -> list[PolicyAttestationDB]:
        """Retrieve all attestations for a plan, ordered by attested_at."""

    async def health_check(self) -> bool:
        """Check database connectivity via shared adapter."""
```

**Shared infrastructure usage**:
- `get_database_adapter()` from `shared/database/adapter.py` for `get_session()`
- `PolicyTable`, `PolicyAttestationTable` from `shared/database/models.py`
- Uses `session.begin()` context manager for auto-commit/rollback

### 7.2 PolicyCacheAdapter (`adapters/cache.py`)

**Responsibility**: Redis caching for policy rules with graceful degradation.

```python
CACHE_TTL_SECONDS = 300  # 5 minutes
CACHE_KEY_PREFIX = "policy_cache"

class PolicyCacheAdapter:
    def __init__(self, redis_client: object | None) -> None:
        self._redis = redis_client

    async def get_policy(self, policy_id: str, version: int) -> PolicyRule | None:
        """Cache lookup. Returns None on miss or error."""

    async def set_policy(self, policy_id: str, version: int, rule: PolicyRule) -> None:
        """Cache a policy. Silent failure if Redis unavailable."""

    async def invalidate(self, policy_id: str, version: int) -> None:
        """Remove cache entry. Silent failure if Redis unavailable."""
```

**Key pattern**: `policy_cache:{policy_id}:{version}`
**TTL**: 300 seconds (5 minutes)
**Degradation**: All errors caught and logged as warnings — never propagated.

---

## 8. Service Implementation

### 8.1 Core Flow — `evaluate_spawn()`

```
1. Log request (plan_id, spawning_step, policy_ref)
2. Resolve policy:
   a. If policy_ref provided → get_policy(policy_ref)
   b. If not found → try learned policy: get_policy("learned:{role}:{tool}") for each proposed step
   c. If learned policy found → evaluate constraints against it (continue to step 3)
   d. If nothing found → return ALLOWED with requires_approval=True, policy_matched=False (user approval fallback)
3. Validate count limits:
   a. proposed_count > rule.max_spawned_steps → violation
   b. proposed_count > 10 (hard cap) → violation
   c. current + proposed > 100 (hard cap) → violation
4. For each proposed step:
   a. can_spawn=true → violation (no recursive spawning)
   b. role not in allowed_roles → violation
   c. role == "Booker" → force requires_approval=true
   d. tool not in allowed_tools (unless wildcard) → violation
   e. tool not in plan_plugins → violation
   f. call in forbidden_actions → violation
5. If violations → return DENIED with all violations
6. If no violations → return ALLOWED with requires_approval flag
```

### 8.2 Policy Resolution

- **Cache-first** (only when version is specified): Redis → DB fallback
- **DB-only** (when version is None): get latest from PostgreSQL
- **Cache population**: On DB hit, write to cache (best-effort, errors logged)
- **Cache invalidation**: On `create_policy()`, invalidate the cached version

### 8.3 Attestation Creation

```
1. Generate unique ULID (26 chars) for attestation_id
2. Timestamp with datetime.now(UTC).isoformat()
3. Build PolicyAttestation (shared schema model)
4. Build PolicyAttestationDB (domain model for DB)
5. Store via db_adapter.store_attestation()
6. On failure → raise AttestationError
7. Log: attestation_id, plan_id, policy_id
8. Return PolicyAttestation to caller
```

### 8.4 Hard Caps

```python
_MAX_STEPS_PER_SPAWN = 10   # Absolute max steps per single spawn operation
_MAX_TOTAL_PLAN_STEPS = 100  # Absolute max steps in any plan
```

---

## 9. Sequences

### 9.1 Happy Path — Spawn Approved

```
ExecuteOrchestrator       PolicyService         CacheAdapter    DatabaseAdapter
        │                       │                    │                │
        │ evaluate_spawn(req)   │                    │                │
        ├──────────────────────→│                    │                │
        │                       │ get_policy(ref, v) │                │
        │                       ├───────────────────→│                │
        │                       │     PolicyRule     │                │
        │                       │←───────────────────│                │
        │                       │                    │                │
        │                       │ [check constraints]│                │
        │                       │ all pass           │                │
        │                       │                    │                │
        │ PolicyDecision(true)  │                    │                │
        │←──────────────────────│                    │                │
        │                       │                    │                │
        │ create_attestation()  │                    │                │
        ├──────────────────────→│                    │                │
        │                       │    store_attestation()              │
        │                       ├────────────────────────────────────→│
        │                       │              True                   │
        │                       │←────────────────────────────────────│
        │  PolicyAttestation    │                    │                │
        │←──────────────────────│                    │                │
```

### 9.2 Fallback Path — No Matching Policy → User Approval

```
ExecuteOrchestrator       PolicyService         CacheAdapter    DatabaseAdapter
        │                       │                    │                │
        │ evaluate_spawn(req)   │                    │                │
        ├──────────────────────→│                    │                │
        │                       │ get_policy(ref)    │                │
        │                       ├───────────────────→│  miss          │
        │                       │←───────────────────│                │
        │                       │ get_policy(ref)    │                │
        │                       ├────────────────────────────────────→│
        │                       │              None                   │
        │                       │←────────────────────────────────────│
        │                       │                    │                │
        │                       │ [try learned policy lookup]         │
        │                       │ get_policy("learned:{role}:{tool}") │
        │                       ├────────────────────────────────────→│
        │                       │              None                   │
        │                       │←────────────────────────────────────│
        │                       │                    │                │
        │ PolicyDecision(true)  │ requires_approval=true              │
        │ policy_matched=false  │ "fallback to user approval"         │
        │←──────────────────────│                    │                │
        │                       │                    │                │
        │ [user approves]       │                    │                │
        │ learn_from_approval() │                    │                │
        ├──────────────────────→│                    │                │
        │                       │ create_policy(learned:role:tool)    │
        │                       ├────────────────────────────────────→│
        │                       │              True                   │
        │                       │←────────────────────────────────────│
        │  PolicyRule (learned) │                    │                │
        │←──────────────────────│                    │                │
```

### 9.3 Deny Path — Constraint Violations

```
ExecuteOrchestrator       PolicyService
        │                       │
        │ evaluate_spawn(req)   │
        ├──────────────────────→│
        │                       │ [resolve policy → found]
        │                       │ [check constraints]
        │                       │   ✗ tool not in allowed_tools
        │                       │   ✗ role not in allowed_roles
        │                       │
        │ PolicyDecision(false) │ violations=["tool...", "role..."]
        │←──────────────────────│
```

### 9.4 Redis Unavailable — Graceful Degradation

```
ExecuteOrchestrator       PolicyService         CacheAdapter    DatabaseAdapter
        │                       │                    │                │
        │ evaluate_spawn(req)   │                    │                │
        ├──────────────────────→│                    │                │
        │                       │ get_policy(ref, v) │                │
        │                       ├───────────────────→│                │
        │                       │  [ConnectionError] │                │
        │                       │  log warning       │                │
        │                       │  return None       │                │
        │                       │←───────────────────│                │
        │                       │                    │                │
        │                       │ get_policy(ref, v) │                │
        │                       ├────────────────────────────────────→│
        │                       │          PolicyDB                   │
        │                       │←────────────────────────────────────│
        │                       │                    │                │
        │                       │ [check constraints → pass]         │
        │ PolicyDecision(true)  │                    │                │
        │←──────────────────────│                    │                │
```

---

## 10. Shared Infrastructure Usage

### 10.1 DI Wiring — `shared/app.py`

```python
# In lifespan function:
policy_db = PolicyDatabaseAdapter()
app.state.policy_service = create_policy_service(
    db_adapter=policy_db,
    redis_client=None,  # DB-only initially
)

# After Redis is initialized (e.g., from Intake):
if intake_redis is not None:
    app.state.policy_service._cache = PolicyCacheAdapter(intake_redis)
```

### 10.2 Dependency — `shared/dependencies.py`

```python
def get_policy_service(request: Request) -> Any:
    """Get PolicyService singleton from app state."""
    return request.app.state.policy_service
```

### 10.3 Database

- Uses `SharedDatabaseAdapter` via `get_database_adapter()` — no local connection setup
- Uses `session.begin()` context manager for auto-commit/rollback
- Imports `PolicyTable`, `PolicyAttestationTable` from `shared/database/models.py`

---

## 11. Caching Strategy

### 11.1 What's Cached

| Data | Key Pattern | TTL | Invalidation |
|------|------------|-----|--------------|
| PolicyRule (versioned) | `policy_cache:{policy_id}:{version}` | 5m | On `create_policy()` call |

### 11.2 Cache Behavior

- **Read path**: `get_policy(id, version)` → Redis → DB → populate cache (best-effort)
- **Write path**: `create_policy(rule)` → DB → invalidate cache entry
- **Unversioned reads**: Skip cache (version=None), go directly to DB for latest
- **TTL rationale**: 5m balances freshness vs. load reduction. Policies change rarely (admin operations), so staleness is bounded and acceptable.

### 11.3 Degradation

Redis unavailable → all cache operations return None / silently fail → DB-only mode. No impact on correctness, only on latency (from <50ms to <200ms).

---

## 12. Observability & Safety

### 12.1 Structured Logging

| Event | Level | Fields |
|-------|-------|--------|
| `evaluate_spawn` entry | INFO | plan_id, spawning_step, proposed_count, policy_ref |
| `evaluate_spawn` ALLOWED | INFO | plan_id, policy_id, requires_approval |
| `evaluate_spawn` DENIED | INFO | plan_id, policy_id, violations |
| `Attestation created` | INFO | attestation_id, plan_id, policy_id |
| `Policy stored` | INFO | policy_id, version |
| `Cache read failed` | WARNING | policy_id, version |
| `Cache write failed` | WARNING | policy_id, version |

### 12.2 Prometheus Metrics (Planned)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `policy_evaluate_duration_seconds` | Histogram | policy_id, decision | Evaluation latency |
| `policy_evaluate_total` | Counter | decision (allowed/denied) | Total evaluations |
| `policy_violations_total` | Counter | violation_type | Violation breakdown |
| `policy_attestation_total` | Counter | — | Attestations created |
| `policy_cache_hit_total` | Counter | — | Cache hits |
| `policy_cache_miss_total` | Counter | — | Cache misses |

### 12.3 PII Protection

- No user data (emails, names, credentials) enters PolicyEngine
- SpawnRequest contains only plan_id, step numbers, tool IDs, and role names
- Verified by `test_observability.py::test_no_pii_in_logs`

### 12.4 Error Classes

Domain errors are defined in `domain/models.py`. No HTTP routes exist (library component), so error handling is done by the caller (ExecuteOrchestrator).

---

## 13. Non-Functional Requirements

### 13.1 Performance

| Operation | Target (cached) | Target (DB-only) | Justification |
|-----------|-----------------|-------------------|---------------|
| `evaluate_spawn()` | p95 < 50ms | p95 < 200ms | GLOBAL_SPEC §2.9: <5ms target, realistic with network overhead |
| `create_attestation()` | — | p95 < 100ms | Single DB insert |
| `get_policy()` (cache hit) | p95 < 10ms | — | Redis GET |
| `get_policy()` (cache miss) | — | p95 < 100ms | Single DB SELECT |

### 13.2 Testing Strategy

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_unit.py` | 27 | Fallback-to-approval, learned policy resolution, tools, roles, counts, forbidden actions, plugins, recursive spawn, Booker HITL |
| `test_service.py` | 22 | Cache-first lookups, attestation creation, CRUD, cache populate on miss, learn-from-approval |
| `test_contract.py` | 15 | Model conformance (PolicyRule, PolicyDecision, PolicyAttestation, SpawnRequest) |
| `test_observability.py` | 5 | Structured logging, PII protection |
| **Total** | **69** | |

---

## 14. Architectural Considerations

### 14.1 Fallback-to-Approval with Learned Policies

When no policy rule matches a spawn request, the action is **allowed but requires user approval** (HITL gate). This balances safety with usability:
- Legitimate spawns that lack pre-configured policies are not hard-denied
- The user retains control via the approval gate
- On approval, a **learned policy** (`learned:{role}:{tool}`) is created so future similar spawns auto-approve
- `PolicyDecision.policy_matched=False` signals to callers that the decision came from the fallback path
- Constraint violations (e.g. forbidden tools, recursive spawning) are still hard-denied regardless of learned policies

### 14.2 Attestations as Audit Records

Spawned steps receive PolicyEngine attestations as immutable audit records. This provides:
- A clear chain of authorization for every runtime plan modification
- No dependency on external signing infrastructure in the execution context
- Audit chain: `policy_attestations[] = full spawn authorization provenance`

### 14.3 Booker HITL as Non-Overridable

Even if a PolicyRule sets `require_approval=false`, any spawned step with `role="Booker"` forces `requires_approval=true`. This prevents:
- Silently spawning write operations
- Bypassing human approval for financial or data-modifying actions

### 14.4 No Recursive Spawning

Spawned steps cannot have `can_spawn=true`. This prevents:
- Exponential explosion of plan complexity
- Unbounded runtime graph growth
- Difficulty in auditing deeply nested spawn chains

### 14.5 Fault Isolation

PolicyEngine is consumed by ExecuteOrchestrator. If PolicyEngine fails:
- ExecuteOrchestrator treats it as fail-closed (deny all spawns)
- Pure API plans (no LLM reasoning steps) are unaffected
- The failure does not cascade to Planner or other components

---

## 15. Architecture Decision Records

### ADR-001: Library Component (No HTTP Routes)

**Context**: PolicyEngine could expose REST endpoints for policy CRUD.
**Decision**: Library component consumed internally by ExecuteOrchestrator and Planner.
**Rationale**: Policies are configured by system setup scripts, not end users. Adding HTTP endpoints would require auth/authz overhead with no user-facing value.
**Status**: Accepted. May add admin endpoints in Phase 4 if needed.

### ADR-002: Single Redis Key per Version

**Context**: Could cache all versions under one key, or cache per-version.
**Decision**: Separate key per `{policy_id}:{version}` pair.
**Rationale**: Version-specific lookups are the hot path (evaluate_spawn uses specific versions). Per-version keys allow fine-grained TTL and invalidation.
**Status**: Accepted.

### ADR-003: Attestations as Standalone Audit Records

**Context**: Spawned steps need an authorization record for audit purposes.
**Decision**: PolicyEngine creates immutable attestation records for every approved spawn.
**Rationale**: Attestations provide a self-contained audit trail linking spawn decisions to the governing policy. Aligns with GLOBAL_SPEC §2.4.1.
**Status**: Accepted (documented in Project_HLD §2c).

---

## 16. Dependencies

### 16.1 Python Packages

| Package | Version | Justification |
|---------|---------|---------------|
| `pydantic` | >=2.5.0 | Domain models, shared schemas |
| `sqlalchemy` | >=2.0 | Async database operations |
| `asyncpg` | >=0.29.0 | PostgreSQL async driver |
| `redis[hiredis]` | >=5.0.0 | Cache adapter |
| `ulid-py` | >=1.1.0 | ULID generation for attestation IDs |

### 16.2 Internal Dependencies

| Component | Usage |
|-----------|-------|
| `shared/database/adapter.py` | SharedDatabaseAdapter for session management |
| `shared/database/models.py` | PolicyTable, PolicyAttestationTable |
| `shared/schemas/policy.py` | PolicyRule, PolicyDecision, PolicyAttestation, ReasoningConfig |

### 16.3 External Services

| Service | Purpose | Failure Impact |
|---------|---------|----------------|
| PostgreSQL 16 | Policy storage, attestation persistence | Fail-closed: deny all |
| Redis 7 | Cache hot-path policy lookups | Graceful degradation: DB-only |

---

## 17. Risks & Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Policy hierarchy not fully implemented (step → role → system fallback) | Medium | Current: explicit `policy_ref` only. GLOBAL_SPEC §2.9 mentions hierarchy but deny-by-default covers the gap. Implement in Phase 4 if needed. |
| No PluginRegistry validation in spawn eval | Low | MODULAR_ARCHITECTURE lists PluginRegistry as dependency. Current: plugin check uses `plan_plugins` list (correct per spec). Direct registry query could be added for real-time tool availability. |
| Audit component not yet implemented | Low | MODULAR_ARCHITECTURE lists Audit as dependency. Current: logging provides audit trail. Full Audit component is a separate implementation. |

### Open Questions

- **OQ-1**: Should PolicyEngine expose HTTP endpoints for admin CRUD? Current: no.
- **OQ-2**: Should the policy hierarchy (step → role → system) be fully implemented, or is explicit `policy_ref` + deny-by-default sufficient? Current: explicit only.
- **OQ-3**: Should attestations include the full plan snapshot at the time of spawn, or just the delta (new steps)?  Current: delta only.

---

## 18. Validation Checklist

- [x] Data model fields match GLOBAL_SPEC §2.9 contracts
- [x] Conformance header references current document versions (GLOBAL_SPEC v3.0, MODULAR_ARCHITECTURE v2.0, Project_HLD v6.1)
- [x] Table ownership matches MODULAR_ARCHITECTURE §3 (policies, policy_attestations)
- [x] Component dependencies match MODULAR_ARCHITECTURE §4 (PluginRegistry noted, Audit noted as not-yet-implemented)
- [x] Every upstream consumer has documented interface contract (ExecuteOrchestrator, Planner)
- [x] Redis caching strategy documented with key pattern, TTL, invalidation rules
- [x] Prometheus metrics defined with names and types
- [x] No deprecated library versions
- [x] Error handling uses domain exceptions in `domain/models.py`
- [x] Database adapter uses SharedDatabaseAdapter from `shared/database/`
- [x] DDL included for owned tables with indexes
- [x] Migration file specification documented (007_create_policy_tables.sql)
- [x] No PII in logs (verified by test)
