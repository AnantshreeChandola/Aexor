# Tasks: ApprovalGate

**Created**: 2026-04-05
**Branch**: feat/approvalgate-hitl-approval
**SPEC**: specs/028-approvalgate-hitl-approval/spec.md
**LLD**: components/ApprovalGate/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture (domain models, adapters, service, DI wiring, observability, tests). ApprovalGate is a **library component** -- no HTTP routes, no owned database tables, no API handler phase. Redis-only ephemeral state.

**Important -- JWT library alignment**: The LLD specifies PyJWT, but the existing codebase uses `python-jose` (`from jose import jwt`) consistently across ExecuteOrchestrator, AuthMiddleware, and auth routes. The implementation MUST use `python-jose` (already in `pyproject.toml` as `python-jose[cryptography]>=3.3.0`) for consistency. The TokenIssuer adapter wraps `jose.jwt.encode()` / `jose.jwt.decode()` instead of `jwt.encode()` / `jwt.decode()`.

---

## Phase 0: Setup & Scaffolding

### Install Dependencies (from LLD.md Section 12)

- [ ] [T000] Create `components/ApprovalGate/__init__.py` with module docstring.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/__init__.py`
  - Content: Empty module init with docstring referencing SPEC 028 and stating this is a library component (no HTTP routes).

- [ ] [T001] Create directory structure for the component.
  - Directories to create (via `__init__.py` files in each):
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/domain/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/adapters/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/__init__.py`
  - Each `__init__.py` is empty or contains a module-level docstring.

- [ ] [T002] Verify Python package dependencies are already available.
  - Packages required (from LLD Section 12.1):
    - `pydantic>=2.0` -- already in `pyproject.toml`
    - `python-jose[cryptography]>=3.3.0` -- already in `pyproject.toml` (used instead of PyJWT per codebase consistency)
    - `redis[hiredis]>=5.0` -- already in `pyproject.toml`
    - `ulid-py>=1.1.0` -- already in `pyproject.toml`
  - Verify: `pip install -e .` succeeds and `from jose import jwt; import redis; import ulid` work.
  - No new dependencies need to be added to `pyproject.toml`.

- [ ] [T003] Verify internal component dependencies are importable.
  - Verify these imports resolve without error:
    - `from components.PreviewOrchestrator.service.preview_service import PreviewService` (for `get_preview_state()`)
    - `from components.PreviewOrchestrator.domain.models import PreviewStepResult` (for type hints)
    - `from components.PolicyEngine.service.policy_service import PolicyService` (for `learn_from_approval()`)
    - `from shared.schemas.plan import Plan, PlanStep` (for `gate_id` field)
    - `from shared.schemas.policy import PolicyRule, PolicyDecision` (for `policy_matched` field)

---

## Phase 1: Domain Models & Exceptions (Foundation)

### Acceptance Criteria: SPEC FR-001, FR-002 (token binding), FR-007 (validation), SPEC Key Entities

