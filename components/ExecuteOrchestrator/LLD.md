# Low-Level Design — ExecuteOrchestrator

**Component**: `components/ExecuteOrchestrator/`
**Layer**: Orchestration Layer
**Type**: Service component (HTTP route + library service)
**Created**: 2026-04-01
**SPEC**: `specs/021-executeorchestrator-pure-agentic/spec.md`

---

## 1. Purpose & Scope

ExecuteOrchestrator is the **pure agentic runtime execution engine**. It receives a signed, approved plan and executes every step to completion, producing a `PlanOutcome`.

**Responsibilities:**
- Verify Ed25519 signature and approval token before execution
- Resolve plan DAG via topological sort into parallel execution levels
- Dispatch API steps via MCP tool invocations with decrypted credentials
- Dispatch LLM reasoning steps via Anthropic API with two-tier trust enforcement
- Evaluate spawn requests via PolicyEngine, create attestations, extend graph
- Enforce idempotency (3-state Redis) for Booker steps
- Acquire/release resource locks for write operations
- Execute Saga-pattern compensation on failure
- Resolve template args (`{{step_N.result.field}}`) from execution context
- Route failures to nearest Reasoner for LLM-adaptive recovery (hybrid plans)
- Return `PlanOutcome` with final graph, revision, and attestations

**Out of scope:**
- Preview execution (PreviewOrchestrator)
- Approval token issuance (ApprovalGate)
- Stuck execution detection (ExecutionMonitor)
- Credential CRUD (PluginRegistry)
- Long-running durable scheduling (APScheduler — Phase 4)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v3.0 | §1 Safety Model (Execute), §2.3 Plan, §2.4 Signature, §2.4.1 PolicyAttestation, §2.6 Execute Wrapper, §2.7 Approval Token, §2.8 Runtime Agent Roles, §2.9 PolicyEngine Contract, §8 Safety & Governance, §8.1 Credential Vault, §8.2 Two-Tier LLM |
| MODULAR_ARCHITECTURE.md | v2.1 | §1 Orchestration Layer, §3 Redis key ownership (idempotency, locks, reasoning_context), §4 dependency matrix, §5 execution flow, §7 multi-gate approvals, §8 preview state caching |
| Project_HLD.md | v6.1 | §3 ExecuteOrchestrator, §4 Runtime Agent Roles, §5 Safety (idempotency, compensation, resource locking, retry), §13 Async Execution, §14 Parallel Steps |
| SHARED_INFRASTRUCTURE.md | v1.0.0 | §4.1 shared schemas, DI wiring pattern |

---

## 3. Architecture Overview

### Layer Placement

```
Orchestration Layer
├── PreviewOrchestrator  (read-only MCP, stubs)
├── ApprovalGate         (JWT tokens, Redis)
├── ExecuteOrchestrator  ← THIS COMPONENT
└── ExecutionMonitor     (stuck detection)
```

### Blast Radius Analysis

| Failure | Impact | Containment |
|---------|--------|-------------|
| MCP server unreachable | Single step fails | Step-level retry (3x exponential) → Reasoner recovery → terminal |
| Anthropic API down | LLM reasoning steps fail | Circuit breaker → plan terminal for hybrid plans; pure API plans unaffected |
| Redis unavailable | Idempotency + locking degraded | Booker steps fail-safe (refuse to execute) → plan terminal |
| Credential vault error | API steps cannot authenticate | Step fails → no retry (security boundary) → plan terminal |
| PolicyEngine error | Spawn evaluation fails | Spawning denied (fail-closed) → Reasoner receives denial |

### Component Boundaries

```
                    ┌─────────────────────────┐
                    │   ExecuteOrchestrator    │
                    │                         │
  ExecuteRequest ──→│  ┌─── DAG Resolver ───┐ │
                    │  │ Topological sort    │ │
                    │  │ Parallel grouping   │ │
                    │  └────────┬────────────┘ │
                    │           │              │
                    │  ┌────────▼────────────┐ │
                    │  │ Step Dispatcher     │ │──→ MCP Client (API steps)
                    │  │  type: api          │ │──→ LLM Client (reasoning)
                    │  │  type: llm_reasoning│ │──→ PolicyEngine (spawning)
                    │  │  type: policy_check │ │
                    │  └────────┬────────────┘ │
                    │           │              │
                    │  ┌────────▼────────────┐ │
                    │  │ Safety Layer        │ │──→ Redis (idempotency, locks)
                    │  │  Idempotency        │ │──→ Credential Vault (decrypt)
                    │  │  Resource locks     │ │──→ Signer (verify)
                    │  │  Compensation       │ │
                    │  └────────┬────────────┘ │
                    │           │              │
                    │  ┌────────▼────────────┐ │
                    │  │ Outcome Builder     │ │──→ PlanWriter (persist)
                    │  └─────────────────────┘ │
                    └─────────────────────────┘
```

### Dependency Contract Table

| Dependency | Method | Input | Output | Error Handling |
|-----------|--------|-------|--------|----------------|
| Signer | `verify_signature(plan_data, sig_data)` | dict, dict | `True` or raises | `InvalidSignatureError` → reject execution |
| PolicyEngine | `evaluate_spawn(request)` | `SpawnRequest` | `PolicyDecision` | Deny-by-default on error |
| PluginRegistry | `get_tool(tool_id)` | str | `ToolModel` | `ToolNotFoundError` → step fails |
| PluginRegistry | `get_operation(tool_id, op_id)` | str, str | `OperationModel` | `OperationNotFoundError` → step fails |
| PlanWriter | `write_outcome(plan_id, signature, outcome, metrics)` | str, Signature, PlanOutcome, PlanMetrics | None | Non-fatal — log warning, don't fail execution |

