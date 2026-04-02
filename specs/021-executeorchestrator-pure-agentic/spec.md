# Feature Specification: ExecuteOrchestrator

**Feature Branch**: `feat/executeorchestrator-pure-agentic`
**Created**: 2026-04-01
**Status**: Draft
**Spec ID**: 021
**Target Component**: `components/ExecuteOrchestrator/`

## Overview

ExecuteOrchestrator is the **pure agentic runtime execution engine** in the Orchestration Layer. It receives a signed, approved plan and dispatches every step — API steps via MCP tool invocations, LLM reasoning steps via Anthropic API with two-tier trust enforcement, and policy checks via PolicyEngine. It handles DAG resolution, parallel execution (`asyncio.gather`), credential decryption, idempotency, resource locking, step spawning (with PolicyEngine attestations), retry/recovery, and Saga-pattern compensation. It is the single component that ties together Signer (verification), PolicyEngine (governance), PluginRegistry (tool catalog), and the credential vault (AES-256-GCM) to execute plans safely.

## Goals
- Execute signed plans end-to-end: verify signature + approval token, resolve DAG, dispatch all steps, return `PlanOutcome`
- Enforce two-tier LLM trust model at runtime (Tier 1 sandboxed vs Tier 2 agent)
- Dispatch API steps via MCP tool invocations with decrypted credentials
- Dispatch LLM reasoning steps via Anthropic API with `ReasoningConfig`
- Handle step spawning: evaluate via PolicyEngine, create attestations, extend graph
- Implement idempotency (3-state Redis records) for all side-effecting (Booker) steps
- Implement resource locking for write operations (Redis-based, multi-user scoped)
- Implement Saga-pattern compensation (reverse-order undo on failure)
- Support parallel execution of independent steps via `asyncio.gather()`
- Reuse cached preview state for steps marked `execute_mode: "preview_only"`
- Route step failures to nearest Reasoner for LLM-adaptive recovery (hybrid plans)
- Produce structured `PlanOutcome` with `final_graph_json`, `plan_revision`, and `policy_attestations`

## Non-Goals
- **Not a Preview engine** — PreviewOrchestrator handles read-only preview (separate component)
- **Not an approval service** — ApprovalGate handles token issuance (separate component)
- **Not an infrastructure monitor** — ExecutionMonitor detects stuck/hung tasks (separate component)
- **Not a scheduler** — APScheduler handles long-running polling (Watcher role durable mode is out of scope for MVP)
- **Not an MCP server** — ExecuteOrchestrator is an MCP *client* that invokes external MCP servers
- **No workflow-level replay** — failed plans after recovery exhaustion are terminal
- **No credential management** — credential CRUD is out of scope; ExecuteOrchestrator only reads/decrypts at execution time

---

## User Scenarios & Testing

### User Story 1 — Execute a Pure API Plan (Priority: P1)

A user has approved a deterministic plan with only `type: "api"` steps (e.g., "Book meeting with Alice"). ExecuteOrchestrator verifies the signature, resolves the DAG, dispatches each step via MCP, and returns a successful outcome.

**Why this priority**: This is the core happy path — the majority of plans are pure API. If this doesn't work, nothing works.

**Independent Test**: Can be fully tested by mocking MCP tool invocations and verifying DAG traversal order, parallel grouping, and outcome assembly.

**Acceptance Scenarios**:

1. **Given** a signed plan with 4 API steps (2 parallel Fetchers -> 1 Analyzer -> 1 Booker) and a valid approval token, **When** `execute_plan()` is called, **Then** steps 1-2 execute in parallel, step 3 executes after both complete, step 4 executes after step 3, and a `PlanOutcome(success=True)` is returned with all step results.
2. **Given** a plan with `execute_mode: "preview_only"` on step 1, **When** executed, **Then** step 1 is skipped and its cached result from preview state is used for downstream template resolution.
3. **Given** a plan with a Booker step, **When** executed, **Then** an idempotency key is claimed in Redis before MCP invocation, and the result is cached with `SUCCEEDED` state.

---

### User Story 2 — Idempotency Prevents Duplicate Operations (Priority: P1)

A Booker step (write operation) is retried after a network failure. The idempotency mechanism detects the prior execution and returns the cached result instead of creating a duplicate.