- [ ] [T100] Create domain models and custom exceptions in `components/ApprovalGate/domain/models.py`.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/domain/models.py`
  - Classes to implement (per LLD Section 5.1, 5.2):
    - `ApprovalRequest(BaseModel)` -- `plan_id: str` (26-char ULID, min/max_length=26), `user_id: str` (min_length=1), `gate_id: str` (default `"gate-A"`, pattern `^gate-[A-Za-z0-9]+$`), `scopes: list[str]` (min_length=1, max_length=10), `selected_option: dict[str, Any] | None` (default None), `trace_id: str` (default `""`), `policy_matched: bool` (default True), `role: str | None` (default None), `tool: str | None` (default None)
    - `ApprovalToken(BaseModel)` -- `token: str`, `plan_id: str` (26-char ULID), `user_id: str`, `gate_id: str`, `scopes: list[str]`, `exp: str` (ISO 8601), `iat: str` (ISO 8601), `token_id: str` (ULID)
    - `ApprovalState(BaseModel)` -- `plan_id: str` (26-char ULID), `gate_id: str`, `status: Literal["approved", "pending", "expired"]`, `token_claims: dict[str, Any]`, `preview_state: dict[int, dict[str, Any]] | None`, `selected_option: dict[str, Any] | None`, `approved_at: str`
    - `ApprovalError(Exception)` -- base error
    - `ApprovalConfigError(ApprovalError)` -- missing JWT secret at startup
    - `InvalidGateError(ApprovalError)` -- invalid gate_id (stores `gate_id` attribute)
    - `TokenExpiredError(ApprovalError)` -- token exp in the past
    - `TokenValidationError(ApprovalError)` -- signature/plan_id/scope mismatch (stores `reason` attribute)
    - `TokenConsumedError(ApprovalError)` -- token already used (single-use enforcement)
  - GLOBAL_SPEC S2.7 alignment: `ApprovalToken` maps directly to the Approval Token contract -- `token`, `plan_id`, `user_id`, `exp`, `scopes`, extended with `gate_id`, `iat`, and `token_id`.
  - `approval_token.schema.json` alignment: All required fields (`token`, `plan_id`, `user_id`, `exp`, `scopes`) present. Optional fields (`gate_id`, `iat`) supported.

---

## Phase 2: Adapters (TokenIssuer & GateStore)

### Acceptance Criteria: SPEC FR-001 (JWT issuance), FR-003 (single-use), FR-005 (Redis gate state), FR-012 (Redis graceful degradation)

- [ ] [T200] Implement `TokenIssuer` in `components/ApprovalGate/adapters/token_issuer.py`.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/adapters/token_issuer.py`
  - Class: `TokenIssuer`
  - Constructor: `__init__(self, secret: str, algorithm: str = "HS256")`
  - Methods (per LLD Section 6.1):
    - `def sign(self, claims: dict[str, Any]) -> str` -- Sign claims into a JWT string using `jose.jwt.encode(claims, self._secret, algorithm=self._algorithm)`. Claims must include: `plan_id`, `user_id`, `gate_id`, `scopes`, `exp`, `iat`, `token_id`. Returns JWT string.
    - `def verify(self, token: str) -> dict[str, Any]` -- Verify and decode JWT using `jose.jwt.decode(token, self._secret, algorithms=[self._algorithm])`. Returns decoded claims dict. Raises `TokenExpiredError` on `jose.ExpiredSignatureError`. Raises `TokenValidationError("invalid_signature")` on `jose.JWTError`.
  - Design decisions per LLD Section 6.1:
    - `exp` and `iat` stored as Unix timestamps in JWT (standard).
    - `selected_option` NOT embedded in JWT (may be large) -- stored in Redis.
    - `token_id` (ULID) used as key for single-use consumed tracking.
    - Uses `python-jose` (`from jose import jwt, ExpiredSignatureError, JWTError`) for codebase consistency.