---

## 4. Interfaces

### 4.1 Service Interface

```python
class ExecuteService:
    """Pure agentic plan execution engine."""

    async def execute_plan(self, request: ExecuteRequest) -> PlanOutcome:
        """
        Execute a signed, approved plan end-to-end.

        Args:
            request: Validated execution request with plan, signature,
                     approval token, user context, and preview state.

        Returns:
            PlanOutcome with success/failure status, step results,
            final graph (including spawned steps), plan revision,
            and policy attestations.

        Raises:
            SignatureVerificationError: Ed25519 signature invalid.
            ApprovalTokenError: Token expired, invalid, or scope mismatch.
            PlanExpiredError: Plan TTL exceeded.
        """
```

### 4.2 Factory Function

```python
def create_execute_service(
    signer_service: SignerService,
    policy_service: PolicyService,
    registry_service: RegistryService,
    plan_writer_service: PlanWriterService,
    mcp_client: MCPClient,
    llm_client: LLMClient,
    credential_vault: CredentialVaultAdapter,
    redis_client: Redis,
) -> ExecuteService:
    """Create ExecuteService with all dependencies.

    Called once during app lifespan startup in shared/app.py.
    """
```

### 4.3 HTTP Endpoint

```python
@router.post("/execute", response_model=PlanOutcome)
async def execute_plan(
    request: ExecuteRequest,
    service: ExecuteService = Depends(get_execute_service),
) -> PlanOutcome:
    """Execute a signed, approved plan."""
```

**Route**: `POST /api/v1/execute`

### 4.4 Consumer Contracts

**Upstream consumers** (who calls ExecuteOrchestrator):
- **Frontend/API Gateway**: Sends `ExecuteRequest` after user approves preview. Receives `PlanOutcome`.

**Downstream consumers** (who ExecuteOrchestrator calls):
- **PlanWriter**: Called after execution completes (success or failure) to persist outcome.
- **PolicyEngine**: Called synchronously during execution when a Reasoner proposes spawning.
- **Signer**: Called once at pre-execution to verify plan signature.

---

## 5. Data Model

### 5.1 Domain Models (`domain/models.py`)

```python
class ExecuteRequest(BaseModel):
    """Input contract for plan execution."""
    plan: Plan                                    # shared/schemas/plan.py
    signature: Signature                          # shared/schemas/signature.py
    approval_token: str                           # JWT from ApprovalGate
    user_id: str                                  # UUID
    trace_id: str                                 # Distributed tracing correlation
    preview_state: dict[str, Any] | None = None   # step_num → cached result
    integration_credentials: dict[str, str] = {}   # tool_id → credential_vault_id

class StepResult(BaseModel):
    """Result of executing a single step."""
    step: int
    status: Literal["completed", "failed", "skipped"]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    latency_ms: int = 0
    retries: int = 0

class CompensationRecord(BaseModel):
    """Undo info for a completed Booker step."""
    step: int
    tool_id: str
    operation: str
    result: dict[str, Any]
    compensation_operation: str | None
    compensation_args: dict[str, Any] | None

class ExecutionContext:
    """Mutable runtime state (not a Pydantic model — internal only)."""
    plan: Plan
    user_id: str
    trace_id: str
    step_results: dict[int, StepResult]        # step_num → result
    compensation_stack: list[CompensationRecord]
    spawned_steps: list[PlanStep]
    attestations: list[PolicyAttestation]
    plan_revision: int
    recovery_action_count: int
```

### 5.2 Domain Errors (`domain/models.py`)

```python
class ExecuteError(Exception):
    """Base error for ExecuteOrchestrator."""

class SignatureVerificationError(ExecuteError):
    """Plan signature verification failed."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Signature verification failed: {reason}")

class ApprovalTokenError(ExecuteError):
    """Approval token invalid or expired."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Approval token error: {reason}")

class PlanExpiredError(ExecuteError):
    """Plan TTL exceeded."""
    def __init__(self, plan_id: str, ttl_s: int):
        self.plan_id = plan_id
        super().__init__(f"Plan {plan_id} expired (TTL {ttl_s}s)")

class StepExecutionError(ExecuteError):
    """Step failed after retries."""
    def __init__(self, step: int, reason: str, retries: int = 0):
        self.step = step
        self.retries = retries
        super().__init__(f"Step {step} failed: {reason}")

class IdempotencyConflict(ExecuteError):
    """Another execution owns this idempotency slot."""
    def __init__(self, key: str):
        self.key = key
        super().__init__(f"Idempotency conflict: {key}")

class ResourceLockTimeout(ExecuteError):
    """Could not acquire resource lock within timeout."""
    def __init__(self, lock_key: str, timeout_s: int):
        self.lock_key = lock_key
        super().__init__(f"Lock timeout ({timeout_s}s): {lock_key}")

class MCPInvocationError(ExecuteError):
    """MCP tool invocation failed."""
    def __init__(self, server: str, tool: str, reason: str):
        self.server = server
        self.tool = tool
        super().__init__(f"MCP error ({server}/{tool}): {reason}")

class SpawnDeniedError(ExecuteError):
    """PolicyEngine denied a spawn request."""
    def __init__(self, reason: str, violations: list[str]):
        self.violations = violations
        super().__init__(f"Spawn denied: {reason}")

class RecoveryExhaustedError(ExecuteError):
    """All recovery attempts exhausted."""
    def __init__(self, step: int, attempts: int):
        self.step = step
        super().__init__(f"Recovery exhausted for step {step} after {attempts} attempts")
```

