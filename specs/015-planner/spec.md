# Feature Spec — Planner

**Feature Branch**: `feat/planner`
**Created**: 2026-03-26
**Status**: Draft
**Target Component**: `components/Planner/`

---

## Overview

Planner is a **deterministic, one-shot plan generator** in the Domain/Service Layer that transforms an Intent + Evidence into a signed, executable plan graph. Given an Intent (GLOBAL_SPEC §2.1), it calls `ContextRAGService.gather_evidence()` to assemble context, queries `PluginRegistry` for available tools, invokes the Anthropic Claude API (temperature=0) via LLMAdapter protocol to generate a Plan (GLOBAL_SPEC §2.3), validates the output through a 3-layer pipeline (JSON parse → Pydantic schema → business rules), signs it via `Signer`, and returns a `PlannerResult` containing the Plan + Signature. The Planner is a **library component** (no HTTP routes) consumed via DI by the Orchestration Layer (PreviewOrchestrator, ExecuteOrchestrator).

---

## Goals

- Generate deterministic execution plans from Intent + Evidence + tool catalog (same inputs = same plan hash = same signature)
- Validate LLM output through 3 layers: JSON parsing, Pydantic schema validation, business rule enforcement
- Integrate with ContextRAG (evidence), PluginRegistry (tools), and Signer (Ed25519 signature)
- Support 4-level fallback hierarchy: Claude Opus → Sonnet → template from PlanLibrary → minimal safe plan
- Enforce hard constraints: max 50 steps, max 100KB plan size, valid step dependencies, valid tool references
- Support HITL gate insertion (`gate_id` on plan steps for approval checkpoints)

## Non-Goals

- Multi-turn iterative planning (one-shot only — no agentic loops)
- Plan execution (that is WorkflowBuilder/PreviewOrchestrator/ExecuteOrchestrator)
- Credential resolution (plans reference credential IDs; n8n resolves at execution time)
- HTTP routes (Planner is a library component consumed via DI)
- Plan storage (PlanWriter handles persistence after execution)
- LLM provider management (Planner receives an LLM adapter via DI)

---

## User Scenarios & Testing

### User Story 1 — Generate a plan for a known intent (Priority: P1)

When the Orchestration Layer needs a plan for a user request, Planner generates a validated, signed execution graph from Intent + Evidence + available tools.

**Why this priority**: This is the primary flow — every user request that reaches planning needs a valid plan.

**Independent Test**: Call `generate_plan(intent)` with a valid `schedule_meeting` Intent and mock LLM/services; verify the returned `PlannerResult` contains a valid Plan with correct step graph and a verifiable Ed25519 signature.

**Acceptance Scenarios**:

1. **Given** a valid Intent with `intent="schedule_meeting"` and ContextRAG returns preferences + history evidence, **When** `generate_plan()` is called, **Then** it returns a `PlannerResult` with a Plan containing steps with valid roles (Fetcher, Analyzer, Booker, Notifier), valid tool references from PluginRegistry, and a signed Signature.
2. **Given** the same Intent + Evidence + registry version, **When** `generate_plan()` is called twice, **Then** both calls produce plans with identical `meta.canonical_hash` values (determinism).
3. **Given** a valid plan is generated, **When** the Signature is verified via `Signer.verify_signature()`, **Then** verification passes.

---

### User Story 2 — 3-layer validation catches invalid LLM output (Priority: P1)

When the LLM generates malformed or invalid output, Planner's validation pipeline catches it and either retries or falls back.

**Why this priority**: LLM output is inherently unreliable — robust validation prevents bad plans from reaching execution.

**Independent Test**: Pass malformed JSON, invalid schema, or business-rule-violating plans through the validator; verify appropriate errors are raised at each layer.

**Acceptance Scenarios**:

1. **Given** the LLM returns invalid JSON, **When** validation runs, **Then** Layer 1 (JSON parsing) raises `PlanValidationError` with `layer="json_parse"`.
2. **Given** the LLM returns valid JSON but with a step referencing a non-existent tool, **When** validation runs, **Then** Layer 3 (business rules) raises `PlanValidationError` with `layer="business_rules"` and details about the missing tool.
3. **Given** the LLM returns a plan with forward dependencies (step 2 depends on step 5), **When** validation runs, **Then** Layer 2 (schema validation) catches the invalid dependency graph.
4. **Given** the LLM returns a plan with >50 steps, **When** validation runs, **Then** Layer 3 raises a complexity violation error.