- [ ] [T201] Implement `GateStore` in `components/ApprovalGate/adapters/gate_store.py`.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/adapters/gate_store.py`
  - Class: `GateStore`
  - Constructor: `__init__(self, redis_client: object | None, default_ttl_s: int = 900)`
  - Methods (per LLD Section 6.2):
    - `async def store_gate(self, plan_id, gate_id, token_id, preview_state, selected_option, token_claims, ttl_s=None) -> bool` -- Key: `gate:{plan_id}:{gate_id}`. Value: JSON with `{status, token_id, preview_state, selected_option, token_claims, approved_at}`. TTL matches token TTL. Returns True on success, False on Redis failure.
    - `async def get_gate(self, plan_id, gate_id) -> dict | None` -- Retrieve gate state. Returns None if missing/expired/Redis down.
    - `async def get_all_gates(self, plan_id, gate_ids) -> dict[str, str]` -- Get status for multiple gates by specific gate_ids. Returns gate_id -> status mapping.
    - `async def get_all_gates_by_prefix(self, plan_id) -> dict[str, str]` -- Scan Redis for `gate:{plan_id}:*` keys. Returns gate_id -> status mapping. Used by `get_gate_status()`.
    - `async def mark_consumed(self, token_id, ttl_s) -> bool` -- Key: `consumed:{token_id}`. Uses Redis SET NX with TTL for atomic single-use enforcement. Returns True if successfully marked (first use), False if already consumed. Returns True if Redis unavailable (fail-open per ADR-004).
    - `async def is_consumed(self, token_id) -> bool` -- Check if token was already consumed. Returns False if Redis unavailable (fail-open per ADR-004).
  - **Graceful degradation** (per LLD Section 6.2): All Redis operations wrapped in `try/except Exception`. Failures logged as warnings via `logging.getLogger(__name__)`, never propagated. On Redis failure:
    - `store_gate()` returns False (logged warning)
    - `get_gate()` returns None
    - `mark_consumed()` returns True (fail-open -- JWT expiry still enforced)
    - `is_consumed()` returns False (fail-open)
  - **Concurrent approval protection**: `mark_consumed()` uses Redis `SET NX` (set if not exists) for atomic single-use enforcement.
  - Redis key patterns per LLD Section 6.2:
    - `gate:{plan_id}:{gate_id}` -- gate approval state (TTL: token TTL)
    - `consumed:{token_id}` -- single-use flag (TTL: token TTL)

---

## Phase 3: Test Fixtures & Conftest

### Supporting infrastructure for all test files

- [ ] [T300] Create `conftest.py` with fixtures, mock adapters, and sample data.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/conftest.py`
  - Fixtures to implement:
    - `jwt_secret()` -- Returns a test JWT secret string (e.g., `"test-approval-gate-secret-key-minimum-32-chars"`).
    - `token_ttl_s()` -- Returns default token TTL: `900` (15 minutes).
    - `sample_plan_id()` -- Returns a hardcoded 26-char ULID string (e.g., `"01JXYZ1234567890ABCDEFGHIJ"`).
    - `sample_user_id()` -- Returns a test UUID string (e.g., `"user-uuid-12345678-abcd-efgh"`).
    - `sample_gate_ids()` -- Returns `["gate-A", "gate-B", "gate-C"]` for multi-gate testing.
    - `sample_scopes()` -- Returns `["calendar.write"]`.
    - `sample_approval_request(sample_plan_id, sample_user_id, sample_scopes)` -- Returns an `ApprovalRequest` with default values.
    - `sample_approval_request_multi_gate(sample_plan_id, sample_user_id, sample_scopes)` -- Returns a factory function that creates `ApprovalRequest` for a given gate_id.
    - `sample_approval_request_spawned(sample_plan_id, sample_user_id)` -- Returns an `ApprovalRequest` with `policy_matched=False`, `role="Fetcher"`, `tool="google.calendar"`, `gate_id="gate-spawn-8"`.
    - `token_issuer(jwt_secret)` -- Returns a `TokenIssuer` instance.
    - `mock_redis_client()` -- Fake async Redis client with in-memory dict storage. Supports `set()`, `get()`, `delete()`, `keys()` with TTL tracking. Supports `set(..., nx=True)` for SET NX semantics. Supports `set(..., ex=...)` for TTL.
    - `gate_store(mock_redis_client, token_ttl_s)` -- Returns a `GateStore` instance with mock Redis.
    - `gate_store_no_redis(token_ttl_s)` -- Returns a `GateStore` instance with `redis_client=None`.
    - `mock_preview_service()` -- Mock for `PreviewService.get_preview_state()`. Returns configurable dict of step results. Supports raising exceptions.
    - `mock_policy_service()` -- Mock for `PolicyService.learn_from_approval()`. Returns a `PolicyRule`. Supports raising exceptions.
    - `approval_service(token_issuer, gate_store, mock_preview_service, mock_policy_service, jwt_secret, token_ttl_s)` -- Returns an `ApprovalService` instance with all dependencies wired.
    - `approval_service_minimal(jwt_secret, token_ttl_s)` -- Returns an `ApprovalService` with `preview_service=None`, `policy_service=None`, `redis_client=None`. Tests graceful degradation.
  - Import `ApprovalRequest`, `ApprovalToken`, `ApprovalState` from `components.ApprovalGate.domain.models`.
  - Import `TokenIssuer` from `components.ApprovalGate.adapters.token_issuer`.
  - Import `GateStore` from `components.ApprovalGate.adapters.gate_store`.

---

## Phase 4: Service Layer (Core Approval Logic)

### Acceptance Criteria: SPEC US1 (approve plan), US2 (multi-gate), US3 (preview state binding), US4 (spawned step gate), US5 (token expiration/rejection), FR-001 through FR-012

- [ ] [T400] Implement `ApprovalService` class structure and constructor in `components/ApprovalGate/service/approval_service.py`.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/approval_service.py`
  - Class: `ApprovalService`
  - Constructor dependencies (injected per LLD Section 4.1):
    - `_token_issuer: TokenIssuer` (from adapters)
    - `_gate_store: GateStore` (from adapters)
    - `_preview_service: Any | None` (PreviewOrchestrator, optional)
    - `_policy_service: Any | None` (PolicyEngine, optional)
    - `_token_ttl_s: int` (default 900)
  - Public methods (signatures only in this task):
    - `async def approve(self, request: ApprovalRequest) -> ApprovalToken`
    - `async def validate_token(self, token: str, plan_id: str, gate_id: str | None = None) -> dict`
    - `async def get_gate_status(self, plan_id: str) -> dict[str, str]`
    - `async def get_approval_state(self, plan_id: str, gate_id: str) -> ApprovalState | None`

- [ ] [T401] Implement `approve()` core algorithm per LLD Section 9.1.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/approval_service.py` (within `ApprovalService`)
  - Steps (per LLD Section 9.1 pseudocode):
    1. Validate request: scopes must not be empty (raise `ApprovalError`).
    2. Check idempotency: call `self._gate_store.get_gate(plan_id, gate_id)`. If gate already approved, reconstruct and return existing `ApprovalToken` from stored data via `_build_token_from_stored()`.
    3. Retrieve preview state from `self._preview_service.get_preview_state(plan_id, user_id)` (best-effort; wrapped in try/except, logs warning on failure, sets `preview_state=None`). Serialize `PreviewStepResult` models to dicts via `model_dump()` if returned.
    4. Generate `token_id` via `ulid.new().str`, compute `now` and `exp` (UTC).
    5. Build JWT claims dict: `{plan_id, user_id, gate_id, scopes, exp (unix timestamp), iat (unix timestamp), token_id}`.
    6. Sign JWT via `self._token_issuer.sign(claims)`.
    7. Store gate state via `self._gate_store.store_gate(...)` (best-effort; failure logged, not propagated).
    8. If `policy_matched=False` and `request.role` and `request.tool`: call `self._policy_service.learn_from_approval(role, tool)` (best-effort; wrapped in try/except, logs warning on failure).
    9. Return `ApprovalToken` with JWT string, metadata, and ISO 8601 timestamps.
  - Private helper: `_build_token_from_stored(existing_gate_data, request) -> ApprovalToken` -- reconstructs an `ApprovalToken` from stored gate data for idempotent re-approval.

