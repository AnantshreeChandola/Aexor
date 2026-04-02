# Tasks: ExecuteOrchestrator

**Created**: 2026-04-02
**Branch**: feat/executeorchestrator-pure-agentic
**SPEC**: specs/021-executeorchestrator-pure-agentic/spec.md
**LLD**: components/ExecuteOrchestrator/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture (domain models, adapters, service, API, DI, safety, tests). Each task maps to one or more SPEC acceptance criteria (US-1 through US-9) and functional requirements (FR-001 through FR-021).

---

## Phase 0: Setup & Dependencies

### Install Dependencies (from LLD.md Section 15)

- [ ] [T000] Verify Python packages are present in `pyproject.toml`
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/pyproject.toml`
  - Verify/add: `httpx>=0.27.0`, `anthropic>=0.18.0`, `cryptography>=42.0.0`, `redis[hiredis]>=5.0.0`, `ulid-py>=1.1.0`, `PyJWT>=2.8.0`, `pydantic>=2.5.0`, `fastapi>=0.109.0`
  - Note: `httpx` and `anthropic` already exist; verify version pins. `PyJWT` must be checked (project uses `python-jose[cryptography]` -- confirm JWT validation approach aligns with existing pattern)

- [ ] [T001] Create component directory scaffolding
  - Create empty `__init__.py` files at:
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/domain/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/api/__init__.py`
    - `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/__init__.py`

- [ ] [T002] Verify internal component dependencies are accessible
  - Confirm imports work for:
    - `components.Signer.service.signer_service.SignerService` -- `verify_signature(plan_data: dict, signature_data: dict) -> bool`
    - `components.PolicyEngine.service.policy_service.PolicyService` -- `evaluate_spawn(request: SpawnRequest) -> PolicyDecision`
    - `components.PolicyEngine.domain.models.SpawnRequest` -- fields: `plan_id`, `plan_revision`, `spawning_step`, `proposed_steps`, `current_step_count`, `plan_plugins`, `policy_ref`
    - `components.PluginRegistry.service.registry_service.RegistryService` -- `get_tool(tool_id)`, `get_operation(tool_id, op_id)`
    - `components.PlanWriter.service.plan_writer_service.PlanWriterService` -- `persist_outcome(user_id, plan, signature, outcome, metrics)`
    - `shared.schemas.plan.Plan`, `shared.schemas.plan.PlanStep`
    - `shared.schemas.signature.Signature`
    - `shared.schemas.outcome.PlanOutcome`
    - `shared.schemas.policy.PolicyDecision`, `shared.schemas.policy.PolicyAttestation`, `shared.schemas.policy.ReasoningConfig`
    - `shared.schemas.metrics.PlanMetrics`
    - `shared.api.error_handlers.APIErrorHandler`, `shared.api.error_handlers.ErrorResponse`
    - `shared.database.adapter.SharedDatabaseAdapter`
    - `shared.database.models.CredentialVaultTable`

---

## Phase 1: Domain Models & Error Classes (Foundation)

### Acceptance Criteria: US-1 (Pure API Plan), US-2 (Idempotency), US-5 (Credential Isolation), US-6 (Compensation)
### Functional Requirements: FR-001 through FR-021 (domain model foundation)

- [ ] [T100] Create domain models for ExecuteOrchestrator
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/domain/models.py`
  - Implement `ExecuteRequest(BaseModel)`:
    - `plan: Plan` (from `shared.schemas.plan`)
    - `signature: Signature` (from `shared.schemas.signature`)
    - `approval_token: str` (JWT from ApprovalGate)
    - `user_id: str` (UUID)
    - `trace_id: str` (distributed tracing correlation)
    - `preview_state: dict[str, Any] | None = None` (step_num -> cached result)
    - `integration_credentials: dict[str, str] = {}` (tool_id -> credential_vault_id)
  - Implement `StepResult(BaseModel)`:
    - `step: int`
    - `status: Literal["completed", "failed", "skipped"]`
    - `result: dict[str, Any] | None = None`
    - `error: dict[str, Any] | None = None`
    - `latency_ms: int = 0`
    - `retries: int = 0`
  - Implement `CompensationRecord(BaseModel)`:
    - `step: int`
    - `tool_id: str`
    - `operation: str`
    - `result: dict[str, Any]`
    - `compensation_operation: str | None`
    - `compensation_args: dict[str, Any] | None`
  - Implement `ExecutionContext` (plain class, NOT BaseModel -- mutable runtime state):
    - `plan: Plan`
    - `user_id: str`
    - `trace_id: str`
    - `step_results: dict[int, StepResult]`
    - `compensation_stack: list[CompensationRecord]`
    - `spawned_steps: list[PlanStep]`
    - `attestations: list[PolicyAttestation]`
    - `plan_revision: int = 0`
    - `recovery_action_count: int = 0`

- [ ] [T101] Create domain error classes
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/domain/models.py` (same file as T100)
  - Implement error hierarchy per LLD Section 5.2:
    - `ExecuteError(Exception)` -- base
    - `SignatureVerificationError(ExecuteError)` -- `reason: str`
    - `ApprovalTokenError(ExecuteError)` -- `reason: str`
    - `PlanExpiredError(ExecuteError)` -- `plan_id: str`, `ttl_s: int`
    - `StepExecutionError(ExecuteError)` -- `step: int`, `reason: str`, `retries: int`
    - `IdempotencyConflict(ExecuteError)` -- `key: str`
    - `ResourceLockTimeout(ExecuteError)` -- `lock_key: str`, `timeout_s: int`
    - `MCPInvocationError(ExecuteError)` -- `server: str`, `tool: str`, `reason: str`
    - `SpawnDeniedError(ExecuteError)` -- `reason: str`, `violations: list[str]`
    - `RecoveryExhaustedError(ExecuteError)` -- `step: int`, `attempts: int`
    - `CycleDetectedError(ExecuteError)` -- for DAG cycle detection