---

### User Story 3 — Fallback hierarchy on LLM failure (Priority: P1)

When the primary LLM fails, Planner falls through a 4-level fallback hierarchy to ensure a plan is always returned.

**Why this priority**: System availability must not depend on a single LLM endpoint.

**Independent Test**: Mock the primary LLM to raise an error; verify fallback models are tried in order and a minimal plan is returned as last resort.

**Acceptance Scenarios**:

1. **Given** Claude Opus is unavailable (circuit breaker open), **When** `generate_plan()` is called, **Then** it falls back to Claude Sonnet and returns a valid plan.
2. **Given** both Claude Opus and Sonnet fail, **When** `generate_plan()` is called, **Then** it queries PlanLibrary for a template plan matching the intent type.
3. **Given** all LLM models and template lookup fail, **When** `generate_plan()` is called, **Then** it returns a minimal safe plan (single Fetcher step with `uses="system.echo"`, `call="echo"`, `dry_run=true`).
4. **Given** a fallback plan is used, **When** the result is returned, **Then** `PlannerResult.fallback_level` indicates which level was used (1=Opus, 2=Sonnet, 3=template, 4=minimal).

---

### User Story 4 — Circuit breaker protects against cascading LLM failures (Priority: P2)

When the LLM experiences repeated failures, the circuit breaker opens to prevent wasted API calls and latency.

**Why this priority**: Prevents cascading failures and controls cost during LLM outages.

**Independent Test**: Configure circuit breaker with low thresholds; send repeated failing requests; verify the circuit opens and subsequent calls skip directly to fallback.

**Acceptance Scenarios**:

1. **Given** 5 consecutive LLM failures, **When** the next `generate_plan()` is called, **Then** the circuit breaker is OPEN and the call goes directly to the next fallback level without attempting the primary LLM.
2. **Given** the circuit has been OPEN for >60 seconds, **When** `generate_plan()` is called, **Then** the circuit enters HALF_OPEN state and attempts one call to the primary LLM.
3. **Given** the circuit is HALF_OPEN and 2 consecutive successes occur, **When** the next call is made, **Then** the circuit returns to CLOSED state.

---

### User Story 5 — HITL gate insertion in plans (Priority: P3)

For plans that involve write operations (Booker role), the Planner inserts `gate_id` markers so the Orchestration Layer can pause for human approval.

**Why this priority**: HITL gates are essential for safety but the gate logic is handled by downstream components.

**Independent Test**: Generate a plan for an intent that requires a purchase; verify Booker steps have `gate_id` assigned.

**Acceptance Scenarios**:

1. **Given** a plan with a Booker step (write operation), **When** `generate_plan()` is called, **Then** the Booker step has a non-null `gate_id` (e.g., `"gate-A"`).
2. **Given** a plan with only Fetcher and Analyzer steps, **When** `generate_plan()` is called, **Then** no steps have `gate_id` (read-only plans don't need approval gates).

---

### Edge Cases

- Intent with empty `entities` — still generates a plan (may be a generic plan based on intent type alone)
- ContextRAG returns empty evidence (all sources degraded) — Planner still generates a plan using Intent + tools only; `PlannerResult.context_degraded=true`
- PluginRegistry returns empty tool catalog — plan generation fails (no tools available); returns minimal echo plan
- LLM returns empty string — caught by Layer 1 (JSON parse), triggers retry then fallback
- LLM returns valid JSON but wrong shape (array instead of object) — caught by Layer 2 (Pydantic)
- Plan references a tool that was deactivated between planning and validation — caught by Layer 3 (business rules, `validate_plan_tools()`)
- Very long intent text (>10KB) — truncate before passing to LLM prompt
- Concurrent calls for same user — must be safe (Planner is stateless, circuit breaker is per-instance)
- LLM returns plan with duplicate step numbers — caught by Layer 2 (schema validation)
- LLM returns plan with circular dependencies — caught by Layer 2 (dependency validation, no forward refs)

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST accept an `Intent` (GLOBAL_SPEC §2.1) and return a `PlannerResult` containing a `Plan` (GLOBAL_SPEC §2.3) and `Signature` (GLOBAL_SPEC §2.4)
- **FR-002**: System MUST call `ContextRAGService.gather_evidence(intent)` to assemble evidence before plan generation
- **FR-003**: System MUST call `RegistryService.list_catalog()` to obtain the current tool catalog snapshot, and include `registry_version` in planning context
- **FR-004**: System MUST invoke the Anthropic Claude API (temperature=0) via LLMAdapter protocol with a structured prompt containing Intent, Evidence, and tool catalog to generate the plan graph
- **FR-005**: System MUST validate LLM output through 3 layers: (1) JSON parsing, (2) Pydantic Plan schema validation, (3) business rule enforcement
- **FR-006**: System MUST call `RegistryService.validate_plan_tools(registry_version, tool_ids)` during Layer 3 validation to verify all referenced tools are active
- **FR-007**: System MUST call `SignerService.sign_plan(plan_data)` to produce an Ed25519 signature for the validated plan
- **FR-008**: System MUST compute `meta.canonical_hash` as SHA-256 of the canonical plan JSON (sorted keys, no whitespace)
- **FR-009**: System MUST implement a 4-level fallback hierarchy: (1) primary model, (2) fallback model, (3) template from PlanLibrary, (4) minimal safe plan
- **FR-010**: System MUST implement a circuit breaker for LLM calls with configurable failure_threshold (default: 5), timeout (default: 60s), and success_threshold (default: 2)
- **FR-011**: System MUST enforce hard constraints: max 50 steps, no forward/self dependencies, no duplicate step numbers, valid roles, valid tool references
- **FR-012**: System MUST ensure `plan.constraints.scopes` contains all OAuth scopes required by the tools used in the plan graph
- **FR-013**: System MUST set `dry_run=true` on all steps by default (preview-first safety)
- **FR-014**: System MUST NOT include credential values in plans — only credential ID templates from PluginRegistry
- **FR-015**: System MUST generate plan_id as a ULID (26 characters, monotonically sortable)
- **FR-016**: System MUST populate `plan.plugins[]` with the unique tool IDs used across all graph steps
- **FR-017**: System MUST support `gate_id` on steps where the LLM determines human approval is needed (typically Booker role steps)

### Key Entities

- **`Plan`** (from `shared/schemas/plan.py`): The output — deterministic execution graph with steps, constraints, and metadata
- **`PlanStep`** (from `shared/schemas/plan.py`): Single execution step with role, tool, operation, dependencies, and optional gate_id
- **`Signature`** (from `shared/schemas/signature.py`): Ed25519 cryptographic signature binding plan_hash
- **`PlannerResult`** (new domain model): Wrapper containing Plan, Signature, fallback_level, context_degraded, generation_duration_ms
- **`PlanValidationError`** (new domain error): Validation failure with layer, message, and details
- **`CircuitOpenError`** (new domain error): Circuit breaker is open, LLM calls rejected

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: `generate_plan()` returns a valid, signed Plan for all test intents (schedule_meeting, send_email, search_flights)
- **SC-002**: Same Intent + Evidence + registry version produces identical `meta.canonical_hash` across repeated calls (determinism)
- **SC-003**: 3-layer validation catches 100% of invalid plans in test suite (malformed JSON, bad schema, business rule violations)
- **SC-004**: Fallback hierarchy returns a plan even when all LLM models are unavailable (minimal safe plan)
- **SC-005**: Circuit breaker opens after configured failure threshold and recovers after timeout

---

## Interfaces & Contracts (conform to GLOBAL_SPEC v2.2)

### Input: Intent (GLOBAL_SPEC §2.1)

```json
{
  "intent": "schedule_meeting",
  "entities": {"person": "Alice", "timeframe": "next week"},
  "constraints": {"duration_min": 30},
  "tz": "America/Chicago",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "context_budget": 3
}
```

### Output: PlannerResult (domain model)

```json
{
  "plan": {
    "plan_id": "01HXYZABCDEFGHIJKLMNOPQRS",
    "intent": { "...Intent..." },
    "trace_id": "trace-abc-123",
    "graph": [
      {
        "step": 1, "mode": "interactive", "role": "Fetcher",
        "uses": "google.calendar", "call": "get_availability",
        "args": {"email": "alice@company.com"}, "after": [],
        "timeout_s": 30, "gate_id": null, "dry_run": true
      },
      {
        "step": 2, "mode": "interactive", "role": "Analyzer",
        "uses": "internal.scheduler", "call": "find_overlap",
        "args": {}, "after": [1], "timeout_s": 30, "gate_id": null, "dry_run": true
      },
      {
        "step": 3, "mode": "interactive", "role": "Booker",
        "uses": "google.calendar", "call": "create_event",
        "args": {"summary": "Meeting with Alice", "duration_min": 30},
        "after": [2], "timeout_s": 30, "gate_id": "gate-A", "dry_run": true
      },
      {
        "step": 4, "mode": "interactive", "role": "Notifier",
        "uses": "slack", "call": "send_message",
        "args": {"text": "Meeting booked"}, "after": [3],
        "timeout_s": 30, "gate_id": null, "dry_run": true
      }
    ],
    "constraints": {"scopes": ["calendar.read", "calendar.write", "slack.write"], "ttl_s": 900, "max_retries": 3},
    "plugins": ["google.calendar", "internal.scheduler", "slack"],
    "meta": {
      "created_at": "2026-03-26T15:30:00Z",
      "author": "planner@system",
      "version": "v2.0.0",
      "canonical_hash": "sha256:a1b2c3d4...",
      "hash_algo": "sha256"
    }
  },
  "signature": {
    "algo": "Ed25519",
    "signer": "planner@system",
    "signature": "base64:...",
    "pubkey_id": "k1",
    "plan_hash": "sha256:a1b2c3d4..."
  },
  "fallback_level": 1,
  "context_degraded": false,
  "generation_duration_ms": 1200
}
```

### Service Interface (library component — no HTTP routes)

```python
class PlannerService:
    async def generate_plan(self, intent: Intent) -> PlannerResult:
        """Generate a deterministic, validated, signed execution plan."""
```

**Note**: Planner is a **library component** (like ContextRAG, Signer, PlanWriter). It has no HTTP routes. It is consumed via dependency injection by the Orchestration Layer.

### Consumer: PreviewOrchestrator / ExecuteOrchestrator

The Orchestration Layer calls `generate_plan(intent)` and passes the resulting Plan + Signature to WorkflowBuilder for n8n workflow generation.

Reference: docs/architecture/GLOBAL_SPEC.md v2.2

---

## Component Mapping

- **Target**: `components/Planner/`
- **Files expected**:
  - `__init__.py`
  - `domain/__init__.py`
  - `domain/models.py` — `PlannerResult`, `PlannerError`, `PlanValidationError`, `CircuitOpenError`, `PlanGenerationError`
  - `service/__init__.py`
  - `service/planner_service.py` — `PlannerService` with `generate_plan()` + `create_planner_service()` factory
  - `adapters/__init__.py`
  - `adapters/llm_adapter.py` — LLM client adapter (calls Claude API, handles response parsing)
  - `adapters/plan_validator.py` — 3-layer validation pipeline
  - `adapters/circuit_breaker.py` — Circuit breaker for LLM calls
  - `adapters/prompt_builder.py` — Builds structured prompt from Intent + Evidence + tools
  - `adapters/plan_hasher.py` — Canonical JSON serialization + SHA-256 hash computation
  - `tests/__init__.py`
  - `tests/conftest.py` — Shared fixtures (mock LLM, sample intents, mock services)
  - `tests/test_unit.py` — Unit tests for validator, circuit breaker, prompt builder, hasher
  - `tests/test_service.py` — Service-level tests with mock adapters
  - `tests/test_contract.py` — Plan schema compliance, signature verification, determinism
  - `tests/test_observability.py` — Logging, no PII/credentials in logs

---

## Dependencies & Risks

### Component Dependencies

| Component | Service | Method | Usage |
|-----------|---------|--------|-------|
| ContextRAG | `ContextRAGService` | `gather_evidence(intent)` | Assemble evidence for LLM prompt |
| PluginRegistry | `RegistryService` | `list_catalog()` | Get tool catalog snapshot for LLM prompt |
| PluginRegistry | `RegistryService` | `validate_plan_tools(version, tool_ids)` | Verify tools in generated plan are active |
| PluginRegistry | `RegistryService` | `get_version()` | Get current registry version for determinism |
| Signer | `SignerService` | `sign_plan(plan_data)` | Sign validated plan with Ed25519 |
| PlanLibrary | `PlanService` | `get_plans_by_intent(intent_type)` | Fallback Level 3: template plan lookup |

### External Dependencies

| Dependency | Usage | Justification |
|------------|-------|---------------|
| `anthropic` | Claude API client | LLM calls for plan generation |
| `ulid-py` or equivalent | ULID generation | Plan ID generation per GLOBAL_SPEC |

### Shared Infrastructure

| Dependency | Usage |
|------------|-------|
| `shared/schemas/intent.py` | Input contract (`Intent` model) |
| `shared/schemas/plan.py` | Output contract (`Plan`, `PlanStep`, `PlanConstraints`, `PlanMeta`) |
| `shared/schemas/signature.py` | Signature contract (`Signature`) |
| `shared/schemas/evidence.py` | Evidence items from ContextRAG |
| `shared/app.py` | DI wiring via lifespan |
| `shared/dependencies.py` | `Depends(get_planner_service)` |

### Risks

- **LLM latency**: Plan generation depends on LLM response time (typically 1-3s). Mitigation: circuit breaker + fallback hierarchy + per-call timeout.
- **LLM output quality**: The LLM may generate invalid plans. Mitigation: 3-layer validation pipeline, fallback to template/minimal plans.
- **Determinism under model updates**: LLM behavior may change across model versions even at temperature=0. Mitigation: pin model version in config, canonical hash verification.
- **PluginRegistry stale data**: Tool catalog may change between plan generation and execution. Mitigation: include `registry_version` in plan metadata for staleness detection.
- **Cost**: Each plan generation costs ~$0.01-0.05 in LLM tokens. Mitigation: fallback to cheaper models, circuit breaker prevents waste during outages.

---

## Non-Functional Requirements

### Performance

| Metric | Target | Notes |
|--------|--------|-------|
| `generate_plan()` p95 | < 5 s | LLM-bound; includes context assembly, LLM call, validation, signing |
| `generate_plan()` p99 | < 10 s | Timeout on primary, fallback to faster model |
| Validation pipeline | < 50 ms | Pure computation, no I/O |
| Plan signing | < 10 ms | Ed25519 is fast |
| Context assembly (ContextRAG) | < 150 ms | Already verified in ContextRAG spec |

### Observability

- Structured logging correlated by `intent.trace_id` and `plan_id`
- No PII in logs — log intent_type and entity keys, never entity values or constraint values
- No credential values in logs — only credential ID templates
- Log per-step LLM call durations and token usage
- Prometheus metrics:
  - `planner_generate_duration_seconds` (histogram, labels: `intent_type`, `fallback_level`)
  - `planner_llm_call_duration_seconds` (histogram, labels: `model`)
  - `planner_llm_token_usage` (counter, labels: `model`, `type=[input|output]`)
  - `planner_validation_error_total` (counter, labels: `layer`)
  - `planner_circuit_state` (gauge, labels: `model`, values: 0=closed, 1=half_open, 2=open)
  - `planner_fallback_total` (counter, labels: `level`)

### Safety

- Plans NEVER contain credential values (only ID templates)
- No PII or secrets in logs
- `dry_run=true` by default on all steps (preview-first)
- Circuit breaker prevents cascading LLM failures
- Minimal safe plan ensures system always returns something

---

## Open Questions

1. **LLM provider configuration**: Should the LLM adapter support multiple providers (Anthropic, Ollama, vLLM) via a common interface? Recommendation: Yes — define an `LLMAdapter` protocol, implement `AnthropicAdapter` for MVP, allow future `OllamaAdapter`.
2. **Prompt versioning**: Should the system prompt be versioned and stored alongside the plan for reproducibility? Recommendation: Yes — store prompt version in `plan.meta` for audit.
3. **Token budget**: What is the max token budget for a single LLM call? Recommendation: 8K input + 4K output for MVP, configurable per model.
4. **Template plan format**: What exact format should PlanLibrary template plans use for Level 3 fallback? Recommendation: Use existing `Plan` schema with placeholder args that get filled from Intent entities.
5. **Scope aggregation**: Should Planner query `RegistryService.verify_scopes()` per-step, or aggregate all scopes from tool definitions? Recommendation: Aggregate from tool definitions during catalog fetch, verify at plan level.

---

## Conformance

This work conforms to docs/architecture/GLOBAL_SPEC.md v2.2.
This work conforms to docs/architecture/MODULAR_ARCHITECTURE.md v1.3.
This work conforms to docs/architecture/Project_HLD.md v4.0.
