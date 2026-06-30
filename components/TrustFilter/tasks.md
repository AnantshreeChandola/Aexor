# Tasks: TrustFilter (Trust Boundary Pipeline)

**Created**: 2026-04-09
**Branch**: feat/trust-boundary-pipeline
**SPEC**: specs/037-trust-boundary-pipeline/spec.md
**LLD**: components/TrustFilter/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
This is a large cross-component feature. The TrustFilter component itself is new,
but the feature also modifies shared schemas, Planner (plan validator + prompt builder),
ExecuteOrchestrator (sanitizer dispatch + Tier 1 schema validation), and PolicyEngine
(trust verdict rules).

Phases 0-6 cover the new TrustFilter component.
Phases 7-10 cover cross-component modifications (shared schemas, Planner, ExecuteOrchestrator, PolicyEngine).
Phase 11 covers end-to-end integration tests.

---

## Phase 0: Setup and Scaffolding

### Scaffold the TrustFilter component directory

- [ ] [T000] Create component directory structure and `__init__.py` files
  - `components/TrustFilter/__init__.py`
  - `components/TrustFilter/api/__init__.py`
  - `components/TrustFilter/service/__init__.py`
  - `components/TrustFilter/domain/__init__.py`
  - `components/TrustFilter/domain/prompts/` (directory)
  - `components/TrustFilter/adapters/__init__.py`
  - `components/TrustFilter/schemas/` (directory)
  - `components/TrustFilter/tests/__init__.py`
  - `components/TrustFilter/tests/fixtures/` (directory)
  - `components/TrustFilter/notes/` (directory)
  - No new Python packages required (anthropic, pydantic already in pyproject.toml)

- [ ] [T001] Verify external service access
  - Confirm `ANTHROPIC_API_KEY` environment variable is documented for S2 Haiku judge
  - Confirm `anthropic>=0.49.0` is already in `pyproject.toml`
  - Confirm `pydantic>=2.7` is already in `pyproject.toml`

---

## Phase 1: Shared Schemas (Foundation)

### AC: FR-012, FR-013, FR-014, FR-015, FR-016 -- New and modified shared schemas

These schemas are consumed by TrustFilter, ExecuteOrchestrator, Planner (validator), and PolicyEngine.
They must be created first because all downstream phases depend on them.

- [ ] [T100] Create `shared/schemas/trust.py` -- TrustVerdict model (FR-013)
  - Define `Verdict = Literal["clean", "suspicious", "injection"]`
  - Define `TrustVerdict(BaseModel)` with fields: `verdict`, `confidence` (0.0-1.0), `reason` (max_length=512), `stage` (Literal["s1", "s2", "s1_only_degraded"])
  - Reference: LLD Section 5.1

- [ ] [T101] Create `shared/schemas/sanitized_payload.py` -- SanitizedPayload model (FR-012)
  - Define `SanitizedPayload(BaseModel)` with fields: `original_shape` (Any), `stripped_fields` (list[str]), `trust_verdict` (Verdict), `confidence` (0.0-1.0), `scanner_degraded` (bool, default=False), `scanner_version` (str), `scanned_at` (str, ISO-8601)
  - Import `Verdict` from `shared/schemas/trust.py`
  - Reference: LLD Section 5.1

- [ ] [T102] Create `shared/schemas/reasoner_outputs/__init__.py` -- Schema registry (FR-014)
  - Export `SCHEMA_REGISTRY: dict[str, type[BaseModel]]` mapping string keys to Pydantic classes
  - Initial entries: `"slot_proposal_v1"`, `"free_slots_v1"`, `"flight_recommendation_v1"`, `"email_summary_v1"`, `"freebusy_sanitized_v1"`

- [ ] [T103] Create `shared/schemas/reasoner_outputs/slot_proposal.py` -- SlotProposalV1
  - Define Pydantic model with fields: `proposed_start` (str, ISO datetime), `proposed_end` (str, ISO datetime), `has_conflict` (bool), `conflicts` (list[str]), `reason` (str)
  - This is the schema that gets validated for Tier 1 reasoners with `output_schema_ref: "slot_proposal_v1"`

- [ ] [T104] Create `shared/schemas/reasoner_outputs/free_slots.py` -- FreeSlotsV1
  - Define Pydantic model for free-slot listing output (aligned with current Reasoner output shape)
  - Fields: `recommended_time` (str), `has_conflict` (bool), `conflicts` (list[str]), `free_slots` (list of slot objects with start/end/label), `reason` (str)

- [ ] [T105] Create `shared/schemas/reasoner_outputs/flight_recommendation.py` -- FlightRecommendationV1
  - Define Pydantic model for flight recommendation output

- [ ] [T106] Create `shared/schemas/reasoner_outputs/email_summary.py` -- EmailSummaryV1
  - Define Pydantic model for email summary output

- [ ] [T107] Create `shared/schemas/reasoner_outputs/freebusy_sanitized.py` -- FreeBusySanitizedV1
  - Define Pydantic model for free/busy sanitized data output

- [ ] [T108] Modify `shared/schemas/plan.py` -- Add sanitizer type and Guard role (FR-015)
  - Add `"sanitizer"` to `PlanStep.type` Literal: change from `Literal["api", "llm_reasoning", "policy_check"]` to `Literal["api", "llm_reasoning", "policy_check", "sanitizer"]`
  - Add `"Guard"` to `PlanStep.role` Literal: change from `Literal["Fetcher", "Analyzer", "Watcher", "Resolver", "Booker", "Notifier", "Reasoner"]` to include `"Guard"`
  - Backward-compatible: default for type remains `"api"`