- [ ] [T102] Write domain model unit tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py`
  - Test `ExecuteRequest` Pydantic validation (required fields, defaults, serialization)
  - Test `StepResult` creation and status values
  - Test `CompensationRecord` creation
  - Test `ExecutionContext` initialization and mutability
  - Test all error classes (message formatting, attributes)
  - Test `ExecuteRequest` with preview_state populated
  - Test `ExecuteRequest` with empty integration_credentials
  - Target: ~12 tests

---

## Phase 2: Adapters (External Integrations)

### 2A: DAG Resolver
### Acceptance Criteria: US-1 (DAG resolution, parallel grouping)
### Functional Requirements: FR-003

- [ ] [T200] Implement DAG resolver adapter
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/dag_resolver.py`
  - Class `DAGResolver` with method:
    - `resolve(graph: list[PlanStep]) -> list[list[PlanStep]]`
    - Topological sort using Kahn's algorithm
    - Group steps into execution levels (level 0 = no deps, level 1 = depends on level 0, etc.)
    - Raise `CycleDetectedError` on circular dependencies
    - Handle spawned steps that are appended at runtime (re-resolve capability)

- [ ] [T201] Write DAG resolver unit tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py` (append to existing)
  - Test linear chain: A -> B -> C gives 3 levels
  - Test parallel: A, B (no deps) -> C (after A, B) gives 2 levels
  - Test diamond: A -> B, A -> C, B+C -> D gives 3 levels
  - Test cycle detection: A -> B -> A raises `CycleDetectedError`
  - Test single step (no deps): 1 level
  - Test empty graph raises ValueError
  - Target: ~6 tests

### 2B: Template Resolver
### Acceptance Criteria: US-1 (template arg resolution)
### Functional Requirements: FR-018

- [ ] [T210] Implement template resolver adapter
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/template_resolver.py`
  - Class `TemplateResolver` with method:
    - `resolve(args: dict, step_results: dict[int, StepResult], preview_state: dict | None = None) -> dict`
    - Pattern: `{{step_N.result.field}}` -- extract field from step N's result dict
    - Pattern: `{{preview.cached_state.step_N_result.field}}` -- from preview state
    - Recursive resolution for nested dicts/lists
    - Raise `KeyError` with descriptive message on missing reference

- [ ] [T211] Write template resolver unit tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py` (append)
  - Test simple template: `{{step_1.result.event_id}}` resolves correctly
  - Test nested template: `{{step_2.result.data.name}}` resolves dot-path
  - Test no-template passthrough: plain args returned as-is
  - Test missing step reference: raises KeyError
  - Test preview state template: `{{preview.cached_state.step_1_result.selected}}` resolves
  - Test multiple templates in one args dict
  - Target: ~6 tests

### 2C: Idempotency Adapter
### Acceptance Criteria: US-2 (all 4 scenarios)
### Functional Requirements: FR-008

- [ ] [T220] Implement idempotency adapter
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/idempotency.py`
  - Class `IdempotencyAdapter`:
    - `__init__(self, redis: Redis)`
    - `build_key(user_id, integration_id, plan_id, step, call, args) -> str`
      - Format: `idem:{user_id}:{integration_id}:{plan_id}:{step}:{call}:{sha256_hash[:16]}`
    - `async check_and_claim(key, execution_id, timeout_minutes=5) -> StepResult | None`
      - SUCCEEDED state: return cached result
      - IN_FLIGHT + recent (< timeout): raise `IdempotencyConflict`
      - IN_FLIGHT + stale (> timeout): take over, return None (proceed)
      - FAILED state: delete record, return None (proceed)
      - Not found: create IN_FLIGHT record, return None (proceed)
    - `async mark_succeeded(key, result) -> None`
    - `async mark_failed(key, error) -> None`
    - TTL: 24 hours on all keys
    - Redis hash structure: `{state, execution_id, result_json, error, claimed_at}`

- [ ] [T221] Write idempotency adapter tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_idempotency.py`
  - Test `build_key` produces deterministic hash for same args
  - Test `build_key` produces different hash for different args
  - Test `check_and_claim` with no prior record: returns None, creates IN_FLIGHT
  - Test `check_and_claim` with SUCCEEDED record: returns cached StepResult
  - Test `check_and_claim` with IN_FLIGHT recent: raises IdempotencyConflict
  - Test `check_and_claim` with IN_FLIGHT stale (> 5 min): takes over, returns None
  - Test `check_and_claim` with FAILED record: deletes and returns None
  - Test `mark_succeeded` sets state and caches result
  - Test `mark_failed` sets FAILED state
  - Test TTL is set to 24 hours
  - Use `fakeredis.aioredis` for Redis mocking
  - Target: ~10 tests

### 2D: Resource Lock Adapter
### Acceptance Criteria: US-7 (all 2 scenarios)
### Functional Requirements: FR-009

- [ ] [T230] Implement resource lock adapter
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/resource_lock.py`
  - Class `ResourceLockAdapter`:
    - `__init__(self, redis: Redis)`
    - `async acquire(lock_key: str, timeout_s: int = 30) -> bool`
      - Uses Redis SET NX with 30s TTL
      - Polls every 0.5s until acquired or timeout
      - Raises `ResourceLockTimeout` on timeout
    - `async release(lock_key: str) -> None`
      - DEL key (only if owned -- use Lua script or check value)
    - Lock key format: `lock:resource:{user_id}:{integration_id}:{resource}:{entity}:{operation}`