- [ ] [T402] Implement `validate_token()` per LLD Section 9.2.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/approval_service.py` (within `ApprovalService`)
  - Steps (per LLD Section 9.2 pseudocode):
    1. Verify JWT signature and expiry via `self._token_issuer.verify(token)`. Raises `TokenExpiredError` or `TokenValidationError`.
    2. Validate `claims["plan_id"] == plan_id`. If mismatch, raise `TokenValidationError("plan_id_mismatch")`.
    3. If `gate_id is not None`, validate `claims.get("gate_id") == gate_id`. If mismatch, raise `TokenValidationError("gate_id_mismatch")`.
    4. Check single-use: `await self._gate_store.is_consumed(token_id)`. If True, raise `TokenConsumedError()`.
    5. Mark as consumed: `await self._gate_store.mark_consumed(token_id, ttl_s=self._token_ttl_s)`. If returns False (concurrent race), raise `TokenConsumedError()`.
    6. Return decoded claims dict.

- [ ] [T403] Implement `get_gate_status()` per LLD Section 9.3.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/approval_service.py` (within `ApprovalService`)
  - Delegates to `self._gate_store.get_all_gates_by_prefix(plan_id)`.
  - Returns `dict[str, str]` -- gate_id -> status mapping.
  - Returns empty dict if Redis unavailable.

- [ ] [T404] Implement `get_approval_state()` per LLD Section 9.4.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/approval_service.py` (within `ApprovalService`)
  - Retrieves gate data via `self._gate_store.get_gate(plan_id, gate_id)`.
  - If None, returns None.
  - Otherwise, constructs and returns `ApprovalState` from gate data.

- [ ] [T405] Implement `create_approval_service()` factory function per LLD Section 4.2.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/approval_service.py` (module-level function)
  - Signature:
    ```python
    def create_approval_service(
        preview_service: Any | None = None,
        policy_service: Any | None = None,
        redis_client: Any | None = None,
        jwt_secret: str = "",
        token_ttl_s: int = 900,
    ) -> ApprovalService:
    ```
  - Logic:
    1. Read `APPROVAL_TOKEN_SECRET` from `os.environ` (fallback to `jwt_secret` parameter). If empty, raise `ApprovalConfigError("JWT secret not configured")`.
    2. Read `APPROVAL_TOKEN_TTL_S` from `os.environ` (fallback to `token_ttl_s` parameter). Parse to int.
    3. Create `TokenIssuer(secret, algorithm="HS256")`.
    4. Create `GateStore(redis_client, default_ttl_s=token_ttl_s)`.
    5. Return `ApprovalService(token_issuer, gate_store, preview_service, policy_service, token_ttl_s)`.
  - Raises `ApprovalConfigError` if JWT secret is empty or too short (< 16 chars).

---

## Phase 5: Unit Tests (Core Logic)

### Acceptance Criteria: SPEC US1-US5, SC-001 through SC-005

