# Tasks: Planner

**Created**: 2026-03-26
**Branch**: `feat/planner`
**SPEC**: `specs/015-planner/spec.md`
**LLD**: `components/Planner/LLD.md`

## Task Organization

Tasks are organized by implementation phase following the LLD architecture. The Planner is a **stateless library component** (no HTTP routes, no database tables). It depends on ContextRAG, PluginRegistry, and PlanLibrary (fallback). All three dependencies are already implemented and wired in `shared/app.py`.

---

## Phase 0: Setup & Scaffolding

### T000 -- Verify external packages are available

**Files**: (read-only verification)
- `/Users/anantshreechandola/Desktop/Personal-agent/pyproject.toml`

**Description**: Confirm that `anthropic` (>=0.18.0) and `ulid-py` (>=1.1.0) are already listed in `pyproject.toml` dependencies. Both are present. No package installation step is required.

**Acceptance**: Both packages confirmed present in `pyproject.toml` `[project.dependencies]`.

---

### T001 -- Create component directory skeleton

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/domain/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/domain/models.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/adapters/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/service/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/conftest.py`

**Description**: Create the standard component directory layout following ContextRAG pattern. All `__init__.py` files are initially empty except the top-level one which re-exports public names (following ContextRAG's `__init__.py` pattern). `domain/models.py` and `tests/conftest.py` will be populated in later tasks.

**blockedBy**: none

**Acceptance**: All directories and files exist. `ruff check` passes. Imports from `components.Planner` do not error.

---

## Phase 1: Domain Models (Foundation)

### T100 -- Implement domain models

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/domain/models.py`

**Description**: Define all Planner domain models and error classes per LLD SS5.1 and SS5.2.

**Key contents**:
- `PlannerResult(BaseModel)` with fields: `plan: Plan`, `signature: Signature`, `fallback_level: int` (Field ge=1, le=4), `context_degraded: bool`, `generation_duration_ms: int` (ge=0), `registry_version: int`
- `PlannerError(Exception)` -- base error
- `PlanValidationError(PlannerError)` -- `__init__(self, layer: str, message: str, details: dict | None = None)`, attributes: `layer`, `message`, `details`
- `CircuitOpenError(PlannerError)` -- `__init__(self, model: str)`, attribute: `model`
- `PlanGenerationError(PlannerError)` -- all fallback levels exhausted
- `LLMCallError(PlannerError)` -- `__init__(self, model: str, reason: str)`, attributes: `model`, `reason`

**Imports**: `shared.schemas.plan.Plan`, `shared.schemas.signature.Signature`, `pydantic.BaseModel`, `pydantic.Field`

**blockedBy**: T001

**Acceptance criteria**:
- All five error classes are defined with correct `__init__` signatures
- `PlannerResult` validates correctly with sample Plan + Signature data
- Maps to SPEC key entities section
- Passes `ruff check` and `ruff format`

---

### T101 -- Populate top-level `__init__.py` with re-exports

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/__init__.py`

**Description**: Re-export public names following ContextRAG `__init__.py` pattern.

**Key contents**:
```python
from .domain.models import (
    CircuitOpenError,
    LLMCallError,
    PlanGenerationError,
    PlannerError,
    PlannerResult,
    PlanValidationError,
)
from .service.planner_service import PlannerService, create_planner_service

__all__ = [
    "CircuitOpenError",
    "LLMCallError",
    "PlanGenerationError",
    "PlannerError",
    "PlannerResult",
    "PlannerService",
    "PlanValidationError",
    "create_planner_service",
]
```

**Note**: This file will initially fail to import until the service module exists (T200). The implementer should create this file with the correct content but defer import verification to after T200.

**blockedBy**: T100

**Acceptance criteria**: After all implementation tasks complete, `from components.Planner import PlannerService, create_planner_service, PlannerResult` works.

---

## Phase 2: Adapters (Utilities)

### T200 -- Implement plan hasher

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/adapters/plan_hasher.py`

**Description**: Canonical JSON serialization + SHA-256 hash computation per LLD SS6.5. This intentionally duplicates `components/Signer/adapters/canonicalizer.py` to avoid a circular dependency (Planner computes hash before Signer re-verifies).

**Key contents**:
- `canonicalize_plan(plan_data: dict) -> str` -- `json.dumps(plan_data, sort_keys=True, separators=(",", ":"))`
- `compute_plan_hash(plan_data: dict) -> str` -- SHA-256 hex digest of canonical bytes

**Imports**: `hashlib`, `json`

