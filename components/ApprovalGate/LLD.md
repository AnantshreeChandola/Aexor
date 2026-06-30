# ApprovalGate — Low-Level Design

**Component**: `components/ApprovalGate/`
**Layer**: Orchestration Layer
**Type**: Library component (no HTTP routes, no owned DB tables)
**Spec**: `specs/028-approvalgate-hitl-approval/spec.md`
**Status**: Draft

---

## 1. Purpose & Scope

ApprovalGate is the **enforcement point of the preview-first safety model** (GLOBAL_SPEC v3.0 §1). It sits between PreviewOrchestrator and ExecuteOrchestrator, issuing JWT approval tokens after user confirmation and managing multi-gate approval flows. No write operation executes without a valid approval token.

**Responsibilities:**
- Issue JWT approval tokens (HS256 signed, configurable TTL) binding plan_id, user_id, gate_id, scopes
- Enforce single-use tokens via Redis consumed-token tracking
- Support multi-gate approval flows (gate-A → gate-B → gate-C) with per-gate tokens
- Retrieve and bind preview state from PreviewOrchestrator on approval
- Store user selections (e.g., chosen time slot) with approval state
- Coordinate with PolicyEngine to learn from approval decisions (spawned step gates)
- Provide gate status queries and full approval state retrieval for downstream consumers