- [ ] [T231] Write resource lock adapter tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py` (append)
  - Test acquire succeeds when no lock exists
  - Test acquire fails when lock held by another, raises ResourceLockTimeout
  - Test release clears the lock
  - Test TTL auto-expires (simulate)
  - Use `fakeredis.aioredis` for Redis mocking
  - Target: ~4 tests

### 2E: Credential Vault Adapter
### Acceptance Criteria: US-5 (credential decryption and isolation)
### Functional Requirements: FR-004, FR-020

- [ ] [T240] Implement credential vault adapter
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/credential_vault.py`
  - Class `CredentialVaultAdapter`:
    - `__init__(self, db: SharedDatabaseAdapter)`
    - Read `CREDENTIAL_MASTER_KEY` from env var at init; raise at startup if missing
    - `async decrypt(credential_id: str, user_id: str) -> str`
      - Query `CredentialVaultTable` from `shared.database.models` for `encrypted_value`, `iv`, `key_version`
      - Verify `user_id` matches record (security check)
      - Derive key from master key + key_version
      - Decrypt AES-256-GCM using `cryptography` library
      - Return plaintext string
    - Note: Caller is responsible for zeroing plaintext after use (set variable to None)

- [ ] [T241] Write credential vault adapter tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py` (append)
  - Test decrypt returns plaintext for valid credential (mock DB query)
  - Test decrypt raises on user_id mismatch (security)
  - Test decrypt raises on missing credential (not found)
  - Test decrypt raises on missing CREDENTIAL_MASTER_KEY env var
  - Mock `SharedDatabaseAdapter` and `CredentialVaultTable`
  - Target: ~4 tests

### 2F: MCP Client Adapter
### Acceptance Criteria: US-1 (API step dispatch), US-5 (credential passing)
### Functional Requirements: FR-004

- [ ] [T250] Implement MCP client adapter (Protocol + httpx implementation)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/mcp_client.py`
  - Define `MCPClient(Protocol)` (runtime_checkable):
    - `async invoke(server, tool, args, credentials=None, timeout_s=30) -> dict`
  - Implement `MCPClientAdapter`:
    - `__init__(self, registry_service: RegistryService)`
    - Uses `httpx.AsyncClient` for SSE transport
    - Resolves MCP server URL from PluginRegistry `get_tool(tool_id)` -> `mcp_server` field
    - Resolves MCP tool name from `get_operation(tool_id, call)` -> `mcp_tool` field
    - Builds MCP tool_call JSON-RPC request
    - Sends via httpx with configurable timeout
    - Normalizes errors to `MCPInvocationError`
    - Handles HTTP 503, 504, timeouts, connection resets as transient (retryable)

- [ ] [T251] Write MCP client adapter tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py` (append)
  - Test invoke returns result dict on success (mock httpx response)
  - Test invoke raises MCPInvocationError on HTTP error
  - Test invoke raises MCPInvocationError on timeout
  - Test credentials are passed in request body (not logged)
  - Mock `RegistryService` and `httpx.AsyncClient`
  - Target: ~4 tests

### 2G: LLM Client Adapter
### Acceptance Criteria: US-3 (two-tier trust enforcement), US-4 (spawning)
### Functional Requirements: FR-005, FR-006, FR-007

- [ ] [T260] Implement LLM client adapter (Protocol + Anthropic implementation)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/llm_client.py`
  - Define `LLMClient(Protocol)` (runtime_checkable):
    - `async reason(config: ReasoningConfig, context: list[dict], trust_level: Literal["untrusted_input", "trusted"]) -> dict`
  - Implement `AnthropicReasoningAdapter`:
    - `__init__(self, api_key: str | None = None)`
    - Uses `anthropic.AsyncAnthropic`
    - Tier 1 (`untrusted_input`): tools=[], enforce `output_schema_ref` as response format, strict output validation
    - Tier 2 (`trusted`): enable tool_use for spawn request tool definitions, parse spawn requests from tool_use blocks
    - Build messages from context list
    - Load system prompt from `system_prompt_ref` (file-based or registry-based)
    - Parse response: extract text content, tool_use blocks (spawn requests)
    - Return structured dict with `content`, `spawn_requests` (list, Tier 2 only)

- [ ] [T261] Write LLM client adapter tests (trust tier enforcement)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_trust_tiers.py`
  - Test Tier 1 call: tools disabled, output schema enforced
  - Test Tier 1 call: response schema-validated against output_schema_ref
  - Test Tier 1 call: tool_use blocks rejected (if returned despite tools=[])
  - Test Tier 2 call: tools enabled
  - Test Tier 2 call: spawn requests extracted from tool_use blocks
  - Test Tier 2 call: response without spawn requests returns empty list
  - Test invalid trust_level raises ValueError
  - Test output schema validation failure raises error
  - Mock `anthropic.AsyncAnthropic.messages.create`
  - Target: ~8 tests

### 2H: Retry Adapter
### Acceptance Criteria: US-9 (step-level retries)
### Functional Requirements: FR-015

- [ ] [T270] Implement retry adapter
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/retry.py`
  - Class `RetryPolicy`:
    - `max_retries: int = 3`
    - `backoff_base_s: float = 1.0` (exponential: 1s, 2s, 4s)
    - `retry_on: set[str] = {"503", "504", "timeout", "connection_reset"}`
    - `async execute_with_retry(operation: Callable, step: PlanStep) -> dict`
      - Retry up to max_retries on retryable errors
      - Exponential backoff between retries
      - Log each retry with step/attempt/backoff info
      - Raise original error after exhaustion