**blockedBy**: T001

**Acceptance criteria**:
- FR-008: `compute_plan_hash` returns a 64-character hex string
- Same dict input always produces same hash output (determinism)
- Produces identical output to `components/Signer/adapters/canonicalizer.py` for the same input

---

### T201 -- Implement circuit breaker

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/adapters/circuit_breaker.py`

**Description**: In-memory, per-model circuit breaker per LLD SS6.3.

**Key contents**:
- `CircuitState(Enum)` with values `CLOSED`, `OPEN`, `HALF_OPEN`
- `CircuitBreaker` class:
  - `__init__(self, failure_threshold: int = 5, timeout_s: int = 60, success_threshold: int = 2)`
  - State fields: `state: CircuitState`, `failure_count: int`, `success_count: int`, `last_failure_time: float | None`
  - `async def call(self, func, *args, **kwargs)` -- executes func with circuit breaker protection; raises `CircuitOpenError` if OPEN and timeout not elapsed
  - `def get_state(self) -> CircuitState` -- returns current state for metrics
- State machine: CLOSED -[failures >= threshold]-> OPEN -[timeout elapsed]-> HALF_OPEN -[successes >= threshold]-> CLOSED; HALF_OPEN -[any failure]-> OPEN

**Imports**: `enum.Enum`, `time`, `components.Planner.domain.models.CircuitOpenError`

**blockedBy**: T100

**Acceptance criteria**:
- FR-010: Circuit opens after `failure_threshold` consecutive failures
- US-4 AC-1: 5 consecutive failures -> OPEN state
- US-4 AC-2: After 60s timeout, transitions to HALF_OPEN
- US-4 AC-3: 2 consecutive successes in HALF_OPEN -> CLOSED
- Thread-safe for concurrent async calls within a single event loop

---

### T202 -- Implement prompt builder

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/adapters/prompt_builder.py`

**Description**: Builds structured system + user prompts per LLD SS6.4.

**Key contents**:
- `PromptBuilder` class:
  - `def build_system_prompt(self) -> str` -- static system prompt with Plan JSON schema instructions, role definitions, constraint rules, HITL gate instructions, output format requirements
  - `def build_user_prompt(self, intent: Intent, evidence: list[EvidenceItem], catalog: CatalogResponse) -> str` -- per-request prompt with intent, evidence, and tool catalog; truncates intent text to 10KB max

**Imports**:
- `shared.schemas.intent.Intent`
- `shared.schemas.evidence.EvidenceItem`
- `components.PluginRegistry.domain.models.CatalogResponse`

**blockedBy**: T001

**Acceptance criteria**:
- System prompt includes Plan schema definition, all 6 role names, `dry_run=true` rule, `gate_id` on Booker rule, no credential values rule, raw JSON output format requirement
- User prompt includes serialized intent, evidence list, and tool catalog
- Intent text truncated at 10KB (edge case from SPEC)
- No PII in prompts (entity keys yes, entity values are passed but that is intentional for the LLM -- the LLM needs the actual values to generate a plan)

---

### T203 -- Implement LLM adapter protocol and Anthropic adapter

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/adapters/llm_adapter.py`

**Description**: LLM adapter protocol + Anthropic Claude implementation per LLD SS5.3 and SS6.1.

**Key contents**:
- `LLMAdapter(Protocol)` (runtime_checkable):
  - `async def generate(self, model: str, system_prompt: str, user_prompt: str, max_tokens: int = 4096, temperature: float = 0.0) -> str`
- `AnthropicAdapter` class:
  - `__init__(self, api_key: str | None = None)` -- reads from `ANTHROPIC_API_KEY` env var if not provided; creates `anthropic.AsyncAnthropic` client
  - `async def generate(...)` -- calls Claude Messages API; wraps errors in `LLMCallError`; respects `PLANNER_LLM_TIMEOUT_S` (default 30s)
- Environment variable reads: `ANTHROPIC_API_KEY`, `PLANNER_LLM_TIMEOUT_S`

**Imports**: `typing.Protocol`, `typing.runtime_checkable`, `anthropic.AsyncAnthropic`, `components.Planner.domain.models.LLMCallError`

**blockedBy**: T100

**Acceptance criteria**:
- FR-004: Uses Anthropic Claude API with temperature=0
- `LLMAdapter` is a runtime-checkable Protocol
- `AnthropicAdapter` implements `LLMAdapter` (verified by `isinstance` check)
- API errors (timeout, rate limit, 500) are wrapped in `LLMCallError`
- FR-014: No credential values in prompts or plans (adapter does not inject credentials)

---

### T204 -- Implement plan validator (3-layer pipeline)

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/adapters/plan_validator.py`