**Why this priority**: Without idempotency, retries create duplicate calendar events, duplicate emails, etc. This is a safety-critical feature.

**Independent Test**: Can be tested by pre-populating a Redis idempotency key with `SUCCEEDED` state and verifying the step returns the cached result without MCP invocation.

**Acceptance Scenarios**:

1. **Given** a Booker step whose idempotency key is in `SUCCEEDED` state, **When** the step executes, **Then** the cached result is returned and no MCP call is made.
2. **Given** a Booker step whose idempotency key is in `IN_FLIGHT` state and is less than 5 minutes old, **When** a concurrent execution attempts the same step, **Then** an `IdempotencyConflict` error is raised.
3. **Given** a Booker step whose idempotency key is in `IN_FLIGHT` state and is more than 5 minutes old (stale), **When** executed, **Then** the stale record is taken over and the step re-executes.
4. **Given** a Booker step whose idempotency key is in `FAILED` state, **When** retried, **Then** the old record is deleted and the step re-executes.

---

### User Story 3 — Two-Tier LLM Trust Enforcement (Priority: P1)

A hybrid plan contains LLM reasoning steps with different trust levels. ExecuteOrchestrator enforces Tier 1 (sandboxed, no tools, strict schema) and Tier 2 (agent, PolicyEngine-bounded) at runtime.

**Why this priority**: Trust tier enforcement is the primary defense against prompt injection. Without it, untrusted API data could manipulate agent reasoning.

**Independent Test**: Can be tested by mocking the Anthropic API and verifying that Tier 1 calls disable tools and require output schema validation, while Tier 2 calls enable spawning.

**Acceptance Scenarios**:

1. **Given** a step with `type: "llm_reasoning"` and `trust_level: "untrusted_input"`, **When** dispatched, **Then** the Anthropic API call has tools disabled, `output_schema_ref` is enforced, and the response is schema-validated before passing downstream.
2. **Given** a step with `type: "llm_reasoning"` and `trust_level: "trusted"`, **When** dispatched, **Then** the Anthropic API call is made with the step's `reasoning_config`, and spawn requests in the response are forwarded to PolicyEngine.

---

### User Story 4 — Step Spawning with PolicyEngine Attestation (Priority: P2)

A Tier 2 Reasoner with `can_spawn: true` proposes a new Fetcher step at runtime. PolicyEngine evaluates and approves. The spawned step is appended to the graph, `plan_revision` increments, and a `PolicyAttestation` is created.

**Why this priority**: Spawning is what makes plans adaptive — essential for open-ended tasks (flight search, research).

**Independent Test**: Can be tested by mocking PolicyEngine.evaluate() to return `allowed=True` and verifying graph extension, revision increment, and attestation creation.

**Acceptance Scenarios**:

1. **Given** a Tier 2 Reasoner step with `can_spawn: true` returns a spawn request for a Fetcher, **When** PolicyEngine approves, **Then** a new PlanStep is appended to the graph with `spawned_by` set, `plan_revision` increments by 1, a `PolicyAttestation` is created, and the spawned step executes.
2. **Given** a spawn request where the tool is NOT in the plan's `plugins` array, **When** PolicyEngine evaluates, **Then** the spawn is denied and the Reasoner receives the denial reason.
3. **Given** a spawn request for a Booker role, **When** PolicyEngine evaluates, **Then** a `gate_id` is injected and execution pauses for HITL approval.
4. **Given** a Reasoner has already spawned `max_spawned_steps` steps, **When** it proposes another, **Then** the spawn is denied with reason "spawn limit exceeded".

---

### User Story 5 — Credential Decryption and Isolation (Priority: P2)

API steps require credentials to invoke MCP tools. ExecuteOrchestrator decrypts credentials from the vault at execution time and passes them to the MCP client. Credentials are never exposed to LLM reasoning steps.

**Why this priority**: Credential isolation is a security boundary — LLM must never see plaintext tokens.

**Independent Test**: Can be tested by mocking the credential vault and verifying decryption is called only for API steps, plaintext is passed to MCP, and the value is not included in step results or logs.

**Acceptance Scenarios**:

1. **Given** an API step referencing a credential ID, **When** executed, **Then** the credential is decrypted from the vault using AES-256-GCM, passed to the MCP tool invocation, and zeroed from memory after the call completes.
2. **Given** an LLM reasoning step, **When** dispatched, **Then** no credential decryption occurs and the Anthropic API call contains zero credential values.

