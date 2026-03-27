# Verification Report: Intake

**Date**: 2026-03-26
**Branch**: feat/intake
**Status**: PASS

## Test Results

### Intake Component (`components/Intake/tests/`)
- **Passed: 60**
- **Failed: 0**
- **Skipped: 0**
- Runtime: 0.99s

### Planner Component (`components/Planner/tests/`) -- Backward Compatibility
- **Passed: 67**
- **Failed: 0**
- **Skipped: 0**
- Runtime: 0.79s

### Ruff Lint
- `components/Intake/`: All checks passed
- `components/Planner/domain/models.py`: All checks passed
- `components/Planner/service/planner_service.py`: All checks passed

## Schema Validation Matrix

| Schema | Location | Status | Notes |
|--------|----------|--------|-------|
| Intent (GLOBAL_SPEC SS2.1) | `shared/schemas/intent.py` | UNCHANGED | Intake imports, does not redefine; fields match spec (intent, entities, constraints, tz, user_id, context_budget, session_id, trace_id) |
| Session | `components/Intake/domain/models.py` | NEW | Matches LLD SS5.1 exactly: session_id=ses_+ULID(26 chars=30 total), user_id, turns, detected_intent, extracted_entities, extracted_constraints, profile_defaults_offered, created_at, updated_at |
| IntakeMessage | `components/Intake/domain/models.py` | NEW | message: str(min=1, max=10000), session_id: str|None -- matches spec FR-001 |
| IntakeResponse | `components/Intake/domain/models.py` | NEW | status: Literal["collecting","ready"], session_id, detected_intent, collected_entities, missing_fields, follow_up, turn_count, intent (dict when ready) |
| ParseResult | `components/Intake/domain/models.py` | NEW | intent: str|None, entities: dict, constraints: dict |
| SessionResetResponse | `components/Intake/domain/models.py` | NEW | status: Literal["reset"], session_id |
| EntityRequirement | `components/Planner/domain/models.py` | NEW (additive) | name, description, required, default_preference_key -- no existing fields removed |
| RequiredEntitiesResult | `components/Planner/domain/models.py` | NEW (additive) | intent_type, resolved_tools, required_entities, missing_entities -- no existing fields removed |
| ToolNotAvailableError | `components/Planner/domain/models.py` | NEW (additive) | intent_type, required_tools -- subclasses PlannerError, no existing classes modified |
| EvidenceItem | `shared/schemas/evidence.py` | UNCHANGED | Only binary .pyc cache files differ (recompile artifact) |

## Backward Compatibility Assessment

### Planner Domain Models (`components/Planner/domain/models.py`)
- **Risk: NONE** -- All changes are purely additive:
  - Added `EntityRequirement` class (new)
  - Added `RequiredEntitiesResult` class (new)
  - Added `ToolNotAvailableError` class (new)
  - No existing classes modified, renamed, or removed
  - No existing fields modified or removed
  - All 67 existing Planner tests pass without modification

### Planner Service (`components/Planner/service/planner_service.py`)
- **Risk: NONE** -- Added new method `get_required_entities()`:
  - New public method on `PlannerService` (additive)
  - No changes to existing `generate_plan()` method
  - No changes to constructor signature
  - No changes to `create_planner_service()` factory
  - All 67 existing Planner tests pass without modification

### Shared Schemas (`shared/schemas/`)
- **Risk: NONE** -- No source files changed. Only binary `.pyc` cache files differ.

### Shared DI Wiring (`shared/app.py`, `shared/dependencies.py`)
- **Risk: NONE** -- Changes are additive only:
  - `shared/app.py`: Added Intake service initialization block after Planner (new code at end of lifespan), added intake router registration, added Redis close in shutdown
  - `shared/dependencies.py`: Added `get_intake_service()` function

## Contract Verification Details

### Intent SS2.1 Conformance (test_contract.py)
- Emitted Intent passes `Intent.model_validate()` -- 5 tests
- All required fields present: intent, entities, constraints, tz, user_id
- Optional fields populated by Intake: session_id (from session), trace_id (32-char hex via `secrets.token_hex(16)`)
- `context_budget` correctly left as None (not set by Intake)

### Error Handling Pattern (routes.py)
- Uses `ErrorResponse` from `shared/api/error_handlers.py` -- CORRECT
- Local `_handle_domain_error(exc)` maps domain errors to HTTP status codes -- CORRECT
- Falls back to `APIErrorHandler.handle_generic_error(exc)` for unknown errors -- CORRECT
- Domain errors stay in `domain/models.py` -- CORRECT
- Shared module does NOT import Intake internals -- CORRECT