**Description**: 3-layer validation pipeline per LLD SS6.2.

**Key contents**:
- `PlanValidator` class:
  - `__init__(self, registry_service: Any)` -- stores reference to RegistryService for Layer 3 tool validation
  - `async def validate(self, raw_output: str, intent: Intent, registry_version: int, tool_ids: set[str]) -> Plan` -- runs all 3 layers, returns validated Plan
  - Private methods for each layer:
    - `_validate_json(raw_output: str) -> dict` -- `json.loads()`, raises `PlanValidationError(layer="json_parse")` on failure
    - `_validate_schema(data: dict) -> Plan` -- `Plan.model_validate()`, raises `PlanValidationError(layer="schema")` on failure. Also checks: no self-dependencies, no forward dependencies, no duplicate step numbers, all `after` references point to existing steps
    - `async def _validate_business_rules(plan: Plan, registry_version: int, tool_ids: set[str]) -> Plan` -- checks: all `step.uses` tool_ids active (calls `registry_service.validate_plan_tools()`), max 50 steps, `dry_run=true` on all steps, `gate_id` present on Booker role steps, no step args >10KB, total plan size <=100KB, `constraints.scopes` aggregation validation

**Imports**: `json`, `shared.schemas.plan.Plan`, `shared.schemas.intent.Intent`, `components.Planner.domain.models.PlanValidationError`

**blockedBy**: T100

**Acceptance criteria**:
- FR-005: 3 layers validated in sequence
- US-2 AC-1: Invalid JSON raises `PlanValidationError(layer="json_parse")`
- US-2 AC-2: Non-existent tool raises `PlanValidationError(layer="business_rules")`
- US-2 AC-3: Forward dependencies caught by layer="schema"
- US-2 AC-4: >50 steps raises `PlanValidationError(layer="business_rules")`
- FR-011: No forward/self deps, no duplicate step numbers, valid roles, valid tool refs
- FR-013: `dry_run=true` enforced on all steps
- FR-017: `gate_id` enforcement on Booker steps

---

## Phase 3: Service Layer (Orchestration)

