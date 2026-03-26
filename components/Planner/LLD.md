# Planner — Low-Level Design (LLD)

**Component**: `components/Planner/`
**Layer**: Domain / Service Layer
**Type**: Library component (no HTTP routes)
**Created**: 2026-03-26
**SPEC**: `specs/015-planner/spec.md`

---

## 1. Purpose & Scope

Planner is a **deterministic, one-shot plan generator** that transforms an Intent + Evidence + tool catalog into a signed, executable Plan graph. It is a **stateless library component** consumed via dependency injection — no database tables, no HTTP routes.

**Responsibilities**:
- Accept an `Intent` (GLOBAL_SPEC §2.1) and produce a `PlannerResult` containing a `Plan` (§2.3) + `Signature` (§2.4)
- Call ContextRAG to assemble evidence, PluginRegistry for tool catalog
- Invoke Anthropic Claude API (temperature=0) via LLMAdapter protocol with structured prompt
- Validate LLM output through 3-layer pipeline (JSON → Pydantic → business rules)
- Sign validated plan via Signer (Ed25519)
- Implement 4-level fallback hierarchy and per-model circuit breakers

**Out of scope**:
- Plan execution (WorkflowBuilder / Orchestrators)
- Plan persistence (PlanWriter)
- Credential resolution (n8n at execution time)
- HTTP endpoints (library component)
- Multi-turn planning (one-shot only)

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v2.2 | Canonical contracts §2.1 (Intent), §2.3 (Plan), §2.4 (Signature) |
| MODULAR_ARCHITECTURE.md | v1.3 | Planner dependency graph (§4), stateless service (§10) |
| Project_HLD.md | v4.0 | §14 LLM Guardrails, §5 Deterministic Planning |
| SHARED_INFRASTRUCTURE.md | v1.0.0 | Shared schemas (§4.1), DI wiring (implicit) |
| ADR-0001 | Accepted | Component-first folder layout |

---

## 3. Architecture Overview

### 3.1 Layer Placement

Planner sits in the **Domain/Service Layer** alongside ContextRAG and Signer. It has no database dependencies (stateless). It is consumed by the Orchestration Layer (PreviewOrchestrator, ExecuteOrchestrator) via DI.

```
Orchestration Layer
  ├── PreviewOrchestrator ──┐
  └── ExecuteOrchestrator ──┤
                            ▼
                    ┌──────────────┐
                    │   Planner    │  Domain/Service Layer
                    └──────┬───────┘
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
      ContextRAG    PluginRegistry    Signer
            │              │
            ▼              ▼
      Memory Layer    PostgreSQL
```

### 3.2 Blast Radius Analysis

- **Failure mode**: Planner failure → no plan generated → Orchestration Layer cannot proceed
- **Containment**: 4-level fallback hierarchy ensures a plan is always returned (even if minimal)
- **LLM isolation**: Circuit breakers prevent cascading failures; each model has an independent breaker
- **No persistent state**: Planner crash loses nothing — retry is safe and side-effect-free
- **Dependency failures**: ContextRAG never raises (returns empty ContextResult); PluginRegistry failure → empty catalog → minimal plan; Signer failure → fatal (cannot sign)

### 3.3 Component Boundaries

| Boundary | Direction | Contract |
|----------|-----------|----------|
| ContextRAG | Planner → | `gather_evidence(intent) → ContextResult` |
| PluginRegistry | Planner → | `list_catalog() → CatalogResponse`, `validate_plan_tools(version, ids) → ValidationResult`, `get_version() → int` |
| Signer | Planner → | `sign_plan(plan_data, signer_identity) → PlanSignature` |
| PlanLibrary | Planner → | `get_plans_by_intent(intent_type) → list[EvidenceItem]` (Level 3 fallback) |
| Orchestration | → Planner | `generate_plan(intent) → PlannerResult` |

---

## 4. Interfaces

### 4.1 Service Interface (library — no HTTP routes)

```python
class PlannerService:
    """Deterministic plan generator with fallback hierarchy."""

    async def generate_plan(self, intent: Intent) -> PlannerResult:
        """Generate a validated, signed execution plan.

        Args:
            intent: Validated Intent model (GLOBAL_SPEC §2.1).

        Returns:
            PlannerResult containing Plan, Signature, and metadata.

        Raises:
            PlanGenerationError: If all fallback levels fail
                (should never happen — Level 4 is deterministic).
        """
```

### 4.2 Factory Function