- [ ] [T109] Modify `shared/schemas/policy.py` -- Add TrustVerdictRule and extend PolicyRule (FR-016)
  - Define `TrustVerdictRule(BaseModel)` with fields: `verdict` (Verdict from trust.py), `action` (Literal["require_approval", "block"]), `roles` (list[str], optional scope filter), `enabled` (bool, default=True)
  - Add `trust_verdict_rules: list[TrustVerdictRule] = Field(default_factory=list)` to `PolicyRule`
  - Backward-compatible: empty list default means existing policies are unaffected

- [ ] [T110] Write schema tests for all new shared schemas
  - `tests/test_shared_schemas_trust.py` -- validate TrustVerdict construction, field constraints, Verdict enum
  - `tests/test_shared_schemas_sanitized_payload.py` -- validate SanitizedPayload, field constraints, serialization
  - `tests/test_shared_schemas_reasoner_registry.py` -- validate SCHEMA_REGISTRY contains all 5 entries, each is a BaseModel subclass
  - `tests/test_shared_schemas_plan_extension.py` -- validate PlanStep accepts `type="sanitizer"` and `role="Guard"`; existing plans with `type="api"` still validate
  - `tests/test_shared_schemas_policy_extension.py` -- validate TrustVerdictRule model; PolicyRule with `trust_verdict_rules` serializes correctly; empty list default works

---

## Phase 2: TrustFilter Domain Layer

### AC: FR-003 (S1 rules), FR-006/FR-007/FR-010 (tree walker), domain errors

- [ ] [T200] Create `components/TrustFilter/domain/errors.py` -- Domain error hierarchy (LLD Section 5.3)
  - `TrustFilterError(Exception)` with `error_type: str`
  - `LoadBearingFlaggedError(TrustFilterError)` with `field_path`, `rule_id`
  - `PayloadTooLargeError(TrustFilterError)` with `size_bytes`
  - `PayloadDepthExceededError(TrustFilterError)`
  - `MalformedInputError(TrustFilterError)`
  - `S1InternalError(TrustFilterError)` -- internal only, never propagated
  - `HaikuUnreachableError(TrustFilterError)` -- raised by S2 adapter
  - All errors must have custom `__str__` that omits payload content (privacy guarantee)

- [ ] [T201] Create `components/TrustFilter/domain/models.py` -- Internal domain models (LLD Section 5.2)
  - `ScanContext(BaseModel)`: plan_id, step_number, trace_id, load_bearing_fields (set[str]), strict_mode (bool)
  - `RuleHit(BaseModel)`: field_path, rule_id, severity (Literal["low", "med", "high"]), matched_substring (str) -- matched_substring is NEVER logged
  - `S1Result(BaseModel)`: verdict, confidence, hits (list[RuleHit]), fields_scanned (int)
  - `S2Result(BaseModel)`: verdict, confidence, reason, degraded (bool)

- [ ] [T202] Create `components/TrustFilter/domain/regex_rules.py` -- S1 rule pack definitions (FR-003, LLD Section 6.1)
  - Define `Rule(BaseModel)`: rule_id, pattern (str), flags (int), severity, category
  - Define `RulePack(BaseModel)`: rules (list[Rule]), version (str), sha256 (str)
  - Define `load_default_rule_pack() -> RulePack` -- loads frozen JSON from file or builds from code
  - Rule categories per LLD Section 6.1 table:
    - HIGH: role-switching phrases (`ignore_previous_instructions`, `you_are_now_x`, `new_system_prompt`), instruction delimiters (`instructions_tag`, `system_colon_prefix`, `assistant_colon_prefix`), fake tool-call syntax (`fake_tool_use_xml`, `fake_function_call_json`)
    - MED: zero-width characters (`zero_width_space`, `zero_width_joiner`, `byte_order_mark`), homoglyphs/RTL (`rtl_override`, `cyrillic_lookalike_a_e_o`), base64/hex blobs (`base64_blob_gt_256b`, `hex_blob_gt_256b`)
    - LOW: excessive markdown link density (`md_link_density_gt_10pct`), suspicious URL in description (`suspicious_url_in_description`)

- [ ] [T203] Create `components/TrustFilter/domain/tree_walker.py` -- JSON tree traversal (FR-006, FR-007, LLD Section 6.3)
  - Define constants: `MAX_PAYLOAD_BYTES = 1_048_576` (1MB), `MAX_DEPTH = 32`, `ALWAYS_SCAN_FIELD_NAMES`, `STRUCTURED_TYPE_HINTS`
  - `JsonTreeWalker` class with methods:
    - `walk(payload, *, depth=0, path="") -> Iterator[tuple[str, str]]` -- yields (dotted_path, string_value) for leaf strings
    - `apply_strips(payload, stripped_paths: set[str]) -> Any` -- returns copy with stripped paths replaced by `"[redacted: injection]"`
  - Structured-field detection: skip strings whose field name is in known-structured set (*_id, *_at, email, url, uuid, timezone, timestamp) OR parses as ISO-8601, UUID, RFC-5322, RFC-3986, or pure number-as-string
  - Fields in `ALWAYS_SCAN_FIELD_NAMES` are scanned regardless of structured heuristics
  - Lists indexed as `parent[i].child`; dicts joined with dots `parent.child`
  - Enforce depth limit via `PayloadDepthExceededError`

- [ ] [T204] Create `components/TrustFilter/domain/prompts/s2_judge_v1.txt` -- Locked Haiku system prompt (FR-004)
  - Full prompt text as specified in LLD Section 6.2
  - Instruct model to classify data as clean/suspicious/injection
  - Instruct model that user message is `data_to_classify` (structural defense)
  - Instruct model to respond ONLY with JSON `{"verdict": "...", "confidence": 0.0-1.0, "reason": "..."}`
  - Instruct model: no tools, no browsing, no output other than JSON

