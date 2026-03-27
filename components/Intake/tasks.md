# Tasks: Intake

**Created**: 2026-03-26
**Branch**: feat/intake
**SPEC**: specs/017-intake/spec.md
**LLD**: components/Intake/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
Each task maps to one or more SPEC acceptance criteria (SC/FR) and LLD sections.
Phases are dependency-ordered: later phases import from earlier ones.

---

## Phase 0: Setup & Scaffolding

### T000 -- Create package structure and `__init__.py` files

**Files to create:**
- `components/Intake/__init__.py`
- `components/Intake/domain/__init__.py`
- `components/Intake/adapters/__init__.py`
- `components/Intake/service/__init__.py`
- `components/Intake/api/__init__.py`
- `components/Intake/tests/__init__.py`

**Details:** Empty `__init__.py` files to make each subdirectory a Python package.
No new pip packages required -- all dependencies (`fastapi`, `pydantic`, `redis[hiredis]`,
`ulid-py`, `anthropic`) are already in `pyproject.toml`.

**Satisfies:** Prerequisite for all subsequent tasks.

---

### T001 -- Verify external service access

- [ ] Confirm `redis[hiredis]>=5.0` is in `pyproject.toml` (it is).
- [ ] Confirm `anthropic>=0.18.0` is in `pyproject.toml` (it is).
- [ ] Confirm `ulid-py>=1.1.0` is in `pyproject.toml` (it is).
- [ ] No `uv add` commands needed.

**Satisfies:** LLD Section 9 (Dependencies).

---

## Phase 1: Domain Models (Foundation)

### T100 -- Create domain models and error hierarchy

**File to create:** `components/Intake/domain/models.py`

**Models to implement (from LLD Section 5):**

1. `SessionTurn(BaseModel)` -- message, timestamp, extracted_intent, extracted_entities,
   extracted_constraints.
2. `Session(BaseModel)` -- session_id (`ses_<ULID>`), user_id, turns (list[SessionTurn]),
   detected_intent, extracted_entities, extracted_constraints, profile_defaults_offered,
   created_at, updated_at.
3. `IntakeMessage(BaseModel)` -- message (str, min_length=1, max_length=10_000),
   session_id (str | None).
4. `IntakeResponse(BaseModel)` -- status (Literal["collecting", "ready"]), session_id,
   detected_intent, collected_entities, missing_fields, follow_up, turn_count, intent.
5. `SessionResetResponse(BaseModel)` -- status (Literal["reset"]), session_id.
6. `ParseResult(BaseModel)` -- intent (str | None), entities (dict), constraints (dict).

**Error hierarchy (from LLD Section 5.4):**

7. `IntakeError(Exception)` -- base.
8. `SessionNotFoundError(IntakeError)` -- session_id attr.
9. `SessionOwnershipError(IntakeError)` -- session_id, user_id attrs.
10. `MaxTurnsExceededError(IntakeError)` -- session_id, max_turns=20 attrs.
11. `SessionStoreUnavailableError(IntakeError)` -- reason attr.
12. `IntentParserError(IntakeError)` -- reason attr.
13. `ToolNotAvailableError(IntakeError)` -- intent_type, required_tools attrs.
    This is Intake's own error, re-raised from Planner's `ToolNotAvailableError`.

**Constraints:**
- Use Pydantic v2, `Field()`, `datetime.now(timezone.utc)`.
- Session ID format: `ses_` prefix + 26-char ULID string.
- Keep file under 200 lines.

**Satisfies:** FR-001 (IntakeMessage validation), FR-002 (Session model), FR-005 (IntakeResponse),
FR-008 (SessionResetResponse), LLD Section 5.

---

### T101 -- Write domain model unit tests

**File to create:** `components/Intake/tests/test_models.py`

