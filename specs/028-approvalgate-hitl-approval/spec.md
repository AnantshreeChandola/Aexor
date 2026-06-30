# Feature Specification: ApprovalGate

**Feature Branch**: `feat/approvalgate-hitl-approval`
**Created**: 2026-04-05
**Status**: Draft
**Spec ID**: 028
**Input**: User description: "ApprovalGate -- HITL approval tokens with multi-gate support, preview state binding, and Redis-backed async approval flow"

---

## Overview

ApprovalGate is an **Orchestration Layer** library component that provides the human-in-the-loop (HITL) safety mechanism between PreviewOrchestrator and ExecuteOrchestrator. It issues JWT approval tokens after user confirmation, binds preview state (user selections, cached results) to the token, manages multi-gate approval sequences (e.g., gate-A → gate-B → gate-C in a shopping flow), and coordinates with PolicyEngine to learn from approval decisions. This is the enforcement point of the **preview-first safety model** (GLOBAL_SPEC v3.0 §1) — no write operation executes without a valid approval token.

---

## User Scenarios & Testing

### User Story 1 - Approve a Previewed Plan (Priority: P1)

The user previewed a meeting booking plan and selected "Tuesday 10:00-10:30." They click "Approve" and ApprovalGate issues a JWT token binding the plan_id, user_id, gate_id, scopes, and user selection. ExecuteOrchestrator later validates this token to proceed with execution.

**Why this priority**: This is the core value proposition — the gateway between "show me" and "do it." Without this, no plan can ever execute.

**Independent Test**: Can be fully tested by calling `approve()` with a plan_id, user_id, gate_id, scopes, and user selection, then verifying the returned token is a valid JWT with correct claims.

**Acceptance Scenarios**:

1. **Given** a valid plan_id and user_id with scopes `["calendar.write"]`, **When** `approve(request)` is called, **Then** a JWT token is returned containing `plan_id`, `user_id`, `gate_id`, `scopes`, and `exp` (15min TTL).
2. **Given** an approval request with `selected_option: {"slot": "Tuesday 10:00"}`, **When** the token is issued, **Then** the selected option is stored in Redis alongside the approval state (not embedded in the JWT).
3. **Given** a valid approval token, **When** `validate_token(token, plan_id)` is called, **Then** it returns the decoded claims if the token is valid, not expired, and plan_id matches.
4. **Given** a valid approval token, **When** `validate_token()` is called a second time with the same token, **Then** the token is rejected (single-use enforcement).

---

### User Story 2 - Multi-Gate Approval Flow (Priority: P1)

A shopping plan has 3 gates: gate-A (choose item), gate-B (review cart), gate-C (confirm purchase). Each gate requires separate user approval. ApprovalGate issues gate-specific tokens and tracks which gates have been approved for a given plan.

**Why this priority**: Multi-gate is essential for any plan with sequential write steps that each need user confirmation (shopping, booking, payments).

**Independent Test**: Create a plan with 3 gate_ids, approve them sequentially, and verify each produces a distinct token bound to its gate_id.

**Acceptance Scenarios**:

1. **Given** a plan with steps having `gate_id: "gate-A"`, `gate_id: "gate-B"`, `gate_id: "gate-C"`, **When** the user approves gate-A, **Then** a token is issued with `gate_id: "gate-A"` and gates B and C remain pending.
2. **Given** gate-A is approved, **When** `get_gate_status(plan_id)` is called, **Then** it returns `{"gate-A": "approved", "gate-B": "pending", "gate-C": "pending"}`.
3. **Given** gate-A is approved but gate-B is not, **When** ExecuteOrchestrator requests validation for gate-B, **Then** validation fails (token for gate-A does not cover gate-B).
4. **Given** all 3 gates are approved sequentially, **When** `get_gate_status(plan_id)` is called, **Then** all gates show `"approved"`.

---

### User Story 3 - Preview State Binding (Priority: P1)

After preview, ApprovalGate retrieves the cached preview state from PreviewOrchestrator and binds it to the approval. ExecuteOrchestrator can then skip re-running previewable steps by using this cached state.

**Why this priority**: This is the key optimization — preview state reuse eliminates redundant work during execution.

**Independent Test**: Approve a plan, then call `get_approval_state()` and verify it includes the preview results from PreviewOrchestrator.

**Acceptance Scenarios**:

1. **Given** a completed preview with cached state for plan_id, **When** the user approves, **Then** ApprovalGate retrieves preview state via `PreviewService.get_preview_state(plan_id, user_id)` and stores it with the approval.
2. **Given** an approved plan, **When** `get_approval_state(plan_id, gate_id)` is called, **Then** it returns the token claims, user selection, and cached preview state.
3. **Given** a preview cache miss (expired or Redis down), **When** the user approves, **Then** approval still succeeds (preview state is None; ExecuteOrchestrator will re-run previewable steps).

---

### User Story 4 - Spawned Step Gate Approval (Priority: P2)

During execution, a Tier 2 Reasoner spawns a Booker step. PolicyEngine flags it as `requires_approval: true`. ExecuteOrchestrator pauses and requests ApprovalGate to issue a gate token for the spawned step. The user reviews and approves. ApprovalGate optionally calls PolicyEngine to learn from the approval.

**Why this priority**: Spawned gate approval is the runtime safety mechanism for adaptive execution. It only triggers for plans with LLM reasoning steps.

**Independent Test**: Simulate a spawned step approval request with `policy_matched=False`, approve it, and verify `learn_from_approval()` is called on PolicyEngine.

**Acceptance Scenarios**:

1. **Given** a spawned Booker step with `gate_id: "gate-spawn-8"`, **When** `approve(request)` is called, **Then** a token is issued for the spawned gate.
2. **Given** the approval request includes `policy_matched: False` (no existing policy matched), **When** the user approves, **Then** ApprovalGate calls `PolicyEngine.learn_from_approval(role, tool)` so future similar spawns auto-approve.
3. **Given** the approval request includes `policy_matched: True` (existing policy matched), **When** the user approves, **Then** no learning call is made (policy already exists).

---

### User Story 5 - Token Expiration and Rejection (Priority: P2)

A user's approval token has expired (past the 15-minute TTL). When ExecuteOrchestrator tries to validate it, the token is rejected, and the user must re-approve.

**Why this priority**: Security — expired tokens must not allow execution.

**Independent Test**: Issue a token with a past expiration, attempt validation, verify rejection with clear error.

**Acceptance Scenarios**:

1. **Given** a token with `exp` in the past, **When** `validate_token()` is called, **Then** `TokenExpiredError` is raised.
2. **Given** a token with `plan_id` that doesn't match the request, **When** `validate_token()` is called, **Then** `TokenValidationError` is raised with reason `"plan_id_mismatch"`.
3. **Given** a token with insufficient scopes (token scopes don't cover required scopes), **When** `validate_token()` is called, **Then** `TokenValidationError` is raised with reason `"insufficient_scopes"`.

---

### Edge Cases

- What happens when Redis is unavailable for gate state storage? Approval still succeeds; gate state tracking degrades gracefully (logged warning). Token is still valid JWT that can be validated without Redis.
- What happens when the same gate_id is approved twice? Second approval is idempotent — returns the existing token (does not issue a duplicate).
- What happens when a gate_id not in the plan is submitted? `InvalidGateError` is raised.
- What happens when the JWT signing key is not configured? `ApprovalConfigError` is raised at service startup.
- What happens when a user tries to approve someone else's plan? `TokenValidationError` with reason `"user_id_mismatch"`.

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST issue JWT approval tokens (HS256 signed) with configurable TTL (default 15 minutes).
- **FR-002**: System MUST bind tokens to `{plan_id, user_id, gate_id, scopes, exp}` per GLOBAL_SPEC §2.7.
- **FR-003**: System MUST enforce single-use tokens — a consumed token cannot be reused.
- **FR-004**: System MUST support multi-gate approval flows with independent per-gate tokens.
- **FR-005**: System MUST track gate approval status in Redis (`gate:{plan_id}:{gate_id}`, TTL-based).
- **FR-006**: System MUST retrieve and bind preview state from PreviewOrchestrator on approval.
- **FR-007**: System MUST validate tokens: signature, expiry, plan_id match, user_id match, scope coverage.
- **FR-008**: System MUST call `PolicyEngine.learn_from_approval()` when approving a spawn with `policy_matched=False`.
- **FR-009**: System MUST provide `get_gate_status(plan_id)` for downstream gate state queries.
- **FR-010**: System MUST provide `get_approval_state(plan_id, gate_id)` returning token claims + preview state + user selection.
- **FR-011**: System MUST log all approval operations with `plan_id`, `gate_id` correlation, no PII/secrets.
- **FR-012**: System MUST handle Redis unavailability gracefully — token issuance and validation work without Redis (gate state tracking degrades).

### Key Entities

- **ApprovalRequest**: plan_id, user_id, gate_id, scopes, selected_option, trace_id, policy_matched -- input to `approve()`.
- **ApprovalToken**: token (JWT string), plan_id, user_id, gate_id, scopes, exp, iat -- issued by `approve()`.
- **ApprovalState**: token claims + preview_state + selected_option -- returned by `get_approval_state()`.
- **GateStatus**: Map of `gate_id -> status` (pending/approved/expired) for a plan.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Token issuance completes in p95 < 50ms (CPU-bound JWT signing + Redis SET).
- **SC-002**: Token validation completes in p95 < 20ms (CPU-bound JWT decode + Redis GET for single-use check).
- **SC-003**: Multi-gate flow (3 gates) completes all approvals with correct per-gate isolation (verified by integration test).
- **SC-004**: 100% of approval operations logged with plan_id, gate_id correlation.
- **SC-005**: Zero token reuse — single-use enforcement verified by contract test.

---

## Interfaces & Contracts

### Service Interface

```python
class ApprovalService:
    async def approve(self, request: ApprovalRequest) -> ApprovalToken:
        """Issue an approval token for a plan gate.

        Flow:
            1. Validate request (plan_id, user_id, gate_id, scopes)
            2. Retrieve preview state from PreviewOrchestrator (best-effort)
            3. Sign JWT with claims {plan_id, user_id, gate_id, scopes, exp, iat}
            4. Store gate state in Redis (approved, token_id, preview_state, selected_option)
            5. If policy_matched=False: call PolicyEngine.learn_from_approval()
            6. Return ApprovalToken

        Raises:
            InvalidGateError: If gate_id is invalid for this plan.
            ApprovalError: If approval fails for any other reason.
        """

    async def validate_token(
        self, token: str, plan_id: str, gate_id: str | None = None
    ) -> dict:
        """Validate an approval token and mark it as consumed.

        Returns decoded claims if valid.

        Raises:
            TokenExpiredError: If token has expired.
            TokenValidationError: If signature, plan_id, or scopes invalid.
            TokenConsumedError: If token was already used (single-use).
        """

    async def get_gate_status(self, plan_id: str) -> dict[str, str]:
        """Get approval status for all gates of a plan.

        Returns dict of gate_id -> status (pending/approved/expired).
        """

    async def get_approval_state(
        self, plan_id: str, gate_id: str
    ) -> ApprovalState | None:
        """Get full approval state including preview results and user selection.

        Returns None if gate not found or expired.
        """
```

### Factory Function

```python
def create_approval_service(
    preview_service: PreviewService,
    policy_service: PolicyService | None = None,
    redis_client: object | None = None,
    jwt_secret: str = "",
    token_ttl_s: int = 900,
) -> ApprovalService:
    """Create ApprovalService with all dependencies."""
```

### Approval Token (output per GLOBAL_SPEC §2.7)

```json
{
  "token": "eyJ...",
  "plan_id": "01JXYZ...",
  "user_id": "user-uuid-123",
  "gate_id": "gate-A",
  "scopes": ["calendar.write"],
  "exp": "2026-04-05T10:15:00Z",
  "iat": "2026-04-05T10:00:00Z"
}
```

### Approval State (returned by get_approval_state)

```json
{
  "plan_id": "01JXYZ...",
  "gate_id": "gate-A",
  "status": "approved",
  "token_claims": { "plan_id": "...", "user_id": "...", "scopes": [...] },
  "preview_state": { "1": {"status": "completed", "result": {...}}, "2": {...} },
  "selected_option": { "slot": "Tuesday 10:00-10:30" },
  "approved_at": "2026-04-05T10:00:00Z"
}
```

Reference: docs/architecture/GLOBAL_SPEC.md (v3.0)

---

## Component Mapping

- **Target**: `components/ApprovalGate/`
- **Files expected to change**:
  - `components/ApprovalGate/__init__.py`
  - `components/ApprovalGate/domain/models.py` -- ApprovalRequest, ApprovalToken, ApprovalState, GateStatus, custom exceptions
  - `components/ApprovalGate/service/approval_service.py` -- ApprovalService with `approve()`, `validate_token()`, `get_gate_status()`, `get_approval_state()`
  - `components/ApprovalGate/adapters/token_issuer.py` -- JWT signing/verification (PyJWT)
  - `components/ApprovalGate/adapters/gate_store.py` -- Redis gate state storage
  - `components/ApprovalGate/tests/conftest.py` -- Fixtures, mock adapters, sample plans
  - `components/ApprovalGate/tests/test_unit.py` -- Core approval logic, multi-gate, single-use
  - `components/ApprovalGate/tests/test_service.py` -- Token issuance, validation, gate state
  - `components/ApprovalGate/tests/test_contract.py` -- Model conformance, GLOBAL_SPEC §2.7
  - `components/ApprovalGate/tests/test_observability.py` -- Logging, no PII
- **Shared files touched**:
  - `shared/app.py` -- DI wiring: `create_approval_service()` in lifespan
  - `shared/dependencies.py` -- `get_approval_service()` accessor

### Dependencies (import, no duplication)

| Dependency | Source | Usage |
|-----------|--------|-------|
| PreviewService | `components/PreviewOrchestrator/service/preview_service.py` | `get_preview_state()` for preview state binding |
| PolicyService | `components/PolicyEngine/service/policy_service.py` | `learn_from_approval()` for learned policies |
| Plan, PlanStep | `shared/schemas/plan.py` | Plan input model (gate_id field) |

---

## Dependencies & Risks

### Dependencies

| Dependency | Type | Risk |
|-----------|------|------|
| PreviewOrchestrator | Internal (service call) | `get_preview_state()` for preview state. Graceful degradation if unavailable. |
| PolicyEngine | Internal (service call) | `learn_from_approval()` for learned policies. Optional — approval succeeds without it. |
| Redis | External | Gate state storage, single-use token tracking. Graceful degradation if unavailable. |
| PyJWT | Python package | JWT signing/verification. Must be in pyproject.toml. |

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| JWT secret leaked | High | Environment variable only; never logged; rotate-capable |
| Token replay attack | High | Single-use enforcement via Redis consumed-token set |
| Redis unavailable — single-use check fails | Medium | Fail-open for reads (allow execution), fail-closed for writes (log warning). JWT signature + expiry still enforced without Redis. |
| Preview state expired by approval time | Low | Graceful degradation — approval succeeds, ExecuteOrchestrator re-runs previewable steps |
| Concurrent approvals for same gate | Low | Redis atomic operations (SET NX) prevent race conditions |

---

## Non-Functional Requirements

Inherit baseline from GLOBAL_SPEC v3.0 §3, with these specifics:

| Requirement | Target | Notes |
|------------|--------|-------|
| Token issuance latency (p95) | < 50ms | CPU-bound JWT sign + Redis SET |
| Token validation latency (p95) | < 20ms | CPU-bound JWT decode + Redis GET |
| Token TTL | 900s (15min) | Configurable via `APPROVAL_TOKEN_TTL_S` env var |
| Redis gate key pattern | `gate:{plan_id}:{gate_id}` | Namespaced, TTL-based |
| Redis consumed token pattern | `consumed:{token_id}` | TTL matches token expiry |
| Structured logging | plan_id, gate_id, latency_ms | Correlated by plan_id per GLOBAL_SPEC §3 |
| No PII/secrets in logs | Enforced | Token values hashed in logs, no user selections logged |
| JWT signing algorithm | HS256 | Configurable; RS256 for production |

---

## Open Questions

- **OQ-1**: Should ApprovalGate expose HTTP routes or remain a library component? Current recommendation: library component (same pattern as PreviewOrchestrator, PolicyEngine), with routes added when the API gateway layer is built.
- **OQ-2**: Should the JWT secret be shared with ExecuteOrchestrator for validation, or should ExecuteOrchestrator call ApprovalGate to validate? Current recommendation: shared secret (simpler for single-process deployment); abstract behind Protocol for future separation.
- **OQ-3**: Should consumed tokens be tracked in Redis or PostgreSQL? Current recommendation: Redis with TTL matching token expiry (auto-cleanup, no DB maintenance).
- **OQ-4**: Should `learn_from_approval()` be called synchronously during approval or asynchronously? Current recommendation: synchronous (fast DB write; approval response waits for it).

---

## Conformance

This work conforms to `docs/architecture/GLOBAL_SPEC.md v3.0`:
- Approval Token per §2.7
- Safety model per §1 (Execute only after approval)
- NFRs per §3 (structured logging, no PII)
- Observability per §3 (plan_id correlation)

This work conforms to `docs/architecture/Project_HLD.md v6.1`:
- ApprovalGate described in Layer 3 and §2a Step 4
- Multi-gate support per shopping example §6
- Preview state caching per ApprovalGate section

This work conforms to `docs/architecture/MODULAR_ARCHITECTURE.md v2.1`:
- Orchestration Layer placement
- Dependencies: PreviewOrchestrator (service call), PolicyEngine (optional)
- Redis key patterns per §3