- [ ] [T205] Write unit tests for domain errors
  - `components/TrustFilter/tests/test_errors.py`
  - Test each error type has correct `error_type` string attribute
  - Test `LoadBearingFlaggedError` stores `field_path` and `rule_id`
  - Test `PayloadTooLargeError` stores `size_bytes`
  - Test all error `__str__` methods do not contain raw payload content

- [ ] [T206] Write unit tests for domain models
  - Tests in `components/TrustFilter/tests/test_domain_models.py`
  - Test `ScanContext` validates load_bearing_fields as set
  - Test `RuleHit` field constraints
  - Test `S1Result` / `S2Result` verdict values and confidence ranges

- [ ] [T207] Write unit tests for regex rules (FR-003, SC-004)
  - `components/TrustFilter/tests/test_regex_scanner.py`
  - Test each HIGH-severity rule category with known injection phrases
  - Test each MED-severity rule category (zero-width chars, homoglyphs, RTL, base64/hex blobs)
  - Test each LOW-severity rule category (markdown links, suspicious URLs)
  - Test aggregation logic: any HIGH -> injection/0.95; >=2 MED -> injection/0.85; 1 MED -> suspicious/0.60; only LOW -> clean/0.70; no hits -> clean/0.99
  - Test against `tests/fixtures/injection_patterns_50.json` -- require >= 95% detection rate (SC-004)
  - Test against `tests/fixtures/benign_tool_responses_20.json` -- require 0% false positive rate

- [ ] [T208] Write unit tests for tree walker (FR-006, FR-007, FR-010)
  - `components/TrustFilter/tests/test_tree_walker.py`
  - Test nested dict/list traversal with correct dotted paths (e.g. `a.b[0].note`)
  - Test depth limit enforcement (depth=33 raises `PayloadDepthExceededError`)
  - Test structured field skipping (ISO dates, UUIDs, emails, URLs, `*_id` fields)
  - Test `ALWAYS_SCAN_FIELD_NAMES` override (description, notes, body, etc. always scanned even if they look structured)
  - Test `apply_strips` produces shape-preserving output with `[redacted: injection]` at correct paths
  - Test empty payload (`{}`, `None`) produces no yields
  - Test list-at-root payload
  - Test path formatting for arrays: `parent[0].child`

---

## Phase 3: TrustFilter Adapters (S1 + S2)

### AC: FR-002, FR-003, FR-004, FR-005 -- RegexScanner and HaikuJudge

- [ ] [T300] Create `components/TrustFilter/adapters/regex_scanner.py` -- S1 scanner (FR-003, LLD Section 6.1)
  - `RegexScanner` class:
    - `__init__(self, rule_pack: RulePack | None = None)` -- loads default rule pack if none provided; compiles all regexes
    - `scan_string(self, value: str, field_path: str) -> list[RuleHit]` -- returns all rule hits on a single string
    - `aggregate(self, hits: list[RuleHit]) -> tuple[Verdict, float]` -- aggregation per LLD rules
  - Must catch internal regex errors and raise `S1InternalError` (not crash)
  - Import rule pack from `domain/regex_rules.py`

- [ ] [T301] Create `components/TrustFilter/adapters/haiku_judge.py` -- S2 LLM-as-judge (FR-004, FR-005, LLD Section 6.2)
  - Define `HaikuJudgeAdapter(Protocol)` -- runtime-checkable protocol for dependency injection
  - Define `HaikuJudgeAdapterImpl` class implementing the protocol:
    - `MODEL = "claude-haiku-4-5-20251001"`
    - `LOCKED_SYSTEM_PROMPT: Final[str]` loaded from `domain/prompts/s2_judge_v1.txt` at import time
    - `__init__(self, api_key: str | None = None)` -- creates `anthropic.AsyncAnthropic` client
    - `classify(self, payload_text: str, s1_hits: list[str], timeout_s: float = 3.0) -> S2Result`
    - Uses `asyncio.wait_for()` with `timeout_s`
    - Uses `tools=[]` (CRITICAL: judge must never invoke tools)
    - Uses `temperature=0.0`
    - Uses `max_tokens=256`
    - Wraps payload as `{"data_to_classify": "<payload>"}` inside user message (structural defense against meta-injection)
    - On `asyncio.TimeoutError` or `anthropic.APIError`, raises `HaikuUnreachableError`
    - Parses response as JSON `{verdict, confidence, reason}`, returns `S2Result`

- [ ] [T302] Write S2 adapter integration tests with mocks
  - `components/TrustFilter/tests/test_haiku_judge.py`
  - Mock `anthropic.AsyncAnthropic` client
  - Test happy path: mock returns valid JSON verdict -> S2Result
  - Test timeout: mock raises `asyncio.TimeoutError` -> `HaikuUnreachableError`
  - Test API error: mock raises `anthropic.APIError` -> `HaikuUnreachableError`
  - Test rate limit (429): mock raises appropriate error -> `HaikuUnreachableError`
  - Test malformed response: mock returns non-JSON -> should handle gracefully
  - Verify `tools=[]` is always passed
  - Verify `temperature=0.0` is always passed
  - Verify payload is wrapped in `data_to_classify` field

---

## Phase 4: TrustFilter Service Layer (FilterService Orchestrator)

### AC: FR-001, FR-002, FR-005, FR-008, FR-009, FR-010, FR-011