**Tests:**
1. `test_session_turn_creation` -- Valid turn.
2. `test_session_creation_defaults` -- Default empty lists/dicts, auto-timestamps.
3. `test_session_id_format` -- Starts with `ses_`, 30 chars total.
4. `test_intake_message_valid` -- Valid message.
5. `test_intake_message_empty_rejects` -- Empty string fails min_length=1.
6. `test_intake_message_too_long_rejects` -- 10,001 chars fails max_length=10_000.
7. `test_intake_response_collecting` -- status="collecting" with follow_up.
8. `test_intake_response_ready` -- status="ready" with intent dict.
9. `test_parse_result_defaults` -- intent=None, empty dicts.
10. `test_error_hierarchy` -- All errors are subclasses of IntakeError.
11. `test_tool_not_available_error_message` -- String includes intent_type and tools.

**Satisfies:** FR-001 (message validation), FR-005 (response shapes), LLD Section 5.

---

## Phase 2: Adapters (SessionStore + IntentParser)

### T200 -- Implement SessionStore protocol and RedisSessionStore

**File to create:** `components/Intake/adapters/session_store.py`

**Classes (from LLD Section 7.1):**

1. `SessionStore(Protocol)` -- runtime_checkable.
   - `async def get(self, user_id: str, session_id: str) -> Session | None`
   - `async def save(self, session: Session) -> None`
   - `async def delete(self, user_id: str, session_id: str) -> bool`

2. `RedisSessionStore` -- implements SessionStore.
   - `__init__(self, redis_client: redis.asyncio.Redis, ttl_seconds: int = 3600)`
   - Key pattern: `session:{user_id}:{session_id}`
   - Serialization: `Session.model_dump_json()` / `Session.model_validate_json(raw)`
   - TTL: Refreshed on every `save()` via `SETEX`.
   - `get()`: Returns None if key missing.
   - `delete()`: Returns True if deleted, False if key did not exist.
   - Error handling: Any `redis.RedisError` -> `SessionStoreUnavailableError`.

**Satisfies:** FR-002 (Redis sessions, `session:{user_id}:{session_id}` key, 1h TTL),
FR-009 (session scoped to user_id), LLD Section 7.1, MODULAR_ARCHITECTURE Section 3.

---

### T201 -- Implement IntentParser protocol and LLMBasedParser

**File to create:** `components/Intake/adapters/intent_parser.py`

**Classes (from LLD Section 7.2):**

1. `IntentParser(Protocol)` -- runtime_checkable.
   - `async def parse(self, message: str, context: Session | None = None) -> ParseResult`