---

### User Story 6 — Compensation on Failure (Saga Pattern) (Priority: P2)

Step 3 fails after steps 1 and 2 (both Booker) succeeded. ExecuteOrchestrator executes compensation operations in reverse order for all completed Booker steps that have declared compensation in PluginRegistry.

**Why this priority**: Without compensation, partial execution leaves users in an inconsistent state (event created but email not sent).

**Independent Test**: Can be tested by failing step 3 deliberately and verifying compensation calls are made for steps 2 then 1 in reverse order.

**Acceptance Scenarios**:

1. **Given** steps 1 (Booker, compensation: "delete_event") and 2 (Booker, compensation: null) succeeded, and step 3 fails, **When** compensation runs, **Then** step 1's compensation operation ("delete_event") is invoked via MCP, step 2 is skipped (no compensation declared), and the final `PlanOutcome` includes `error_type: "step_failure"`.
2. **Given** a compensation operation itself fails, **When** executing undo, **Then** the failure is logged with structured context but does not prevent other compensations from running.

---

### User Story 7 — Resource Locking for Write Operations (Priority: P2)

Two concurrent plan executions attempt to write to the same calendar resource for the same user. Resource locking prevents conflicts.

**Why this priority**: Without locking, concurrent writes can create duplicate or conflicting entries.

**Independent Test**: Can be tested by simulating concurrent lock acquisition on the same resource key.

**Acceptance Scenarios**:

1. **Given** a Booker step for user A's Google Calendar, **When** a lock is acquired, **Then** the lock key is `lock:resource:{user_id}:{integration_id}:{resource}:{entity}:{operation}` and the step proceeds.
2. **Given** another execution holds the lock on the same resource, **When** a second execution attempts the same step, **Then** it waits up to 30 seconds, and if the lock is not released, raises a `ResourceLockTimeout` error.

---

### User Story 8 — LLM-Adaptive Failure Recovery (Priority: P3)

An API step in a hybrid plan fails after step-level retries. The failure routes to the nearest Reasoner, which proposes a recovery action. PolicyEngine approves, and the recovery step executes.

**Why this priority**: Adaptive recovery reduces plan failure rates for open-ended tasks.

**Independent Test**: Can be tested by failing an API step, mocking the Reasoner's recovery proposal, and verifying PolicyEngine evaluation + spawned recovery step execution.

**Acceptance Scenarios**:

1. **Given** step 2 (Fetcher) fails with 503 after 3 retries, and step 4 is a Reasoner with `can_spawn: true`, **When** the failure routes to step 4, **Then** the Reasoner receives the error object, proposes a recovery Fetcher with adjusted args, PolicyEngine evaluates, and the recovery step executes.
2. **Given** recovery retries are exhausted (per `max_recovery_actions` policy), **When** another failure occurs, **Then** the plan is marked terminal with `PlanOutcome(success=False, error_type="recovery_exhausted")`.

---

### User Story 9 — Step-Level Retries for Transient Failures (Priority: P3)

An API step returns a 503 error. The retry policy retries with exponential backoff (1s, 2s, 4s) up to 3 times before marking the step as failed.

**Why this priority**: Transient failures are common with external APIs; retries handle them without user intervention.

**Independent Test**: Can be tested by mocking MCP to fail twice then succeed on the third attempt.

**Acceptance Scenarios**:

1. **Given** an API step with `max_retries: 3`, **When** the MCP call returns 503 twice then succeeds, **Then** the step completes successfully after 2 retries and the outcome shows no failure.
2. **Given** an API step with `max_retries: 3`, **When** all 3 retries fail, **Then** the step is marked `status: "failed"` and failure routes to recovery (hybrid) or terminal (pure API).

---

### Edge Cases