- [ ] [T400] Create `components/TrustFilter/service/filter_service.py` -- FilterService (LLD Sections 6.4, 7.1-7.4)
  - `FilterService` class:
    - `SCANNER_VERSION: Final[str] = "trust_filter@0.1.0"`
    - `__init__(self, regex_scanner, haiku_adapter, tree_walker=None)` -- DI constructor
    - `async scan(self, raw_payload, *, load_bearing_fields, strict_mode, plan_id, step_number, trace_id) -> SanitizedPayload` -- main pipeline
  - Pipeline implementation:
    - Guard: `_check_payload_limits()` -- check JSON-serializable, size <= 1MB, depth check deferred to walker
    - S1: `_run_s1()` -- collect hits via walker + scanner; catch `S1InternalError` -> degrade (return empty hits)
    - Early exit: if no S1 hits -> return clean payload, skip S2 for latency
    - S2: `_run_s2()` -- call Haiku; on `HaikuUnreachableError` -> degrade to S1-only + `scanner_degraded=true`
    - Combine: `_combine_verdicts()` -- pick more paranoid; paranoia order: clean < suspicious < injection; same verdict -> average confidence
    - Strip: `_select_fields_to_strip()` -- injection or suspicious+strict: strip med/high hit fields; raise `LoadBearingFlaggedError` if load-bearing field is in strip set
    - Build: `_build_payload()` -- apply strips via walker, construct `SanitizedPayload`
  - Structured logging per LLD Section 10.1 (never log payload content or matched substrings)

- [ ] [T401] Create `create_filter_service()` factory function in same file
  - Accept optional `haiku_adapter` and `regex_scanner` for DI/testing
  - Default: `HaikuJudgeAdapterImpl()` reading `ANTHROPIC_API_KEY`, `RegexScanner()`
  - Returns configured `FilterService`

- [ ] [T402] Write FilterService unit tests -- verdict combiner
  - `components/TrustFilter/tests/test_filter_service.py` (verdict combiner subset)
  - Test `_combine_verdicts` paranoia ordering: injection > suspicious > clean
  - Test S2=None (degraded): S1 alone decides
  - Test same verdict -> average confidence
  - Test S2 more paranoid than S1 -> S2 wins
  - Test S1 more paranoid than S2 -> S1 wins

- [ ] [T403] Write FilterService integration tests -- full pipeline
  - `components/TrustFilter/tests/test_filter_service.py` (integration subset)
  - Test clean payload -> verdict=clean, no stripped fields, scanner_degraded=false
  - Test injection payload -> verdict=injection, fields stripped, scanner_degraded=false
  - Test S2 unreachable -> verdict from S1 only, scanner_degraded=true (FR-005, SC-006)
  - Test load-bearing field flagged -> `LoadBearingFlaggedError` raised (FR-009)
  - Test oversized payload (> 1MB) -> `PayloadTooLargeError` (edge case)
  - Test malformed payload (not JSON) -> `MalformedInputError` (edge case)
  - Test deeply nested payload (depth > 32) -> `PayloadDepthExceededError` (edge case)
  - Test empty payload (`{}`) -> verdict=clean, no stripped fields (edge case)
  - Test null payload -> verdict=clean, no stripped fields (edge case)
  - Test strict_mode=true: suspicious fields also stripped
  - Test strict_mode=false: suspicious fields pass through
  - Test shape preservation: `original_shape` matches raw payload minus stripped fields (FR-010)
  - Test `ALWAYS_SCAN_FIELD_NAMES` are scanned even below size threshold (FR-007)

- [ ] [T404] Write FilterService S1-only latency test
  - Test S1-only fallback completes in <= 200ms on mocked adapter (SC-006)

---

## Phase 5: TrustFilter Test Fixtures and Contract Tests

### AC: SC-004, SC-005, SC-011

- [ ] [T500] Create `components/TrustFilter/tests/fixtures/injection_patterns_50.json`
  - 50 known prompt-injection variants covering all S1 rule categories
  - Include: role-switching phrases, instruction delimiters, fake tool-call syntax, zero-width characters, homoglyphs, RTL overrides, base64 blobs, mixed attacks
  - Reference: SC-004 (>= 95% detection rate)

- [ ] [T501] Create `components/TrustFilter/tests/fixtures/benign_tool_responses_20.json`
  - 20 benign MCP tool responses (calendar events, email metadata, flight data, etc.)
  - Must include strings with substrings that could false-positive (e.g. `system_calendar_id`, dates, URLs)
  - Reference: SC-005 (0% false positive rate)

- [ ] [T502] Create `components/TrustFilter/tests/fixtures/novel_injections_20.json`
  - 20 held-out novel injection payloads for S2 recall testing
  - These are NOT in the S1 rule pack -- designed to test S2 Haiku classification
  - Reference: SC-005 (>= 90% detection by S2)

- [ ] [T503] Write contract tests against SanitizedPayload schema
  - `components/TrustFilter/tests/test_contract.py`
  - Validate output of `FilterService.scan()` conforms to `shared/schemas/sanitized_payload.py`
  - Test all required fields present
  - Test `trust_verdict` is valid Verdict enum value
  - Test `confidence` is in [0.0, 1.0]
  - Test `stripped_fields` are valid dotted paths
  - Test `scanner_version` matches expected format `trust_filter@<semver>`
  - Test `scanned_at` is valid ISO-8601

- [ ] [T504] Write contract test for unknown MCP tool (SC-011, US5)
  - In `components/TrustFilter/tests/test_contract.py`
  - Pass an arbitrary nested JSON dict (simulating unknown tool) with injection strings in deeply-nested fields
  - Verify output preserves original shape, strips flagged fields, populates `stripped_fields` with dotted paths
  - Verify no per-tool schema needed

---

## Phase 6: TrustFilter Observability and Safety

### AC: NFR deltas -- logging, metrics, privacy

- [ ] [T600] Write observability tests
  - `components/TrustFilter/tests/test_observability.py`
  - Test structured log events emitted with correct fields: `component`, `op`, `plan_id`, `step`, `trace_id`
  - Test that NO log record contains payload content or `matched_substring` values
  - Test `scanner_version` format: `trust_filter@<semver>+<rulepack_sha>+<prompt_sha>`
  - Test metrics emitted with correct labels (mock metrics collector)