2. `LLMBasedParser` -- implements IntentParser.
   - `__init__(self, llm_adapter: LLMAdapter, model: str = "claude-haiku-4-5-20251001", max_tokens: int = 512)`
   - Model configurable via `INTAKE_PARSER_MODEL` env var (with `os.environ.get()`).
   - System prompt from LLD Section 7.2 (open taxonomy, JSON output, context merging).
   - When `context` is provided, include `detected_intent` and `extracted_entities`
     in the user prompt so LLM can merge.
   - Parse LLM response as JSON -> `ParseResult`.
   - Strip markdown fences (```` ``` ````) if present (same pattern as Planner).
   - Error handling: any LLM exception -> `IntentParserError`.
   - Return empty `ParseResult(intent=None, entities={}, constraints={})` on error
     (service layer decides what to do with it).

**Imports:**
- `from components.Planner.adapters.llm_adapter import LLMAdapter` (Protocol only)
- `from components.Intake.domain.models import ParseResult, Session, IntentParserError`

**Satisfies:** FR-003 (LLM parsing via IntentParser protocol, open taxonomy),
LLD Section 7.2.

---

### T202 -- Write adapter unit tests (mocked Redis, mocked LLM)

**File to create:** `components/Intake/tests/test_adapters.py`

**SessionStore tests (mocked redis.asyncio.Redis):**
1. `test_redis_session_store_save_and_get` -- Save a session, get it back.
2. `test_redis_session_store_get_missing` -- Returns None for non-existent key.
3. `test_redis_session_store_delete_existing` -- Returns True.
4. `test_redis_session_store_delete_missing` -- Returns False.
5. `test_redis_session_store_ttl_refresh` -- Verify SETEX called with 3600.
6. `test_redis_session_store_error_wrapping` -- `RedisError` -> `SessionStoreUnavailableError`.
7. `test_redis_key_format` -- Key is `session:{user_id}:{session_id}`.

**IntentParser tests (mocked LLMAdapter):**
8. `test_llm_parser_single_message` -- Returns ParseResult with intent + entities.
9. `test_llm_parser_with_context` -- Prior session context included in prompt.
10. `test_llm_parser_handles_markdown_fences` -- Strips ``` fences from JSON.
11. `test_llm_parser_handles_llm_error` -- LLM exception -> IntentParserError.
12. `test_llm_parser_handles_invalid_json` -- Bad JSON -> IntentParserError.
13. `test_llm_parser_handles_partial_result` -- Missing intent field -> intent=None.

**Satisfies:** FR-002, FR-003, FR-009, LLD Sections 7.1-7.2.

---

### T203 -- Create test fixtures (conftest.py)

**File to create:** `components/Intake/tests/conftest.py`

**Fixtures to provide:**
1. `sample_session()` -- A Session with 1 turn, detected_intent="schedule_meeting",
   some extracted_entities.
2. `empty_session()` -- A Session with 0 turns.
3. `sample_parse_result()` -- ParseResult with intent="schedule_meeting",
   entities={"attendee": "Alice"}.
4. `sample_intake_message()` -- IntakeMessage with a meeting booking text.
5. `mock_redis_client()` -- `AsyncMock` of `redis.asyncio.Redis`.
6. `mock_llm_adapter()` -- `AsyncMock` implementing LLMAdapter protocol.
7. `mock_planner_service()` -- `AsyncMock` with `get_required_entities` returning
   a RequiredEntitiesResult.
8. `mock_preference_service()` -- `AsyncMock` with `get_preference` returning an EvidenceItem.
9. `sample_required_entities_result()` -- RequiredEntitiesResult with schedule_meeting
   entities (attendee, time, duration_min).
10. `sample_auth_context()` -- dict with user_id, context_tier=2, email.

**Satisfies:** Prerequisite for all test phases.

---

## Phase 3: Service Layer (Business Logic)

### T300 -- Implement IntakeService

**File to create:** `components/Intake/service/intake_service.py`

**Class: `IntakeService` (from LLD Section 4.2)**

Constructor:
```python
def __init__(
    self,
    session_store: SessionStore,
    intent_parser: IntentParser,
    planner_service: Any,
    preference_service: Any,
    max_turns: int = 20,
) -> None:
```

**Method: `async def process_message()`** (from LLD Section 8.1-8.6)
Parameters: user_id, message, context_tier, session_id (optional), tz (default "America/Chicago")
Returns: IntakeResponse

Logic flow:
1. If `session_id` provided, load session from store. If not found, create new session.
   If no `session_id`, create new session with `ses_` + ULID.
2. Check `len(session.turns) >= max_turns` -> raise `MaxTurnsExceededError`.
3. Call `intent_parser.parse(message, session)` -> ParseResult.
   - On `IntentParserError`: log warning, use empty ParseResult, continue.
4. Merge ParseResult into session: update detected_intent (if not None),
   merge extracted_entities (new overrides old), merge extracted_constraints.
5. Append new `SessionTurn` to session.turns.
6. If `detected_intent` is set, call `planner_service.get_required_entities(intent_type, entities)`.
   - On Planner's `ToolNotAvailableError`: re-raise as Intake's `ToolNotAvailableError`.
   - On any other exception: log warning, fall back to heuristic
     (intent + >=1 entity -> ready).
7. Determine readiness:
   - If Planner responded: `missing_entities` is empty -> ready.
   - If Planner unavailable (heuristic): `detected_intent` set AND `len(entities) >= 1` -> ready.
   - Otherwise: collecting.
8. If `status == "collecting"` and `context_tier >= 2`:
   - For each missing entity with `default_preference_key`:
     - If entity not already in `session.profile_defaults_offered`:
       - Call `preference_service.get_preference(user_id, key, context_tier)`.
       - On success: record in `session.profile_defaults_offered[entity_name] = value`.
       - On any exception (ConsentDeniedError, etc.): skip silently.
   - Build follow-up prompt with profile defaults where available.
9. If `status == "collecting"` and `context_tier < 2`:
   - Build simple follow-up prompt listing missing fields without defaults.
10. If `status == "ready"`:
    - Build Intent (from `shared/schemas/intent.py`):
      - intent = session.detected_intent
      - entities = session.extracted_entities
      - constraints = session.extracted_constraints
      - tz = tz parameter
      - user_id = user_id
      - session_id = session.session_id
      - trace_id = generate 32-char hex (secrets.token_hex(16))
11. Save session to store.
12. Return IntakeResponse.

**Method: `async def reset_session()`** (from LLD Section 8.9)
Parameters: user_id, session_id
Returns: None
- Call `session_store.delete(user_id, session_id)`.
- If returns False -> raise `SessionNotFoundError(session_id)`.

**Factory function (from LLD Section 4.2):**
```python
def create_intake_service(
    redis_client: redis.asyncio.Redis,
    llm_adapter: LLMAdapter,
    planner_service: Any,
    preference_service: Any,
) -> IntakeService:
```
Creates RedisSessionStore, LLMBasedParser, returns IntakeService.

**Logging (FR-010):** All log statements use structured fields:
`session_id`, `user_id`, `intent` (type string only), `entity_count`, `turn_count`.
NEVER log `message` content.

**Satisfies:** FR-003 (LLM parse), FR-004 (Planner readiness), FR-005 (collecting/ready),
FR-006 (trace_id), FR-007 (session_id on Intent), FR-008 (reset), FR-009 (session scoping),
FR-010 (no PII in logs), FR-011 (consent-tier-aware defaults), SC-001, SC-002, SC-003.

---

### T301 -- Write IntakeService unit tests

**File to create:** `components/Intake/tests/test_service.py`

All tests use mocked adapters (from conftest.py fixtures).

**Happy path tests:**
1. `test_single_turn_ready` -- Full message -> status="ready", Intent emitted.
   (SC-001, User Story 1 scenario 1)
2. `test_multi_turn_collecting_then_ready` -- Turn 1 vague -> "collecting".
   Turn 2 fills missing -> "ready". (SC-002, User Story 2 scenario 1+2)
3. `test_new_session_created_when_no_session_id` -- No session_id -> new session.
   (User Story 3 scenario 1)
4. `test_existing_session_loaded` -- Provide session_id -> session loaded, extended.
   (User Story 3 scenario 2)
5. `test_entity_override_in_subsequent_turn` -- Second turn overrides prior entity.
   (User Story 2 scenario 3)

**Readiness via Planner tests:**
6. `test_planner_determines_readiness` -- Planner says missing=[] -> ready.
   (FR-004)
7. `test_planner_determines_missing` -- Planner says missing=[time] -> collecting.
   (FR-004)
8. `test_planner_unavailable_fallback_heuristic_ready` -- Planner throws, intent+entity -> ready.
   (LLD Section 8.4)
9. `test_planner_unavailable_fallback_heuristic_collecting` -- Planner throws, no intent -> collecting.

**ToolNotAvailableError tests:**
10. `test_tool_not_available_from_planner` -- Planner raises ToolNotAvailableError ->
    Intake re-raises Intake's ToolNotAvailableError. (LLD Section 8.6)

**Consent-gated profile defaults tests:**
11. `test_tier2_offers_profile_defaults` -- context_tier=2, missing entity has
    default_preference_key -> ProfileStore queried, default offered in follow_up.
    (FR-011, LLD Section 8.2)
12. `test_tier1_skips_profile_defaults` -- context_tier=1 -> ProfileStore NOT called.
    (FR-011, LLD Section 8.3)
13. `test_profile_store_unavailable_skips_default` -- ProfileStore throws -> default skipped,
    entity listed as missing without default. (LLD Section 7.4)
14. `test_profile_defaults_not_re_offered` -- Once offered, same entity default
    not re-offered on next turn.

**LLM failure tests:**
15. `test_llm_parser_error_returns_collecting` -- LLM down -> empty ParseResult,
    status="collecting", generic clarification prompt. (LLD Section 8.5)

**Session lifecycle tests:**
16. `test_reset_session_success` -- Session deleted, no error. (FR-008, User Story 4 scenario 1)
17. `test_reset_session_not_found` -- SessionNotFoundError raised. (User Story 4 scenario 2)
18. `test_max_turns_exceeded` -- 20 turns -> MaxTurnsExceededError. (Edge case)

**Intent fields tests:**
19. `test_emitted_intent_has_trace_id` -- trace_id is 32-char hex. (FR-006)
20. `test_emitted_intent_has_session_id` -- session_id matches session. (FR-007)
21. `test_emitted_intent_has_tz` -- tz from parameter. (GLOBAL_SPEC Section 2.1)
22. `test_emitted_intent_passes_model_validate` -- `Intent.model_validate(intent_dict)` succeeds.
    (SC-004)

**Satisfies:** FR-001 through FR-012, SC-001 through SC-004, all User Stories.

---

## Phase 4: API Routes (Thin Wrappers)

### T400 -- Implement API routes

**File to create:** `components/Intake/api/routes.py`

**Router:** `APIRouter(prefix="/intake", tags=["intake"])`

**Routes (from LLD Section 4.1):**

1. `POST /intake/message` -- `submit_message()`
   - Depends: `get_auth_context` (from `shared/api/auth.py`), `get_intake_service` (from `shared/dependencies.py`).
   - Extract `user_id`, `context_tier` from auth_context.
   - Extract `tz` from `X-Timezone` request header, default `"America/Chicago"`.
   - Validate body as `IntakeMessage` (Pydantic does this via FastAPI).
   - Call `service.process_message(user_id, message, context_tier, session_id, tz)`.
   - Return IntakeResponse (200).
   - Catch domain errors via `_handle_domain_error()`.

2. `DELETE /intake/session/{session_id}` -- `reset_session()`
   - Depends: `get_auth_context`, `get_intake_service`.
   - Call `service.reset_session(user_id, session_id)`.
   - Return `SessionResetResponse(session_id=session_id)` (200).
   - Catch domain errors.

3. `GET /intake/health` -- `health_check()`
   - No auth.
   - Return `{"status": "ok", "service": "intake"}`.

**Local error handler: `_handle_domain_error(exc)`** (from LLD Section 7.5)
Maps domain exceptions to HTTP + ErrorResponse:

| Exception | HTTP Status | error_code |
|-----------|------------|------------|
| `SessionNotFoundError` | 404 | `SESSION_NOT_FOUND` |
| `SessionOwnershipError` | 403 | `SESSION_OWNERSHIP_DENIED` |
| `MaxTurnsExceededError` | 400 | `MAX_TURNS_EXCEEDED` |
| `SessionStoreUnavailableError` | 503 | `SESSION_STORE_UNAVAILABLE` |
| `ToolNotAvailableError` | 422 | `TOOL_NOT_AVAILABLE` |
| Other | 500 | via `APIErrorHandler.handle_generic_error()` |

**Imports:**
- `from shared.api.auth import get_auth_context`
- `from shared.api.error_handlers import APIErrorHandler, ErrorResponse`
- `from shared.dependencies import get_intake_service`
- `from ..domain.models import (IntakeMessage, SessionNotFoundError, SessionOwnershipError, MaxTurnsExceededError, SessionStoreUnavailableError, ToolNotAvailableError)`

**Satisfies:** FR-001, FR-008, FR-009, FR-010, LLD Section 4.1, LLD Section 7.5.

---

### T401 -- Write API route tests

**File to create:** `components/Intake/tests/test_api.py`

Use `httpx.AsyncClient` with FastAPI test client. Mock `IntakeService` on `app.state`.

**Tests:**
1. `test_submit_message_ready` -- 200, status="ready", Intent in body.
2. `test_submit_message_collecting` -- 200, status="collecting", follow_up present.
3. `test_submit_message_no_body` -- 422 (Pydantic validation). (User Story 1 scenario 2)
4. `test_submit_message_empty_message` -- 422 (min_length=1). (FR-001)
5. `test_submit_message_too_long` -- 422 (max_length=10_000). (Edge case)
6. `test_submit_message_session_store_unavailable` -- 503 SESSION_STORE_UNAVAILABLE. (Edge case)
7. `test_submit_message_tool_not_available` -- 422 TOOL_NOT_AVAILABLE. (LLD Section 8.6)
8. `test_submit_message_max_turns` -- 400 MAX_TURNS_EXCEEDED. (Edge case)
9. `test_reset_session_success` -- 200, status="reset". (User Story 4 scenario 1)
10. `test_reset_session_not_found` -- 404 SESSION_NOT_FOUND. (User Story 4 scenario 2)
11. `test_health_check` -- 200, no auth required.
12. `test_unauthenticated_request` -- 401 (no auth context).
13. `test_x_timezone_header_used` -- tz extracted from header.
14. `test_x_timezone_header_missing_defaults_to_chicago` -- Default America/Chicago.

**Satisfies:** FR-001, FR-005, FR-008, FR-009, all edge cases from SPEC.

---

## Phase 5: DI Wiring (shared/app.py + shared/dependencies.py)

### T500 -- Add IntakeService to shared/app.py lifespan

**File to modify:** `shared/app.py`

**Changes (from LLD Section 7.5):**

1. In `lifespan()`, after Planner initialization block, add Intake initialization:
   ```python
   # Intake service
   import redis.asyncio as aioredis
   from components.Intake.service.intake_service import create_intake_service

   redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
   intake_redis = aioredis.from_url(redis_url, decode_responses=True)

   app.state.intake_service = create_intake_service(
       redis_client=intake_redis,
       llm_adapter=llm_adapter,  # Reuse shared AnthropicAdapter
       planner_service=app.state.planner_service,
       preference_service=app.state.preference_service,
   )
   ```
   NOTE: `llm_adapter` needs to be extracted from the Planner wiring or created
   once and shared. If no shared adapter instance exists yet, create one
   `AnthropicAdapter()` once and pass to both Planner and Intake. Handle the case
   where `ANTHROPIC_API_KEY` is not set -- wrap in try/except like Planner does.

2. In `lifespan()` shutdown section, close `intake_redis` connection.

3. In `create_app()`, register the Intake router:
   ```python
   from components.Intake.api.routes import router as intake_router
   app.include_router(intake_router)
   ```

**Satisfies:** LLD Section 7.5 (DI wiring, shared LLMAdapter).

---

### T501 -- Add get_intake_service to shared/dependencies.py

**File to modify:** `shared/dependencies.py`

**Add:**
```python
def get_intake_service(request: Request) -> Any:
    """Get IntakeService singleton from app state."""
    return request.app.state.intake_service
```

**Satisfies:** LLD Section 7.5 (DI for route handlers).

---

## Phase 6: Contract Tests & Integration

### T600 -- Write Intent contract conformance tests

**File to create:** `components/Intake/tests/test_contract.py`

**Tests (from LLD Section 12, SC-004):**
1. `test_intent_conforms_to_global_spec` -- Build an Intent from IntakeService output,
   validate with `Intent.model_validate()`. All required fields present.
2. `test_intent_has_required_fields` -- `intent`, `entities`, `constraints`,
   `tz`, `user_id` are all present and non-None.
3. `test_intent_session_id_matches_session` -- session_id on Intent matches
   the session's session_id.
4. `test_intent_trace_id_is_32_hex` -- trace_id matches `^[0-9a-f]{32}$`.
5. `test_intent_tz_default` -- Default tz is "America/Chicago".
6. `test_collecting_response_has_follow_up` -- When status="collecting",
   follow_up is not None.
7. `test_ready_response_has_intent` -- When status="ready", intent dict is not None.
8. `test_collecting_response_has_missing_fields` -- missing_fields list is populated.

**Satisfies:** SC-004 (all emitted Intents pass model_validate), GLOBAL_SPEC Section 2.1.

---

### T601 -- Write observability / no-PII tests

**File to create:** `components/Intake/tests/test_observability.py`

**Tests (from LLD Section 10, FR-010, SC-005):**
1. `test_no_message_content_in_logs` -- Call `process_message()` with a distinctive
   message string. Capture log output (via `caplog` or `logging.Handler` mock).
   Assert the message content does NOT appear in any log record.
2. `test_no_pii_in_error_logs` -- Trigger an IntentParserError. Verify the user
   message is NOT in the error log.
3. `test_structured_log_fields_present` -- Verify logs include `session_id`,
   `user_id` fields (via extra dict).
4. `test_intent_type_logged_on_ready` -- When status="ready", log includes
   `intent` field (type string, not full entities).
5. `test_no_entity_values_in_logs` -- Entity values (e.g., "Alice", "10 AM")
   do NOT appear in log output.

**Satisfies:** FR-010, SC-005 (no user message content in any log output),
LLD Section 10, Constitution Section VI.

---

### T602 -- Write end-to-end integration test (mocked externals)

**File to create:** `components/Intake/tests/test_integration.py`

**Tests:** Full-stack integration with real IntakeService, mocked Redis + LLM + Planner + ProfileStore.

1. `test_e2e_single_turn_flow` -- POST message -> IntakeService -> Redis save ->
   LLM parse -> Planner check -> ready -> Intent emitted.
2. `test_e2e_multi_turn_flow` -- Two POSTs: first collecting, second ready.
   Verify session state persists across calls.
3. `test_e2e_tier2_profile_defaults_flow` -- Tier 2 user, missing entity with
   pref key -> ProfileStore queried -> default offered in follow_up.
4. `test_e2e_tool_not_available_flow` -- LLM parses "book_flight", Planner raises
   ToolNotAvailableError -> HTTP 422.
5. `test_e2e_llm_down_graceful_degradation` -- LLM raises -> collecting with
   generic clarification prompt.
6. `test_e2e_planner_down_graceful_degradation` -- Planner raises -> fallback
   heuristic -> ready if intent + entity.
7. `test_e2e_redis_down` -- Redis raises -> HTTP 503.

**Satisfies:** SC-001, SC-002, SC-003, SC-004, all edge cases from SPEC, LLD Sections 8.1-8.9.

---

## Phase 7: Fault Isolation & Architectural Safety

### T700 -- Verify LLM circuit breaker reuse

**No new file.** Verification task during implementation.

- [ ] Confirm `LLMBasedParser` uses the shared `LLMAdapter` (which goes through Planner's
  `CircuitBreaker` when called by Planner). For Intake's own calls, the Haiku model
  is called through the same adapter but via a different code path.
- [ ] LLD Section 13 specifies reusing Planner's CircuitBreaker for LLM calls.
  Decision: If Intake needs its own circuit breaker (separate from Planner's Sonnet/Haiku
  breakers), create one in `create_intake_service()` with `CircuitBreaker(model_name="intake-haiku")`.
  Otherwise, rely on the LLMAdapter's timeout behavior and catch `LLMCallError`.
- [ ] For MVP: wrap LLM calls in a try/except in `LLMBasedParser.parse()`.
  The Planner's breaker applies to Planner's own calls. Intake's LLM calls go direct
  through the adapter. If a dedicated breaker is needed later, the protocol allows it.

**Satisfies:** LLD Section 13 (fault isolation, circuit breaker for LLM).

---

### T701 -- Verify graceful degradation paths

**No new file.** Verification task (covered by tests in T301 and T602).

Checklist:
- [ ] LLM down -> empty ParseResult -> "collecting" with generic prompt. (T301 test 15, T602 test 5)
- [ ] Planner down -> heuristic fallback. (T301 tests 8-9, T602 test 6)
- [ ] ProfileStore down -> skip defaults, ask directly. (T301 test 13)
- [ ] Redis down -> SessionStoreUnavailableError -> HTTP 503. (T602 test 7)
- [ ] ToolNotAvailableError -> HTTP 422. (T301 test 10, T602 test 4)

**Satisfies:** LLD Section 3 (blast radius analysis), LLD Section 13 (fault isolation).

---

### T702 -- Verify no PII in logs (audit)

**No new file.** Verification task (covered by T601).

Checklist:
- [ ] `message` field NEVER appears in any log call in `intake_service.py`.
- [ ] `message` field NEVER appears in any log call in `intent_parser.py`.
- [ ] `message` field NEVER appears in any log call in `routes.py`.
- [ ] Only `session_id`, `user_id`, `intent` (type string), `entity_count`, `turn_count`
  appear in structured log extra dicts.

**Satisfies:** FR-010, SC-005, Constitution Section VI, GLOBAL_SPEC Section 3.

---

## Task Summary

| Phase | Task IDs | Count | Description |
|-------|----------|-------|-------------|
| 0: Setup | T000-T001 | 2 | Package scaffolding, dependency verification |
| 1: Domain | T100-T101 | 2 | Models, errors, model unit tests |
| 2: Adapters | T200-T203 | 4 | SessionStore, IntentParser, adapter tests, conftest |
| 3: Service | T300-T301 | 2 | IntakeService, service tests |
| 4: API | T400-T401 | 2 | Routes, route tests |
| 5: DI Wiring | T500-T501 | 2 | shared/app.py, shared/dependencies.py |
| 6: Contract | T600-T602 | 3 | Intent conformance, observability, integration |
| 7: Safety | T700-T702 | 3 | Circuit breaker, degradation, PII audit |
| **Total** | | **20** | |

---

## Dependencies

### External (from LLD Section 9)

| Package | Version | Status |
|---------|---------|--------|
| `fastapi` | >=0.109.0 | Already in pyproject.toml |
| `pydantic` | >=2.0 | Already in pyproject.toml |
| `redis[hiredis]` | >=5.0 | Already in pyproject.toml |
| `ulid-py` | >=1.1.0 | Already in pyproject.toml |
| `anthropic` | >=0.18.0 | Already in pyproject.toml |

No new packages to install.

### Internal (from LLD Section 9)

| Component/Module | Import | Purpose |
|------------------|--------|---------|
| `shared/schemas/intent.py` | `Intent` | Output contract (GLOBAL_SPEC Section 2.1) |
| `shared/api/auth.py` | `get_auth_context` | JWT auth extraction |
| `shared/api/error_handlers.py` | `ErrorResponse`, `APIErrorHandler` | Error formatting |
| `shared/dependencies.py` | `get_intake_service` (to add) | DI for route handlers |
| `shared/app.py` | lifespan (to modify) | Service initialization |
| `components/Planner/adapters/llm_adapter.py` | `LLMAdapter`, `AnthropicAdapter` | Shared LLM protocol |
| `components/Planner/domain/models.py` | `EntityRequirement`, `RequiredEntitiesResult`, `ToolNotAvailableError` | Planner query types |
| `components/Planner/service/planner_service.py` | `PlannerService.get_required_entities()` | Readiness checking |
| `components/ProfileStore/service/preference_service.py` | `PreferenceService.get_preference()` | Consent-gated defaults |

---

## Architectural Considerations

### Blast Radius (from LLD Section 3, 13)

- **If Intake fails**: No Intents emitted. Downstream (ContextRAG, Planner) unaffected.
  Users see HTTP errors. No data corruption (Redis is ephemeral).
- **If Redis fails**: All session operations fail with HTTP 503. No cascade.
- **If LLM fails**: Intent parsing fails. Fallback: empty ParseResult, "collecting" with generic prompt. No cascade.
- **If Planner fails**: Entity requirements unknown. Fallback: heuristic (intent + >=1 entity -> ready). May emit incomplete Intents, but downstream validation catches.
- **If ProfileStore fails**: Cannot offer defaults. Fallback: ask user directly. No cascade.
- **Containment**: Each external dependency fails independently. No compound failures.

### Determinism (from LLD Section 13)

- **LLM parser is NOT deterministic** -- same message may produce slightly different ParseResults. Acceptable for conversational interface.
- **Planner readiness check IS deterministic** -- same intent_type + entities -> same missing list (given same tool catalog).
- **Intent output is deterministic** -- given same session state, same Intent fields.

### State Management

- **Redis only** -- no PostgreSQL tables. Sessions are ephemeral (1h TTL).
- **Session loss risk** -- Redis restart loses all sessions. Acceptable.
- **No background tasks** -- all operations are synchronous request-response.

### Key Pattern Deviation

- MODULAR_ARCHITECTURE Section 3 shows `session:{user_id}` but this design uses `session:{user_id}:{session_id}` to support multiple concurrent sessions per user.
- This deviation is documented in LLD Section 14 and Section 15.