---

## 6. Adapters

### 6.1 MCP Client (`adapters/mcp_client.py`)

```python
@runtime_checkable
class MCPClient(Protocol):
    """Protocol for MCP tool invocations."""
    async def invoke(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None = None,
        timeout_s: int = 30,
    ) -> dict[str, Any]: ...

class MCPClientAdapter:
    """MCP client using httpx for SSE transport.

    Resolves MCP server + tool name from PluginRegistry ToolTable
    (mcp_server, transport columns) and OperationTable (mcp_tool column).
    """
    def __init__(self, registry_service: RegistryService):
        self._registry = registry_service

    async def invoke(self, server, tool, args, credentials=None, timeout_s=30):
        # 1. Resolve MCP server URL from PluginRegistry
        # 2. Build MCP tool_call request
        # 3. Send via httpx with timeout
        # 4. Parse and return result dict
        # 5. Normalize errors → MCPInvocationError
```

**MCP transport**: For MVP, use httpx-based SSE client. The `mcp` Python package (from Anthropic) can be adopted later if it stabilizes.

### 6.2 LLM Client (`adapters/llm_client.py`)

```python
@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM reasoning dispatch."""
    async def reason(
        self,
        config: ReasoningConfig,
        context: list[dict[str, Any]],
        trust_level: Literal["untrusted_input", "trusted"],
    ) -> dict[str, Any]: ...

class AnthropicReasoningAdapter:
    """Anthropic API adapter for reasoning steps.

    Enforces two-tier trust:
    - Tier 1 (untrusted_input): tools=[], output schema validated
    - Tier 2 (trusted): tools enabled, spawn requests parsed
    """
    def __init__(self, api_key: str | None = None):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def reason(self, config, context, trust_level):
        messages = self._build_messages(context)
        kwargs = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "system": self._load_system_prompt(config.system_prompt_ref),
            "messages": messages,
        }
        # Tier 1: no tools, enforce output schema
        if trust_level == "untrusted_input":
            kwargs["tools"] = []
            # output_schema_ref → JSON schema for response_format
        # Tier 2: enable tool_use for spawn requests
        elif trust_level == "trusted":
            kwargs["tools"] = self._build_spawn_tools()

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response, trust_level)
```

### 6.3 Credential Vault (`adapters/credential_vault.py`)

```python
class CredentialVaultAdapter:
    """AES-256-GCM credential decryption.

    Reads encrypted values from credential_vault table,
    decrypts with master key from CREDENTIAL_MASTER_KEY env var.
    Zeroes plaintext after use.
    """
    def __init__(self, db: SharedDatabaseAdapter):
        self._db = db
        self._master_key = os.environ.get("CREDENTIAL_MASTER_KEY", "").encode()

    async def decrypt(self, credential_id: str, user_id: str) -> str:
        """Decrypt credential value. Returns plaintext (caller must zero after use)."""
        # 1. Query credential_vault table for encrypted_value + iv + key_version
        # 2. Derive key from master_key + key_version
        # 3. Decrypt AES-256-GCM
        # 4. Return plaintext string
```

### 6.4 Idempotency Adapter (`adapters/idempotency.py`)

```python
class IdempotencyAdapter:
    """Redis 3-state idempotency for Booker steps.

    Key format: idem:{user_id}:{integration_id}:{plan_id}:{step}:{call}:{input_hash}
    States: IN_FLIGHT → SUCCEEDED | FAILED
    TTL: 24 hours
    """
    def __init__(self, redis: Redis):
        self._redis = redis

    async def check_and_claim(
        self, key: str, execution_id: str, timeout_minutes: int = 5
    ) -> StepResult | None:
        """Check idempotency state. Returns cached result or None (proceed).
        Raises IdempotencyConflict if another execution is in progress."""

    async def mark_succeeded(self, key: str, result: dict) -> None:
        """Mark as SUCCEEDED with cached result."""

    async def mark_failed(self, key: str, error: str) -> None:
        """Mark as FAILED (available for retry)."""

    def build_key(
        self, user_id: str, integration_id: str, plan_id: str,
        step: int, call: str, args: dict
    ) -> str:
        """Build deterministic idempotency key with args hash."""
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()[:16]
        return f"idem:{user_id}:{integration_id}:{plan_id}:{step}:{call}:{args_hash}"
```

### 6.5 Resource Lock Adapter (`adapters/resource_lock.py`)

```python
class ResourceLockAdapter:
    """Redis-based resource locks for Booker steps.

    Key format: lock:resource:{user_id}:{integration_id}:{resource}:{entity}:{operation}
    TTL: 30 seconds (auto-release on crash)
    """
    def __init__(self, redis: Redis):
        self._redis = redis

    async def acquire(self, lock_key: str, timeout_s: int = 30) -> bool:
        """Acquire lock with polling. Returns True on success.
        Raises ResourceLockTimeout after timeout_s."""

    async def release(self, lock_key: str) -> None:
        """Release lock."""
```

### 6.6 Template Resolver (`adapters/template_resolver.py`)

```python
class TemplateResolver:
    """Resolve {{step_N.result.field}} templates from execution context."""

    def resolve(self, args: dict, step_results: dict[int, StepResult]) -> dict:
        """Recursively resolve template references in step args.

        Patterns:
        - {{step_N.result.field}} — extract field from step N's result
        - {{preview.cached_state.step_N_result.field}} — from preview state
        """
```

### 6.7 DAG Resolver (`adapters/dag_resolver.py`)