- [ ] [T500] Write unit tests for domain models and exceptions.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/test_unit.py`
  - Test cases (~25):
    - **ApprovalRequest validation**:
      - Accepts valid request with all fields.
      - Rejects `plan_id` shorter than 26 chars.
      - Rejects `plan_id` longer than 26 chars.
      - Rejects empty `user_id`.
      - Rejects `gate_id` not matching pattern `^gate-[A-Za-z0-9]+$` (e.g., `"invalid"`, `"gate-"`, `"GATE-A"`).
      - Rejects empty `scopes` list.
      - Rejects `scopes` list with more than 10 items.
      - Accepts `selected_option=None` (default).
      - Accepts `policy_matched=True` (default).
    - **ApprovalToken validation**:
      - Round-trip: `model_dump()` then `model_validate()` produces identical model.
      - All required fields present (token, plan_id, user_id, gate_id, scopes, exp, iat, token_id).
      - `plan_id` enforces 26-char ULID constraint.
    - **ApprovalState validation**:
      - Status accepts all three values: `"approved"`, `"pending"`, `"expired"`.
      - Status rejects invalid value (e.g., `"unknown"`).
      - `preview_state` defaults to None.
      - `selected_option` defaults to None.
    - **Exception hierarchy**:
      - `ApprovalError` is base class.
      - `ApprovalConfigError` is subclass of `ApprovalError`.
      - `InvalidGateError` stores `gate_id` attribute and is subclass of `ApprovalError`.
      - `TokenExpiredError` is subclass of `ApprovalError`.
      - `TokenValidationError` stores `reason` attribute and is subclass of `ApprovalError`.
      - `TokenConsumedError` is subclass of `ApprovalError`.
    - **Idempotent re-approval**:
      - Second `approve()` for same gate_id returns existing token (does not issue duplicate).
      - Re-approval returns same `token_id` as first approval.
    - **Multi-gate isolation**:
      - Approving gate-A does not affect gate-B status (pending).
      - Three sequential gate approvals produce three distinct tokens with distinct gate_ids.
      - `get_gate_status()` returns correct mapping after partial approvals.

- [ ] [T501] Write unit tests for TokenIssuer adapter.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/test_unit.py` (TokenIssuer section)
  - Test cases (~8):
    - `sign()` returns a JWT string starting with `"eyJ"`.
    - `verify()` decodes a valid token and returns claims dict with expected keys.
    - `verify()` raises `TokenExpiredError` for expired token (set `exp` in the past).
    - `verify()` raises `TokenValidationError` for token signed with wrong secret.
    - `verify()` raises `TokenValidationError` for malformed token string.
    - Round-trip: `sign(claims)` then `verify(token)` returns claims with matching `plan_id`, `user_id`, `gate_id`, `scopes`, `token_id`.
    - `sign()` includes `exp` and `iat` as integer Unix timestamps in JWT payload.
    - Claims with all required fields (`plan_id`, `user_id`, `gate_id`, `scopes`, `exp`, `iat`, `token_id`) round-trip correctly.

---

## Phase 6: Service Tests (Integration-Level)

### Acceptance Criteria: SPEC US1 (approve), US3 (preview state binding), US4 (spawned step), US5 (expiration), FR-003 (single-use), FR-006 (preview state), FR-008 (learn_from_approval), FR-010 (get_approval_state), FR-012 (Redis degradation)

- [ ] [T600] Write service-level tests for approve flow and token validation.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/test_service.py`
  - Test cases (~20):
    - **US1 / FR-001**: `approve()` returns an `ApprovalToken` with valid JWT, correct `plan_id`, `user_id`, `gate_id`, `scopes`, `exp`.
    - **US1 / FR-001**: Token `exp` is ~15 minutes after `iat` (within 2s tolerance).
    - **US1 / FR-002**: Token JWT contains claims `{plan_id, user_id, gate_id, scopes, exp, iat, token_id}`.
    - **US1**: `approve()` stores gate state in Redis (verify via `get_gate` on mock_redis).
    - **US3 / FR-006**: `approve()` calls `preview_service.get_preview_state(plan_id, user_id)` and binds result to gate state.
    - **US3 / FR-006**: When preview_service is None, approval succeeds with `preview_state=None`.
    - **US3 / FR-006**: When preview_service raises exception, approval succeeds with `preview_state=None` (logged warning).
    - **US3 / FR-010**: `get_approval_state()` returns `ApprovalState` with `token_claims`, `preview_state`, `selected_option`.
    - **US3 / FR-010**: `get_approval_state()` returns None when gate not found.
    - **US4 / FR-008**: When `policy_matched=False` and `role` and `tool` provided, `learn_from_approval()` is called on PolicyEngine.
    - **US4 / FR-008**: When `policy_matched=True`, `learn_from_approval()` is NOT called.
    - **US4 / FR-008**: When PolicyEngine is None, approval succeeds without learning.
    - **US4 / FR-008**: When PolicyEngine raises exception, approval succeeds (logged warning).
    - **US5**: `validate_token()` returns decoded claims for valid token.
    - **US5 / FR-003**: `validate_token()` raises `TokenConsumedError` on second call with same token (single-use).
    - **US5**: `validate_token()` raises `TokenExpiredError` for expired token.
    - **US5**: `validate_token()` raises `TokenValidationError("plan_id_mismatch")` when plan_id does not match.
    - **US5**: `validate_token()` raises `TokenValidationError("gate_id_mismatch")` when gate_id does not match.
    - **FR-009**: `get_gate_status()` returns correct mapping after multiple gate approvals.
    - **FR-012**: When Redis unavailable (GateStore with `redis_client=None`), `approve()` still returns valid JWT. `validate_token()` still works (fail-open). `get_gate_status()` returns empty dict. `get_approval_state()` returns None.

- [ ] [T601] Write service tests for GateStore adapter.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/test_service.py` (GateStore section)
  - Test cases (~10):
    - `store_gate()` returns True when Redis available.
    - `store_gate()` returns False when Redis is None (no-client mode).
    - `store_gate()` returns False and logs warning when Redis raises `ConnectionError`.
    - `get_gate()` returns stored gate data on hit.
    - `get_gate()` returns None on cache miss (key not found).
    - `get_gate()` returns None when Redis is None.
    - `mark_consumed()` returns True on first call (SET NX succeeds).
    - `mark_consumed()` returns False on second call (SET NX fails -- already consumed).
    - `mark_consumed()` returns True when Redis is None (fail-open).
    - `is_consumed()` returns False when Redis is None (fail-open).
    - `get_all_gates_by_prefix()` returns gate_id -> status mapping for multiple gates.
    - `get_all_gates_by_prefix()` returns empty dict when Redis is None.