### T300 -- Implement PlannerService with generate_plan and fallback hierarchy

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/service/planner_service.py`

**Description**: Core service orchestrating the full plan generation flow per LLD SS7. This is the heart of the component.

**Key contents**:
- `PlannerService` class:
  - `__init__(self, context_rag_service, registry_service, plan_service, llm_adapter, prompt_builder, validator, primary_breaker, fallback_breaker, primary_model, fallback_model, max_output_tokens)` -- all injected by factory
  - `async def generate_plan(self, intent: Intent) -> PlannerResult` -- main public method (LLD SS7.1 flow):
    1. Gather evidence from ContextRAG (`gather_evidence(intent)`)
    2. Get tool catalog from PluginRegistry (`list_catalog()`)
    3. Build prompts via PromptBuilder
    4. Try fallback hierarchy (`_generate_with_fallback()`)
    5. Compute canonical hash, populate `meta.canonical_hash`
    6. Return `PlannerResult`
  - `async def _generate_with_fallback(...)` -- 4-level fallback per LLD SS7.2:
    - Level 1: Primary model via primary circuit breaker
    - Level 2: Fallback model via fallback circuit breaker
    - Level 3: Template from PlanLibrary (`get_plans_by_intent()`)
    - Level 4: Minimal safe plan (`_create_minimal_plan()`)
  - `def _create_minimal_plan(self, intent: Intent) -> Plan` -- single Fetcher step with `system.echo` per LLD SS7.3
  - `def _finalize_plan(self, plan: Plan, intent: Intent) -> Plan` -- populate plan_id (ULID), intent, plugins[], enforce dry_run per LLD SS7.4
  - `def _instantiate_template(self, template_evidence, intent, evidence, tool_ids) -> Plan` -- fill Level 3 template from intent entities

- `create_planner_service(context_rag_service, registry_service, plan_service, llm_adapter=None) -> PlannerService` -- factory function (LLD SS4.2):
  - Reads env vars: `PLANNER_PRIMARY_MODEL` (default `claude-sonnet-4-5-20250929`), `PLANNER_FALLBACK_MODEL` (default `claude-haiku-4-5-20251001`), `PLANNER_MAX_OUTPUT_TOKENS` (default 4096)
  - Creates `AnthropicAdapter` if `llm_adapter` is None
  - Creates `PromptBuilder`, `PlanValidator(registry_service)`, two `CircuitBreaker` instances (primary and fallback)
  - Returns configured `PlannerService`

**Imports**:
- `shared.schemas.intent.Intent`
- `shared.schemas.plan.Plan, PlanStep, PlanConstraints, PlanMeta`
- `shared.schemas.evidence.EvidenceItem`
- `components.Planner.domain.models.PlannerResult, PlanValidationError, CircuitOpenError, PlanGenerationError, LLMCallError`
- `components.Planner.adapters.llm_adapter.LLMAdapter, AnthropicAdapter`
- `components.Planner.adapters.prompt_builder.PromptBuilder`
- `components.Planner.adapters.plan_validator.PlanValidator`
- `components.Planner.adapters.circuit_breaker.CircuitBreaker`
- `components.Planner.adapters.plan_hasher.compute_plan_hash`
- `ulid`
- `datetime`, `time`, `logging`, `os`

**blockedBy**: T100, T200, T201, T202, T203, T204

**Acceptance criteria**:
- FR-001: Accepts Intent, returns PlannerResult with Plan
- FR-002: Calls `context_rag_service.gather_evidence(intent)`
- FR-003: Calls `registry_service.list_catalog()` and captures `registry_version`
- FR-004: Invokes LLM via adapter with temperature=0
- FR-008: Computes `meta.canonical_hash` as SHA-256 of canonical JSON
- FR-009: 4-level fallback hierarchy implemented
- FR-012: `constraints.scopes` contains all required scopes
- FR-013: `dry_run=true` on all steps
- FR-015: `plan_id` is a ULID (26 characters)
- FR-016: `plugins[]` populated with unique tool IDs
- US-1 AC-1: Valid intent returns PlannerResult with valid plan
- US-1 AC-2: Same inputs produce identical canonical_hash (determinism)
- US-3 AC-1: Primary unavailable -> fallback to Sonnet
- US-3 AC-2: Both models fail -> PlanLibrary template
- US-3 AC-3: All fail -> minimal safe plan (system.echo, dry_run=true)
- US-3 AC-4: `fallback_level` indicates which level was used

---

### T301 -- Wire Planner into shared/app.py lifespan

**File to modify**: `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`

**Description**: Add PlannerService initialization to the application lifespan, following the existing pattern for ContextRAG and PlanWriter.

**Key changes**: Add after the ContextRAG service initialization block:
```python
# Planner service (library -- no routes)
from components.Planner.service.planner_service import create_planner_service

app.state.planner_service = create_planner_service(
    context_rag_service=app.state.context_rag_service,
    registry_service=app.state.registry_service,
    plan_service=app.state.plan_service,
)
```

**blockedBy**: T300

**Acceptance criteria**:
- `app.state.planner_service` is set during lifespan startup
- LLD SS9.1 wiring matches exactly
- Planner is initialized AFTER ContextRAG (it depends on ContextRAG evidence)
- No circular imports

---

### T302 -- Add get_planner_service to shared/dependencies.py

**File to modify**: `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py`

**Description**: Add the `get_planner_service` dependency function for downstream Orchestration Layer consumers.

**Key changes**: Add at the end of the file:
```python
def get_planner_service(request: Request) -> Any:
    """Get PlannerService singleton from app state."""
    return request.app.state.planner_service
```

**blockedBy**: T301

**Acceptance criteria**:
- LLD SS9.1 dependency function present
- Follows exact pattern of existing dependency functions in the file
- Future Orchestration Layer can use `Depends(get_planner_service)`

---

## Phase 4: Test Fixtures

### T400 -- Create shared test fixtures (conftest.py)

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/conftest.py`

**Description**: Shared fixtures with mocked downstream services, sample intents, sample LLM responses, and configured PlannerService instances. Follows ContextRAG `conftest.py` pattern.

**Key contents**:
- Sample data constants:
  - `SAMPLE_USER_ID` (UUID string)
  - `SAMPLE_INTENT` (schedule_meeting Intent with entities, constraints, trace_id)
  - `SAMPLE_EVIDENCE` (list of EvidenceItem: 2 preferences + 1 history)
  - `SAMPLE_VALID_PLAN_JSON` (raw JSON string representing a valid Plan output from LLM -- 4 steps: Fetcher, Analyzer, Booker with gate_id, Notifier)
  - `SAMPLE_INVALID_JSON` (malformed JSON string)
  - `SAMPLE_PLAN_MISSING_TOOL` (valid JSON, references non-existent tool)
  - `SAMPLE_PLAN_FORWARD_DEP` (valid JSON, step 2 depends on step 5)
  - `SAMPLE_PLAN_TOO_MANY_STEPS` (valid JSON, 51 steps)