- [ ] [T271] Write retry adapter tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py` (append)
  - Test succeeds on first try: no retries
  - Test fails twice, succeeds on third: returns result
  - Test fails all retries: raises original error
  - Test non-retryable error: no retry, raises immediately
  - Test backoff timing (approximate)
  - Target: ~5 tests

---

## Phase 3: Service Layer (Business Logic)

### 3A: Core Execution Flow
### Acceptance Criteria: US-1 (end-to-end pure API plan)
### Functional Requirements: FR-001, FR-002, FR-003, FR-017, FR-018, FR-019, FR-021

- [ ] [T300] Implement `ExecuteService` class with `__init__` and factory function
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py`
  - Class `ExecuteService` with constructor taking all adapter dependencies:
    - `signer_service: Any` (SignerService)
    - `policy_service: Any` (PolicyService)
    - `registry_service: Any` (RegistryService)
    - `plan_writer_service: Any` (PlanWriterService)
    - `mcp_client: MCPClient`
    - `llm_client: LLMClient`
    - `credential_vault: CredentialVaultAdapter`
    - `idempotency: IdempotencyAdapter`
    - `resource_lock: ResourceLockAdapter`
    - `dag_resolver: DAGResolver`
    - `template_resolver: TemplateResolver`
    - `retry_policy: RetryPolicy`
  - Factory function `create_execute_service(signer_service, policy_service, registry_service, plan_writer_service, mcp_client, llm_client, credential_vault, redis_client) -> ExecuteService`
    - Creates internal adapters (IdempotencyAdapter, ResourceLockAdapter, DAGResolver, TemplateResolver, RetryPolicy) from redis_client
    - Returns configured ExecuteService

- [ ] [T301] Implement pre-execution verification methods
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - `async _verify_signature(plan: Plan, signature: Signature) -> None`
    - Calls `signer_service.verify_signature(plan.model_dump(), signature.model_dump())`
    - Catches `InvalidSignatureError` from Signer, wraps as `SignatureVerificationError`
  - `_validate_approval_token(token: str, plan: Plan) -> None`
    - Decode JWT, validate expiry (15min TTL), validate plan_hash matches, validate scopes
    - Raises `ApprovalTokenError` on failure
  - `_check_plan_ttl(plan: Plan) -> None`
    - Compare `plan.meta.created_at` + `plan.constraints.ttl_s` against current time
    - Raise `PlanExpiredError` if expired
  - `_validate_plan(plan: Plan) -> None`
    - Min 1 step (Plan.graph already enforces via Pydantic min_length=1)
    - Max 100 steps
    - Validate step numbers are unique

- [ ] [T302] Implement `execute_plan()` core flow
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - Per LLD Section 7.1:
    - Phase 1: Pre-execution verification (_verify_signature, _validate_approval_token, _check_plan_ttl)
    - Phase 2: DAG resolution via _dag_resolver.resolve()
    - Phase 3: Level-by-level execution with asyncio.gather() for parallel steps
    - Phase 4: Build PlanOutcome via _build_outcome()
    - Phase 5: Persist outcome via _persist_outcome() (non-fatal)
  - `_should_skip(step, request)`: skip if `execute_mode == "preview_only"` and inject cached result from preview_state
  - `_build_outcome(ctx, start_time) -> PlanOutcome`: assemble success/failure with final_graph_json, plan_revision, attestations
  - `_build_error_outcome(error, start_time) -> PlanOutcome`: assemble error outcome with error_type and error_details
  - `_persist_outcome(request, outcome)`: call PlanWriter.persist_outcome() in try/except (log warning on failure, never fail execution)

- [ ] [T303] Implement step dispatch router
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - `async _execute_step(step, ctx, request) -> StepResult`
  - Per LLD Section 7.2:
    - Match on `step.type`:
      - `"api"` -> `_execute_api_step()`
      - `"llm_reasoning"` -> `_execute_reasoning_step()`
      - `"policy_check"` -> `_execute_policy_check()`
    - Wrap result in `StepResult` with latency_ms measurement
    - Log `step_dispatched` and `step_completed` structured events

### 3B: API Step Execution
### Acceptance Criteria: US-1, US-2, US-5, US-7
### Functional Requirements: FR-004, FR-008, FR-009, FR-015, FR-018, FR-020

- [ ] [T310] Implement `_execute_api_step()` method
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - Per LLD Section 7.3:
    1. Resolve template args via `_template_resolver.resolve()`
    2. Idempotency check (Booker role only): `_idempotency.check_and_claim()` -- return cached if SUCCEEDED
    3. Resource lock (Booker role only): `_resource_lock.acquire()`
    4. Decrypt credentials via `_credential_vault.decrypt()`
    5. MCP invocation with retry: `_retry_policy.execute_with_retry()` wrapping `_mcp_client.invoke()`
    6. Zero credential from memory (`plaintext_cred = None`)
    7. Record compensation info (Booker only, if operation has compensation declared)
    8. Mark idempotency succeeded (Booker only)
    9. Release resource lock in finally block

### 3C: LLM Reasoning Step Execution
### Acceptance Criteria: US-3 (trust tier enforcement)
### Functional Requirements: FR-005, FR-006, FR-007