```python
def create_planner_service(
    context_rag_service: ContextRAGService,
    registry_service: RegistryService,
    signer_service: SignerService,
    plan_service: PlanService,
    llm_adapter: LLMAdapter | None = None,
) -> PlannerService:
    """Create PlannerService with DI-injected dependencies.

    Called once during application lifespan startup in shared/app.py.

    Args:
        context_rag_service: ContextRAG for evidence assembly.
        registry_service: PluginRegistry for tool catalog.
        signer_service: Signer for Ed25519 plan signing.
        plan_service: PlanLibrary for Level 3 template fallback.
        llm_adapter: LLM adapter (default: AnthropicAdapter from env).

    Returns:
        Configured PlannerService.
    """
```

### 4.3 Consumer Contracts

#### PreviewOrchestrator / ExecuteOrchestrator (Orchestration Layer)

```python
# Caller code (future Orchestration Layer):
planner = request.app.state.planner_service

result: PlannerResult = await planner.generate_plan(intent)

# Access:
plan: Plan = result.plan              # GLOBAL_SPEC §2.3
signature: Signature = result.signature  # GLOBAL_SPEC §2.4
fallback_level: int = result.fallback_level  # 1-4
context_degraded: bool = result.context_degraded
generation_duration_ms: int = result.generation_duration_ms
```

**Error handling by consumer**:
- `PlanGenerationError` → show user a generic error (extremely unlikely, Level 4 is deterministic)
- All other errors are internal and should not propagate

---

## 5. Data Model

All field names match GLOBAL_SPEC §2 contracts exactly. Planner **does not own any database tables** — it is stateless.

### 5.1 Domain Models (`domain/models.py`)

```python
from pydantic import BaseModel, Field
from shared.schemas.plan import Plan
from shared.schemas.signature import Signature


class PlannerResult(BaseModel):
    """Result of plan generation."""
    plan: Plan
    signature: Signature
    fallback_level: int = Field(
        ..., ge=1, le=4,
        description="Which fallback level produced this plan "
                    "(1=primary, 2=secondary, 3=template, 4=minimal)"
    )
    context_degraded: bool = Field(
        default=False,
        description="True if ContextRAG returned with degraded sources"
    )
    generation_duration_ms: int = Field(
        ..., ge=0,
        description="Total wall-clock time for generate_plan() in ms"
    )
    registry_version: int = Field(
        ..., description="PluginRegistry version used for this plan"
    )
```

### 5.2 Domain Errors (`domain/models.py`)

```python
class PlannerError(Exception):
    """Base error for Planner component."""

class PlanValidationError(PlannerError):
    """LLM output failed validation."""
    def __init__(self, layer: str, message: str, details: dict | None = None):
        self.layer = layer       # "json_parse", "schema", "business_rules"
        self.message = message
        self.details = details or {}
        super().__init__(f"Validation failed at {layer}: {message}")

class CircuitOpenError(PlannerError):
    """Circuit breaker is open for the requested model."""
    def __init__(self, model: str):
        self.model = model
        super().__init__(f"Circuit breaker open for model: {model}")

class PlanGenerationError(PlannerError):
    """All fallback levels exhausted (should never happen)."""

class LLMCallError(PlannerError):
    """LLM API call failed."""
    def __init__(self, model: str, reason: str):
        self.model = model
        self.reason = reason
        super().__init__(f"LLM call failed ({model}): {reason}")
```