```python
class DAGResolver:
    """Topological sort of plan graph into parallel execution levels."""

    def resolve(self, graph: list[PlanStep]) -> list[list[PlanStep]]:
        """Group steps into execution levels by dependency order.

        Returns: List of levels, each containing independent steps.
        Level 0 = steps with no dependencies (parallel).
        Level 1 = steps depending only on level 0 (parallel).
        etc.

        Raises: CycleDetectedError if circular dependencies found.
        """
```

### 6.8 Retry Adapter (`adapters/retry.py`)

```python
class RetryPolicy:
    """Exponential backoff retry for transient failures."""
    max_retries: int = 3
    backoff_base_s: float = 1.0
    retry_on: set[str] = {"503", "504", "timeout", "connection_reset"}

    async def execute_with_retry(
        self, operation: Callable, step: PlanStep
    ) -> dict[str, Any]:
        """Execute operation with retry. Returns result or raises after exhaustion."""
```

---

## 7. Service Implementation

### 7.1 Core Flow

```python
class ExecuteService:
    async def execute_plan(self, request: ExecuteRequest) -> PlanOutcome:
        start = time.monotonic()
        ctx = ExecutionContext(plan=request.plan, ...)

        try:
            # Phase 1: Pre-execution verification
            await self._verify_signature(request.plan, request.signature)
            self._validate_approval_token(request.approval_token, request.plan)
            self._check_plan_ttl(request.plan)

            # Phase 2: DAG resolution
            levels = self._dag_resolver.resolve(request.plan.graph)

            # Phase 3: Execute levels sequentially, steps within level in parallel
            for level in levels:
                executable = [s for s in level if not self._should_skip(s, request)]
                if not executable:
                    continue

                results = await asyncio.gather(
                    *[self._execute_step(s, ctx, request) for s in executable],
                    return_exceptions=True,
                )

                # Process results and handle failures
                for step, result in zip(executable, results):
                    if isinstance(result, Exception):
                        await self._handle_step_failure(step, result, ctx, request)
                    else:
                        ctx.step_results[step.step] = result

            # Phase 4: Build outcome
            outcome = self._build_outcome(ctx, start)

        except (SignatureVerificationError, ApprovalTokenError, PlanExpiredError) as e:
            outcome = self._build_error_outcome(e, start)

        finally:
            # Phase 5: Persist outcome (non-fatal)
            await self._persist_outcome(request, outcome)

        return outcome
```

### 7.2 Step Dispatch

```python
async def _execute_step(
    self, step: PlanStep, ctx: ExecutionContext, request: ExecuteRequest
) -> StepResult:
    start = time.monotonic()

    match step.type:
        case "api":
            result = await self._execute_api_step(step, ctx, request)
        case "llm_reasoning":
            result = await self._execute_reasoning_step(step, ctx, request)
        case "policy_check":
            result = await self._execute_policy_check(step, ctx)

    latency_ms = int((time.monotonic() - start) * 1000)
    return StepResult(step=step.step, status="completed", result=result, latency_ms=latency_ms)
```

### 7.3 API Step Execution

```python
async def _execute_api_step(self, step, ctx, request):
    # 1. Resolve template args
    resolved_args = self._template_resolver.resolve(step.args, ctx.step_results)

    # 2. Idempotency check (Booker only)
    if step.role == "Booker":
        idem_key = self._idempotency.build_key(...)
        cached = await self._idempotency.check_and_claim(idem_key, ctx.trace_id)
        if cached:
            return cached.result

    # 3. Resource lock (Booker only)
    lock_key = None
    if step.role == "Booker":
        lock_key = f"lock:resource:{request.user_id}:..."
        await self._resource_lock.acquire(lock_key)

    try:
        # 4. Decrypt credentials
        cred_id = request.integration_credentials.get(step.uses)
        plaintext_cred = None
        if cred_id:
            plaintext_cred = await self._credential_vault.decrypt(cred_id, request.user_id)

        # 5. MCP invocation with retry
        result = await self._retry.execute_with_retry(
            lambda: self._mcp_client.invoke(
                server=tool.mcp_server,
                tool=operation.mcp_tool,
                args=resolved_args,
                credentials={"token": plaintext_cred} if plaintext_cred else None,
                timeout_s=step.timeout_s,
            ),
            step,
        )

        # 6. Zero credential
        plaintext_cred = None  # noqa: F841

        # 7. Record compensation info (Booker only)
        if step.role == "Booker" and operation.compensation:
            ctx.compensation_stack.append(CompensationRecord(
                step=step.step, tool_id=step.uses, operation=step.call,
                result=result, compensation_operation=operation.compensation,
                compensation_args=self._build_compensation_args(result, operation),
            ))

        # 8. Mark idempotency succeeded (Booker only)
        if step.role == "Booker":
            await self._idempotency.mark_succeeded(idem_key, result)

        return result

    finally:
        if lock_key:
            await self._resource_lock.release(lock_key)
```

### 7.4 LLM Reasoning Step Execution

```python
async def _execute_reasoning_step(self, step, ctx, request):
    # 1. Gather context from context_from steps
    context = [
        {"step": ref, "result": ctx.step_results[ref].result}
        for ref in step.context_from
        if ref in ctx.step_results
    ]

    # 2. Dispatch to LLM with trust tier enforcement
    response = await self._llm_client.reason(
        config=step.reasoning_config,
        context=context,
        trust_level=step.trust_level,
    )

    # 3. Tier 1: validate output against schema
    if step.trust_level == "untrusted_input":
        self._validate_output_schema(response, step.reasoning_config.output_schema_ref)

    # 4. Tier 2: process spawn requests
    if step.trust_level == "trusted" and step.can_spawn:
        spawn_requests = self._extract_spawn_requests(response)
        for spawn_req in spawn_requests:
            await self._handle_spawn(spawn_req, step, ctx, request)

    return response
```