- [ ] [T601] Create `components/TrustFilter/schemas/response.normalized.json` -- JSON Schema mirror
  - JSON Schema representation of `SanitizedPayload` for documentation/external consumers
  - Validate it matches the Pydantic model structure

- [ ] [T602] Create `components/TrustFilter/notes/rule_pack_governance.md`
  - Document rule pack ownership, review process, versioning strategy
  - Document that rule pack changes require PR review + scanner_version bump

---

## Phase 7: Shared Schema Modifications (plan.py, policy.py)

### AC: FR-015, FR-016

Note: T108 and T109 in Phase 1 create the actual schema changes. This phase is for their
cross-component validation tests.

- [ ] [T700] Write backward-compatibility tests for plan.py modifications
  - In `tests/test_shared_schemas_plan_extension.py` (created in T110)
  - Confirm all existing plan fixtures still validate after adding `"sanitizer"` to type enum and `"Guard"` to role enum
  - Confirm `type="api"` default still works (backward compat)
  - Confirm existing plans without sanitizer steps pass validation

- [ ] [T701] Write backward-compatibility tests for policy.py modifications
  - In `tests/test_shared_schemas_policy_extension.py` (created in T110)
  - Confirm existing PolicyRule instances without `trust_verdict_rules` still validate (empty list default)
  - Confirm serialization/deserialization round-trip works with and without trust_verdict_rules

---

## Phase 8: Plan Validator Modifications (Planner Component)

### AC: FR-017 (Rule E), FR-018 (Rule F), FR-019 (Rule G), FR-020 (Rule H), FR-021, US2

- [ ] [T800] Modify `components/Planner/adapters/plan_validator.py` -- Add Rule E (FR-017)
  - In `_validate_business_rules()`, after existing hybrid execution rules
  - Rule E: Reject any `llm_reasoning` step with `trust_level="untrusted_input"` that has `reasoning_config.output_schema_ref: null`
  - Hard reject with `layer="business_rules"`, message citing Rule E
  - Exact location: after line 260 (after context_from validation block)

- [ ] [T801] Modify `components/Planner/adapters/plan_validator.py` -- Add Rule F (FR-018)
  - Rule F: Reject any plan where an `llm_reasoning` step's `context_from` transitively references an `api` step without an intervening `sanitizer` step in the dependency path
  - Implementation: for each `llm_reasoning` step, walk `context_from` references backward through the DAG; if any referenced step (or its transitive ancestors via `context_from + after`) is `type="api"` and there is no `type="sanitizer"` step between them, reject
  - Upgrade the current soft-log at lines 266-285 (Rule A) to be a hard reject for the case where no sanitizer intervenes
  - Pure-API plans (no `llm_reasoning` steps) are exempt (FR-037)
  - Error metadata: `layer="business_rules"`, message citing Rule F with the offending step numbers
  - The existing Rule A log at lines 266-285 should be preserved for Tier 2 Reasoner auditing (they do have intervening sanitizers), but the case where there is NO intervening sanitizer must now hard-reject

- [ ] [T802] Modify `components/Planner/adapters/plan_validator.py` -- Add Rule G (FR-019)
  - Rule G: Reject any plan where a `sanitizer` step has `can_spawn=true` or has a `trust_level` value set
  - Hard reject with `layer="business_rules"`, message citing Rule G

- [ ] [T803] Modify `components/Planner/adapters/plan_validator.py` -- Add Rule H (FR-020)
  - Rule H: Reject any plan where an `llm_reasoning` step with `trust_level="untrusted_input"` has `can_spawn=true` or references real MCP tools (i.e. step.uses is not a system/internal pseudo-tool)
  - Hard reject with `layer="business_rules"`, message citing Rule H

- [ ] [T804] Modify `components/Planner/adapters/plan_validator.py` -- Add Rule E' (FR-021)
  - Validate that `output_schema_ref` value on Tier 1 reasoners is a key present in `SCHEMA_REGISTRY`
  - Import `SCHEMA_REGISTRY` from `shared/schemas/reasoner_outputs`
  - Hard reject if key not found in registry
  - This can be combined with Rule E validation logic

- [ ] [T805] Modify `components/Planner/adapters/plan_validator.py` -- Update tool existence check
  - Sanitizer steps use pseudo-tool `trust_filter.scan` which is not in the MCP tool catalog
  - Update tool existence check (around line 175) to exclude `sanitizer` type steps (similar to existing exclusion for `llm_reasoning` and `policy_check`)

- [ ] [T806] Write unit tests for Rules E, F, G, H
  - `components/Planner/tests/test_unit.py` (extend existing file)
  - Test Rule E: plan with Tier 1 reasoner missing `output_schema_ref` -> rejected, error cites Rule E
  - Test Rule E: plan with Tier 1 reasoner having valid `output_schema_ref` -> accepted
  - Test Rule E: Tier 2 reasoner with `output_schema_ref: null` -> accepted (Tier 2 is exempt, FR-038)
  - Test Rule F: plan with api step -> llm_reasoning (context_from) without sanitizer -> rejected, error cites Rule F
  - Test Rule F: plan with api step -> sanitizer step -> llm_reasoning (context_from) -> accepted
  - Test Rule F: pure-API plan (all type=api, no llm_reasoning) -> accepted (FR-037)
  - Test Rule F: transitive context_from chain: api -> sanitizer -> intermediate step -> llm_reasoning -> accepted
  - Test Rule G: sanitizer step with `can_spawn=true` -> rejected, error cites Rule G
  - Test Rule G: sanitizer step with `trust_level` set -> rejected, error cites Rule G
  - Test Rule G: sanitizer step with `can_spawn=false` and no trust_level -> accepted
  - Test Rule H: Tier 1 reasoner with `can_spawn=true` -> rejected, error cites Rule H
  - Test Rule H: Tier 1 reasoner referencing MCP tools -> rejected, error cites Rule H
  - Test FR-021: Tier 1 reasoner with `output_schema_ref` not in SCHEMA_REGISTRY -> rejected
  - Test backward compat: existing plans with no llm_reasoning steps -> pass unchanged (SC-009)