- **Empty plan graph**: Rejected at validation (min 1 step) — `PlanOutcome(success=False, error_type="validation_error")`
- **Circular dependencies in `after`**: Detected during DAG resolution — `PlanOutcome(success=False, error_type="cycle_detected")`
- **Step timeout exceeded**: Step cancelled after `timeout_s` — marked failed — routes to recovery or terminal
- **Approval token expired**: JWT validation fails at pre-execution �� `PlanOutcome(success=False, error_type="token_expired")`
- **Signature verification fails**: Ed25519 verification fails — execution rejected entirely
- **Plan TTL exceeded**: `constraints.ttl_s` elapsed since plan creation — execution rejected
- **Redis unavailable**: Idempotency and locking degrade fail-safe — Booker steps refuse to execute without idempotency
- **MCP server unreachable**: Treated as transient failure — step-level retry — recovery — terminal
- **Spawned step count exceeds 100**: Rejected by PolicyEngine (plan-level limit)

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST verify Ed25519 plan signature before execution (via Signer component)
- **FR-002**: System MUST validate approval token (JWT, 15min TTL) before execution
- **FR-003**: System MUST resolve plan DAG via topological sort and group independent steps for parallel execution
- **FR-004**: System MUST dispatch `type: "api"` steps via MCP tool invocations with decrypted credentials
- **FR-005**: System MUST dispatch `type: "llm_reasoning"` steps via Anthropic API with `ReasoningConfig`
- **FR-006**: System MUST enforce Tier 1 trust (no tools, strict output schema) for `trust_level: "untrusted_input"` steps
- **FR-007**: System MUST enforce Tier 2 trust (PolicyEngine-bounded, spawning allowed) for `trust_level: "trusted"` steps
- **FR-008**: System MUST implement 3-state idempotency (IN_FLIGHT, SUCCEEDED, FAILED) for all Booker steps
- **FR-009**: System MUST acquire resource locks before Booker step execution and release after completion
- **FR-010**: System MUST execute compensation operations in reverse order when a step fails after others succeeded
- **FR-011**: System MUST evaluate spawn requests via PolicyEngine and create PolicyAttestations for approved spawns
- **FR-012**: System MUST increment `plan_revision` on each spawn event
- **FR-013**: System MUST inject `gate_id` on spawned Booker steps (non-overridable HITL)
- **FR-014**: System MUST enforce `max_spawned_steps` per Reasoner and 100-step plan limit
- **FR-015**: System MUST implement step-level retries with exponential backoff for transient failures
- **FR-016**: System MUST route step failures to nearest Reasoner for LLM-adaptive recovery (hybrid plans)
- **FR-017**: System MUST skip steps with `execute_mode: "preview_only"` and use cached preview results
- **FR-018**: System MUST resolve template args (`{{step_N.result.field}}`) from execution context
- **FR-019**: System MUST return a `PlanOutcome` with final_graph_json, plan_revision, and policy_attestations
- **FR-020**: System MUST zero credential values from memory after MCP invocation completes
- **FR-021**: System MUST log all step executions with structured context (`plan_id`, `step`, `role`, `latency_ms`) and never log credentials/PII

### Key Entities

- **ExecutionContext**: Runtime state holding step results, credentials, idempotency records, spawned steps, and attestations
- **StepResult**: Result of a single step execution (status, result dict, error dict, latency_ms)
- **IdempotencyRecord**: 3-state Redis record (IN_FLIGHT, SUCCEEDED, FAILED) with owner, timestamps, and cached result
- **ResourceLock**: Redis-based lock scoped by user + integration + resource + entity + operation
- **CompensationRecord**: Undo info for completed Booker steps (step number, operation, result, compensation operation + args)

---

## Interfaces & Contracts (conform to GLOBAL_SPEC v3)

### Input: ExecuteRequest

```python
class ExecuteRequest(BaseModel):
    plan: Plan                     # Signed plan (GLOBAL_SPEC S2.3)
    signature: Signature           # Ed25519 signature (GLOBAL_SPEC S2.4)
    approval_token: str            # JWT from ApprovalGate (15min TTL)
    user_id: str                   # UUID of the requesting user
    trace_id: str                  # Distributed tracing correlation ID
    preview_state: dict | None     # Cached preview results (step_num -> result)
    integration_credentials: dict  # Mapping: tool_id -> credential_vault_id
```

### Output: PlanOutcome (existing shared schema)

Uses `shared/schemas/outcome.py` — `PlanOutcome` with fields: `success`, `error_type`, `error_details`, `execution_start`, `execution_end`, `total_steps`, `failed_step`, `context_data`, `final_graph_json`, `plan_revision`, `policy_attestations`.

### Execute Wrapper (per GLOBAL_SPEC S2.6)