- [ ] [T320] Implement `_execute_reasoning_step()` method
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - Per LLD Section 7.4:
    1. Gather context from `context_from` step references
    2. Dispatch to `_llm_client.reason()` with trust_level from step
    3. Tier 1: validate output against `output_schema_ref`
    4. Tier 2 + `can_spawn`: extract spawn requests, call `_handle_spawn()` for each
    5. Return response dict

### 3D: Spawn Handling
### Acceptance Criteria: US-4 (all 4 scenarios)
### Functional Requirements: FR-011, FR-012, FR-013, FR-014

- [ ] [T330] Implement `_handle_spawn()` method
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - Per LLD Section 7.5:
    1. Check per-step spawn limit (`max_spawned_steps`, default 3, max 10)
    2. Check plan-level step limit (total original + spawned < 100)
    3. Build new PlanStep with `spawned_by`, `can_spawn=False`, `after=[parent_step.step]`
    4. Inject `gate_id` for spawned Booker role (non-overridable)
    5. Build `SpawnRequest` for PolicyEngine using fields from `components.PolicyEngine.domain.models.SpawnRequest`:
       - `plan_id`, `plan_revision` (current), `spawning_step`, `proposed_steps`, `current_step_count`, `plan_plugins`, `policy_ref`
    6. Call `_policy_service.evaluate_spawn(spawn_request)` -> `PolicyDecision`
    7. If denied: raise `SpawnDeniedError` with reason and violations
    8. Create `PolicyAttestation` with ULID, increment `plan_revision`
    9. Append to `ctx.spawned_steps` and `ctx.attestations`
    10. If not gated and not `requires_approval`: execute spawned step immediately

- [ ] [T331] Write spawning tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_spawning.py`
  - Test spawn approved: new step appended, plan_revision increments, attestation created
  - Test spawn denied (tool not in plugins): SpawnDeniedError raised
  - Test spawn denied (plan step limit 100 exceeded): SpawnDeniedError raised
  - Test spawn denied (per-step limit exceeded): SpawnDeniedError raised
  - Test spawned Booker gets gate_id injected
  - Test spawned step has `can_spawn=False` (no recursive spawning)
  - Test spawned step has `spawned_by` set to parent step number
  - Test spawn with PolicyEngine returning `requires_approval=True`: step not auto-executed
  - Test multiple spawns from same Reasoner: count tracked correctly
  - Test attestation fields (attestation_id is ULID, policy_id, decision)
  - Mock PolicyEngine.evaluate_spawn() and LLM client
  - Target: ~10 tests

### 3E: Failure Recovery
### Acceptance Criteria: US-8 (adaptive recovery), US-9 (step-level retry)
### Functional Requirements: FR-015, FR-016

- [ ] [T340] Implement `_handle_step_failure()` and `_find_recovery_reasoner()` methods
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - Per LLD Section 7.6:
    1. Find nearest Reasoner with `can_spawn=True` in the graph
    2. If no Reasoner: run compensation -> raise StepExecutionError (terminal)
    3. Check recovery budget (`recovery_action_count >= 5`): if exceeded -> compensation -> raise RecoveryExhaustedError
    4. Build error context (failed_step, error_type, error_details, step_role, step_tool)
    5. Record failure as StepResult(status="failed")
    6. Increment recovery_action_count
    7. Execute Reasoner step (which may spawn a recovery step)

### 3F: Compensation (Saga Pattern)
### Acceptance Criteria: US-6 (both scenarios)
### Functional Requirements: FR-010

- [ ] [T350] Implement `_run_compensation()` method
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - Per LLD Section 7.7:
    1. Iterate compensation_stack in reverse order
    2. Skip records with `compensation_operation is None`
    3. For each: invoke compensation via `_mcp_client.invoke()` with compensation_operation + args
    4. Log success: `compensation_executed` with plan_id, step, operation
    5. On failure: log `compensation_failed` with error -- DO NOT re-raise, continue with remaining compensations
    6. Each compensation call in its own try/except (blast radius containment)

- [ ] [T351] Write compensation tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_compensation.py`
  - Test compensation runs in reverse order: step 2 before step 1
  - Test step with no compensation_operation is skipped
  - Test compensation MCP call failure is logged but does not stop other compensations
  - Test compensation with all operations succeeding
  - Test empty compensation stack: no-op
  - Test compensation uses correct tool and args from CompensationRecord
  - Test final PlanOutcome after compensation has `success=False` and `error_type="step_failure"`
  - Mock MCP client for compensation calls
  - Target: ~7 tests

### 3G: Policy Check Step Execution

- [ ] [T360] Implement `_execute_policy_check()` method
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (same file)
  - Evaluate policy rule referenced by `step.policy_ref`
  - Return PolicyDecision as step result
  - Fail step if policy evaluation returns denied

---

## Phase 4: Service Layer Tests

### Acceptance Criteria: US-1 through US-9 (full service flow tests)

- [ ] [T400] Write core service flow tests (pure API plan)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_service.py`
  - Test happy path: 4-step pure API plan (2 parallel -> 1 sequential -> 1 Booker)
    - Verify DAG order: steps 1-2 parallel, step 3 after both, step 4 after step 3
    - Verify PlanOutcome(success=True) with all step results
  - Test preview_only step skipped: cached result used for downstream template resolution
  - Test Booker step idempotency integration: idem key claimed, MCP called, marked succeeded
  - Test resource lock acquisition/release for Booker step
  - Test credential decryption called for API step with credentials
  - Test credential NOT decrypted for step without credential mapping
  - Mock all adapters (MCP, Redis, DB, Signer)
  - Target: ~7 tests

- [ ] [T401] Write service flow tests (signature/token/TTL verification)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_service.py` (append)
  - Test invalid signature: PlanOutcome with error_type="signature_invalid"
  - Test expired approval token: PlanOutcome with error_type="token_expired"
  - Test expired plan TTL: PlanOutcome with error_type="plan_expired"
  - Test valid signature + valid token: execution proceeds
  - Target: ~4 tests