---

## Phase 9: ExecuteOrchestrator Modifications

### AC: FR-024, FR-025, FR-026, FR-027, US4

- [ ] [T900] Modify `components/ExecuteOrchestrator/domain/models.py` -- Add trust metadata to ExecutionContext
  - Add `sanitizer_verdicts: dict[int, str] = {}` to `ExecutionContext.__init__` -- maps step number to verdict string
  - Add `sanitizer_degraded: bool = False` to `ExecutionContext.__init__` -- True if any sanitizer step had scanner_degraded=true

- [ ] [T901] Modify `components/ExecuteOrchestrator/service/execute_service.py` -- Add sanitizer dispatcher branch (FR-024)
  - In `_execute_step()` method, add a new branch before the `else` clause (around line 386):
    ```
    elif step.type == "sanitizer":
        result = await self._execute_sanitizer_step(step, ctx, request)
    ```
  - Implement `_execute_sanitizer_step()` method:
    - Resolve upstream payload from `context_from` step results
    - Call `self._filter_service.scan()` with args from step (load_bearing_fields, strict_mode)
    - Catch `LoadBearingFlaggedError` -> return `StepResult(status="failed", error_type="load_bearing_field_flagged")`
    - Catch `PayloadTooLargeError`, `PayloadDepthExceededError`, `MalformedInputError` -> return `StepResult(status="failed", error_type=e.error_type)`
    - On success: propagate trust metadata into `ctx.sanitizer_verdicts[step.step]` and `ctx.sanitizer_degraded |= sanitized.scanner_degraded`
    - Return `StepResult(status="completed", result=sanitized.model_dump())`

- [ ] [T902] Modify `components/ExecuteOrchestrator/service/execute_service.py` -- Add FilterService to constructor and factory
  - Add `filter_service: Any | None = None` parameter to `ExecuteService.__init__`
  - Store as `self._filter_service`
  - Update `create_execute_service()` factory to accept optional `filter_service` parameter

- [ ] [T903] Modify `components/ExecuteOrchestrator/service/execute_service.py` -- Tier 1 schema validation (FR-025)
  - In `_execute_reasoning_step()`, after the LLM returns (after line 753):
  - For `trust_level="untrusted_input"` steps with `reasoning_config.output_schema_ref`:
    - Import `SCHEMA_REGISTRY` from `shared/schemas/reasoner_outputs`
    - Look up the schema class by `output_schema_ref`
    - Attempt `schema_class.model_validate(parsed_output)` on the extracted JSON
    - On validation failure: raise `StepExecutionError` with `error_type="schema_validation_failed"`; do NOT fall back to intent-based guess (replaces lines 787-799 for Tier 1 only)
  - For `trust_level="trusted"` (Tier 2): behavior unchanged -- existing fallback path preserved (FR-038)

- [ ] [T904] Modify `components/ExecuteOrchestrator/service/execute_service.py` -- Propagate trust metadata to PolicyEngine (FR-026)
  - After sanitizer step execution, ensure `ctx.sanitizer_verdicts` and `ctx.sanitizer_degraded` are passed into PolicyEngine evaluation calls
  - This may involve modifying `_handle_spawn()` to include trust metadata in the `SpawnRequest` or in PolicyEngine evaluation context
  - FR-027: Do NOT modify `_build_messages` or `_summarize_context` -- sanitization is upstream

- [ ] [T905] Write sanitizer dispatch integration tests
  - `components/ExecuteOrchestrator/tests/test_sanitizer_dispatch.py` (NEW file)
  - Test sanitizer step dispatch calls FilterService.scan() with correct args
  - Test sanitizer step failure (LoadBearingFlaggedError) -> StepResult(status="failed", error_type="load_bearing_field_flagged")
  - Test sanitizer step success -> StepResult with SanitizedPayload in result
  - Test trust metadata propagation: after sanitizer step, verify ctx.sanitizer_verdicts populated
  - Test ctx.sanitizer_degraded is OR-ed across multiple sanitizer steps

- [ ] [T906] Write Tier 1 schema validation tests
  - `components/ExecuteOrchestrator/tests/test_trust_tiers.py` (extend existing file)
  - Test Tier 1 reasoner with valid output -> schema validates, step succeeds (US4 AC-2)
  - Test Tier 1 reasoner with missing required field -> step fails with `schema_validation_failed`, no fallback (US4 AC-1, SC-010)
  - Test Tier 1 reasoner with malformed JSON -> step fails with `schema_validation_failed`
  - Test Tier 2 reasoner with `output_schema_ref: null` -> step succeeds, no schema validation (US4 AC-3, FR-038)
  - Test backward compat: Tier 2 reasoner with existing fallback behavior unchanged

---

## Phase 10: PolicyEngine Modifications

### AC: FR-028, FR-029, FR-030, FR-031

- [ ] [T1000] Modify `shared/schemas/policy.py` -- Already done in T109
  - (Dependency reminder: T109 adds `TrustVerdictRule` and `trust_verdict_rules` to `PolicyRule`)

- [ ] [T1001] Modify `components/PolicyEngine/service/policy_service.py` -- Add trust verdict evaluation (FR-028, FR-029, FR-030)
  - Add new method `evaluate_trust_verdicts(self, step_dict: dict, ancestor_verdicts: dict[int, str], scanner_degraded: bool, policy_rule: PolicyRule) -> PolicyDecision`
  - Walk ancestor sanitizer steps and evaluate `trust_verdict_rules` from the policy:
    - FR-029 (hardcoded defaults): if any ancestor has `trust_verdict="injection"` OR `scanner_degraded=true` -> `requires_approval=true`
    - FR-030 (configurable): if policy has trust_verdict_rules for `suspicious` with `require_approval` action -> apply; off by default
    - FR-031: if PolicyEngine demands a gate but step has no `gate_id` -> return decision with `allowed=false`, reason `"requires_approval_but_no_gate"`
  - This method is called by ExecuteOrchestrator before executing steps that follow sanitizer steps