### 7.5 Spawn Handling

```python
async def _handle_spawn(self, spawn_req, parent_step, ctx, request):
    # 1. Check per-step spawn limit
    parent_spawn_count = sum(
        1 for s in ctx.spawned_steps if s.spawned_by == parent_step.step
    )
    if parent_spawn_count >= (parent_step.max_spawned_steps or 3):
        raise SpawnDeniedError("spawn limit exceeded", [])

    # 2. Check plan-level limit (100 steps)
    total_steps = len(ctx.plan.graph) + len(ctx.spawned_steps)
    if total_steps >= 100:
        raise SpawnDeniedError("plan step limit (100) exceeded", [])

    # 3. Build spawned PlanStep
    new_step_num = max(s.step for s in ctx.plan.graph) + len(ctx.spawned_steps) + 1
    spawned = PlanStep(
        step=new_step_num,
        type=spawn_req.get("step_type", "api"),
        role=spawn_req["role"],
        uses=spawn_req["uses"],
        call=spawn_req["call"],
        args=spawn_req.get("args", {}),
        spawned_by=parent_step.step,
        can_spawn=False,  # no recursive spawning
        mode="interactive",
        after=[parent_step.step],
    )

    # 4. Inject gate_id for spawned Booker
    if spawned.role == "Booker":
        spawned.gate_id = f"gate-spawn-{new_step_num}"

    # 5. Evaluate via PolicyEngine
    decision = await self._policy_service.evaluate_spawn(
        SpawnRequest(
            plan_id=ctx.plan.plan_id,
            policy_ref=parent_step.policy_ref,
            parent_step=parent_step.step,
            proposed_steps=[spawned.model_dump()],
            plan_plugins=ctx.plan.plugins,
        )
    )

    if not decision.allowed:
        raise SpawnDeniedError(decision.reason, decision.violations)

    # 6. Create attestation
    ctx.plan_revision += 1
    attestation = PolicyAttestation(
        attestation_id=str(ulid.new()),
        plan_id=ctx.plan.plan_id,
        plan_revision=ctx.plan_revision,
        spawned_by_step=parent_step.step,
        new_steps=[spawned.model_dump()],
        policy_id=decision.reason.split("policy ")[1] if "policy" in decision.reason else "unknown",
        policy_version=ctx.plan.constraints.policy_version,
        decision=decision,
        attested_at=datetime.now(UTC).isoformat(),
    )
    ctx.attestations.append(attestation)
    ctx.spawned_steps.append(spawned)

    # 7. Execute spawned step (if not gated)
    if not decision.requires_approval and spawned.gate_id is None:
        result = await self._execute_step(spawned, ctx, request)
        ctx.step_results[spawned.step] = result
```

### 7.6 Failure Recovery

```python
async def _handle_step_failure(self, step, error, ctx, request):
    # 1. Find nearest Reasoner with can_spawn
    reasoner = self._find_recovery_reasoner(step, ctx.plan.graph)

    if reasoner is None:
        # Pure API plan or no Reasoner available → compensation → terminal
        await self._run_compensation(ctx, request)
        raise StepExecutionError(step.step, str(error))

    # 2. Check recovery budget
    if ctx.recovery_action_count >= 5:  # max_recovery_actions
        await self._run_compensation(ctx, request)
        raise RecoveryExhaustedError(step.step, ctx.recovery_action_count)

    # 3. Route failure to Reasoner
    error_context = {
        "failed_step": step.step,
        "error_type": type(error).__name__,
        "error_details": str(error),
        "step_role": step.role,
        "step_tool": step.uses,
    }
    ctx.step_results[step.step] = StepResult(
        step=step.step, status="failed",
        error=error_context, latency_ms=0,
    )

    # 4. Execute Reasoner for recovery (it may spawn a fix)
    ctx.recovery_action_count += 1
    await self._execute_step(reasoner, ctx, request)
```

### 7.7 Compensation (Saga)

```python
async def _run_compensation(self, ctx, request):
    """Execute compensation in reverse order for completed Booker steps."""
    for record in reversed(ctx.compensation_stack):
        if record.compensation_operation is None:
            continue
        try:
            await self._mcp_client.invoke(
                server=...,
                tool=record.compensation_operation,
                args=record.compensation_args or {},
                timeout_s=30,
            )
            logger.info("compensation_executed", extra={
                "step": record.step, "operation": record.compensation_operation,
                "plan_id": ctx.plan.plan_id,
            })
        except Exception as e:
            logger.error("compensation_failed", extra={
                "step": record.step, "operation": record.compensation_operation,
                "error": str(e), "plan_id": ctx.plan.plan_id,
            })
            # Continue with remaining compensations
```

---

## 8. Sequences

### 8.1 Happy Path (Pure API Plan)