**Out of scope:**
- HTTP route handling (library component — routes added when API gateway layer is built)
- Plan execution (ExecuteOrchestrator's responsibility)
- Preview execution (PreviewOrchestrator's responsibility)
- Policy evaluation (PolicyEngine's responsibility)
- Credential management (CredentialVault's responsibility)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v3.0 | §1 (Safety Model — Approval), §2.7 (Approval Token), §3 (NFRs) |
| Project_HLD.md | v6.1 | Layer 3 (Orchestration), §2a Step 4 (ApprovalGate flow), §6 (Multi-gate shopping example) |
| MODULAR_ARCHITECTURE.md | v2.1 | §1 (Orchestration Layer), §3 Redis Keys (approval_token:{token}), §4 (ApprovalGate deps: Preview), §7 (Multi-Gate HITL flow) |
| approval_token.schema.json | — | §2.7 JSON Schema contract (token, plan_id, user_id, exp, scopes, gate_id) |

---

## 3. Architecture Overview

### 3.1 Layer Placement

```
Orchestration Layer
├── PreviewOrchestrator  (upstream — provides preview state)
├── ApprovalGate         ← THIS COMPONENT
└── ExecuteOrchestrator  (downstream consumer — validates tokens)
```

### 3.2 Blast Radius Analysis

| Failure Mode | Impact | Containment |
|-------------|--------|-------------|
| ApprovalGate crashes | User cannot approve plans; no execution possible | No data loss; retry safe. Preview results still cached. Other components unaffected. |
| Redis unavailable | Gate state tracking degrades; single-use enforcement degrades | JWT issuance/validation still works (CPU-only). Token expiry still enforced. Logged warning. |
| JWT secret not configured | Service cannot start | `ApprovalConfigError` at startup — fail-fast. |
| PreviewOrchestrator unavailable | Cannot bind preview state | Approval still succeeds; preview_state=None. ExecuteOrchestrator will re-run previewable steps. |
| PolicyEngine unavailable | Cannot learn from approval | Approval still succeeds; learning is best-effort. Logged warning. |

### 3.3 Component Boundaries

ApprovalGate is a **pure library** — no database tables, no HTTP routes. It receives approval requests and returns JWT tokens. All persistent state is ephemeral (Redis TTL-based).

**Isolation strategy**: ApprovalGate never executes plan steps, never calls MCP tools, never accesses credentials. The only side effects are Redis SET operations for gate state and consumed-token tracking.

```
                    ┌─────────────────────────────────────┐
                    │          ApprovalGate                │
                    │                                      │
 ApprovalRequest ──>│  ┌─── Request Validator ───┐        │
                    │  │ plan_id, user_id,        │        │
                    │  │ gate_id, scopes          │        │
                    │  └────────┬─────────────────┘        │
                    │           │                           │
                    │  ┌────────▼─────────────────┐        │
                    │  │ Preview State Binder      │──> PreviewOrchestrator
                    │  │ get_preview_state()       │    (best-effort)
                    │  └────────┬─────────────────┘        │
                    │           │                           │
                    │  ┌────────▼─────────────────┐        │
                    │  │ Token Issuer              │        │
                    │  │ JWT sign (HS256)          │        │
                    │  └────────┬─────────────────┘        │
                    │           │                           │
                    │  ┌────────▼─────────────────┐        │
                    │  │ Gate Store                │──> Redis (gate state)
                    │  │ state + consumed tracking │──> Redis (consumed tokens)
                    │  └────────┬─────────────────┘        │
                    │           │                           │
                    │  ┌────────▼─────────────────┐        │
                    │  │ Policy Learner (optional) │──> PolicyEngine
                    │  │ learn_from_approval()     │    (best-effort)
                    │  └──────────────────────────┘        │
                    └─────────────────────────────────────┘
                              │
                    ApprovalToken (JWT)
```

### 3.4 Dependency Contract Table

| Dependency | Method | Input | Output | Error Handling |
|-----------|--------|-------|--------|----------------|
| PreviewOrchestrator | `get_preview_state(plan_id, user_id)` | plan_id, user_id | `dict[int, PreviewStepResult] \| None` | None on miss — approval proceeds without preview state |
| PolicyEngine | `learn_from_approval(role, tool)` | role, tool strings | `PolicyRule` | Best-effort; errors caught and logged |
| Redis | `set()`, `get()`, `delete()`, `hset()`, `hgetall()` | key, value | bytes or None | Graceful degradation; JWT operations still work |
| Consumed by: ExecuteOrchestrator | `validate_token(token, plan_id, gate_id)` | JWT string, plan_id, gate_id | decoded claims dict | Raises TokenExpiredError, TokenValidationError, TokenConsumedError |
| Consumed by: ExecuteOrchestrator | `get_approval_state(plan_id, gate_id)` | plan_id, gate_id | `ApprovalState \| None` | None on miss |
| Consumed by: ExecuteOrchestrator | `get_gate_status(plan_id)` | plan_id | `dict[str, str]` | Empty dict on Redis miss |

---

## 4. Interfaces

### 4.1 Service Interface

```python
class ApprovalService:
    """HITL approval token management and multi-gate coordination."""

    async def approve(self, request: ApprovalRequest) -> ApprovalToken:
        """Issue an approval token for a plan gate.

        Flow:
            1. Validate request (plan_id format, scopes non-empty)
            2. Check idempotency: if gate already approved, return existing token
            3. Retrieve preview state from PreviewOrchestrator (best-effort)
            4. Sign JWT with claims {plan_id, user_id, gate_id, scopes, exp, iat, token_id}
            5. Store gate state in Redis (approved, token_id, preview_state, selected_option)
            6. If policy_matched=False: call PolicyEngine.learn_from_approval()
            7. Return ApprovalToken

        Raises:
            InvalidGateError: If gate_id format is invalid.
            ApprovalError: If approval fails for any other reason.
        """

    async def validate_token(
        self, token: str, plan_id: str, gate_id: str | None = None
    ) -> dict:
        """Validate an approval token and mark it as consumed.

        Returns decoded claims if valid.

        Raises:
            TokenExpiredError: If token has expired.
            TokenValidationError: If signature, plan_id, user_id, or scopes invalid.
            TokenConsumedError: If token was already used (single-use).
        """

    async def get_gate_status(self, plan_id: str) -> dict[str, str]:
        """Get approval status for all gates of a plan.

        Returns dict of gate_id -> status (pending/approved/expired).
        Returns empty dict if Redis unavailable or no gates found.
        """

    async def get_approval_state(
        self, plan_id: str, gate_id: str
    ) -> ApprovalState | None:
        """Get full approval state including preview results and user selection.

        Returns None if gate not found, expired, or Redis unavailable.
        """
```

### 4.2 Factory Function

```python
def create_approval_service(
    preview_service: PreviewService | None = None,
    policy_service: PolicyService | None = None,
    redis_client: object | None = None,
    jwt_secret: str = "",
    token_ttl_s: int = 900,
) -> ApprovalService:
    """Create ApprovalService with all dependencies.

    Called once during app lifespan startup in shared/app.py.

    Args:
        preview_service: PreviewOrchestrator for cached state retrieval.
        policy_service: PolicyEngine for learn_from_approval (optional).
        redis_client: Redis client for gate state and consumed-token tracking.
        jwt_secret: Secret key for JWT signing (required; raises ApprovalConfigError if empty).
        token_ttl_s: Token time-to-live in seconds (default 900 / 15min).

    Raises:
        ApprovalConfigError: If jwt_secret is empty or too short.
    """
```

### 4.3 Consumer Contracts

#### ExecuteOrchestrator (primary consumer)

**Calls**: `validate_token(token, plan_id, gate_id)` before executing gated steps
**Input**: JWT token string, plan_id (str), gate_id (str | None)
**Output**: Decoded claims dict (`{plan_id, user_id, gate_id, scopes, exp, iat, token_id}`)
**Error handling**: Raises `TokenExpiredError`, `TokenValidationError`, `TokenConsumedError` — ExecuteOrchestrator must catch and handle (pause for re-approval or fail execution).

**Calls**: `get_approval_state(plan_id, gate_id)` to retrieve cached preview state
**Input**: plan_id (str), gate_id (str)
**Output**: `ApprovalState | None` — contains preview_state and selected_option
**Error handling**: Returns None on miss; ExecuteOrchestrator re-runs previewable steps.

**Calls**: `get_gate_status(plan_id)` to check which gates are approved
**Input**: plan_id (str)
**Output**: `dict[str, str]` — gate_id → status mapping
**Error handling**: Returns empty dict on Redis failure.

#### Intake / API Gateway (upstream caller)

**Calls**: `approve(request)` after user confirms preview
**Input**: `ApprovalRequest(plan_id, user_id, gate_id, scopes, selected_option, trace_id, policy_matched)`
**Output**: `ApprovalToken` with JWT string and metadata
**Error handling**: Must catch `InvalidGateError` and `ApprovalError`.

---

## 5. Data Model

### 5.1 Domain Entities

```python
class ApprovalRequest(BaseModel):
    """Input contract for plan approval."""

    plan_id: str = Field(
        ..., min_length=26, max_length=26, description="ULID plan identifier"
    )
    user_id: str = Field(..., min_length=1, description="User approving the plan")
    gate_id: str = Field(
        default="gate-A",
        pattern=r"^gate-[A-Za-z0-9]+$",
        description="HITL gate identifier",
    )
    scopes: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Approved OAuth scopes for this execution",
    )
    selected_option: dict[str, Any] | None = Field(
        default=None,
        description="Optional user selection from preview (e.g., chosen time slot)",
    )
    trace_id: str = Field(
        default="", description="Distributed tracing ID"
    )
    policy_matched: bool = Field(
        default=True,
        description="Whether a stored policy matched. False triggers learn_from_approval.",
    )
    role: str | None = Field(
        default=None,
        description="Role of the spawned step (for learn_from_approval).",
    )
    tool: str | None = Field(
        default=None,
        description="Tool of the spawned step (for learn_from_approval).",
    )


class ApprovalToken(BaseModel):
    """Issued approval token (GLOBAL_SPEC §2.7)."""

    token: str = Field(..., description="JWT approval token string")
    plan_id: str = Field(
        ..., min_length=26, max_length=26, description="ULID of the approved plan"
    )
    user_id: str = Field(..., description="User who approved")
    gate_id: str = Field(..., description="Gate this token covers")
    scopes: list[str] = Field(..., description="Approved scopes")
    exp: str = Field(..., description="Expiration timestamp (ISO 8601)")
    iat: str = Field(..., description="Issued-at timestamp (ISO 8601)")
    token_id: str = Field(..., description="Unique token identifier (ULID)")


class ApprovalState(BaseModel):
    """Full approval state returned by get_approval_state()."""

    plan_id: str = Field(
        ..., min_length=26, max_length=26, description="ULID plan identifier"
    )
    gate_id: str = Field(..., description="Gate identifier")
    status: Literal["approved", "pending", "expired"] = Field(
        ..., description="Gate approval status"
    )
    token_claims: dict[str, Any] = Field(
        default_factory=dict, description="Decoded JWT claims"
    )
    preview_state: dict[int, dict[str, Any]] | None = Field(
        default=None, description="Cached preview step results"
    )
    selected_option: dict[str, Any] | None = Field(
        default=None, description="User selection from preview"
    )
    approved_at: str = Field(..., description="Approval timestamp (ISO 8601)")
```

**GLOBAL_SPEC §2.7 alignment**: The `ApprovalToken` maps directly to the Approval Token contract — `token`, `plan_id`, `user_id`, `exp`, `scopes`. Extended with `gate_id`, `iat`, and `token_id` per the spec and schema requirements.

**approval_token.schema.json alignment**: All required fields (`token`, `plan_id`, `user_id`, `exp`, `scopes`) present. Optional fields (`gate_id`, `selected_option`, `iat`, `nbf`) supported. `token_id` is internal (not in schema but used for single-use tracking).

### 5.2 Custom Exceptions

```python
class ApprovalError(Exception):
    """Base error for ApprovalGate."""


class ApprovalConfigError(ApprovalError):
    """Configuration error (e.g., missing JWT secret)."""


class InvalidGateError(ApprovalError):
    """Invalid gate_id submitted."""

    def __init__(self, gate_id: str) -> None:
        self.gate_id = gate_id
        super().__init__(f"Invalid gate_id: {gate_id}")


class TokenExpiredError(ApprovalError):
    """Token has expired."""


class TokenValidationError(ApprovalError):
    """Token failed validation (signature, plan_id mismatch, scope mismatch)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Token validation failed: {reason}")


class TokenConsumedError(ApprovalError):
    """Token has already been consumed (single-use enforcement)."""
```

---

## 6. Adapters

### 6.1 TokenIssuer (`adapters/token_issuer.py`)

**Responsibility**: JWT signing and verification using PyJWT.

```python
class TokenIssuer:
    """JWT token signing and verification."""

    def __init__(self, secret: str, algorithm: str = "HS256") -> None:
        self._secret = secret
        self._algorithm = algorithm

    def sign(self, claims: dict[str, Any]) -> str:
        """Sign claims into a JWT string.

        Claims must include: plan_id, user_id, gate_id, scopes, exp, iat, token_id.
        Returns JWT string (eyJ... format).
        """

    def verify(self, token: str) -> dict[str, Any]:
        """Verify and decode a JWT token.

        Returns decoded claims dict.

        Raises:
            TokenExpiredError: If token exp is in the past.
            TokenValidationError: If signature is invalid.
        """
```

**JWT Claims Structure**:
```json
{
  "plan_id": "01JXYZ1234567890ABCDEFGHIJ",
  "user_id": "user-uuid-123",
  "gate_id": "gate-A",
  "scopes": ["calendar.write"],
  "exp": 1712300100,
  "iat": 1712299200,
  "token_id": "01JXYZ9876543210KLMNOPQRST"
}
```

**Design decisions**:
- `exp` and `iat` stored as Unix timestamps in JWT (standard), but exposed as ISO 8601 strings in ApprovalToken model (per GLOBAL_SPEC §2.7)
- `selected_option` NOT embedded in JWT (may be large) — stored in Redis alongside gate state
- `token_id` (ULID) used as the key for single-use consumed tracking
- Algorithm configurable (HS256 default, RS256 for production)

### 6.2 GateStore (`adapters/gate_store.py`)

**Responsibility**: Redis gate state storage, consumed-token tracking, and approval state.

```python
class GateStore:
    """Redis-backed gate state and token consumption tracking."""

    def __init__(self, redis_client: object | None, default_ttl_s: int = 900) -> None:
        self._redis = redis_client
        self._default_ttl_s = default_ttl_s

    async def store_gate(
        self,
        plan_id: str,
        gate_id: str,
        token_id: str,
        preview_state: dict | None,
        selected_option: dict | None,
        token_claims: dict,
        ttl_s: int | None = None,
    ) -> bool:
        """Store gate approval state in Redis.

        Key: gate:{plan_id}:{gate_id}
        Value: JSON hash with status, token_id, preview_state, selected_option, token_claims, approved_at.
        TTL: Matches token TTL.

        Returns True on success, False on Redis failure (graceful degradation).
        """

    async def get_gate(self, plan_id: str, gate_id: str) -> dict | None:
        """Retrieve gate state. Returns None if missing/expired/Redis down."""

    async def get_all_gates(self, plan_id: str, gate_ids: list[str]) -> dict[str, str]:
        """Get status for multiple gates. Returns gate_id -> status mapping."""

    async def mark_consumed(self, token_id: str, ttl_s: int) -> bool:
        """Mark a token as consumed (SET NX with TTL).

        Key: consumed:{token_id}
        Returns True if successfully marked (first use), False if already consumed.
        Returns True if Redis unavailable (fail-open — JWT expiry still enforced).
        """

    async def is_consumed(self, token_id: str) -> bool:
        """Check if token was already consumed.

        Returns False if Redis unavailable (fail-open — JWT expiry still enforced).
        """
```

**Redis Key Patterns**:

| Key Pattern | TTL | Value | Purpose |
|------------|-----|-------|---------|
| `gate:{plan_id}:{gate_id}` | token TTL (15min) | JSON: `{status, token_id, preview_state, selected_option, token_claims, approved_at}` | Gate approval state |
| `consumed:{token_id}` | token TTL (15min) | `"1"` | Single-use enforcement |

**Graceful degradation**: All Redis operations wrapped in try/except. Failures logged as warnings, never propagated. On Redis failure:
- `store_gate()` returns False (logged warning)
- `get_gate()` returns None
- `mark_consumed()` returns True (fail-open — JWT expiry still enforced)
- `is_consumed()` returns False (fail-open)

**Concurrent approval protection**: `mark_consumed()` uses Redis `SET NX` (set if not exists) for atomic single-use enforcement. Two concurrent validations of the same token: only one succeeds.

---

## 7. Shared Infrastructure Usage

### 7.1 Dependency Injection

1. **`shared/app.py` lifespan**: Initialize `ApprovalService` via `create_approval_service()`
2. **`shared/dependencies.py`**: Add `get_approval_service()` Depends accessor
3. **No route registration** (library component — consumed programmatically)

### 7.2 Shared Schemas

| Schema | Location | Usage |
|--------|----------|-------|
| `Plan`, `PlanStep` | `shared/schemas/plan.py` | Plan input model (gate_id field on PlanStep) |
| `PolicyDecision` | `shared/schemas/policy.py` | `policy_matched` field for learn-from-approval |
| `PreviewStepResult` | `components/PreviewOrchestrator/domain/models.py` | Preview state from get_preview_state() |

### 7.3 Error Handling

- Domain errors defined in `components/ApprovalGate/domain/models.py`
- No HTTP routes -> no `ErrorResponse` usage (consumers handle their own HTTP mapping)
- Shared error patterns: callers (future API gateway) will use `ErrorResponse` from `shared/api/error_handlers.py`

---

## 8. Sequences

### 8.1 Happy Path — Approve Plan After Preview

```
Caller      ApprovalService   TokenIssuer   GateStore   PreviewService   PolicyEngine
  |               |               |             |              |               |
  | approve(req)  |               |             |              |               |
  |-------------->|               |             |              |               |
  |               | get_preview_state(plan_id, user_id)        |               |
  |               |------------------------------------------>|               |
  |               | preview_state (or None)                    |               |
  |               |<------------------------------------------|               |
  |               |               |             |              |               |
  |               | sign(claims)  |             |              |               |
  |               |-------------->|             |              |               |
  |               | jwt_string    |             |              |               |
  |               |<--------------|             |              |               |
  |               |               |             |              |               |
  |               | store_gate(plan_id, gate_id, ...)          |               |
  |               |-------------------------->|              |               |
  |               | True                       |              |               |
  |               |<--------------------------|              |               |
  |               |               |             |              |               |
  | ApprovalToken |               |             |              |               |
  |<--------------|               |             |              |               |
```

### 8.2 Multi-Gate Flow — Sequential Approval

```
User        Caller      ApprovalService   GateStore
 |            |               |               |
 | [Preview result shown]     |               |
 | approve gate-A             |               |
 |----------->|               |               |
 |            | approve(gate-A, scopes)        |
 |            |-------------->|               |
 |            |               | store_gate(gate-A)
 |            |               |-------------->|
 |            | token-A       |               |
 |            |<--------------|               |
 |            |               |               |
 | [Execute steps 1-2, reach gate-B]          |
 | [Show intermediate results]                |
 | approve gate-B             |               |
 |----------->|               |               |
 |            | approve(gate-B, scopes)        |
 |            |-------------->|               |
 |            |               | store_gate(gate-B)
 |            |               |-------------->|
 |            | token-B       |               |
 |            |<--------------|               |
 |            |               |               |
 | [Execute steps 3-4, reach gate-C]          |
 | approve gate-C             |               |
 |----------->|               |               |
 |            | approve(gate-C, scopes)        |
 |            |-------------->|               |
 |            | token-C       |               |
 |            |<--------------|               |
```

### 8.3 Token Validation — Single-Use Enforcement

```
ExecuteOrchestrator   ApprovalService   TokenIssuer   GateStore
        |                   |               |             |
        | validate_token(jwt, plan_id, gate_id)           |
        |------------------>|               |             |
        |                   | verify(jwt)   |             |
        |                   |-------------->|             |
        |                   | claims        |             |
        |                   |<--------------|             |
        |                   |               |             |
        |                   | is_consumed(token_id)       |
        |                   |-------------------------->|
        |                   | False (first use)          |
        |                   |<--------------------------|
        |                   |               |             |
        |                   | [validate plan_id match]    |
        |                   | [validate gate_id match]    |
        |                   |               |             |
        |                   | mark_consumed(token_id)     |
        |                   |-------------------------->|
        |                   | True                        |
        |                   |<--------------------------|
        |                   |               |             |
        | claims dict       |               |             |
        |<------------------|               |             |
```

### 8.4 Token Reuse — Rejected

```
ExecuteOrchestrator   ApprovalService   TokenIssuer   GateStore
        |                   |               |             |
        | validate_token(jwt, plan_id)      |             |
        |------------------>|               |             |
        |                   | verify(jwt)   |             |
        |                   |-------------->|             |
        |                   | claims        |             |
        |                   |<--------------|             |
        |                   |               |             |
        |                   | is_consumed(token_id)       |
        |                   |-------------------------->|
        |                   | True (already used!)        |
        |                   |<--------------------------|
        |                   |               |             |
        | TokenConsumedError|               |             |
        |<------------------|               |             |
```

### 8.5 Spawned Step Gate — Learn From Approval

```
Caller      ApprovalService   GateStore   PolicyEngine
  |               |               |             |
  | approve(req)  |               |             |
  | policy_matched=False          |             |
  | role="Fetcher"                |             |
  | tool="google.calendar"        |             |
  |-------------->|               |             |
  |               | [sign JWT, store gate]      |
  |               |               |             |
  |               | learn_from_approval(role, tool)
  |               |------------------------------>|
  |               | PolicyRule (learned)           |
  |               |<------------------------------|
  |               |               |             |
  | ApprovalToken |               |             |
  |<--------------|               |             |
```

### 8.6 Graceful Degradation — Redis Unavailable

```
Caller      ApprovalService   TokenIssuer   GateStore (Redis DOWN)
  |               |               |               |
  | approve(req)  |               |               |
  |-------------->|               |               |
  |               | sign(claims)  |               |
  |               |-------------->|               |
  |               | jwt_string    |               |
  |               |<--------------|               |
  |               |               |               |
  |               | store_gate(...)               |
  |               |----------------------------->|
  |               | False (warning logged)        |  <-- try/except
  |               |<-----------------------------|
  |               |               |               |
  | ApprovalToken | (no cached state key)         |
  |<--------------|               |               |
  |               |               |               |
  | [Later: validate_token]       |               |
  |               | verify(jwt)   |               |
  |               |-------------->|               |  <-- JWT verification works without Redis
  |               | claims        |               |
  |               |<--------------|               |
  |               | is_consumed(token_id)         |
  |               |----------------------------->|
  |               | False (fail-open)             |  <-- Redis down = allow (JWT expiry enforced)
  |               |<-----------------------------|
  |               |               |               |
  | claims dict   |               |               |
  |<--------------|               |               |
```

### 8.7 Idempotent Re-Approval

```
Caller      ApprovalService   GateStore
  |               |               |
  | approve(gate-A) [first time]  |
  |-------------->|               |
  |               | get_gate(plan_id, gate-A)
  |               |-------------->|
  |               | None (not yet approved)
  |               |<--------------|
  |               | [sign, store] |
  | token-A       |               |
  |<--------------|               |
  |               |               |
  | approve(gate-A) [second time] |
  |-------------->|               |
  |               | get_gate(plan_id, gate-A)
  |               |-------------->|
  |               | {status: "approved", token_id: ...}
  |               |<--------------|
  |               | [return existing token]
  | token-A       |               |
  |<--------------|               |
```

---

## 9. Core Algorithm

### 9.1 approve() Flow

```python
async def approve(self, request: ApprovalRequest) -> ApprovalToken:
    # 1. Validate request
    if not request.scopes:
        raise ApprovalError("Scopes cannot be empty")

    # 2. Check idempotency: if gate already approved, return existing token
    existing = await self._gate_store.get_gate(request.plan_id, request.gate_id)
    if existing and existing.get("status") == "approved":
        return self._build_token_from_stored(existing, request)

    # 3. Retrieve preview state (best-effort)
    preview_state = None
    if self._preview_service is not None:
        try:
            preview_state = await self._preview_service.get_preview_state(
                request.plan_id, request.user_id
            )
            if preview_state is not None:
                # Serialize PreviewStepResult models to dicts for storage
                preview_state = {k: v.model_dump() for k, v in preview_state.items()}
        except Exception:
            logger.warning("preview_state_retrieval_failed", extra={
                "plan_id": request.plan_id, "gate_id": request.gate_id,
            })

    # 4. Generate token_id and timestamps
    token_id = ulid.new().str
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=self._token_ttl_s)

    # 5. Build JWT claims and sign
    claims = {
        "plan_id": request.plan_id,
        "user_id": request.user_id,
        "gate_id": request.gate_id,
        "scopes": request.scopes,
        "exp": int(exp.timestamp()),
        "iat": int(now.timestamp()),
        "token_id": token_id,
    }
    jwt_string = self._token_issuer.sign(claims)

    # 6. Store gate state in Redis (best-effort)
    await self._gate_store.store_gate(
        plan_id=request.plan_id,
        gate_id=request.gate_id,
        token_id=token_id,
        preview_state=preview_state,
        selected_option=request.selected_option,
        token_claims=claims,
        ttl_s=self._token_ttl_s,
    )

    # 7. If policy_matched=False, learn from approval (best-effort)
    if not request.policy_matched and request.role and request.tool:
        if self._policy_service is not None:
            try:
                await self._policy_service.learn_from_approval(
                    request.role, request.tool,
                )
            except Exception:
                logger.warning("learn_from_approval_failed", extra={
                    "plan_id": request.plan_id,
                    "role": request.role,
                    "tool": request.tool,
                })

    # 8. Return ApprovalToken
    return ApprovalToken(
        token=jwt_string,
        plan_id=request.plan_id,
        user_id=request.user_id,
        gate_id=request.gate_id,
        scopes=request.scopes,
        exp=exp.isoformat(),
        iat=now.isoformat(),
        token_id=token_id,
    )
```

### 9.2 validate_token() Flow

```python
async def validate_token(
    self, token: str, plan_id: str, gate_id: str | None = None
) -> dict:
    # 1. Verify JWT signature and expiry
    claims = self._token_issuer.verify(token)
    # verify() raises TokenExpiredError or TokenValidationError

    # 2. Validate plan_id match
    if claims["plan_id"] != plan_id:
        raise TokenValidationError("plan_id_mismatch")

    # 3. Validate gate_id match (if provided)
    if gate_id is not None and claims.get("gate_id") != gate_id:
        raise TokenValidationError("gate_id_mismatch")

    # 4. Check single-use: is token already consumed?
    token_id = claims["token_id"]
    if await self._gate_store.is_consumed(token_id):
        raise TokenConsumedError()

    # 5. Mark as consumed (atomic SET NX)
    consumed = await self._gate_store.mark_consumed(
        token_id, ttl_s=self._token_ttl_s,
    )
    if not consumed:
        # Another concurrent call consumed it first
        raise TokenConsumedError()

    # 6. Return decoded claims
    return claims
```

### 9.3 get_gate_status() Flow

```python
async def get_gate_status(self, plan_id: str) -> dict[str, str]:
    # Scan Redis for gate:{plan_id}:* keys
    # For each found gate, return its status
    # Missing/expired gates are omitted (TTL handles cleanup)
    return await self._gate_store.get_all_gates_by_prefix(plan_id)
```

### 9.4 get_approval_state() Flow

```python
async def get_approval_state(
    self, plan_id: str, gate_id: str
) -> ApprovalState | None:
    gate_data = await self._gate_store.get_gate(plan_id, gate_id)
    if gate_data is None:
        return None

    return ApprovalState(
        plan_id=plan_id,
        gate_id=gate_id,
        status=gate_data.get("status", "pending"),
        token_claims=gate_data.get("token_claims", {}),
        preview_state=gate_data.get("preview_state"),
        selected_option=gate_data.get("selected_option"),
        approved_at=gate_data.get("approved_at", ""),
    )
```

---

## 10. Caching Strategy

### 10.1 Redis State

| Data | Key Pattern | TTL | Invalidation |
|------|------------|-----|--------------|
| Gate approval state | `gate:{plan_id}:{gate_id}` | Token TTL (15min) | Auto-expires; re-approval overwrites |
| Consumed token flag | `consumed:{token_id}` | Token TTL (15min) | Auto-expires (cleanup-free) |

### 10.2 Cache Behavior

- **Gate state**: Stored on approval, retrieved by `get_approval_state()` and `get_gate_status()`
- **Consumed tracking**: SET NX on validate, GET on validate — atomic single-use enforcement
- **No explicit invalidation**: TTL-based expiry handles all cleanup. Consumed tokens auto-expire when the original token would have expired.
- **TTL alignment**: Both gate state and consumed-token keys use the same TTL as the JWT token, ensuring consistency

### 10.3 Graceful Degradation

When Redis is unavailable:
- `approve()`: JWT issuance succeeds (CPU-only). Gate state not stored (logged warning). Token still valid.
- `validate_token()`: JWT verification succeeds (CPU-only). Single-use check returns "not consumed" (fail-open). Token expiry still enforced by JWT `exp` claim.
- `get_gate_status()`: Returns empty dict
- `get_approval_state()`: Returns None
- **Risk**: Without Redis, single-use enforcement degrades to JWT-expiry-only. This is acceptable for a short outage (15min window). Logged as warning for ops awareness.

---

## 11. Observability & Safety

### 11.1 Structured Logging

All log entries include `plan_id` and `gate_id` correlation per GLOBAL_SPEC §3:

| Event | Level | Extra Fields |
|-------|-------|-------------|
| `approval_started` | INFO | plan_id, gate_id, user_id, trace_id, scope_count |
| `approval_issued` | INFO | plan_id, gate_id, token_id, exp, scope_count |
| `approval_idempotent` | INFO | plan_id, gate_id (re-approval returned existing) |
| `preview_state_bound` | DEBUG | plan_id, gate_id, step_count |
| `preview_state_retrieval_failed` | WARNING | plan_id, gate_id |
| `token_validated` | INFO | plan_id, gate_id, token_id |
| `token_expired` | WARNING | plan_id, token_id |
| `token_invalid` | WARNING | plan_id, reason |
| `token_consumed` | WARNING | plan_id, token_id (reuse attempt) |
| `gate_status_queried` | DEBUG | plan_id, gate_count |
| `learn_from_approval_called` | INFO | plan_id, role, tool |
| `learn_from_approval_failed` | WARNING | plan_id, role, tool |
| `gate_store_failed` | WARNING | plan_id, gate_id, operation |

### 11.2 No PII/Secrets in Logs

- JWT token values are NEVER logged (only token_id)
- JWT secret is NEVER logged
- User selections (selected_option) are NEVER logged
- Scopes are logged by count only, not content
- Only plan_id, gate_id, token_id (ULID), and structural metadata are logged

### 11.3 Error Classes

| Exception | Domain | When |
|-----------|--------|------|
| `ApprovalError` | Base | Unexpected approval failure |
| `ApprovalConfigError` | `ApprovalError` | Missing JWT secret at startup |
| `InvalidGateError` | `ApprovalError` | Invalid gate_id format |
| `TokenExpiredError` | `ApprovalError` | JWT exp in the past |
| `TokenValidationError` | `ApprovalError` | Signature/plan_id/gate_id/scope mismatch |
| `TokenConsumedError` | `ApprovalError` | Token already used |

### 11.4 Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `approval_issue_duration_seconds` | Histogram | gate_id | Token issuance latency |
| `approval_validate_duration_seconds` | Histogram | result (valid/expired/invalid/consumed) | Token validation latency |
| `approval_issue_total` | Counter | gate_id | Total tokens issued |
| `approval_validate_total` | Counter | result (valid/expired/invalid/consumed) | Total validations by outcome |
| `approval_gate_status_total` | Counter | status (approved/pending/expired) | Gate status distribution |
| `approval_learn_total` | Counter | result (success/failure) | Learn-from-approval calls |
| `approval_redis_operations_total` | Counter | operation (store/get/consume), result (success/failure) | Redis operation tracking |

---

## 12. Dependencies & External Integrations

### 12.1 Python Packages

| Package | Constraint | Justification |
|---------|-----------|---------------|
| `pydantic` | `>=2.0` | Domain models, request/response validation |
| `PyJWT` | `>=2.8` | JWT signing and verification (HS256, RS256) |
| `redis[hiredis]` | `>=5.0` | Gate state and consumed-token tracking |
| `ulid-py` | `>=1.1.0` | ULID generation for token_id |

### 12.2 Internal Dependencies

| Component | Type | What's Used |
|-----------|------|-------------|
| PreviewOrchestrator | Service dependency | `get_preview_state(plan_id, user_id)` — preview state binding |
| PolicyEngine | Service dependency (optional) | `learn_from_approval(role, tool)` — learned policies |
| Redis | Infrastructure | Gate state, consumed tokens (best-effort) |

### 12.3 External Dependencies

| Service | SLA | Usage |
|---------|-----|-------|
| Redis | Best-effort | Gate state, single-use tracking (graceful degradation) |

No external API calls. No MCP invocations. No database writes. Pure CPU + Redis operations.

---

## 13. Non-Functional Requirements

### 13.1 Performance

| Metric | Local Target | Cloud Target | Notes |
|--------|-------------|-------------|-------|
| Token issuance (p95) | < 80ms | < 50ms | CPU-bound JWT sign + Redis SET |
| Token issuance (p99) | < 120ms | < 80ms | Long-tail from Redis latency |
| Token validation (p95) | < 40ms | < 20ms | CPU-bound JWT decode + Redis GET + SET NX |
| Token validation (p99) | < 80ms | < 40ms | Long-tail from Redis latency |
| get_gate_status (p95) | < 30ms | < 15ms | Redis key scan |
| get_approval_state (p95) | < 20ms | < 10ms | Single Redis GET |

### 13.2 Availability

| Environment | Target | Notes |
|-------------|--------|-------|
| Cloud | 99.9% | Per GLOBAL_SPEC §3 (Intake/Preview/Approval tier) |
| Local | Best-effort | Single-process, no HA |

### 13.3 Throughput

| Scenario | Target |
|----------|--------|
| Single-user local | 50 concurrent approvals |
| Multi-user cloud | 200 concurrent approvals |

### 13.4 Testing Strategy

| Test File | Scope | Count (est.) |
|-----------|-------|-------------|
| `test_unit.py` | Core approval logic, multi-gate, single-use, idempotent re-approval, deferral cascade | ~25 |
| `test_service.py` | Token issuance, validation, gate state, preview binding, learn-from-approval | ~20 |
| `test_contract.py` | Model conformance (ApprovalToken vs GLOBAL_SPEC §2.7, schema.json alignment) | ~15 |
| `test_observability.py` | Structured logging, no PII, no secret leakage | ~10 |
| **Total** | | **~70** |

---

## 14. Architectural Considerations

### 14.1 Blast Radius Containment

ApprovalGate is stateless (no owned DB tables). The only persistent side effects are Redis entries with auto-expiration. If ApprovalGate fails:
- No data is corrupted
- No external writes occurred
- User simply retries the approval
- Downstream components degrade gracefully (ExecuteOrchestrator waits for valid token)

### 14.2 Fault Isolation

- **Redis failure**: JWT issuance/validation still works. Single-use degrades to expiry-only. Gate state queries return empty/None.
- **PreviewOrchestrator failure**: Approval proceeds without preview state. ExecuteOrchestrator re-runs previewable steps.
- **PolicyEngine failure**: Approval proceeds without learning. Logged warning. Future spawns may still require approval.
- **JWT secret rotation**: Old tokens remain valid until expiry. New tokens signed with new secret. No coordination needed.

### 14.3 Single-Use Enforcement Design

The single-use mechanism uses a two-phase approach:
1. **Check**: `is_consumed(token_id)` — Redis GET
2. **Consume**: `mark_consumed(token_id)` — Redis SET NX (atomic)

SET NX ensures that even with concurrent validation requests, only one succeeds. The TTL on the consumed key matches the token TTL, providing automatic cleanup.

**Without Redis**: Token validation still works (JWT signature + expiry). The single-use guarantee degrades, but the token will expire within 15 minutes. This is an acceptable trade-off for Redis resilience.

### 14.4 Idempotent Re-Approval

If a user approves the same gate twice (e.g., network retry), the service checks Redis for existing gate state. If the gate is already approved, the original token is reconstructed and returned. This prevents:
- Duplicate token_ids
- Wasted preview state lookups
- Confusing multi-token states for a single gate

### 14.5 JWT vs Opaque Tokens

**Decision**: JWT (HS256) rather than opaque tokens.

**Rationale**:
- ExecuteOrchestrator can validate locally without calling ApprovalGate (no network hop for validation)
- Claims are self-contained (plan_id, user_id, scopes, expiry)
- Standard format with well-tested library (PyJWT)
- HS256 sufficient for single-process deployment; RS256 ready for multi-service
- Trade-off: JWTs cannot be truly revoked (mitigated by single-use Redis tracking + short TTL)

### 14.6 State Management

- **Stateless service**: No persistent state between calls (beyond Redis TTL entries)
- **Ephemeral state**: Redis TTL-based; loss is acceptable (user re-approves)
- **No database writes**: Entire component is Redis + CPU only

---

## 15. Architecture Decision Records

### ADR-001: Library Component (No HTTP Routes)

**Context**: ApprovalGate could expose REST endpoints for approval actions.
**Decision**: Library component consumed internally by API gateway and ExecuteOrchestrator.
**Rationale**: Same pattern as PreviewOrchestrator and PolicyEngine. HTTP endpoints added when the API gateway layer is built. Keeps blast radius small.
**Status**: Accepted.

### ADR-002: JWT Over Opaque Tokens

**Context**: Could use opaque tokens (random strings stored in Redis) or JWTs.
**Decision**: JWT (HS256, PyJWT) with Redis single-use tracking.
**Rationale**: Self-contained claims allow local validation by ExecuteOrchestrator. No network hop for validation. Standard library support. Short TTL mitigates revocation limitations.
**Status**: Accepted.

### ADR-003: Redis-Only State (No PostgreSQL)

**Context**: Could store gate state and consumed tokens in PostgreSQL for durability.
**Decision**: Redis-only with TTL-based auto-cleanup.
**Rationale**: Approval state is inherently ephemeral (15-minute TTL). PostgreSQL adds unnecessary complexity. If Redis is lost, users simply re-approve. No data loss risk for persistent records.
**Status**: Accepted.

### ADR-004: Fail-Open on Redis Unavailability

**Context**: When Redis is down, single-use enforcement cannot work. Options: fail-closed (deny all) or fail-open (allow, rely on JWT expiry).
**Decision**: Fail-open for validation (JWT expiry still enforced). Fail-warning for state storage.
**Rationale**: A Redis outage should not block all plan execution. JWT `exp` provides a 15-minute safety window. Single-use enforcement is defense-in-depth, not the sole security mechanism. Logged warnings alert ops to Redis issues.
**Status**: Accepted.

---

## 16. File Structure

```
components/ApprovalGate/
├── __init__.py
├── LLD.md
├── diagrams/
│   └── flow.md
├── domain/
│   └── models.py                    # ApprovalRequest, ApprovalToken, ApprovalState, exceptions
├── service/
│   └── approval_service.py          # ApprovalService + create_approval_service()
├── adapters/
│   ├── token_issuer.py              # TokenIssuer (JWT signing/verification)
│   └── gate_store.py                # GateStore (Redis gate state + consumed tracking)
└── tests/
    ├── conftest.py                  # Fixtures, mock adapters, sample plans
    ├── test_unit.py                 # Core approval logic, multi-gate, single-use
    ├── test_service.py              # Token issuance, validation, preview binding
    ├── test_contract.py             # Model conformance, GLOBAL_SPEC §2.7
    └── test_observability.py        # Logging, no PII/secrets
```

**Shared files touched:**
- `shared/app.py` — Add `create_approval_service()` in lifespan
- `shared/dependencies.py` — Add `get_approval_service()` accessor

---

## 17. Risks & Open Questions

### 17.1 Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| JWT secret leaked | High | Environment variable only; never logged; rotate-capable. Short TTL (15min) limits exposure window. |
| Token replay attack | High | Single-use enforcement via Redis SET NX. JWT expiry as fallback. |
| Redis unavailable — single-use degrades | Medium | Fail-open (JWT expiry enforced). 15-minute window. Logged warning. |
| Preview state expired by approval time | Low | Graceful degradation — approval succeeds, ExecuteOrchestrator re-runs previewable steps. |
| Concurrent approvals for same gate | Low | Redis atomic operations (SET NX) prevent duplicates. Idempotent re-approval returns existing token. |
| JWT cannot be truly revoked | Low | Short TTL (15min) + single-use tracking. Revocation not needed for this use case. |
| Gate key scan performance | Low | Limited to a few gates per plan (typically 1-3). No performance concern. |

### 17.2 Open Questions

- **OQ-1**: Should ApprovalGate expose HTTP routes? **Current decision**: Library component, routes added with API gateway.
- **OQ-2**: Should JWT secret be shared with ExecuteOrchestrator? **Current decision**: Yes (simpler for single-process deployment). Abstract behind Protocol for future separation.
- **OQ-3**: Should consumed tokens be in Redis or PostgreSQL? **Current decision**: Redis with TTL (auto-cleanup, no DB maintenance).
- **OQ-4**: Should `learn_from_approval()` be synchronous or asynchronous? **Current decision**: Synchronous (fast DB write within approval response).

---

## 18. Post-Generation Validation Checklist

- [x] Data model fields match GLOBAL_SPEC §2.7 Approval Token (token, plan_id, user_id, exp, scopes)
- [x] Data model fields match approval_token.schema.json (gate_id, selected_option, iat, nbf optional)
- [x] `user_id` present on ApprovalRequest (input), ApprovalToken (output), and ApprovalState
- [x] Conformance header references current document versions (GLOBAL_SPEC v3.0, HLD v6.1, MODULAR_ARCHITECTURE v2.1)
- [x] No owned database tables — matches MODULAR_ARCHITECTURE §3 (Redis only: approval_token:{token})
- [x] Component dependencies match MODULAR_ARCHITECTURE §4 (Preview: service call)
- [x] Upstream consumers documented (ExecuteOrchestrator: validate_token, get_approval_state, get_gate_status)
- [x] Idempotent re-approval: duplicate approve() for same gate returns existing token
- [x] No DDL needed (no owned tables)
- [x] Prometheus metrics defined with names and types
- [x] No deprecated library versions (PyJWT >=2.8 is current)
- [x] Error handling follows shared patterns (domain exceptions in domain/models.py)
- [x] Database adapter: N/A (no database access)
- [x] Redis operations use graceful degradation (never fail the approval or validation)
- [x] No PII/secrets in logs (JWT values never logged, selections never logged)

---

**Document Version**: LLD v1.0
**Last Updated**: 2026-04-05
**Author**: Design workflow