- Fixtures:
  - `mock_llm_adapter` -- AsyncMock implementing LLMAdapter protocol; `generate` returns `SAMPLE_VALID_PLAN_JSON`
  - `mock_failing_llm_adapter` -- AsyncMock that raises `LLMCallError`
  - `mock_context_rag_service` -- AsyncMock; `gather_evidence` returns `ContextResult(evidence=SAMPLE_EVIDENCE)`
  - `mock_degraded_context_rag_service` -- returns `ContextResult(evidence=[], degraded_sources=["profilestore", "history"])`
  - `mock_registry_service` -- AsyncMock; `list_catalog` returns `CatalogResponse` with sample tools matching SAMPLE_VALID_PLAN_JSON tool_ids; `validate_plan_tools` returns `ValidationResult(valid=True)`; `get_version` returns 1
  - `mock_empty_registry_service` -- returns empty catalog
  - `mock_plan_service` -- AsyncMock; `get_plans_by_intent` returns list with one EvidenceItem of type="plan"
  - `planner_service` -- fully wired `PlannerService` with all mocks

**Imports**: `unittest.mock.AsyncMock`, `pytest`, `shared.schemas.intent.Intent`, `shared.schemas.evidence.EvidenceItem`, `components.ContextRAG.domain.models.ContextResult`, `components.PluginRegistry.domain.models.CatalogResponse, ToolModel, OperationModel, ValidationResult`, `components.Planner.service.planner_service.PlannerService`, `components.Planner.adapters.circuit_breaker.CircuitBreaker`, `components.Planner.adapters.prompt_builder.PromptBuilder`, `components.Planner.adapters.plan_validator.PlanValidator`

**blockedBy**: T100, T200, T201, T202, T203, T204, T300

**Acceptance criteria**:
- All fixtures produce valid, importable objects
- `planner_service` fixture returns a fully configured `PlannerService` that can run `generate_plan()` with mocked dependencies
- Sample data matches GLOBAL_SPEC contracts (valid Intent, valid Plan structure, valid Signature)

---

## Phase 5: Unit Tests

### T500 -- Unit tests for plan hasher

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_unit.py`

**Description**: Unit tests for `canonicalize_plan` and `compute_plan_hash`.

**Key test cases** (~5 tests):
- `test_canonicalize_produces_sorted_keys` -- verify sorted keys, no whitespace
- `test_canonicalize_deterministic` -- same input dict -> same output string regardless of insertion order
- `test_compute_hash_returns_64_char_hex` -- verify hex digest length
- `test_compute_hash_deterministic` -- same dict -> same hash
- `test_compute_hash_matches_signer_canonicalizer` -- verify output matches `components.Signer.adapters.canonicalizer.compute_plan_hash` for the same input (FR-008 cross-validation)

**blockedBy**: T200

**Acceptance criteria**: FR-008 verified. Determinism verified. All tests pass.

---

### T501 -- Unit tests for circuit breaker

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_unit.py` (append)

**Description**: Unit tests for the CircuitBreaker state machine.

**Key test cases** (~10 tests):
- `test_initial_state_is_closed` -- verify CLOSED on creation
- `test_successful_call_stays_closed` -- call succeeds, state remains CLOSED
- `test_failure_increments_count` -- single failure increments failure_count
- `test_reaches_threshold_opens_circuit` -- 5 failures -> OPEN state (US-4 AC-1)
- `test_open_circuit_raises_circuit_open_error` -- immediate CircuitOpenError on call
- `test_open_transitions_to_half_open_after_timeout` -- after 60s, HALF_OPEN (US-4 AC-2)
- `test_half_open_success_increments_success_count`
- `test_half_open_two_successes_closes_circuit` -- 2 successes -> CLOSED (US-4 AC-3)
- `test_half_open_failure_reopens_circuit` -- failure in HALF_OPEN -> OPEN
- `test_get_state_returns_current_state` -- metric-friendly state access

**blockedBy**: T201

**Acceptance criteria**: FR-010 verified. All 3 state machine transitions tested. US-4 all ACs covered.

---