- [ ] [T1002] Modify `components/PolicyEngine/service/policy_service.py` -- Integrate trust verdict eval into evaluate_spawn
  - When evaluating spawned steps, also check ancestor trust verdicts if available in the request
  - May require extending `SpawnRequest` in `components/PolicyEngine/domain/models.py` with `ancestor_verdicts: dict[int, str] = {}` and `scanner_degraded: bool = False`

- [ ] [T1003] Write PolicyEngine trust verdict rule tests
  - `components/PolicyEngine/tests/test_trust_rules.py` (NEW file)
  - Test: ancestor with `verdict=injection` -> `requires_approval=true` (FR-029)
  - Test: ancestor with `scanner_degraded=true` -> `requires_approval=true` (FR-029)
  - Test: ancestor with `verdict=suspicious` + no explicit rule -> no escalation (FR-030 default)
  - Test: ancestor with `verdict=suspicious` + explicit `TrustVerdictRule(verdict="suspicious", action="require_approval")` -> `requires_approval=true` (FR-030)
  - Test: `verdict=clean`, no degradation -> no change to approval requirement
  - Test: requires_approval but no gate_id on step -> decision includes `requires_approval_but_no_gate` (FR-031)
  - Test backward compat: PolicyRule without `trust_verdict_rules` behaves identically to pre-feature behavior

---

## Phase 11: Planner Prompt Builder Modifications

### AC: FR-022, FR-023

- [ ] [T1100] Modify `components/Planner/adapters/prompt_builder.py` -- Update system prompt (FR-022)
  - Add instruction to the system prompt that the LLM MUST insert a `sanitizer` step (type="sanitizer", role="Guard", uses="trust_filter.scan") after every `api` step whose output flows into any `llm_reasoning` step
  - Add the sanitizer step schema to the output format documentation in the prompt
  - Add note: sanitizer steps have `can_spawn=false`, no trust_level, and `context_from` referencing the api step

- [ ] [T1101] Modify `components/Planner/adapters/prompt_builder.py` -- Add example plans (FR-023)
  - Add at least 2 example plans demonstrating the sanitizer insertion pattern:
    - Example 1: api(Fetcher) -> sanitizer(Guard) -> llm_reasoning(Reasoner, untrusted_input) -> api(Booker)
    - Example 2: api(Fetcher) -> api(Fetcher) -> sanitizer(Guard) -> llm_reasoning(Reasoner, untrusted_input) (multiple api steps feeding into one sanitizer)
  - Examples should show `load_bearing_fields` and `output_schema_ref` usage

---

## Phase 12: Shared Infrastructure Integration

### AC: LLD Section 9.1, 9.2

- [ ] [T1200] Modify `shared/app.py` -- Register FilterService in app lifespan (LLD Section 9.1)
  - Import `create_filter_service` from `components/TrustFilter/service.filter_service`
  - Create and store `app.state.filter_service` during startup
  - Wire into `create_execute_service()` call

- [ ] [T1201] Modify `shared/dependencies.py` -- Add dependency getter (LLD Section 9.1)
  - Add `get_filter_service(request: Request) -> FilterService` function
  - Returns `request.app.state.filter_service`

---

## Phase 13: End-to-End Integration Tests

### AC: SC-001 through SC-012, US1, US6

- [ ] [T1300] Create `tests/integration/test_trust_boundary_e2e.py` -- End-to-end trust boundary tests
  - Test US1 (SC-008): Meeting-booking with poisoned calendar description
    - Seed mock Google Calendar MCP response with injection payload in description
    - Run booking plan end-to-end
    - Verify (a) payload never appears in any LLM context, (b) HITL gate fires with `trust_verdict: injection`, (c) created event has no attacker-controlled text
  - Test SC-003: Seed a known-unique injection string in MCP response, run plan, assert string never appears in any outbound Anthropic API reasoning call
  - Test US6 (SC-009): Run existing pure-API plan integration tests unchanged; verify they pass with zero modifications and TrustFilter is never invoked

- [ ] [T1301] Create regression test for pure-API plan backward compatibility (SC-009, US6)
  - In `tests/integration/test_trust_boundary_e2e.py`
  - Load existing pure-API plan fixtures
  - Run through plan validator -> accepted
  - Run through ExecuteOrchestrator -> no sanitizer step dispatched
  - TrustFilter component never invoked

- [ ] [T1302] Write contract flow test -- Intent -> Plan -> Sanitizer -> Reasoner -> Execute
  - In `tests/integration/test_trust_boundary_e2e.py`
  - Validate full GLOBAL_SPEC envelope conformance for sanitizer-containing plans
  - Test Preview wrapper includes `trust_provenance` when sanitizer steps exist
  - Test Execute wrapper includes `trust_provenance` metadata

- [ ] [T1303] Write integration test for SC-005 -- Haiku recall and false positive rates
  - In `components/TrustFilter/tests/test_filter_service.py` or separate integration test
  - Requires live Anthropic API (mark as integration test, skip in unit test runs)
  - Test against `novel_injections_20.json` -- require >= 90% recall
  - Test against `benign_tool_responses_20.json` -- require 0% false positive
  - Note: this test hits real Anthropic API; must be in a separate test marker/group

---

## Task Summary