### Error-to-HTTP Mapping:
| Domain Error | HTTP Status | Error Code |
|-------------|-------------|------------|
| SessionNotFoundError | 404 | SESSION_NOT_FOUND |
| SessionOwnershipError | 403 | SESSION_OWNERSHIP_DENIED |
| MaxTurnsExceededError | 400 | MAX_TURNS_EXCEEDED |
| SessionStoreUnavailableError | 503 | SESSION_STORE_UNAVAILABLE |
| ToolNotAvailableError | 422 | TOOL_NOT_AVAILABLE |
| IntentParserError | 500 | INTENT_PARSER_ERROR |

### Consent-Gated Profile Defaults (FR-011)
- Tier 1: `_build_follow_up()` skips ProfileStore lookup -- verified by `test_tier1_skips_profile_defaults` (asserts `get_preference` not called)
- Tier 2+: Checks `entity.default_preference_key` and calls `_try_profile_default()` -- verified by `test_tier2_offers_profile_defaults` (asserts "usually use" in follow_up)
- ProfileStore down: Exception caught in `_try_profile_default()`, returns None, user asked directly -- verified by `test_profile_store_down_skips_defaults`

### DI Wiring
- `shared/app.py` lifespan: Creates Redis client from `REDIS_URL` env, reuses LLM adapter from Planner (or creates new), calls `create_intake_service()`, stores on `app.state.intake_service`
- `shared/dependencies.py`: `get_intake_service(request)` returns `request.app.state.intake_service`
- Shutdown: Redis client closed before DB adapter

## Preview Evidence

Intake is an **internal component** (API/Interface Layer). Per GLOBAL_SPEC SS1:
> "This safety model applies to user-facing plans (Intent --> Plan --> Preview --> Execute), NOT to internal component operations."

No Preview/Execute wrappers are needed or implemented. The component:
- Does NOT mutate external resources (no database writes, no external API calls beyond Redis and LLM)
- Redis writes are ephemeral session state with 1h TTL
- LLM calls are read-only (intent extraction)
- Network/file mutation scan: **No `open()`, `write()`, `requests.post()`, `httpx.post()`, `subprocess`, `os.system()`, `shutil.rmtree()`, or `os.remove()` calls found** in the Intake source code (only a doc match in LLD.md)

## Test Coverage Summary

| Test File | Count | Scope |
|-----------|-------|-------|
| `test_models.py` | 17 | Domain models: Session, SessionTurn, IntakeMessage, IntakeResponse, SessionResetResponse, ParseResult, error hierarchy |
| `test_adapters.py` | 13 | Redis session store (mocked): save/get/delete/TTL/error-wrapping/key-format; LLM parser: single/context/fences/error/invalid-json/partial |
| `test_service.py` | 15 | Single-turn (collecting/ready), multi-turn (merge), consent tier (2+/1/down), tool-not-available, degradation (planner-down/parser-down), lifecycle (max-turns/reset/creation) |
| `test_contract.py` | 5 | Intent SS2.1 conformance: model_validate, required fields, trace_id hex, session_id match, collecting has no intent |
| `test_observability.py` | 4 | FR-010: message content absent from logs, structured fields present, parser error no PII, reset no PII |
| `test_integration.py` | 4 | Multi-turn with in-memory store: 2-turn booking, tier-1 no defaults, session reset then new, planner-down heuristic |
| **Total** | **60** | |

## Warnings (Non-blocking)

- [W001] `shared/app.py` line 163 accesses `app.state.planner_service._llm` (private attribute) via `# noqa: SLF001`. This is a DI shortcut to reuse the LLM adapter instance. Acceptable for now but could be improved by exposing the adapter via a public property on PlannerService.
- [W002] `components/Intake/service/intake_service.py` line 20-21 imports `LLMBasedParser` and `RedisSessionStore` but only uses them in the factory function (line 325-334). Not a lint issue but the unused-in-class import pattern is slightly unusual.
- [W003] LLD SS9 notes MODULAR_ARCHITECTURE dependency matrix needs updating: `Intake --> Planner (lightweight query), ProfileStore (consent-gated defaults)`. Not a code issue; documentation update deferred to PR.

## Failures Requiring Implementer Action

None.