### T502 -- Unit tests for prompt builder

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_unit.py` (append)

**Description**: Unit tests for PromptBuilder.

**Key test cases** (~5 tests):
- `test_system_prompt_contains_plan_schema`
- `test_system_prompt_contains_all_roles` -- Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier
- `test_system_prompt_contains_dry_run_rule`
- `test_user_prompt_contains_intent_and_evidence`
- `test_user_prompt_truncates_long_intent` -- intent text >10KB gets truncated

**blockedBy**: T202

**Acceptance criteria**: Prompt structure verified. Truncation edge case handled.

---

### T503 -- Unit tests for plan validator

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_unit.py` (append)

**Description**: Unit tests for the 3-layer PlanValidator. This is the most critical adapter.

**Key test cases** (~12 tests):

Layer 1 (JSON parse):
- `test_layer1_invalid_json_raises_json_parse_error` (US-2 AC-1)
- `test_layer1_empty_string_raises_json_parse_error`

Layer 2 (Schema validation):
- `test_layer2_valid_plan_passes`
- `test_layer2_missing_required_field_raises_schema_error`
- `test_layer2_forward_dependency_raises_schema_error` (US-2 AC-3)
- `test_layer2_self_dependency_raises_schema_error`
- `test_layer2_duplicate_step_numbers_raises_schema_error`
- `test_layer2_invalid_role_raises_schema_error`

Layer 3 (Business rules):
- `test_layer3_nonexistent_tool_raises_business_error` (US-2 AC-2)
- `test_layer3_exceeds_50_steps_raises_business_error` (US-2 AC-4)
- `test_layer3_plan_size_exceeds_100kb_raises_business_error`
- `test_layer3_valid_plan_passes_all_layers`

**blockedBy**: T204, T400 (needs mock_registry_service)

**Acceptance criteria**: FR-005, FR-006, FR-011, FR-013, FR-017. All 4 acceptance scenarios from US-2 covered. SC-003 (100% of invalid plans caught in test suite).

---

### T504 -- Unit tests for LLM adapter

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_unit.py` (append)

**Description**: Unit tests for LLMAdapter protocol and AnthropicAdapter.

**Key test cases** (~4 tests):
- `test_anthropic_adapter_implements_protocol` -- `isinstance(adapter, LLMAdapter)` is True
- `test_anthropic_adapter_wraps_api_errors_in_llm_call_error` -- mock the anthropic client to raise, verify `LLMCallError`
- `test_anthropic_adapter_wraps_timeout_in_llm_call_error` -- mock timeout
- `test_anthropic_adapter_returns_text_content` -- mock successful response

**blockedBy**: T203

**Acceptance criteria**: FR-004 verified. Error wrapping verified. Protocol compliance verified.

---

## Phase 6: Service Integration Tests

### T600 -- Service tests: happy path and determinism

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_service.py`

**Description**: Integration-level tests for `PlannerService.generate_plan()` with mocked dependencies.

**Key test cases** (~7 tests):
- `test_generate_plan_happy_path` -- valid intent -> PlannerResult with plan, fallback_level=1 (US-1 AC-1, SC-001)
- `test_generate_plan_deterministic_hash` -- same inputs -> same `meta.canonical_hash` (US-1 AC-2, SC-002)
- `test_generate_plan_plan_id_is_ulid` -- plan_id is 26 characters, alphanumeric (FR-015)
- `test_generate_plan_plugins_populated` -- `plan.plugins` contains unique tool IDs from graph (FR-016)
- `test_generate_plan_dry_run_enforced` -- all steps have `dry_run=True` (FR-013)
- `test_generate_plan_context_degraded_flag` -- when ContextRAG returns degraded, `context_degraded=True` (SPEC edge case)
- `test_generate_plan_registry_version_in_result` -- `registry_version` matches catalog version

**blockedBy**: T300, T400

**Acceptance criteria**: SC-001, SC-002 verified. FR-001, FR-013, FR-015, FR-016 verified. US-1 all ACs covered.

---