```
Client          ExecuteService    Signer    DAGResolver   MCPClient     Redis
  │                   │             │           │             │           │
  │─ExecuteRequest──→│             │           │             │           │
  │                   │─verify────→│           │             │           │
  │                   │←──ok───────│           │             │           │
  │                   │─resolve────────────→│             │           │
  │                   │←──levels────────────│             │           │
  │                   │                       │             │           │
  │                   │── Level 0: gather(step1, step2) ──→│           │
  │                   │←── results ───────────────────────│           │
  │                   │                                     │           │
  │                   │── Level 1: step3 (Analyzer) ──────→│           │
  │                   │←── result ────────────────────────│           │
  │                   │                                     │           │
  │                   │── Level 2: step4 (Booker) ──────────────────→│
  │                   │                                     │   idem? │
  │                   │←── not found ──────────────────────────────│
  │                   │── invoke ────────────────────────→│           │
  │                   │←── result ───────────────────────│           │
  │                   │── mark_succeeded ────────────────────────→│
  │                   │                                     │           │
  │←─PlanOutcome────│             │           │             │           │
```

### 8.2 Spawn Path (Hybrid Plan)

```
ExecuteService    LLMClient    PolicyEngine    Redis
      │               │              │           │
      │─reason(Tier2)→│              │           │
      │←─response+spawn_req──────│           │
      │                │              │           │
      │─evaluate_spawn─────────→│           │
      │←─PolicyDecision(ok)─────│           │
      │                               │           │
      │─create attestation─────────────────→│
      │                               │           │
      │─execute spawned step──→MCPClient    │
      │←─result───────────────────────│
```

### 8.3 Failure + Recovery Path

```
ExecuteService    MCPClient    RetryPolicy    LLMClient(Reasoner)    PolicyEngine
      │               │            │                  │                    │
      │─invoke───→│            │                  │                    │
      │←─503──────│            │                  │                    │
      │─retry(1s)────────→│                  │                    │
      │←─503──────────────│                  │                    │
      │─retry(2s)────────→│                  │                    │
      │←─503──────────────│                  │                    │
      │─retry(4s)────────→│                  │                    │
      │←─503──────────────│                  │                    │
      │                    │                       │                    │
      │─route to Reasoner────────────────→│                    │
      │←─spawn recovery Fetcher──────────│                    │
      │─evaluate_spawn──────────────────────────────────→│
      │←─approved────────────────────────────────────────│
      │─execute recovery──→MCPClient                          │
      │←─result────────────│                                         │
```

### 8.4 Compensation Path

```
ExecuteService    MCPClient    Redis
      │               │          │
      │ step1(Booker) ok → push compensation_stack
      │ step2(Booker) ok → push compensation_stack
      │ step3 FAILS
      │
      │─ compensate step2 ─→│
      │←─ ok ───────────────│
      │─ compensate step1 ─→│
      │←─ ok ───────────────│
      │
      │─ return PlanOutcome(success=False)
```

### 8.5 Graceful Degradation

| Dependency Down | Behavior |
|----------------|----------|
| Redis | Booker steps refuse (fail-safe). Fetcher/Analyzer proceed without idempotency. |
| Anthropic API | LLM reasoning steps fail → plan terminal for hybrid plans. Pure API unaffected. |
| PostgreSQL | Credential decryption fails → API steps fail → plan terminal. |
| MCP server | Step-level retry → recovery (hybrid) → terminal. |
| PolicyEngine | Spawning denied (fail-closed). Non-spawn steps unaffected. |

---

## 9. Shared Infrastructure Usage

### 9.1 Dependency Injection

**In `shared/app.py` lifespan** (after PolicyEngine, after all Domain Layer services):

```python
# ExecuteOrchestrator service (Orchestration Layer)
from components.ExecuteOrchestrator.adapters.mcp_client import MCPClientAdapter
from components.ExecuteOrchestrator.adapters.llm_client import AnthropicReasoningAdapter
from components.ExecuteOrchestrator.adapters.credential_vault import CredentialVaultAdapter
from components.ExecuteOrchestrator.adapters.idempotency import IdempotencyAdapter
from components.ExecuteOrchestrator.adapters.resource_lock import ResourceLockAdapter
from components.ExecuteOrchestrator.service.execute_service import create_execute_service

app.state.execute_service = create_execute_service(
    signer_service=app.state.signer_service,
    policy_service=app.state.policy_service,
    registry_service=app.state.registry_service,
    plan_writer_service=app.state.plan_writer_service,
    mcp_client=MCPClientAdapter(registry_service=app.state.registry_service),
    llm_client=AnthropicReasoningAdapter(),
    credential_vault=CredentialVaultAdapter(db=app.state.db),
    redis_client=intake_redis,  # reuse shared Redis
)
```

**In `shared/dependencies.py`:**

```python
def get_execute_service(request: Request) -> Any:
    """Get ExecuteService singleton from app state."""
    return request.app.state.execute_service
```

### 9.2 Shared Schemas Used

| Schema | Import | Usage |
|--------|--------|-------|
| `Plan`, `PlanStep` | `shared.schemas.plan` | Input plan, spawned steps |
| `Signature` | `shared.schemas.signature` | Signature verification |
| `PlanOutcome` | `shared.schemas.outcome` | Execution result |
| `PolicyDecision`, `PolicyAttestation` | `shared.schemas.policy` | Spawn evaluation |
| `ReasoningConfig` | `shared.schemas.policy` | LLM step config |

### 9.3 Error Handling

**In `api/routes.py`:**

```python
from shared.api.error_handlers import APIErrorHandler, ErrorResponse

@router.post("/execute")
async def execute_plan(request: ExecuteRequest, service = Depends(get_execute_service)):
    try:
        return await service.execute_plan(request)
    except SignatureVerificationError as e:
        return JSONResponse(status_code=403, content=ErrorResponse(
            error_code="SIGNATURE_INVALID", message=str(e)
        ).model_dump())
    except ApprovalTokenError as e:
        return JSONResponse(status_code=401, content=ErrorResponse(
            error_code="TOKEN_INVALID", message=str(e)
        ).model_dump())
    except PlanExpiredError as e:
        return JSONResponse(status_code=410, content=ErrorResponse(
            error_code="PLAN_EXPIRED", message=str(e)
        ).model_dump())
    except Exception as e:
        return APIErrorHandler.handle_generic_error(e)
```