Each step produces:
```json
{
  "provider": "<tool_id>",
  "result": { "id": "<external_id>", "link": "<optional>" },
  "status": "created|updated|skipped|error"
}
```

### Internal Protocols

```python
class MCPClient(Protocol):
    """MCP tool invocation client."""
    async def invoke(
        self,
        server: str,
        tool: str,
        args: dict,
        credentials: dict,
        timeout_s: int = 30,
    ) -> dict: ...

class LLMClient(Protocol):
    """Anthropic API client for reasoning steps."""
    async def reason(
        self,
        config: ReasoningConfig,
        context: list[dict],
        trust_level: str,
    ) -> dict: ...

class CredentialVault(Protocol):
    """Encrypted credential storage."""
    async def decrypt(self, credential_id: str, user_id: str) -> str: ...
```

Reference: docs/architecture/GLOBAL_SPEC.md v3

---

## Component Mapping

- **Target**: `components/ExecuteOrchestrator/`
- Files expected to change:
  - `__init__.py`
  - `domain/__init__.py`
  - `domain/models.py` — ExecuteRequest, StepResult, IdempotencyRecord, ResourceLock, CompensationRecord, error classes
  - `service/__init__.py`
  - `service/execute_service.py` — Core orchestration: DAG resolution, step dispatch, parallel grouping, outcome assembly
  - `adapters/__init__.py`
  - `adapters/mcp_client.py` — MCP tool invocation adapter (Protocol + implementation)
  - `adapters/llm_client.py` — Anthropic API adapter for reasoning steps (Protocol + implementation)
  - `adapters/credential_vault.py` — AES-256-GCM decryption adapter
  - `adapters/idempotency.py` — Redis 3-state idempotency adapter
  - `adapters/resource_lock.py` — Redis resource lock adapter
  - `adapters/template_resolver.py` — `{{step_N.result.field}}` template arg resolution
  - `api/__init__.py`
  - `api/routes.py` — POST `/execute` endpoint (thin handler)
  - `tests/__init__.py`
  - `tests/conftest.py` — Fixtures, mock factories
  - `tests/test_unit.py` — Domain model tests, DAG resolution, template resolution
  - `tests/test_service.py` — Service layer tests (mocked adapters)
  - `tests/test_idempotency.py` — Idempotency adapter tests
  - `tests/test_compensation.py` — Saga compensation tests
  - `tests/test_trust_tiers.py` — Two-tier LLM enforcement tests
  - `tests/test_spawning.py` — Step spawning + PolicyEngine attestation tests
  - `tests/test_contract.py` — Schema contract tests (PlanOutcome, ExecuteRequest)
  - `tests/test_observability.py` — Structured logging, no PII/secrets in logs

- **Shared files expected to change**:
  - `shared/app.py` — Add `create_execute_service()` in lifespan
  - `shared/dependencies.py` — Add `get_execute_service()` Depends wrapper

---

## Dependencies & Risks

### Component Dependencies
| Dependency | Interface | Risk |
|-----------|-----------|------|
| **Signer** | `verify_signature(plan_data, signature_data)` | Low — already implemented and tested |
| **PolicyEngine** | `evaluate(step, plan, policy_ref)` -> `PolicyDecision` | Low — already implemented |
| **PluginRegistry** | `get_tool(tool_id)` -> tool metadata, `get_operation(tool_id, op_id)` -> compensation info | Low — already implemented |
| **PlanWriter** | `write_outcome(plan_id, outcome)` | Low — already implemented |
| **Redis** | Idempotency, locks, approval tokens | Medium — Redis unavailability degrades Booker safety |
| **PostgreSQL** | Credential vault, policy rules | Medium — DB unavailability blocks credential decryption |
| **Anthropic API** | LLM reasoning steps | Medium — API unavailability blocks hybrid plan reasoning |
| **MCP servers** | External tool invocations | High — external dependency, network failures expected |

### Risks
1. **MCP server diversity**: Each MCP server may have different error semantics — need robust error normalization
2. **Credential vault master key**: If env var missing, all API steps fail — need clear startup validation
3. **Redis as single point of failure for idempotency**: If Redis is down, Booker steps must refuse to execute (fail-safe, not fail-open)
4. **LLM reasoning latency**: Tier 2 reasoning can be slow (2-5s) — affects overall plan execution time
5. **Compensation idempotency**: Compensation operations themselves may fail — need best-effort with logging