### T601 -- Service tests: fallback hierarchy

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_service.py` (append)

**Description**: Tests for the 4-level fallback hierarchy.

**Key test cases** (~7 tests):
- `test_fallback_level_2_on_primary_failure` -- primary LLM fails -> fallback model used, `fallback_level=2` (US-3 AC-1)
- `test_fallback_level_2_on_primary_circuit_open` -- primary breaker open -> skip to fallback
- `test_fallback_level_3_on_both_llms_fail` -- both fail -> PlanLibrary template, `fallback_level=3` (US-3 AC-2)
- `test_fallback_level_4_minimal_plan` -- all fail -> minimal safe plan with `system.echo`, `dry_run=True` (US-3 AC-3, SC-004)
- `test_fallback_level_indicator` -- verify `fallback_level` field is correct at each level (US-3 AC-4)
- `test_minimal_plan_structure` -- minimal plan has 1 Fetcher step, `system.echo`, `call="echo"`, `dry_run=True`
- `test_validation_failure_triggers_fallback` -- LLM returns valid response but validator rejects -> falls to next level

**blockedBy**: T300, T400

**Acceptance criteria**: FR-009 verified. SC-004 verified. US-3 all ACs covered.

---

### T602 -- Service tests: concurrent calls and edge cases

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_service.py` (append)

**Description**: Tests for edge cases and concurrent safety.

**Key test cases** (~4 tests):
- `test_empty_entities_still_generates_plan` -- Intent with `entities={}` (SPEC edge case)
- `test_empty_evidence_context_degraded` -- ContextRAG returns empty evidence (SPEC edge case)
- `test_empty_catalog_fallback_to_minimal` -- empty tool catalog -> minimal plan (SPEC edge case)
- `test_concurrent_calls_safe` -- use `asyncio.gather` with 5 concurrent `generate_plan` calls; all return valid results (SPEC edge case: concurrent calls safe)

**blockedBy**: T300, T400

**Acceptance criteria**: All SPEC edge cases covered. Stateless safety for concurrent calls verified.

---

## Phase 7: Contract Tests

### T700 -- Contract tests: GLOBAL_SPEC envelope conformance

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_contract.py`

**Description**: Verify output conforms to GLOBAL_SPEC canonical contracts.

**Key test cases** (~5 tests):
- `test_plan_conforms_to_global_spec_section_2_3` -- generated Plan validates against `shared.schemas.plan.Plan` model
- `test_plan_intent_embedded` -- `plan.intent` matches original Intent
- `test_plan_constraints_scopes_aggregated` -- `plan.constraints.scopes` contains all scopes from used tools (FR-012)
- `test_plan_meta_has_canonical_hash` -- `plan.meta.canonical_hash` is a 64-char hex string (FR-008)
- `test_plan_meta_author_is_planner_at_system` -- `plan.meta.author == "planner@system"`

**blockedBy**: T300, T400

**Acceptance criteria**: GLOBAL_SPEC v2.2 SS2.3 conformance. FR-008, FR-012 cross-validated.

---

### T701 -- Contract tests: HITL gate insertion

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_contract.py` (append)

**Description**: Verify HITL gate_id insertion behavior.

**Key test cases** (~3 tests):
- `test_booker_steps_have_gate_id` -- plan with Booker step has non-null `gate_id` (US-5 AC-1, FR-017)
- `test_readonly_plan_no_gate_id` -- plan with only Fetcher/Analyzer steps has no `gate_id` on any step (US-5 AC-2)
- `test_gate_id_format` -- gate_id follows pattern `gate-[A-Z]` (convention from GLOBAL_SPEC SS7)

**blockedBy**: T300, T400

**Acceptance criteria**: FR-017 verified. US-5 both ACs covered.

---

## Phase 8: Observability Tests