### 5.3 LLM Adapter Protocol (`adapters/llm_adapter.py`)

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class LLMAdapter(Protocol):
    """Protocol for LLM adapters (enables swapping providers)."""

    async def generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Call LLM and return raw text response.

        Args:
            model: Model identifier (e.g., "claude-opus-4-6").
            system_prompt: System instructions.
            user_prompt: User message with intent + evidence + tools.
            max_tokens: Max output tokens.
            temperature: Sampling temperature (0.0 for determinism).

        Returns:
            Raw text response from LLM (expected to be JSON).

        Raises:
            LLMCallError: On API failure, timeout, or rate limit.
        """
        ...
```

---

## 6. Adapters

### 6.1 LLM Adapter — `adapters/llm_adapter.py`

**`AnthropicAdapter`** — implements `LLMAdapter` protocol using the `anthropic` SDK.

```python
class AnthropicAdapter:
    """Anthropic Claude API adapter."""

    def __init__(self, api_key: str | None = None):
        """Initialize with API key from env ANTHROPIC_API_KEY."""

    async def generate(
        self, model, system_prompt, user_prompt, max_tokens, temperature
    ) -> str:
        """Call Claude Messages API. Raises LLMCallError on failure."""
```

**Configuration** (environment variables):
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `PLANNER_PRIMARY_MODEL` | No | `claude-sonnet-4-5-20250929` | Primary LLM model |
| `PLANNER_FALLBACK_MODEL` | No | `claude-haiku-4-5-20251001` | Fallback LLM model |
| `PLANNER_MAX_INPUT_TOKENS` | No | `8192` | Max input token budget |
| `PLANNER_MAX_OUTPUT_TOKENS` | No | `4096` | Max output token budget |
| `PLANNER_LLM_TIMEOUT_S` | No | `30` | Per-call LLM timeout in seconds |

### 6.2 Plan Validator — `adapters/plan_validator.py`

3-layer validation pipeline:

```python
class PlanValidator:
    """Multi-layer validation for LLM plan output."""

    def __init__(self, registry_service: RegistryService):
        self._registry = registry_service

    async def validate(
        self,
        raw_output: str,
        intent: Intent,
        registry_version: int,
        tool_ids: set[str],
    ) -> Plan:
        """Validate LLM output through 3 layers.

        Layer 1 (JSON parsing): json.loads() — catches malformed JSON
        Layer 2 (Schema validation): Plan.model_validate() — catches
            missing fields, wrong types, invalid deps, duplicate steps
        Layer 3 (Business rules): tool existence, scope aggregation,
            complexity limits, constraint enforcement

        Returns:
            Validated Plan model.

        Raises:
            PlanValidationError with layer="json_parse"|"schema"|"business_rules"
        """
```

**Layer 2 detail — dependency validation**:
- No self-dependencies (`step.after` must not contain `step.step`)
- No forward dependencies (`step.after` values must all be < `step.step`)
- No duplicate step numbers
- All `after` references point to existing steps
- `graph` has 1–100 steps (from `shared/schemas/plan.py` max_length=100)

**Layer 3 detail — business rules**:
- All `step.uses` tool_ids are active in PluginRegistry (`validate_plan_tools()`)
- Plan has ≤ 50 steps (hard constraint from HLD §14)
- `plan.constraints.scopes` contains union of all required scopes from tool operations
- `dry_run=true` on all steps (preview-first safety)
- `gate_id` present on Booker role steps (HITL enforcement)
- No step `args` exceed 10KB serialized
- Total plan size ≤ 100KB

### 6.3 Circuit Breaker — `adapters/circuit_breaker.py`

Per-model circuit breaker (each LLM model gets its own breaker instance):

```python
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """In-memory circuit breaker for LLM calls."""

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_s: int = 60,
        success_threshold: int = 2,
    ):
        self.state: CircuitState = CircuitState.CLOSED
        self.failure_count: int = 0
        self.success_count: int = 0
        self.last_failure_time: float | None = None
        self._failure_threshold = failure_threshold
        self._timeout_s = timeout_s
        self._success_threshold = success_threshold

    async def call(self, func, *args, **kwargs):
        """Execute func with circuit breaker protection.

        Raises CircuitOpenError if circuit is OPEN and timeout
        has not elapsed.
        """

    def get_state(self) -> CircuitState:
        """Return current circuit state (for metrics)."""
```

**State machine**:
```
CLOSED ──[failure_count >= threshold]──→ OPEN
OPEN ──[timeout elapsed]──→ HALF_OPEN
HALF_OPEN ──[success_count >= threshold]──→ CLOSED
HALF_OPEN ──[any failure]──→ OPEN
```

### 6.4 Prompt Builder — `adapters/prompt_builder.py`

Builds structured system + user prompts for the LLM.

```python
class PromptBuilder:
    """Build LLM prompts from Intent + Evidence + tool catalog."""

    def build_system_prompt(self) -> str:
        """Return the system prompt with plan schema instructions.

        Includes: Plan JSON schema, role definitions, constraint rules,
        HITL gate instructions, output format requirements.
        """

    def build_user_prompt(
        self,
        intent: Intent,
        evidence: list[EvidenceItem],
        catalog: CatalogResponse,
    ) -> str:
        """Build user prompt with intent, evidence, and tools.

        Truncates intent text to 10KB max.
        Serializes evidence items (type, key, value, confidence).
        Lists available tools with operations and scopes.
        """
```

**Prompt structure**:
1. **System prompt** (static, cacheable):
   - You are a plan generator. Output valid JSON matching the Plan schema.
   - Plan step roles: Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier
   - Rules: `dry_run=true` always, `gate_id` on Booker steps, no credential values
   - Output format: raw JSON object, no markdown fences, no explanation
2. **User prompt** (per-request):
   - Intent: `{intent_type}` with entities and constraints
   - Evidence: list of typed evidence items
   - Available tools: tool catalog with operations, scopes, and descriptions
   - Request: Generate an execution plan

### 6.5 Plan Hasher — `adapters/plan_hasher.py`

Reuses the existing canonical JSON + SHA-256 pattern from Signer.

```python
import hashlib
import json


def canonicalize_plan(plan_data: dict) -> str:
    """Canonical JSON (sorted keys, no whitespace)."""
    return json.dumps(plan_data, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(plan_data: dict) -> str:
    """SHA-256 hex digest of canonical plan bytes."""
    canonical = canonicalize_plan(plan_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

**Note**: This duplicates `components/Signer/adapters/canonicalizer.py` intentionally — Planner computes the hash to populate `meta.canonical_hash` before passing to Signer. Signer independently recomputes to verify. Keeping both avoids a circular dependency.

---

## 7. Service Implementation — `service/planner_service.py`

### 7.1 `generate_plan()` Flow

```python
async def generate_plan(self, intent: Intent) -> PlannerResult:
    start = time.monotonic()

    # 1. Gather evidence from ContextRAG
    context_result = await self._context_rag.gather_evidence(intent)
    context_degraded = len(context_result.degraded_sources) > 0

    # 2. Get tool catalog from PluginRegistry
    catalog = await self._registry.list_catalog()
    registry_version = catalog.registry_version
    tool_ids = {t.tool_id for t in catalog.tools}

    # 3. Build prompts
    system_prompt = self._prompt_builder.build_system_prompt()
    user_prompt = self._prompt_builder.build_user_prompt(
        intent, context_result.evidence, catalog
    )

    # 4. Try fallback hierarchy
    plan, fallback_level = await self._generate_with_fallback(
        intent, system_prompt, user_prompt,
        registry_version, tool_ids, context_result.evidence
    )

    # 5. Compute canonical hash and populate meta
    plan_dict = plan.model_dump()
    canonical_hash = compute_plan_hash(plan_dict)
    plan_dict["meta"]["canonical_hash"] = canonical_hash
    plan = Plan.model_validate(plan_dict)

    # 6. Sign plan via Signer
    signature_obj = await self._signer.sign_plan(
        plan.model_dump(), signer_identity="planner@system"
    )
    signature = Signature(
        algo=signature_obj.algo,
        signer=signature_obj.signer,
        signature=signature_obj.signature,
        pubkey_id=signature_obj.pubkey_id,
        plan_hash=canonical_hash,
    )

    # 7. Return result
    duration_ms = int((time.monotonic() - start) * 1000)
    return PlannerResult(
        plan=plan,
        signature=signature,
        fallback_level=fallback_level,
        context_degraded=context_degraded,
        generation_duration_ms=duration_ms,
        registry_version=registry_version,
    )
```

### 7.2 Fallback Hierarchy

```python
async def _generate_with_fallback(
    self, intent, system_prompt, user_prompt,
    registry_version, tool_ids, evidence
) -> tuple[Plan, int]:
    """Try 4 fallback levels. Returns (Plan, level)."""

    # Level 1: Primary model (e.g., claude-sonnet-4-5)
    try:
        raw = await self._primary_breaker.call(
            self._llm.generate,
            model=self._primary_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        plan = await self._validator.validate(
            raw, intent, registry_version, tool_ids
        )
        return self._finalize_plan(plan, intent), 1
    except CircuitOpenError:
        logger.warning("primary_circuit_open", extra={...})
    except (PlanValidationError, LLMCallError) as exc:
        logger.warning("primary_failed", extra={...})

    # Level 2: Fallback model (e.g., claude-haiku-4-5)
    try:
        raw = await self._fallback_breaker.call(
            self._llm.generate,
            model=self._fallback_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        plan = await self._validator.validate(
            raw, intent, registry_version, tool_ids
        )
        return self._finalize_plan(plan, intent), 2
    except (CircuitOpenError, PlanValidationError, LLMCallError) as exc:
        logger.warning("fallback_failed", extra={...})

    # Level 3: Template from PlanLibrary
    try:
        templates = await self._plan_service.get_plans_by_intent(
            intent.intent, limit=1
        )
        if templates:
            plan = self._instantiate_template(
                templates[0], intent, evidence, tool_ids
            )
            return plan, 3
    except Exception as exc:
        logger.warning("template_failed", extra={...})

    # Level 4: Minimal safe plan (deterministic, never fails)
    return self._create_minimal_plan(intent), 4
```

### 7.3 Minimal Safe Plan (Level 4)

```python
def _create_minimal_plan(self, intent: Intent) -> Plan:
    """Deterministic minimal plan — single Fetcher echo step."""
    plan_id = ulid.new().str
    now = datetime.now(timezone.utc).isoformat()
    plan_dict = {
        "plan_id": plan_id,
        "intent": intent.model_dump(),
        "trace_id": intent.trace_id,
        "graph": [{
            "step": 1,
            "mode": "interactive",
            "role": "Fetcher",
            "uses": "system.echo",
            "call": "echo",
            "args": {"message": "Planner unavailable, manual action required"},
            "after": [],
            "timeout_s": 30,
            "gate_id": None,
            "dry_run": True,
        }],
        "constraints": {"scopes": [], "ttl_s": 300, "max_retries": 0},
        "plugins": ["system.echo"],
        "meta": {
            "created_at": now,
            "author": "planner@system",
            "version": "v2.0.0",
            "canonical_hash": "",  # filled in step 5
            "hash_algo": "sha256",
        },
    }
    return Plan.model_validate(plan_dict)
```

### 7.4 Plan Finalization

```python
def _finalize_plan(self, plan: Plan, intent: Intent) -> Plan:
    """Populate plan_id, intent, plugins, enforce dry_run/gate_id."""
    plan_dict = plan.model_dump()

    # Generate ULID plan_id
    plan_dict["plan_id"] = ulid.new().str

    # Embed original intent
    plan_dict["intent"] = intent.model_dump()
    plan_dict["trace_id"] = intent.trace_id

    # Enforce dry_run=true on all steps
    for step in plan_dict["graph"]:
        step["dry_run"] = True

    # Collect unique tool IDs into plugins[]
    plan_dict["plugins"] = sorted({
        step["uses"] for step in plan_dict["graph"]
    })

    # Populate meta
    plan_dict["meta"]["created_at"] = datetime.now(timezone.utc).isoformat()
    plan_dict["meta"]["author"] = "planner@system"
    plan_dict["meta"]["version"] = "v2.0.0"
    plan_dict["meta"]["hash_algo"] = "sha256"
    # canonical_hash is computed after finalization

    return Plan.model_validate(plan_dict)
```

---

## 8. Sequences

### 8.1 Happy Path

```
Caller                PlannerService      ContextRAG     PluginRegistry     LLM          Validator     Signer
  │                        │                  │               │              │               │            │
  │──generate_plan(intent)─▶                  │               │              │               │            │
  │                        │──gather_evidence──▶              │              │               │            │
  │                        │◀──ContextResult───│              │              │               │            │
  │                        │──list_catalog()───────────────────▶             │               │            │
  │                        │◀──CatalogResponse─────────────────│             │               │            │
  │                        │──build_prompts()──│               │              │               │            │
  │                        │──circuit.call(llm.generate)───────────────────▶│               │            │
  │                        │◀──raw JSON────────────────────────────────────│               │            │
  │                        │──validate(raw, intent, version, tools)─────────────────────▶│            │
  │                        │◀──Plan────────────────────────────────────────────────────│            │
  │                        │──finalize + hash──│               │              │               │            │
  │                        │──sign_plan(plan_dict)───────────────────────────────────────────────────▶│
  │                        │◀──PlanSignature────────────────────────────────────────────────────────│
  │◀──PlannerResult────────│                  │               │              │               │            │
```

### 8.2 Fallback Path (Primary + Fallback LLM Fail)

```
Caller      PlannerService     ContextRAG    PluginRegistry    Primary LLM    Fallback LLM    PlanLibrary
  │              │                 │               │               │               │               │
  │──generate──▶│                 │               │               │               │               │
  │              │──gather_evidence──▶            │               │               │               │
  │              │◀──ContextResult──│             │               │               │               │
  │              │──list_catalog()─────────────────▶              │               │               │
  │              │◀──CatalogResponse───────────────│              │               │               │
  │              │──primary_breaker.call()──────────────────────▶│               │               │
  │              │◀──CircuitOpenError───────────────────────────│               │               │
  │              │──fallback_breaker.call()─────────────────────────────────────▶│               │
  │              │◀──LLMCallError──────────────────────────────────────────────│               │
  │              │──get_plans_by_intent(intent)──────────────────────────────────────────────────▶│
  │              │◀──[EvidenceItem(type="plan")]───────────────────────────────────────────────│
  │              │──instantiate_template()─│               │               │               │
  │              │──sign + return──▶       │               │               │               │
  │◀──PlannerResult(level=3)──│            │               │               │               │
```

### 8.3 Circuit Breaker Recovery

```
Time    Event                               Primary CB State    Fallback CB State
─────   ─────                               ────────────────    ─────────────────
t=0     5 consecutive primary failures       OPEN                CLOSED
t=1s    Request → skip primary → fallback    OPEN                CLOSED
t=60s   Timeout elapsed                      → HALF_OPEN         CLOSED
t=61s   Request → try primary (1 call)       HALF_OPEN           CLOSED
t=62s   Primary succeeds                     HALF_OPEN (1/2)     CLOSED
t=63s   Primary succeeds again               → CLOSED            CLOSED
```

### 8.4 Graceful Degradation

| Dependency | Failure Mode | Planner Behavior |
|------------|--------------|------------------|
| ContextRAG | Returns empty ContextResult | Plan generated with intent + tools only; `context_degraded=true` |
| PluginRegistry | API error | `list_catalog()` raises → empty catalog → Level 4 minimal plan |
| Primary LLM | Timeout / 500 / rate limit | Circuit breaker trips → Level 2 fallback model |
| Fallback LLM | Also fails | Level 3 template from PlanLibrary |
| PlanLibrary | No templates match | Level 4 minimal safe plan |
| Signer | Key not configured | Fatal — `SigningKeyNotConfiguredError` propagates (startup failure) |

---

## 9. Shared Infrastructure Usage

### 9.1 Dependency Injection

**`shared/app.py`** lifespan addition:
```python
# Planner service (library -- no routes)
from components.Planner.service.planner_service import create_planner_service

app.state.planner_service = create_planner_service(
    context_rag_service=app.state.context_rag_service,
    registry_service=app.state.registry_service,
    signer_service=app.state.signer_service,
    plan_service=app.state.plan_service,
)
```

**`shared/dependencies.py`** addition:
```python
def get_planner_service(request: Request) -> Any:
    """Get PlannerService singleton from app state."""
    return request.app.state.planner_service
```

### 9.2 Shared Schemas

| Schema | Import | Usage |
|--------|--------|-------|
| `Intent` | `shared.schemas.intent` | Input contract |
| `Plan`, `PlanStep`, `PlanConstraints`, `PlanMeta` | `shared.schemas.plan` | Output contract (LLM target) |
| `Signature` | `shared.schemas.signature` | Signature contract |
| `EvidenceItem` | `shared.schemas.evidence` | ContextRAG output, prompt input |

### 9.3 Database & Transactions

**Not applicable** — Planner is stateless, owns no tables, and makes no direct database calls. All persistence happens through downstream components (ContextRAG queries Memory Layer; Signer is in-memory).

### 9.4 API Error Handling

**Not applicable** — Planner has no HTTP routes. Domain errors (`PlanValidationError`, `CircuitOpenError`, `PlanGenerationError`) are handled internally by the fallback hierarchy. Only `PlanGenerationError` could propagate to the caller, and the Orchestration Layer will handle it.

---

## 10. Observability & Safety

### 10.1 Structured Logging

All log entries include:
- `component: "planner"`, `op: "<operation>"`
- `intent_type: intent.intent`, `trace_id: intent.trace_id`, `plan_id: plan.plan_id`
- `user_id: intent.user_id` (for correlation, never logged with entity values)

**Log events**:
| Event | Level | Extra Fields |
|-------|-------|-------------|
| `generate_plan_start` | INFO | intent_type, user_id, trace_id |
| `context_gathered` | INFO | evidence_count, degraded_sources, duration_ms |
| `catalog_fetched` | INFO | tool_count, registry_version |
| `llm_call_start` | INFO | model, token_budget |
| `llm_call_complete` | INFO | model, duration_ms, input_tokens, output_tokens |
| `llm_call_failed` | WARNING | model, error_type, error_reason |
| `validation_passed` | INFO | step_count, plan_size_bytes |
| `validation_failed` | WARNING | layer, error_message (no plan content) |
| `circuit_state_change` | WARNING | model, from_state, to_state |
| `fallback_triggered` | WARNING | from_level, to_level, reason |
| `plan_signed` | INFO | plan_hash, plan_id |
| `generate_plan_complete` | INFO | plan_id, fallback_level, duration_ms, context_degraded |

### 10.2 No PII in Logs

- **Never log**: entity values, constraint values, LLM prompt content, plan step args, credential references
- **Safe to log**: intent_type (action name), entity keys (not values), plan_id, plan_hash, step count, tool_ids, model name

### 10.3 Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `planner_generate_duration_seconds` | histogram | `intent_type`, `fallback_level` | End-to-end `generate_plan()` duration |
| `planner_llm_call_duration_seconds` | histogram | `model` | Per-model LLM call duration |
| `planner_llm_token_usage_total` | counter | `model`, `type` (input/output) | Token usage per model |
| `planner_validation_error_total` | counter | `layer` | Validation failures by layer |
| `planner_circuit_state` | gauge | `model` | 0=closed, 1=half_open, 2=open |
| `planner_fallback_total` | counter | `level` | Plans generated at each fallback level |
| `planner_generation_error_total` | counter | `error_type` | Fatal generation errors |

### 10.4 Safety Guarantees

- Plans **never** contain credential values — only credential ID templates
- `dry_run=true` on **all** steps (preview-first safety)
- No secrets in logs
- Circuit breaker prevents cost explosion during LLM outages
- Minimal safe plan ensures system always returns something

---

## 11. Dependencies & External Integrations

### 11.1 Python Packages

| Package | Version | Justification |
|---------|---------|---------------|
| `anthropic` | `>=0.49.0` | Claude API client for plan generation |
| `python-ulid` | `>=3.0.0` | ULID generation for plan_id (GLOBAL_SPEC) |
| `pydantic` | `>=2.7` | Domain models, Plan schema validation (already in project) |

### 11.2 Internal Component Dependencies

| Component | Via | Methods Used |
|-----------|-----|-------------|
| ContextRAG | `ContextRAGService` | `gather_evidence(intent) → ContextResult` |
| PluginRegistry | `RegistryService` | `list_catalog() → CatalogResponse`, `validate_plan_tools(version, ids) → ValidationResult`, `get_version() → int` |
| Signer | `SignerService` | `sign_plan(plan_data, signer_identity) → PlanSignature` |
| PlanLibrary | `PlanService` | `get_plans_by_intent(intent_type) → list[EvidenceItem]` |

**Matches MODULAR_ARCHITECTURE v1.3** §4 Planner dependency graph:
- ContextRAG (evidence input)
- PluginRegistry (tool catalog)
- External: Anthropic Claude API

**Additional dependency** not in MODULAR_ARCHITECTURE:
- **Signer** — required for plan signing (FR-007). Planner calls `sign_plan()` directly.
- **PlanLibrary** — Level 3 fallback template lookup. Queries `get_plans_by_intent()` for successful past plans.

> **Note**: MODULAR_ARCHITECTURE v1.3 lists only ContextRAG and PluginRegistry as Planner dependencies. Signer and PlanLibrary should be added in the next MODULAR_ARCHITECTURE update.

### 11.3 Development/Testing Dependencies

| Package | Usage |
|---------|-------|
| `pytest` | Test framework |
| `pytest-asyncio` | Async test support |
| `ruff` | Linting and formatting |

---

## 12. Non-Functional Requirements

### 12.1 Performance

| Operation | p95 (local) | p95 (cloud) | p99 (local) | Notes |
|-----------|-------------|-------------|-------------|-------|
| `generate_plan()` | < 8 s | < 5 s | < 12 s | LLM-bound; includes context + LLM + validation + signing |
| Context assembly (ContextRAG) | < 200 ms | < 150 ms | < 300 ms | Already verified |
| Catalog fetch (PluginRegistry) | < 50 ms | < 30 ms | < 100 ms | DB query |
| Validation pipeline | < 50 ms | < 50 ms | < 100 ms | Pure computation |
| Plan signing (Signer) | < 10 ms | < 10 ms | < 20 ms | Ed25519 |
| LLM call (primary) | < 6 s | < 4 s | < 10 s | Model-dependent |

### 12.2 Availability

| Target | Local | Cloud |
|--------|-------|-------|
| generate_plan() | Best-effort | 99.9% (via fallback hierarchy) |
| Level 4 minimal plan | 100% (deterministic, no external deps) | 100% |

### 12.3 Testing Strategy

| Category | Count (target) | Coverage |
|----------|----------------|----------|
| Unit tests | ~30 | Validator (all 3 layers), circuit breaker (state machine), prompt builder, hasher |
| Service tests | ~15 | Fallback hierarchy, context degradation, concurrent calls |
| Contract tests | ~10 | Plan schema compliance, signature verification, determinism |
| Observability tests | ~5 | No PII in logs, no credentials in logs, metric names |

**Total target: ~60 tests**

---

## 13. Architectural Considerations

### 13.1 Blast Radius Containment

- Planner failure does not affect Memory Layer or other Domain components
- Circuit breaker prevents LLM cost explosion and latency cascading
- Fallback hierarchy ensures degraded-but-functional service
- No shared mutable state — safe for concurrent calls

### 13.2 Determinism Guarantees

**Same inputs → same plan hash → same signature** when:
1. LLM model version is pinned (same model = same output at temperature=0)
2. Evidence is sorted deterministically (ContextRAG enforces tier+confidence ordering)
3. Tool catalog is captured at a specific `registry_version`
4. Plan JSON is canonicalized (sorted keys, no whitespace)

**Caveats**: LLM determinism is approximate — model updates may produce different outputs even at temperature=0. The `canonical_hash` provides post-hoc verification, not guaranteed pre-hoc determinism.

### 13.3 State Management

Planner is **fully stateless**:
- Circuit breaker state is in-memory, per-process. On restart, all breakers reset to CLOSED (acceptable — brief retry storm, then stabilizes).
- No persistent queues, no background tasks.

### 13.4 Cross-Component Interactions

| Interaction | Pattern | Notes |
|-------------|---------|-------|
| Planner → ContextRAG | Direct service call | Never raises |
| Planner → PluginRegistry | Direct service call | May raise ToolNotFoundError |
| Planner → Signer | Direct service call | sign_plan() with dict |
| Planner → PlanLibrary | Direct service call (fallback only) | get_plans_by_intent() |
| Orchestration → Planner | DI via `get_planner_service()` | Single method: `generate_plan(intent)` |

---

## 14. Architecture Decision Records

| ADR | Decision | Relevance |
|-----|----------|-----------|
| ADR-0001 | Component-first folder layout | Planner follows `components/Planner/` structure |

**New decisions requiring ADR**:
1. **LLM adapter protocol**: Abstract LLM calls behind `LLMAdapter` protocol to enable future provider swaps (Ollama, vLLM). Implement `AnthropicAdapter` for MVP.
2. **Plan hash recomputation**: Planner computes `canonical_hash` independently from Signer. Signer recomputes during signing. This duplicates the canonicalization logic but avoids circular dependency.
3. **Per-model circuit breakers**: Each fallback level has its own CircuitBreaker instance. A failing primary model does not affect the fallback model's breaker state.

---

## 15. Risks & Open Questions

### 15.1 Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| LLM output quality varies | High | 3-layer validation + fallback hierarchy |
| LLM latency unpredictable | Medium | Circuit breaker + per-call timeout + fallback |
| Model updates break determinism | Medium | Pin model version in config; canonical_hash for verification |
| Anthropic API cost | Medium | Circuit breaker prevents waste; fallback to cheaper models |
| Template plans may be stale | Low | Templates filtered by success_threshold (0.7) |
| Circuit breaker state lost on restart | Low | Acceptable — resets to CLOSED, brief retry storm |

### 15.2 Open Questions

1. **Prompt versioning**: Should prompt text be versioned and stored alongside the plan? → **Recommendation**: Store prompt version string in `plan.meta` as an extra field (Pydantic `model_config = {"extra": "allow"}` on PlanMeta).
2. **Token budget configuration**: Max token budget per model? → **Recommendation**: 8K input + 4K output for MVP, configurable via env vars.
3. **Template instantiation**: How to fill placeholder args in Level 3 templates? → **Recommendation**: Simple string interpolation from Intent entities into template plan args.
4. **Scope aggregation**: Per-step or plan-level? → **Recommendation**: Aggregate all scopes from tool operation definitions in catalog during plan finalization.
5. **MODULAR_ARCHITECTURE update**: Planner depends on Signer and PlanLibrary (fallback) — not reflected in v1.3. Needs update.

---

## 16. Post-Generation Validation Checklist

- [x] Data model fields match GLOBAL_SPEC §2 contracts (Intent §2.1, Plan §2.3, Signature §2.4)
- [x] `user_id` present on input (Intent.user_id) — Planner owns no entities
- [x] Conformance header references current versions (GLOBAL_SPEC v2.2, MODULAR_ARCHITECTURE v1.3, HLD v4.0)
- [x] No owned tables (Planner is stateless) — N/A for table ownership
- [x] Component dependencies match MODULAR_ARCHITECTURE (+ noted Signer/PlanLibrary deviation)
- [x] Upstream consumer contract documented (PreviewOrchestrator/ExecuteOrchestrator)
- [x] N/A for storage idempotency (no storage APIs)
- [x] N/A for DDL (no owned tables)
- [x] Prometheus metrics defined with names and types (7 metrics)
- [x] No deprecated library versions (anthropic >=0.49.0, python-ulid >=3.0.0)
- [x] N/A for Evidence Item keys (Planner does not generate Evidence Items)
- [x] N/A for error handling via ErrorResponse (no HTTP routes)
- [x] N/A for database adapter patterns (no database access)

**Deviations documented**:
- Signer and PlanLibrary not listed in MODULAR_ARCHITECTURE v1.3 Planner deps → flagged in §15.2 Q5
- Canonical hash computation duplicated from Signer → documented in §6.5 and §14