---

## Phase 7: Contract Tests (GLOBAL_SPEC Conformance)

### Acceptance Criteria: SPEC SC-005, Conformance section (GLOBAL_SPEC S2.7, approval_token.schema.json)

- [ ] [T700] Write GLOBAL_SPEC S2.7 Approval Token conformance tests.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/test_contract.py`
  - Test cases (~15):
    - **Schema conformance**:
      - `ApprovalToken.model_dump()` output contains all GLOBAL_SPEC S2.7 required fields: `token`, `plan_id`, `user_id`, `exp`, `scopes`.
      - `ApprovalToken.model_dump()` output contains extended fields: `gate_id`, `iat`, `token_id`.
      - `ApprovalToken.token` field matches JWT format pattern: `^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$`.
      - `ApprovalToken.plan_id` is exactly 26 characters.
      - `ApprovalToken.exp` is a valid ISO 8601 timestamp.
      - `ApprovalToken.iat` is a valid ISO 8601 timestamp.
      - `ApprovalToken.scopes` is a non-empty list.
    - **approval_token.schema.json validation** (load schema from `/Users/anantshreechandola/Desktop/Personal-agent/shared/schemas/approval_token.schema.json`):
      - Full `approve()` call produces an `ApprovalToken` that validates against `approval_token.schema.json` (using `jsonschema.validate()`). Note: Must map `token_id` out since schema has `additionalProperties: false`.
      - Verify schema required fields (`token`, `plan_id`, `user_id`, `exp`, `scopes`) are all present.
    - **End-to-end flow**:
      - Issue token via `approve()`, then validate via `validate_token()` -- succeeds.
      - Issue token via `approve()`, validate once, validate again -- `TokenConsumedError` (single-use).
      - Issue tokens for gate-A, gate-B, gate-C -- each has distinct `gate_id` and `token_id`.
      - `get_gate_status()` after full 3-gate approval shows all `"approved"`.
    - **Error contract**:
      - All custom exceptions are subclasses of `ApprovalError`.
      - `TokenValidationError("plan_id_mismatch")` has `reason` attribute set to `"plan_id_mismatch"`.
      - `InvalidGateError("bad-gate")` has `gate_id` attribute set to `"bad-gate"`.

---

## Phase 8: Observability & Safety

### Acceptance Criteria: SPEC FR-011 (structured logging), SC-004 (plan_id correlation), NFR (no PII/secrets)

- [ ] [T800] Add structured logging throughout `ApprovalService`.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/service/approval_service.py`
  - Use `logging.getLogger(__name__)` at module level.
  - Log events per LLD Section 11.1:
    - `approval_started` (INFO): plan_id, gate_id, user_id, trace_id, scope_count.
    - `approval_issued` (INFO): plan_id, gate_id, token_id, exp, scope_count.
    - `approval_idempotent` (INFO): plan_id, gate_id (re-approval returned existing).
    - `preview_state_bound` (DEBUG): plan_id, gate_id, step_count.
    - `preview_state_retrieval_failed` (WARNING): plan_id, gate_id.
    - `token_validated` (INFO): plan_id, gate_id, token_id.
    - `token_expired` (WARNING): plan_id, token_id.
    - `token_invalid` (WARNING): plan_id, reason.
    - `token_consumed` (WARNING): plan_id, token_id (reuse attempt).
    - `gate_status_queried` (DEBUG): plan_id, gate_count.
    - `learn_from_approval_called` (INFO): plan_id, role, tool.
    - `learn_from_approval_failed` (WARNING): plan_id, role, tool.
    - `gate_store_failed` (WARNING): plan_id, gate_id, operation.
  - All log calls use `extra={}` dict for structured data.
  - NO JWT token values in logs (only token_id).
  - NO JWT secret in logs.
  - NO user selections (selected_option) in logs.
  - Scopes logged by count only, not content.