---

## 10. Observability & Safety

### Structured Logging

| Event | Level | Extra Fields |
|-------|-------|-------------|
| `execution_started` | INFO | `plan_id`, `user_id`, `trace_id`, `total_steps`, `step_types` |
| `step_dispatched` | INFO | `plan_id`, `step`, `role`, `type`, `trust_level`, `uses` |
| `step_completed` | INFO | `plan_id`, `step`, `role`, `latency_ms`, `status` |
| `step_failed` | WARNING | `plan_id`, `step`, `role`, `error_type`, `retries` |
| `step_retried` | INFO | `plan_id`, `step`, `attempt`, `backoff_s` |
| `spawn_requested` | INFO | `plan_id`, `parent_step`, `proposed_role`, `proposed_tool` |
| `spawn_approved` | INFO | `plan_id`, `spawned_step`, `attestation_id`, `plan_revision` |
| `spawn_denied` | WARNING | `plan_id`, `parent_step`, `reason`, `violations` |
| `compensation_executed` | INFO | `plan_id`, `step`, `operation` |
| `compensation_failed` | ERROR | `plan_id`, `step`, `operation`, `error` |
| `execution_completed` | INFO | `plan_id`, `success`, `total_steps`, `duration_ms`, `plan_revision` |
| `credential_decrypted` | DEBUG | `plan_id`, `step`, `tool_id` (NEVER the credential value) |

### Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `execute_plan_duration_seconds` | histogram | `success`, `plan_type` | End-to-end plan execution |
| `execute_step_duration_seconds` | histogram | `role`, `type`, `status` | Per-step latency |
| `execute_step_retries_total` | counter | `role`, `type` | Retry count |
| `execute_idempotency_hits_total` | counter | `state` | Idempotency cache hits (SUCCEEDED/IN_FLIGHT/FAILED) |
| `execute_spawn_total` | counter | `decision` | Spawn requests (allowed/denied) |
| `execute_compensation_total` | counter | `status` | Compensation operations (success/failed) |
| `execute_lock_wait_seconds` | histogram | - | Resource lock acquisition time |
| `execute_llm_reasoning_duration_seconds` | histogram | `trust_level`, `model` | LLM reasoning latency |

### Safety