- [ ] [T402] Write service flow tests (failure + recovery)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_service.py` (append)
  - Test pure API plan step failure: compensation runs, PlanOutcome(success=False)
  - Test hybrid plan step failure: routes to Reasoner, Reasoner spawns recovery step
  - Test recovery exhausted: PlanOutcome with error_type="recovery_exhausted"
  - Test step retry: MCP fails twice, succeeds third time
  - Test Redis unavailable: Booker step fails-safe (refuses to execute)
  - Target: ~5 tests

- [ ] [T403] Write service flow tests (parallel execution)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_service.py` (append)
  - Test 3 independent steps execute via asyncio.gather
  - Test parallel step failure does not prevent other parallel steps from completing
  - Test results from parallel steps available for downstream dependencies
  - Target: ~3 tests

- [ ] [T404] Write service flow tests (outcome persistence)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_service.py` (append)
  - Test PlanWriter.persist_outcome called with correct arguments on success
  - Test PlanWriter failure does not cause execute_plan to fail (logged warning)
  - Target: ~2 tests

---

## Phase 5: API Routes (Thin Wrapper)

### Acceptance Criteria: All user stories (HTTP entry point)
### Functional Requirements: FR-001 through FR-021 (exposed via HTTP)

- [ ] [T500] Implement API route handler
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/api/routes.py`
  - Create `router = APIRouter(prefix="/api/v1", tags=["execute"])`
  - `POST /execute` endpoint:
    - Input: `ExecuteRequest` (Pydantic body)
    - Output: `PlanOutcome`
    - Dependency: `service = Depends(get_execute_service)`
    - Error handling per LLD Section 9.3:
      - `SignatureVerificationError` -> 403, `SIGNATURE_INVALID`
      - `ApprovalTokenError` -> 401, `TOKEN_INVALID`
      - `PlanExpiredError` -> 410, `PLAN_EXPIRED`
      - Generic -> `APIErrorHandler.handle_generic_error()`
    - Use `ErrorResponse` from `shared.api.error_handlers`
    - Use `_handle_domain_error()` local helper per project pattern

- [ ] [T501] Write API route handler tests
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_unit.py` (append)
  - Test POST /api/v1/execute with valid request returns PlanOutcome
  - Test POST with invalid body returns 422 validation error
  - Test 403 on SignatureVerificationError
  - Test 401 on ApprovalTokenError
  - Test 410 on PlanExpiredError
  - Test 500 on unexpected error
  - Use `httpx.AsyncClient` with TestClient or ASGI transport
  - Target: ~6 tests

---

## Phase 6: DI Wiring (Shared Infrastructure)

### From LLD Section 9.1

- [ ] [T600] Add `create_execute_service()` wiring in `shared/app.py`
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`
  - Add imports at the appropriate location in the lifespan function (after PolicyEngine, after all Domain Layer services):
    ```python
    from components.ExecuteOrchestrator.adapters.mcp_client import MCPClientAdapter
    from components.ExecuteOrchestrator.adapters.llm_client import AnthropicReasoningAdapter
    from components.ExecuteOrchestrator.adapters.credential_vault import CredentialVaultAdapter
    from components.ExecuteOrchestrator.service.execute_service import create_execute_service
    ```
  - Wire up `app.state.execute_service`:
    ```python
    app.state.execute_service = create_execute_service(
        signer_service=app.state.signer_service,
        policy_service=app.state.policy_service,
        registry_service=app.state.registry_service,
        plan_writer_service=app.state.plan_writer_service,
        mcp_client=MCPClientAdapter(registry_service=app.state.registry_service),
        llm_client=AnthropicReasoningAdapter(),
        credential_vault=CredentialVaultAdapter(db=app.state.db),
        redis_client=intake_redis,
    )
    ```
  - Wrap in try/except with graceful degradation (log warning, set to None)
  - Must come AFTER PolicyEngine service, PlanWriter service, and Redis initialization

- [ ] [T601] Add `get_execute_service()` dependency in `shared/dependencies.py`
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py`
  - Add:
    ```python
    def get_execute_service(request: Request) -> Any:
        """Get ExecuteService singleton from app state."""
        return request.app.state.execute_service
    ```

- [ ] [T602] Register ExecuteOrchestrator router in `shared/app.py`
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`
  - Add import: `from components.ExecuteOrchestrator.api.routes import router as execute_router`
  - Add: `app.include_router(execute_router)`

---

## Phase 7: Test Fixtures & Configuration