- [ ] [T801] Add structured logging to `GateStore` adapter.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/adapters/gate_store.py`
  - Log `gate_store_failed` (WARNING) events with plan_id/gate_id correlation when Redis operations fail.
  - Log `consumed_check_failed` (WARNING) when consumed-token Redis operations fail.

- [ ] [T802] Write observability tests.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ApprovalGate/tests/test_observability.py`
  - Test cases (~10):
    - `approval_started` log emitted with correct plan_id, gate_id, trace_id.
    - `approval_issued` log emitted with token_id (not token value).
    - `approval_idempotent` log emitted on re-approval.
    - `token_validated` log emitted with plan_id and token_id.
    - `token_consumed` log emitted at WARNING level on reuse attempt.
    - `preview_state_retrieval_failed` log emitted when preview service fails.
    - `learn_from_approval_failed` log emitted when policy service fails.
    - No PII in logs: assert JWT `token` value is NOT present in any log record.
    - No PII in logs: assert `selected_option` values are NOT present in any log record.
    - plan_id correlation: all log records from a single approve() call share the same plan_id in extra.
  - Use `caplog` pytest fixture to capture and inspect log records.

---

## Phase 9: DI Wiring (Shared Infrastructure)

### From LLD Section 7.1

- [ ] [T900] Add `create_approval_service()` call in `shared/app.py` lifespan.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`
  - Location: After the PreviewOrchestrator initialization block (ApprovalGate depends on PreviewService).
  - Logic (wrapped in try/except for graceful degradation):
    ```python
    # ApprovalGate service (library -- no routes, graceful degradation)
    try:
        from components.ApprovalGate.service.approval_service import (
            create_approval_service,
        )

        app.state.approval_service = create_approval_service(
            preview_service=app.state.preview_service,
            policy_service=app.state.policy_service,
            redis_client=intake_redis,
            jwt_secret=os.environ.get("APPROVAL_TOKEN_SECRET", ""),
            token_ttl_s=int(os.environ.get("APPROVAL_TOKEN_TTL_S", "900")),
        )
    except Exception as exc:
        logger.warning("ApprovalGate init failed: %s", exc)
        app.state.approval_service = None
    ```
  - Placement: After the PreviewOrchestrator block but before `yield`, so `intake_redis`, `app.state.preview_service`, and `app.state.policy_service` are available.
  - Note: If `APPROVAL_TOKEN_SECRET` env var is not set, the factory will raise `ApprovalConfigError`, which is caught by the try/except and logs a warning. The service will be None (graceful degradation for development environments).

- [ ] [T901] Add `get_approval_service()` accessor in `shared/dependencies.py`.
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py`
  - Add function:
    ```python
    def get_approval_service(request: Request) -> Any:
        """Get ApprovalService singleton from app state."""
        return request.app.state.approval_service
    ```
  - Place after `get_preview_service()` to maintain alphabetical/logical ordering.

---

## Task Summary

- **Total Tasks**: 22
- **Phase 0 (Setup)**: T000-T003 (4 tasks)
- **Phase 1 (Domain)**: T100 (1 task)
- **Phase 2 (Adapters)**: T200-T201 (2 tasks)
- **Phase 3 (Fixtures)**: T300 (1 task)
- **Phase 4 (Service)**: T400-T405 (6 tasks)
- **Phase 5 (Unit Tests)**: T500-T501 (2 tasks)
- **Phase 6 (Service Tests)**: T600-T601 (2 tasks)
- **Phase 7 (Contract Tests)**: T700 (1 task)
- **Phase 8 (Observability)**: T800-T802 (3 tasks)
- **Phase 9 (DI Wiring)**: T900-T901 (2 tasks)

---

## Estimated Test Counts

| Test File | Scope | Count (est.) | LLD Target |
|-----------|-------|-------------|------------|
| `test_unit.py` | Core approval logic, multi-gate, single-use, idempotent re-approval, TokenIssuer adapter | ~33 (25 + 8) | ~25 |
| `test_service.py` | Token issuance, validation, gate state, preview binding, learn-from-approval, GateStore adapter | ~30 (20 + 10) | ~20 |
| `test_contract.py` | Model conformance, GLOBAL_SPEC S2.7, approval_token.schema.json, end-to-end flows | ~15 | ~15 |
| `test_observability.py` | Structured logging, no PII, no secret leakage | ~10 | ~10 |
| **Total** | | **~88** | **~70** |

---

## Dependencies

### External (from LLD.md Section 12.1)

| Package | Constraint | Status |
|---------|-----------|--------|
| `pydantic` | `>=2.0` | Already in pyproject.toml |
| `python-jose[cryptography]` | `>=3.3.0` | Already in pyproject.toml (replaces PyJWT per codebase consistency) |
| `redis[hiredis]` | `>=5.0` | Already in pyproject.toml |
| `ulid-py` | `>=1.1.0` | Already in pyproject.toml |