### T800 -- Observability tests: no PII, no credentials, structured logging

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/Planner/tests/test_observability.py`

**Description**: Verify observability safety per LLD SS10.

**Key test cases** (~5 tests):
- `test_no_pii_in_log_output` -- capture log output during `generate_plan()`, verify no entity values (e.g., "Alice"), no constraint values appear in logs
- `test_no_credential_values_in_logs` -- verify no credential_template values or API keys in logs
- `test_structured_log_fields_present` -- verify `component`, `op`, `intent_type`, `plan_id` fields present in log entries
- `test_circuit_state_change_logged` -- verify `circuit_state_change` event logged when breaker transitions
- `test_fallback_triggered_logged` -- verify `fallback_triggered` event logged with `from_level` and `to_level`

**blockedBy**: T300, T400

**Acceptance criteria**: LLD SS10.1 (structured logging), SS10.2 (no PII). Constitution VI (no PII in logs).

---

## Phase 9: Final Verification

### T900 -- Run full test suite and verify CI readiness

**Files**: (no new files)

**Description**: Run `uv run pytest components/Planner/tests/ -v` and verify all tests pass. Run `uv run ruff check components/Planner/` and `uv run ruff format --check components/Planner/` to verify linting and formatting. Verify no import errors.

**blockedBy**: T500, T501, T502, T503, T504, T600, T601, T602, T700, T701, T800

**Acceptance criteria**:
- All tests pass (target ~60 tests per LLD SS12.3)
- Zero ruff violations
- Zero import errors
- Coverage >80% for `components/Planner/`

---

## Task Summary

| Phase | Tasks | Count |
|-------|-------|-------|
| Phase 0: Setup | T000-T001 | 2 |
| Phase 1: Domain Models | T100-T101 | 2 |
| Phase 2: Adapters | T200-T204 | 5 |
| Phase 3: Service + DI | T300-T302 | 3 |
| Phase 4: Fixtures | T400 | 1 |
| Phase 5: Unit Tests | T500-T504 | 5 |
| Phase 6: Service Tests | T600-T602 | 3 |
| Phase 7: Contract Tests | T700-T701 | 2 |
| Phase 8: Observability | T800 | 1 |
| Phase 9: Verification | T900 | 1 |
| **Total** | | **25 tasks** |

**Estimated test count**: ~65 tests (30 unit + 18 service + 8 contract + 5 observability + margin)

---

## Dependencies

### External (from LLD SS11.1)

| Package | Version | Status |
|---------|---------|--------|
| `anthropic` | >=0.18.0 | Already in pyproject.toml |
| `ulid-py` | >=1.1.0 | Already in pyproject.toml (as `ulid-py`) |
| `pydantic` | >=2.0 | Already in pyproject.toml |

No new packages need to be installed.

### Internal Component Dependencies (from LLD SS11.2)

| Component | Service | Status |
|-----------|---------|--------|
| ContextRAG | `ContextRAGService.gather_evidence()` | Merged (PR #9 ancestry) |
| PluginRegistry | `RegistryService.list_catalog()`, `.validate_plan_tools()`, `.get_version()` | Merged (PR #7) |
| PlanLibrary | `PlanService.get_plans_by_intent()` | Merged (early PRs) |

All three dependencies are implemented, merged, and wired in `shared/app.py`.

### Shared Infrastructure

| Module | Usage |
|--------|-------|
| `shared/schemas/intent.py` | `Intent` model -- input contract |
| `shared/schemas/plan.py` | `Plan`, `PlanStep`, `PlanConstraints`, `PlanMeta` -- output contract |
| `shared/schemas/evidence.py` | `EvidenceItem` -- evidence from ContextRAG |
| `shared/app.py` | Lifespan DI wiring (to be modified in T301) |
| `shared/dependencies.py` | `get_planner_service()` (to be added in T302) |

---

## Architectural Considerations

### Blast Radius (LLD SS3.2, SS13.1)

- **If Planner fails**: No plan generated -> Orchestration Layer cannot proceed. However, the 4-level fallback hierarchy means total failure is nearly impossible (Level 4 is deterministic with no external deps).
- **Containment**: Circuit breakers on each LLM model, graceful degradation through fallback levels, stateless design means crash-restart is safe.
- **Dependency isolation**: ContextRAG never raises (returns empty ContextResult). PluginRegistry failure -> empty catalog -> Level 4 minimal plan.

### Determinism (LLD SS13.2)

- **Preview safety**: Same inputs -> same plan hash -> same signature when LLM model is pinned and temperature=0.
- **Canonical hash**: Provides post-hoc verification of plan identity.
- **Caveat**: LLM determinism is approximate. Model updates may produce different outputs. The canonical_hash provides detection, not prevention.

### State Management (LLD SS13.3)

- **Fully stateless**: No database tables, no persistent queues, no background tasks.
- **Circuit breaker state**: In-memory, per-process. On restart, all breakers reset to CLOSED (acceptable -- brief retry storm, then stabilizes).

### File Structure (Final)

```
components/Planner/
  __init__.py                          # T001, T101
  LLD.md                               # existing
  domain/
    __init__.py                        # T001
    models.py                          # T100
  adapters/
    __init__.py                        # T001
    plan_hasher.py                     # T200
    circuit_breaker.py                 # T201
    prompt_builder.py                  # T202
    llm_adapter.py                     # T203
    plan_validator.py                  # T204
  service/
    __init__.py                        # T001
    planner_service.py                 # T300
  tests/
    __init__.py                        # T001
    conftest.py                        # T400
    test_unit.py                       # T500-T504
    test_service.py                    # T600-T602
    test_contract.py                   # T700-T701
    test_observability.py              # T800
```

### Modified Shared Files

- `shared/app.py` -- T301 (add planner_service initialization)
- `shared/dependencies.py` -- T302 (add get_planner_service)