- **Total Tasks**: 63
- **Phase 0 (Setup)**: T000-T001 (2 tasks)
- **Phase 1 (Shared Schemas)**: T100-T110 (11 tasks)
- **Phase 2 (Domain)**: T200-T208 (9 tasks)
- **Phase 3 (Adapters)**: T300-T302 (3 tasks)
- **Phase 4 (Service)**: T400-T404 (5 tasks)
- **Phase 5 (Fixtures/Contract)**: T500-T504 (5 tasks)
- **Phase 6 (Observability)**: T600-T602 (3 tasks)
- **Phase 7 (Schema Compat)**: T700-T701 (2 tasks)
- **Phase 8 (Plan Validator)**: T800-T806 (7 tasks)
- **Phase 9 (ExecuteOrchestrator)**: T900-T906 (7 tasks)
- **Phase 10 (PolicyEngine)**: T1000-T1003 (4 tasks)
- **Phase 11 (Prompt Builder)**: T1100-T1101 (2 tasks)
- **Phase 12 (Shared Infra)**: T1200-T1201 (2 tasks)
- **Phase 13 (E2E Integration)**: T1300-T1303 (4 tasks)

---

## Dependencies

### External (from LLD Section 11)

- `anthropic >= 0.49.0` -- S2 Haiku judge API client (already in pyproject.toml)
- `pydantic >= 2.7` -- domain models and shared schemas (already in pyproject.toml)
- `pytest`, `pytest-asyncio` -- test framework (already in pyproject.toml)
- `ruff`, `mypy` -- linting/typing (already in pyproject.toml)
- **No new third-party dependencies required.**

### Internal (from LLD Section 11.2)

| Direction | Component | Contract |
|---|---|---|
| Upstream (caller) | ExecuteOrchestrator | Dispatches `step.type == "sanitizer"` into `filter_service.scan()` |
| Downstream (consumer) | PolicyEngine | Reads `trust_verdict` + `scanner_degraded` from `ExecutionContext.sanitizer_verdicts` |
| Modified | Planner (plan_validator) | New Rules E, F, G, H (hard reject) |
| Modified | Planner (prompt_builder) | Instructs LLM to insert sanitizer steps |
| Shared schemas | `shared/schemas/trust.py`, `shared/schemas/sanitized_payload.py`, `shared/schemas/plan.py`, `shared/schemas/policy.py`, `shared/schemas/reasoner_outputs/` | New and modified shared contracts |

### Task Dependency Graph

```
Phase 0 (Setup)
    |
    v
Phase 1 (Shared Schemas: T100-T110)
    |
    +---> Phase 2 (Domain: T200-T208)
    |         |
    |         v
    |     Phase 3 (Adapters: T300-T302)
    |         |
    |         v
    |     Phase 4 (Service: T400-T404)
    |         |
    |         v
    |     Phase 5 (Fixtures/Contract: T500-T504)
    |         |
    |         v
    |     Phase 6 (Observability: T600-T602)
    |
    +---> Phase 7 (Schema Compat: T700-T701) -- can run in parallel with Phases 2-6
    |
    +---> Phase 8 (Plan Validator: T800-T806) -- depends on T108, T109, T102
    |
    +---> Phase 9 (ExecuteOrchestrator: T900-T906) -- depends on T101, T102, T400
    |
    +---> Phase 10 (PolicyEngine: T1000-T1003) -- depends on T109, T100
    |
    +---> Phase 11 (Prompt Builder: T1100-T1101) -- can run in parallel after Phase 1
    |
    +---> Phase 12 (Shared Infra: T1200-T1201) -- depends on T401
    |
    v
Phase 13 (E2E Integration: T1300-T1303) -- depends on ALL prior phases
```

---

## Architectural Considerations

### Blast Radius (from LLD Section 3.2, 13.1)

- **If TrustFilter crashes**: Sanitizer step fails hard. No downstream reasoner runs. No attacker payload reaches LLM. Fail-closed on total component failure.
- **If S2 (Haiku) is unreachable**: S1-only fallback with `scanner_degraded=true`. PolicyEngine escalates to HITL. System remains operational.
- **If S1 regex engine panics**: Treat as 0 hits. S2 carries the load. Logged as `s1_internal_error`.
- **Containment**: TrustFilter is fully stateless, process-local, safe for concurrent `scan()` calls. A crash or slowdown is isolated to the specific step being sanitized. No propagation to other plans or steps.
- **Key guarantee**: A TrustFilter failure NEVER results in unsanitized data reaching a reasoner.

### Determinism (from LLD Section 13.2)

- **S1**: Fully deterministic -- same input + same rule pack = same hits
- **S2**: Approximately deterministic -- temperature=0.0, locked prompt, fixed model version. Model updates tracked via `scanner_version`
- **Strip order**: Deterministic (sorted dotted path)
- **Preview**: Same inputs -> same outputs (except `scanned_at` timestamp, excluded from canonical hash)
- **Execute**: Idempotent sanitizer -- rerunning scan on same payload produces same `SanitizedPayload` (minus timestamp)

### Cross-Component Safety

- **Plan validator (Rule F)** ensures no plan can wire API outputs into LLM reasoning without a sanitizer
- **ExecuteOrchestrator** fails hard on any sanitizer step failure -- no data leakage path
- **PolicyEngine** escalates to HITL on any injection verdict or scanner degradation
- **Locked S2 prompt** is not modifiable at runtime -- defense against configuration tampering
- **Payload wrapping** (data_to_classify JSON field) provides structural defense against meta-injection of the judge itself

### Backward Compatibility (FR-037, FR-038, SC-009)

- Existing pure-API plans (no `llm_reasoning` steps) continue to work unchanged
- Existing Tier 2 reasoners with `trust_level="trusted"` keep current behavior
- All new schema fields have backward-compatible defaults
- Plan validator Rule F only applies to plans containing `llm_reasoning` steps
- Schema validation (Tier 1 hard-fail) replaces intent-based fallback only for `trust_level="untrusted_input"` steps