- Credential values NEVER appear in logs, step results, error messages, or LLM context
- `user_id` present on all requests for multi-user isolation
- Idempotency keys scoped by `user_id:integration_id` (no cross-user collision)
- Resource locks scoped by `user_id:integration_id` (no cross-user blocking)
- Booker steps fail-safe when Redis unavailable (refuse, don't proceed unsafely)

---

## 11. Caching Strategy (Redis)

| Key Pattern | TTL | Purpose | Invalidation |
|-------------|-----|---------|--------------|
| `idem:{user_id}:{integration_id}:{plan_id}:{step}:{call}:{hash}` | 24h | Idempotency | Auto-expire. FAILED state allows retry. |
| `lock:resource:{user_id}:{integration_id}:{resource}:{entity}:{op}` | 30s | Resource locks | Released after step completes. Auto-expire on crash. |
| `reasoning_context:{plan_id}:{step}` | 1h | LLM context data | Auto-expire after plan completes. |

**Graceful degradation**: If Redis is unavailable, idempotency and locking are disabled. Booker steps refuse to execute (fail-safe). Non-Booker steps proceed normally.

---

## 12. Non-Functional Requirements

| Metric | Target | Notes |
|--------|--------|-------|
| Execute latency (pure API, 4 steps) | p95 < 2s | GLOBAL_SPEC §3 |
| Execute latency (hybrid, 6 steps + reasoning) | p95 < 8s | LLM adds 2-5s |
| Execute latency (pure API, 4 steps) | p99 < 5s | Network variance |
| Idempotency check | p95 < 5ms | Redis GET |
| Resource lock acquire | p95 < 10ms | Redis SET NX |
| PolicyEngine evaluation | p95 < 5ms | Redis-cached |
| Credential decryption | p95 < 2ms | AES-256-GCM in-memory |
| Availability | 99.5% | GLOBAL_SPEC §3 (Execute path) |

### Testing Strategy

| Category | Target Count | Coverage |
|----------|-------------|----------|
| Unit tests | ~30 | DAG resolver, template resolver, domain models, error classes |
| Service tests | ~20 | Execute flow, parallel dispatch, recovery, compensation |
| Adapter tests | ~15 | Idempotency states, resource locks, credential vault |
| Trust tier tests | ~10 | Tier 1 enforcement, Tier 2 spawning, isolation |
| Spawning tests | ~10 | PolicyEngine integration, attestation, limits |
| Contract tests | ~5 | PlanOutcome schema, ExecuteRequest schema |
| Observability tests | ~5 | No PII in logs, metrics names, credential isolation |
| **Total** | **~95** | |

---

## 13. Architectural Considerations

### Blast Radius Containment
- Each MCP server failure is isolated to the step using it
- LLM reasoning failures don't affect API steps in the same plan
- Compensation runs in a separate try/except per step (one failure doesn't block others)
- Redis failure triggers fail-safe (refuse Booker execution) rather than fail-open

### Fault Isolation
- **Circuit breaker**: On LLM client (Anthropic API) — after 5 consecutive failures, open for 60s
- **Retry policy**: Per-step with exponential backoff, configurable per role
- **Timeout enforcement**: Per-step via `timeout_s` field, per-plan via `constraints.ttl_s`

### State Management
- **Stateless service**: All mutable state is in `ExecutionContext` (in-memory for duration of request)
- **Persistent state**: Redis (idempotency, locks) + PostgreSQL (credential vault)
- **No background task durability needed**: Execution is synchronous within the HTTP request. Long-running durable execution deferred to Phase 4.

### Cross-Component Interactions
- ExecuteOrchestrator does NOT own any PostgreSQL tables (reads from credential_vault via adapter)
- Redis keys are owned by ExecuteOrchestrator per MODULAR_ARCHITECTURE §3
- PlanWriter is called post-execution to persist outcomes (fire-and-forget with logging)

---

## 14. Architecture Decision Records

### ADR: MCP Client Implementation (NEW)
**Decision**: Use httpx-based custom client for MVP, not the `mcp` Python package.
**Rationale**: The `mcp` package is early-stage. httpx provides reliable HTTP/SSE transport. Protocol-based adapter allows future swap.
**Status**: Proposed (requires ADR file creation)

### ADR: Synchronous Execution Model (NEW)
**Decision**: Execute plans synchronously within the HTTP request (no background task).
**Rationale**: Simplifies state management. Plans are time-bounded (ttl_s). Long-running durable mode deferred to Phase 4 with APScheduler.
**Status**: Proposed

### ADR: PolicyAttestation over Re-Signing (EXISTING — HLD v6.1)
**Decision**: Runtime modifications receive PolicyAttestations, not plan re-signing.
**Rationale**: Signer's private key should not be in execution context. `original_signature + attestations[] = full provenance`.

---

## 15. Dependencies & External Integrations

### Python Packages

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | >=0.109.0 | HTTP framework |
| `pydantic` | >=2.5.0 | Data validation |
| `redis[hiredis]` | >=5.0.0 | Idempotency, locks, context cache |
| `anthropic` | >=0.18.0 | LLM reasoning steps (Tier 1/Tier 2) |
| `httpx` | >=0.27.0 | MCP tool invocation (SSE transport) |
| `cryptography` | >=42.0.0 | AES-256-GCM credential decryption |
| `ulid-py` | >=1.1.0 | ULID generation for attestations |
| `PyJWT` | >=2.8.0 | Approval token validation |

### Internal Dependencies

| Component | Interface Used |
|-----------|---------------|
| Signer | `verify_signature()` |
| PolicyEngine | `evaluate_spawn()` |
| PluginRegistry | `get_tool()`, `get_operation()` |
| PlanWriter | `write_outcome()` |

### External Services

| Service | Usage | SLA Target |
|---------|-------|-----------|
| Anthropic API | LLM reasoning steps | 99.9% (provider SLA) |
| MCP servers | Tool invocations | Varies by provider |
| Redis | Idempotency, locks | 99.9% (self-hosted) |
| PostgreSQL | Credential vault | 99.9% (self-hosted) |

---

## 16. Risks & Open Questions

### Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| MCP server error diversity | Hard to normalize errors across providers | Standardized error wrapper in MCPClientAdapter |
| Redis SPOF for idempotency | Booker steps cannot execute safely | Fail-safe: refuse Booker execution when Redis down |
| LLM reasoning latency (2-5s) | Extends plan execution beyond p95 targets | Separate NFR for hybrid plans (p95 < 8s) |
| Compensation operation failure | Partial undo leaves inconsistent state | Best-effort with logging; manual reconciliation |
| Credential master key missing | All API steps fail at startup | Startup validation check with clear error message |

### Open Questions

1. **MCP client library**: httpx for MVP; evaluate `mcp` package when it stabilizes
2. **Preview state source**: Recommendation: pass inline in ExecuteRequest (simpler than Redis lookup)
3. **Durable mode**: Defer to Phase 4 (APScheduler + ExecutionMonitor)
4. **Execution tracking**: Defer to ExecutionMonitor component (not ExecuteOrchestrator's concern)
5. **Gate pause mechanism**: MVP returns `PlanOutcome` with `error_type: "gate_approval_required"` for spawned Booker gates. Full async pause/resume in ApprovalGate integration.

---

## 17. Post-Generation Validation Checklist

- [x] Data model fields match GLOBAL_SPEC §2 contracts (Plan, PlanStep, Signature, PlanOutcome, PolicyAttestation)
- [x] `user_id` present on ExecuteRequest input
- [x] Conformance header references current versions (GLOBAL_SPEC v3.0, MODULAR_ARCHITECTURE v2.1, HLD v6.1)
- [x] No owned PostgreSQL tables (Redis keys only, per MODULAR_ARCHITECTURE §3)
- [x] Component dependencies match MODULAR_ARCHITECTURE §4 (Signer, ApprovalGate, PluginRegistry, PolicyEngine, PlanWriter)
- [x] Upstream consumer contract documented (Frontend → ExecuteRequest → PlanOutcome)
- [x] Idempotency implemented for Booker steps (3-state Redis)
- [x] N/A: No owned PostgreSQL tables (DDL not required)
- [x] Prometheus metrics defined with names and types (8 metrics)
- [x] No deprecated library versions (all current as of 2026-04)
- [x] N/A: No Evidence Item generation (PlanWriter handles)
- [x] Error handling uses ErrorResponse from shared/api/error_handlers.py
- [x] Database access via SharedDatabaseAdapter (credential vault reads)