- [ ] [T700] Create shared test fixtures and conftest
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/conftest.py`
  - Fixtures:
    - `mock_signer_service`: Mocked SignerService with verify_signature returning True
    - `mock_policy_service`: Mocked PolicyService with evaluate_spawn returning approved PolicyDecision
    - `mock_registry_service`: Mocked RegistryService with get_tool/get_operation returning test data
    - `mock_plan_writer_service`: Mocked PlanWriterService with persist_outcome as no-op
    - `mock_mcp_client`: Mocked MCPClient returning configurable results
    - `mock_llm_client`: Mocked LLMClient returning configurable reasoning results
    - `mock_credential_vault`: Mocked CredentialVaultAdapter returning test credential strings
    - `mock_redis`: fakeredis.aioredis.FakeRedis instance
    - `sample_plan`: Valid 4-step pure API Plan (2 Fetchers parallel, 1 Analyzer, 1 Booker)
    - `sample_hybrid_plan`: Valid 6-step hybrid plan with Reasoner step
    - `sample_signature`: Valid Signature matching sample_plan
    - `sample_execute_request`: Complete ExecuteRequest with all fields
    - `execute_service`: Fully wired ExecuteService with all mocked dependencies
    - `sample_approval_token`: Valid JWT token (signed with test secret)

---

## Phase 8: Safety, Observability & Fault Isolation

### From LLD Section 10, MODULAR_ARCHITECTURE, Constitution

- [ ] [T800] Implement circuit breaker for Anthropic API (LLM client)
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/adapters/llm_client.py` (modify)
  - Add circuit breaker wrapper per LLD Section 13:
    - failure_threshold=5, timeout_s=60, success_threshold=2
    - When open: LLM reasoning steps fail immediately with descriptive error
    - Pure API plans unaffected when circuit is open
  - Can reuse pattern from `components.Planner.adapters.circuit_breaker.CircuitBreaker`

- [ ] [T801] Implement structured logging throughout service
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/service/execute_service.py` (modify)
  - Add logging events per LLD Section 10:
    - `execution_started`: INFO, plan_id, user_id, trace_id, total_steps, step_types
    - `step_dispatched`: INFO, plan_id, step, role, type, trust_level, uses
    - `step_completed`: INFO, plan_id, step, role, latency_ms, status
    - `step_failed`: WARNING, plan_id, step, role, error_type, retries
    - `step_retried`: INFO, plan_id, step, attempt, backoff_s
    - `spawn_requested`: INFO, plan_id, parent_step, proposed_role, proposed_tool
    - `spawn_approved`: INFO, plan_id, spawned_step, attestation_id, plan_revision
    - `spawn_denied`: WARNING, plan_id, parent_step, reason, violations
    - `compensation_executed`: INFO, plan_id, step, operation
    - `compensation_failed`: ERROR, plan_id, step, operation, error
    - `execution_completed`: INFO, plan_id, success, total_steps, duration_ms, plan_revision
    - `credential_decrypted`: DEBUG, plan_id, step, tool_id (NEVER the credential value)

- [ ] [T802] Validate no PII/secrets in logs
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_observability.py`
  - Test that credential values never appear in log output (capture log handler)
  - Test that user PII (email, phone) never appears in structured log extras
  - Test that all log events include plan_id correlation
  - Test that step_completed log includes latency_ms
  - Test that credential_decrypted log has tool_id but NOT credential value
  - Target: ~5 tests

- [ ] [T803] Validate fail-safe behavior when Redis unavailable
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_service.py` (append)
  - Test Booker step refuses to execute when idempotency adapter raises ConnectionError
  - Test Fetcher/Analyzer steps proceed normally without idempotency when Redis is down
  - Target: ~2 tests (counted in Phase 4 total)

---

## Phase 9: Contract Tests & Schema Validation

### Acceptance Criteria: SC-001 through SC-008 (success criteria)

- [ ] [T900] Write contract tests for ExecuteRequest schema
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_contract.py`
  - Test ExecuteRequest validates against expected schema (plan, signature, approval_token required)
  - Test ExecuteRequest with optional fields (preview_state, integration_credentials)
  - Test ExecuteRequest rejects invalid plan (empty graph)
  - Target: ~3 tests