No new package dependencies required.

### Internal (from LLD.md Section 12.2)

| Component | Import Path | What's Used |
|-----------|------------|-------------|
| PreviewOrchestrator | `components.PreviewOrchestrator.service.preview_service` | `PreviewService.get_preview_state()` -- preview state binding |
| PreviewOrchestrator | `components.PreviewOrchestrator.domain.models` | `PreviewStepResult` -- type hint for preview state dicts |
| PolicyEngine | `components.PolicyEngine.service.policy_service` | `PolicyService.learn_from_approval()` -- learned policies |
| Shared | `shared/schemas/plan.py` | `Plan`, `PlanStep` (gate_id field) |
| Shared | `shared/schemas/policy.py` | `PolicyRule`, `PolicyDecision` (policy_matched field) |
| Shared | `shared/schemas/approval_token.schema.json` | JSON schema for contract tests |
| Shared | `shared/app.py` | DI wiring (lifespan) |
| Shared | `shared/dependencies.py` | Depends accessor |

---

## Architectural Considerations

### Blast Radius (from LLD Section 14.1)

- **If ApprovalGate fails**: User cannot approve plans; no execution possible. No data loss -- stateless (no owned DB tables). No external writes occurred. Preview results still cached. User simply retries the approval. Other components unaffected.
- **If Redis unavailable**: JWT issuance/validation still works (CPU-only). Single-use degrades to JWT-expiry-only (15-minute window). Gate state queries return empty/None. Logged as warning.
- **If PreviewOrchestrator unavailable**: Approval proceeds without preview state. ExecuteOrchestrator re-runs previewable steps.
- **If PolicyEngine unavailable**: Approval proceeds without learning. Logged warning. Future spawns may still require approval.
- **Containment**: Pure library (no DB tables, no HTTP routes), TTL-based Redis auto-expires, per-operation error isolation.

### Determinism (from LLD Section 14)

- **Approve**: Given the same input and same JWT secret, produces a valid token (but token_id and timestamps differ). Not deterministic in the GLOBAL_SPEC sense (not a preview).
- **Validate**: Same valid token + same plan_id = same decoded claims (deterministic). Single-use enforcement adds state mutation (consumed flag).
- **No idempotency in the plan_id:step:arg_hash sense**: Idempotency is at the gate level -- re-approving the same gate returns the existing token.

### JWT Library Choice

- **LLD specifies**: PyJWT (`>=2.8`)
- **Codebase uses**: `python-jose` (`from jose import jwt`, `>=3.3.0`)
- **Decision**: Use `python-jose` for consistency. The APIs are similar: `jose.jwt.encode(claims, secret, algorithm=algo)` and `jose.jwt.decode(token, secret, algorithms=[algo])`.
- **Impact**: No functional difference. `jose.ExpiredSignatureError` instead of `jwt.ExpiredSignatureError`. `jose.JWTError` instead of `jwt.PyJWTError`.

### Cross-Component Coupling (from LLD Section 3.4)

- Soft dependency on PreviewOrchestrator (optional, best-effort).
- Soft dependency on PolicyEngine (optional, best-effort).
- Hard dependency on Redis for full functionality (single-use enforcement, gate state). Graceful degradation without Redis.
- Consumed by ExecuteOrchestrator for `validate_token()`, `get_approval_state()`, `get_gate_status()`. Currently ExecuteOrchestrator has its own inline JWT validation (`_validate_approval_token()`). Future task: migrate EO to use ApprovalGate's `validate_token()`.

### File Structure (from LLD Section 16)

```
components/ApprovalGate/
├── __init__.py
├── LLD.md
├── tasks.md                         (this file)
├── diagrams/
│   └── flow.md
├── domain/
│   ├── __init__.py
│   └── models.py                    # ApprovalRequest, ApprovalToken, ApprovalState, exceptions
├── service/
│   ├── __init__.py
│   └── approval_service.py          # ApprovalService + create_approval_service()
├── adapters/
│   ├── __init__.py
│   ├── token_issuer.py              # TokenIssuer (JWT signing/verification via python-jose)
│   └── gate_store.py                # GateStore (Redis gate state + consumed tracking)
└── tests/
    ├── __init__.py
    ├── conftest.py                  # Fixtures, mock adapters, sample plans
    ├── test_unit.py                 # Core approval logic, multi-gate, single-use, TokenIssuer
    ├── test_service.py              # Token issuance, validation, preview binding, GateStore
    ├── test_contract.py             # Model conformance, GLOBAL_SPEC S2.7, schema.json
    └── test_observability.py        # Logging, no PII/secrets
```

**Shared files touched:**
- `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py` -- Add `create_approval_service()` in lifespan
- `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py` -- Add `get_approval_service()` accessor