---

## Non-Functional Requirements

Inherit baseline from GLOBAL_SPEC v3 with these specific targets:

| Metric | Target | Notes |
|--------|--------|-------|
| Execute latency (pure API plan, 4 steps) | p95 < 2s | Per GLOBAL_SPEC S3 |
| Execute latency (hybrid plan, 6 steps + 1 reasoning) | p95 < 8s | LLM reasoning adds 2-5s |
| Idempotency check | p95 < 5ms | Redis GET |
| Resource lock acquire | p95 < 10ms | Redis SET NX |
| PolicyEngine evaluation | p95 < 5ms | Redis-cached |
| Credential decryption | p95 < 2ms | In-memory AES-256-GCM |
| Step-level retry backoff | 1s, 2s, 4s | Exponential, max 3 |
| Idempotency key TTL | 24 hours | Per HLD |
| Resource lock TTL | 30 seconds | Per HLD |
| Approval token TTL | 15 minutes | Per GLOBAL_SPEC S2.7 |
| Observability | Structured logs with `plan_id`, `step`, `role`, `latency_ms` | No secrets/PII |
| Availability | 99.5% | Per GLOBAL_SPEC S3 (Execute path) |

---

## Open Questions

1. **MCP client library**: Which Python MCP client library to use? (`mcp` package from Anthropic, or custom httpx-based client for `stdio`/`sse` transports?)
2. **Preview state source**: How is cached preview state passed to ExecuteOrchestrator — via Redis key reference or inline in the request?
3. **Durable mode (Watcher role)**: Should ExecuteOrchestrator handle APScheduler-based durable steps, or defer to a separate component?
4. **Execution tracking table**: Should ExecuteOrchestrator write to an `execution_tracker` table for ExecutionMonitor, or should that be a separate adapter concern?
5. **Gate pause mechanism**: When a spawned Booker step needs HITL, how does execution pause? Return partial outcome and await callback? Or hold the asyncio task with Redis pub/sub?

---

## Conformance

This work conforms to `docs/architecture/GLOBAL_SPEC.md v3` and `docs/architecture/Project_HLD.md v6.1`.

Specific conformance points:
- Safety Model S1: Execute only after approval + signature verification
- Canonical Contracts S2.3: Uses Plan, PlanStep, PlanConstraints models
- Canonical Contracts S2.4: Verifies Ed25519 signatures via Signer
- Canonical Contracts S2.4.1: Creates PolicyAttestations for spawned steps
- Canonical Contracts S2.6: Returns Execute wrapper per step
- Canonical Contracts S2.7: Validates approval tokens
- Canonical Contracts S2.8: Enforces role-based policies (idempotency, HITL, retry, compensation, locking)
- Canonical Contracts S2.9: PolicyEngine integration for spawn evaluation
- NFRs S3: Execute p95 < 2s, observability
- Safety S8: Credential vault isolation, two-tier LLM execution
- Safety S8.1: Credential lifecycle (decrypt -> MCP call -> zero)
- Safety S8.2: Two-tier trust enforcement (Tier 1 sandboxed, Tier 2 agent)

### Deviations
- **Durable mode (Watcher)**: Deferred to Phase 4 (ExecutionMonitor). MVP handles `mode: "interactive"` only.
- **Full gate pause/resume**: MVP returns error for spawned Booker steps that need HITL — full async pause/resume deferred to ApprovalGate integration.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Pure API plan with 4 steps executes successfully end-to-end with correct DAG ordering in < 2s (mocked MCP)
- **SC-002**: Idempotency prevents duplicate Booker execution — 100% of retried Booker steps return cached result
- **SC-003**: Trust tier enforcement — 100% of Tier 1 LLM calls have tools disabled and output schema enforced
- **SC-004**: Step spawning — spawned steps receive PolicyAttestations and plan_revision increments correctly
- **SC-005**: Compensation — all compensatable Booker steps are undone in reverse order on failure
- **SC-006**: Credential isolation — zero credential values appear in logs, step results, or LLM contexts
- **SC-007**: All tests pass (target: 80+ tests across unit, service, adapter, contract, observability)
- **SC-008**: Ruff lint clean, mypy type check clean, pytest coverage > 80% for new code