- [ ] [T901] Write contract tests for PlanOutcome schema conformance
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_contract.py` (append)
  - Test PlanOutcome produced by execute_plan conforms to shared schema
  - Test PlanOutcome includes final_graph_json when spawned steps exist
  - Test PlanOutcome includes policy_attestations when spawn events occurred
  - Test PlanOutcome error_type values match SPEC edge cases
  - Target: ~4 tests

- [ ] [T902] Write end-to-end contract test: Intent -> Plan -> Execute flow
  - File: `/Users/anantshreechandola/Desktop/Personal-agent/components/ExecuteOrchestrator/tests/test_contract.py` (append)
  - Test full flow with mocked dependencies:
    1. Build valid Plan from shared schemas
    2. Build valid Signature from shared schemas
    3. Create ExecuteRequest
    4. Call execute_plan()
    5. Validate PlanOutcome schema
  - Test idempotency contract: same request twice returns cached result
  - Target: ~3 tests

---

## Task Summary

- **Total Tasks**: 45
- **Setup**: T000-T002 (3 tasks)
- **Domain Models**: T100-T102 (3 tasks)
- **Adapters**: T200-T271 (16 tasks)
  - DAG Resolver: T200-T201 (2)
  - Template Resolver: T210-T211 (2)
  - Idempotency: T220-T221 (2)
  - Resource Lock: T230-T231 (2)
  - Credential Vault: T240-T241 (2)
  - MCP Client: T250-T251 (2)
  - LLM Client: T260-T261 (2)
  - Retry: T270-T271 (2)
- **Service Layer**: T300-T360 (8 tasks)
  - Core Flow: T300-T303 (4)
  - API Step: T310 (1)
  - LLM Step: T320 (1)
  - Spawn: T330-T331 (2)
  - Recovery: T340 (1)
  - Compensation: T350-T351 (2)
  - Policy Check: T360 (1)
- **Service Tests**: T400-T404 (5 tasks)
- **API Routes**: T500-T501 (2 tasks)
- **DI Wiring**: T600-T602 (3 tasks)
- **Test Fixtures**: T700 (1 task)
- **Safety/Observability**: T800-T803 (4 tasks)
- **Contract Tests**: T900-T902 (3 tasks)

### Test Count Breakdown

| Category | File | Count |
|----------|------|-------|
| Domain model unit tests | test_unit.py | ~12 |
| DAG resolver tests | test_unit.py | ~6 |
| Template resolver tests | test_unit.py | ~6 |
| Resource lock tests | test_unit.py | ~4 |
| Credential vault tests | test_unit.py | ~4 |
| MCP client tests | test_unit.py | ~4 |
| Retry adapter tests | test_unit.py | ~5 |
| API route tests | test_unit.py | ~6 |
| Idempotency tests | test_idempotency.py | ~10 |
| Trust tier tests | test_trust_tiers.py | ~8 |
| Spawning tests | test_spawning.py | ~10 |
| Compensation tests | test_compensation.py | ~7 |
| Service flow tests | test_service.py | ~23 |
| Observability tests | test_observability.py | ~5 |
| Contract tests | test_contract.py | ~10 |
| **Total** | | **~120** |

Note: LLD Section 12 targets ~95 tests. Actual count exceeds target due to granular coverage of edge cases. This provides buffer for any tests that get consolidated during implementation.

---

## Dependencies

**External** (from LLD.md Section 15):
- `fastapi>=0.109.0` -- HTTP framework
- `pydantic>=2.5.0` -- Data validation
- `redis[hiredis]>=5.0.0` -- Idempotency, locks, context cache
- `anthropic>=0.18.0` -- LLM reasoning steps (Tier 1/Tier 2)
- `httpx>=0.27.0` -- MCP tool invocation (SSE transport)
- `cryptography>=42.0.0` -- AES-256-GCM credential decryption
- `ulid-py>=1.1.0` -- ULID generation for attestations
- `PyJWT>=2.8.0` -- Approval token validation (check compatibility with existing `python-jose[cryptography]`)

**Internal** (from LLD.md Section 15):
- `Signer.service.signer_service.SignerService` -- `verify_signature(plan_data, signature_data)`
- `PolicyEngine.service.policy_service.PolicyService` -- `evaluate_spawn(SpawnRequest)`
- `PolicyEngine.domain.models.SpawnRequest` -- spawn evaluation input
- `PluginRegistry.service.registry_service.RegistryService` -- `get_tool()`, `get_operation()`
- `PlanWriter.service.plan_writer_service.PlanWriterService` -- `persist_outcome()`
- `shared.schemas.plan.Plan`, `PlanStep`, `PlanConstraints`, `PlanMeta`
- `shared.schemas.signature.Signature`
- `shared.schemas.outcome.PlanOutcome`
- `shared.schemas.metrics.PlanMetrics`
- `shared.schemas.policy.PolicyDecision`, `PolicyAttestation`, `ReasoningConfig`, `PolicyRule`
- `shared.database.adapter.SharedDatabaseAdapter`
- `shared.database.models.CredentialVaultTable`
- `shared.api.error_handlers.APIErrorHandler`, `ErrorResponse`

**Test Dependencies**:
- `fakeredis[aioredis]` -- Redis mocking for idempotency and lock tests
- `pytest-asyncio` -- Async test support
- `httpx` -- ASGI test client

---

## Architectural Considerations

**Blast Radius** (from LLD Section 3, 13):
- If MCP server fails: single step fails, isolated; step-level retry -> Reasoner recovery -> terminal
- If Anthropic API fails: LLM reasoning steps fail; circuit breaker opens after 5 failures; pure API plans unaffected
- If Redis fails: Booker steps fail-safe (refuse to execute); Fetcher/Analyzer steps proceed without idempotency
- If credential vault fails: API steps cannot authenticate -> step fails -> plan terminal (security boundary, no retry)
- If PolicyEngine fails: spawning denied (fail-closed); non-spawn steps unaffected
- Compensation failures are isolated per-step: one compensation failing does not block others

**Determinism** (from LLD, GLOBAL_SPEC):
- Initial plan (revision 0) is immutable and cryptographically signed (Ed25519)
- Execute does NOT modify the original plan graph; spawned steps extend it as new revisions
- Idempotency keys ensure repeated execution produces same result
- Template resolution is deterministic given same step results

**State Management** (from LLD Section 13):
- ExecuteService is stateless; all mutable state lives in per-request `ExecutionContext`
- Persistent state: Redis (idempotency keys 24h TTL, resource locks 30s TTL)
- No background task durability; execution is synchronous within HTTP request
- Redis keys owned by ExecuteOrchestrator per MODULAR_ARCHITECTURE Section 3:
  - `idem:{user_id}:{integration_id}:{plan_id}:{step}:{call}:{hash}` -- 24h
  - `lock:resource:{user_id}:{integration_id}:{resource}:{entity}:{op}` -- 30s
  - `reasoning_context:{plan_id}:{step}` -- 1h

**Credential Isolation** (from GLOBAL_SPEC Section 8.1):
- Credentials decrypted ONLY at execution time, ONLY for API steps
- Plaintext zeroed from memory immediately after MCP invocation
- NEVER appears in logs, step results, error messages, or LLM contexts
- LLM reasoning steps (both tiers) have ZERO access to credential values

**Two-Tier LLM Trust** (from GLOBAL_SPEC Section 8.2):
- Tier 1 (untrusted_input): No tools, strict output schema, input sanitization
- Tier 2 (trusted): MCP tool access via spawning, PolicyEngine-bounded
- Plan validator (Planner-side) ensures Tier 2 Reasoner does not receive raw API output without Tier 1 sanitization
- ExecuteOrchestrator enforces trust tier at runtime call site
