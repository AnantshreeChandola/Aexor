# Low-Level Design: Intake

**Status**: Draft
**Date**: 2026-03-26
**Spec**: [specs/017-intake/spec.md](/specs/017-intake/spec.md)
**Branch**: `feat/intake`

---

## 1. Purpose & Scope

Intake is the system's **HTTP entry point** in the **API/Interface Layer**. It:

1. Receives raw user messages via `POST /intake/message`.
2. Manages multi-turn conversation state via Redis-backed sessions.
3. Parses intent and entities from user messages via **LLM** (Anthropic Claude, behind an `IntentParser` protocol).
4. Determines readiness by querying **Planner's `get_required_entities()`** — the Planner uses its tool knowledge (via PluginRegistry) to determine which entities are required and which are still missing.
5. For missing entities with known ProfileStore defaults (Tier 2+ consent), prompts the user: "I see you usually use X. Use that, or specify a different value?"
6. Emits finalized **Intent** objects (GLOBAL_SPEC §2.1) for the downstream ContextRAG → Planner pipeline.
7. Supports explicit session reset via `DELETE /intake/session/{session_id}`.

**Boundaries**:
- Intake does NOT restrict to a fixed intent taxonomy — open taxonomy, downstream validation.
- Intake does NOT execute plans — it only produces Intent objects.
- Intake does NOT own any PostgreSQL tables — Redis-only for ephemeral state.
- The Preview/Execute safety model does NOT apply (GLOBAL_SPEC §1: internal component).

**Layer**: API/Interface Layer (per MODULAR_ARCHITECTURE §1).

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v2.2 | Intent §2.1 field alignment, NFRs §3, safety model §1 (N/A), consent tiers §7 |
| MODULAR_ARCHITECTURE.md | v1.3 | §1 (API Layer), §3 (Redis key patterns), §4 (dependency matrix) |
| Project_HLD.md | v4.0 | §1 (system layers), Intake entry point |
| SHARED_INFRASTRUCTURE.md | v1.0.0 | §2 (auth middleware), §4 (shared schemas) |
| ADR-0001 | Accepted | Component-first architecture |

**Deviation noted**: MODULAR_ARCHITECTURE §3 Redis Key Patterns shows `session:{user_id}` but this design uses `session:{user_id}:{session_id}` to support multiple concurrent sessions per user. Rationale in §15 Risks.

---

## 3. Architecture Overview

### Layer Placement

```
┌──────────────────────────────────────────────────────────────┐
│ API / INTERFACE LAYER                                         │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ Intake                                                │    │
│  │  routes.py → IntakeService → SessionStore (Redis)     │    │
│  │                            → IntentParser (LLM)       │    │
│  │                            → PlannerService (query)   │    │
│  │                            → PreferenceService (Tier2) │    │
│  └──────────────────────────────────────────────────────┘    │
│           │                           │                       │
│           │ Intent (§2.1)             │ Sessions              │
│           ▼                           ▼                       │
│     [Downstream:                  [Redis]                     │
│      ContextRAG → Planner]                                    │
└──────────────────────────────────────────────────────────────┘

Cross-component dependencies:
  Intake ──► Planner.get_required_entities()  (lightweight query)
  Intake ──► ProfileStore.get_preference()    (consent-gated defaults)
  Intake ──► LLMAdapter (Anthropic Claude)    (intent parsing)
```

### Blast Radius Analysis

| Failure | Impact | Containment |
|---------|--------|-------------|
| Redis down | Cannot create/load sessions | HTTP 503 `SESSION_STORE_UNAVAILABLE` — no cascade |
| LLM unavailable | Cannot parse intent | Fallback: extract nothing, return `status: "collecting"` with generic prompt |
| Planner unavailable | Cannot determine required entities | Fallback: heuristic (intent + ≥1 entity → ready) |
| Tool not available | Intent requires a tool not in PluginRegistry | HTTP 422 `TOOL_NOT_AVAILABLE` — user informed early, no wasted turns |
| ProfileStore unavailable | Cannot check stored defaults | Skip defaults, ask user directly for all missing entities |
| Parser error | Intent not detected | Returns `status: "collecting"` with clarification prompt |
| Max turns exceeded | Session capped | HTTP 400 `MAX_TURNS_EXCEEDED` — session preserved |
| Malformed message | Validation failure | HTTP 422 from Pydantic — no session side effects |

**Isolation strategy**: Three external dependencies (Redis, LLM, Planner/ProfileStore). Redis failure is binary (503). LLM and Planner/ProfileStore failures degrade gracefully — Intake falls back to simpler behavior, never blocks the user. Tool-not-available errors are surfaced early to the user rather than collecting entities for an intent that can't be fulfilled.

---

## 4. Interfaces

### 4.1 API Handlers (routes.py)

Thin wrappers around IntakeService. Router prefix: `/intake`.

| Method | Path | Handler | Auth | Description |
|--------|------|---------|------|-------------|
| POST | `/intake/message` | `submit_message()` | Bearer JWT | Submit user message |
| DELETE | `/intake/session/{session_id}` | `reset_session()` | Bearer JWT | Delete session |
| GET | `/intake/health` | `health_check()` | None | Health check |

**Auth context**: `get_auth_context()` from `shared/api/auth.py` extracts `user_id`, `context_tier`, `email` from JWT.

**Timezone**: `X-Timezone` request header, fallback `America/Chicago`.

### 4.2 Service Layer (intake_service.py)

```python
class IntakeService:
    def __init__(
        self,
        session_store: SessionStore,
        intent_parser: IntentParser,
        planner_service: Any,         # Planner's get_required_entities()
        preference_service: Any,       # ProfileStore's get_preference()
    ) -> None: ...

    async def process_message(
        self,
        user_id: str,
        message: str,
        context_tier: int,
        session_id: str | None = None,
        tz: str = "America/Chicago",
    ) -> IntakeResponse: ...

    async def reset_session(
        self,
        user_id: str,
        session_id: str,
    ) -> None: ...
```

**Key change from MVP**: `process_message` now accepts `context_tier` (from JWT) and the service holds references to `planner_service` and `preference_service`.

**Factory**:
```python
def create_intake_service(
    redis_client: redis.asyncio.Redis,
    llm_adapter: LLMAdapter,
    planner_service: Any,
    preference_service: Any,
) -> IntakeService: ...
```

### 4.3 Consumer Contracts

**Primary consumer**: External HTTP clients (browsers, mobile apps, CLI).

| Consumer | Calls | Input | Output | Errors |
|----------|-------|-------|--------|--------|
| Client app | `POST /intake/message` | `IntakeMessage` JSON | `IntakeResponse` JSON | 401, 422, 400, 503 |
| Client app | `DELETE /intake/session/{id}` | session_id path param | `SessionResetResponse` JSON | 401, 404, 503 |

**Downstream data consumer**: The emitted `Intent` JSON (when `status: "ready"`) is the input to ContextRAG → Planner. This is a data contract, not a direct API call — the client receives the Intent and can forward it.

### 4.4 Planner Lightweight Query (New)

Intake calls `PlannerService.get_required_entities()` to determine what entities are needed for a given intent type. This is a **lightweight query** — no full plan generation, no LLM call, no ContextRAG gathering.

```python
# Called by Intake:
result = await planner_service.get_required_entities(
    intent_type="schedule_meeting",
    collected_entities={"attendee": "Alice"},
)
# result.missing_entities → [EntityRequirement(name="time", ...), ...]
```

See §7.4 for the Planner-side design of this method.

### 4.5 Preview/Execute

**N/A** — Intake is an internal component. Per GLOBAL_SPEC §1: "This safety model applies to user-facing plans (Intent → Plan → Preview → Execute), NOT to internal component operations."

---

## 5. Data Model

### 5.1 Domain Entities (domain/models.py)

All models use Pydantic v2 with `Field()`, `datetime.now(timezone.utc)`.

#### Session

```python
class Session(BaseModel):
    session_id: str                                    # ses_<26-char ULID>
    user_id: str                                       # UUID string from JWT
    turns: list[SessionTurn] = Field(default_factory=list)
    detected_intent: str | None = None
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    extracted_constraints: dict[str, Any] = Field(default_factory=dict)
    profile_defaults_offered: dict[str, Any] = Field(default_factory=dict)  # NEW
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

**New field** `profile_defaults_offered`: Tracks which ProfileStore defaults were offered to the user so we don't re-offer them. Key = entity name, value = offered value.

#### SessionTurn

```python
class SessionTurn(BaseModel):
    message: str
    timestamp: datetime
    extracted_intent: str | None = None
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    extracted_constraints: dict[str, Any] = Field(default_factory=dict)
```

#### IntakeMessage (request)

```python
class IntakeMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=10_000)
    session_id: str | None = None
```

#### IntakeResponse (response)

```python
class IntakeResponse(BaseModel):
    status: Literal["collecting", "ready"]
    session_id: str
    detected_intent: str | None = None
    collected_entities: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    follow_up: str | None = None
    turn_count: int = 0
    intent: dict[str, Any] | None = None  # Serialized Intent when ready
```

#### SessionResetResponse

```python
class SessionResetResponse(BaseModel):
    status: Literal["reset"] = "reset"
    session_id: str
```

#### ParseResult (value object)

```python
class ParseResult(BaseModel):
    intent: str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
```

### 5.2 Intent (GLOBAL_SPEC §2.1)

**Imported from** `shared/schemas/intent.py` — NOT redefined. Field alignment verified:

| GLOBAL_SPEC §2.1 Field | shared/schemas/intent.py | Match |
|-------------------------|--------------------------|-------|
| `intent` | `intent: str` (min_length=1) | ✅ |
| `entities` | `entities: dict[str, Any]` | ✅ |
| `constraints` | `constraints: dict[str, Any]` | ✅ |
| `tz` | `tz: str` (default "America/Chicago") | ✅ |
| `user_id` | `user_id: str` | ✅ |
| `context_budget` | `context_budget: int \| None` (1-5) | ✅ |
| `session_id` | `session_id: str \| None` | ✅ (populated by Intake) |
| `trace_id` | `trace_id: str \| None` | ✅ (generated by Intake) |

### 5.3 Planner Domain Models (New — added to Planner)

These models are added to `components/Planner/domain/models.py`:

```python
class EntityRequirement(BaseModel):
    """A single entity required for an intent type."""
    name: str                               # e.g., "attendee"
    description: str                        # e.g., "Who should attend the meeting?"
    required: bool = True                   # Essential vs optional
    default_preference_key: str | None = None  # ProfileStore key, e.g., "default_meeting_duration"

class RequiredEntitiesResult(BaseModel):
    """Result from Planner's get_required_entities() lightweight query."""
    intent_type: str
    resolved_tools: list[str]                  # Tool IDs from registry that can fulfill this intent
    required_entities: list[EntityRequirement]
    missing_entities: list[EntityRequirement]   # Subset: only entities not yet collected
```

### 5.4 Error Hierarchy

```python
class IntakeError(Exception): ...                          # Base
class SessionNotFoundError(IntakeError):                   # 404
    session_id: str
class SessionOwnershipError(IntakeError):                  # 403
    session_id: str; user_id: str
class MaxTurnsExceededError(IntakeError):                  # 400
    session_id: str; max_turns: int = 20
class SessionStoreUnavailableError(IntakeError):           # 503
    reason: str
class IntentParserError(IntakeError):                      # Internal — triggers fallback
    reason: str
class ToolNotAvailableError(IntakeError):                  # 422
    intent_type: str; required_tools: list[str]
    # Re-raised from Planner's ToolNotAvailableError
```

Note: `ToolNotAvailableError` is raised by Planner's `get_required_entities()` when no registered tool can fulfill the intent. Intake catches it and maps to HTTP 422 with `error_code: "TOOL_NOT_AVAILABLE"` and a message like "No tool is available for intent 'schedule_meeting'. Available tools: google.calendar, outlook.calendar, ...".

---

## 6. Database Schema & Migrations

**No PostgreSQL tables owned**. Intake uses Redis exclusively for ephemeral session state.

Per MODULAR_ARCHITECTURE §3 Table Ownership Map:
> `sessions | public | Intake | (Optional - if not Redis)`

We chose Redis → no migration needed.

### Redis Key Pattern

| Key Pattern | TTL | Max Size | Description |
|-------------|-----|----------|-------------|
| `session:{user_id}:{session_id}` | 3600s (1h) | 50KB | JSON-serialized Session |

---

## 7. Adapters

### 7.1 SessionStore Protocol + RedisSessionStore

```python
@runtime_checkable
class SessionStore(Protocol):
    async def get(self, user_id: str, session_id: str) -> Session | None: ...
    async def save(self, session: Session) -> None: ...
    async def delete(self, user_id: str, session_id: str) -> bool: ...

class RedisSessionStore:
    def __init__(self, redis_client: redis.asyncio.Redis) -> None: ...
```

**Key**: `session:{user_id}:{session_id}`
**Serialization**: `Session.model_dump_json()` / `Session.model_validate(json.loads(raw))`
**TTL**: Refreshed on every `save()` (3600s)
**Error handling**: `redis.RedisError` → `SessionStoreUnavailableError`

### 7.2 IntentParser Protocol + LLMBasedParser

```python
@runtime_checkable
class IntentParser(Protocol):
    async def parse(self, message: str, context: Session | None = None) -> ParseResult: ...

class LLMBasedParser:
    """LLM-based intent parser using Anthropic Claude via LLMAdapter."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 512,
    ) -> None: ...

    async def parse(self, message: str, context: Session | None = None) -> ParseResult: ...
```

**LLM interaction**: The parser sends a system prompt instructing the LLM to extract `intent`, `entities`, and `constraints` from the user message. If a session context is provided, the prompt includes prior detected intent and entities so the LLM can merge new information with existing state.

**System prompt design**:
```
You are an intent extraction engine. Given a user message and optional prior context,
extract: intent (string), entities (dict), constraints (dict).
Return JSON only. Open taxonomy — any intent type is valid.
If context provided, merge: new values override old.
If intent cannot be determined, return intent: null.
```

**LLM model**: `claude-haiku-4-5-20251001` (fast, cheap — parsing is a simple extraction task). Configurable via `INTAKE_PARSER_MODEL` env var.

**Error handling**: `LLMCallError` → `IntentParserError` → service falls back to empty ParseResult (intent=None, entities={}) and returns `status: "collecting"` with generic clarification prompt.

**Context merging**: The LLM receives both the new message and the prior session state (detected_intent, extracted_entities). The LLM is instructed to return the merged result directly.

**Reuse**: `LLMAdapter` protocol and `AnthropicAdapter` from `components/Planner/adapters/llm_adapter.py` — same adapter instance shared via DI, no new code needed for the LLM client.

### 7.3 Planner's `get_required_entities()` (New Method)

Added to `components/Planner/service/planner_service.py`:

```python
async def get_required_entities(
    self,
    intent_type: str,
    collected_entities: dict[str, Any] | None = None,
) -> RequiredEntitiesResult:
    """
    Lightweight query: determine what entities are required for an intent type
    and which are still missing.

    Uses PluginRegistry tool catalog to determine entity requirements.
    Falls back to LLM-based entity inference if no matching tool found.

    NOT a full plan generation — no ContextRAG, no signing, no fallback hierarchy.
    """
```

**Implementation strategy** (LLM-first, then validate):

1. **LLM analysis (no catalog context)**: Ask the LLM what tools and entities the intent requires. The LLM uses its general knowledge — it is NOT given the registry catalog. This keeps the prompt small and avoids leaking the tool catalog into the LLM context. The LLM returns:
   - `tools_needed`: Tool IDs it believes are needed (e.g., `["google.calendar"]`)
   - `entities`: Required parameters with descriptions and ProfileStore preference key mappings

2. **Validate against PluginRegistry**: After the LLM responds, fetch the tool catalog from `PluginRegistry.list_catalog()` and check whether the LLM-suggested tools actually exist. This is a simple set intersection — no LLM cost.

3. **Tool availability check (fail early)**: If **none** of the LLM-suggested tools exist in the registry, raise `ToolNotAvailableError`. This prevents wasted turns collecting entities for an unfulfillable intent. If the registry is unavailable, skip validation and pass through the LLM's suggestions (graceful degradation).

4. **Missing entities computation**: Compare required entities against `collected_entities` to compute the `missing_entities` list.

5. **ProfileStore key mapping**: For each entity, if there's a known user preference key (e.g., `default_meeting_duration` for `duration_min`), include it in `default_preference_key`. This enables Intake to check ProfileStore for defaults.

**Why LLM-first**: Sending the entire catalog to the LLM wastes tokens and constrains its reasoning. Better to let the LLM suggest tools from its knowledge, then validate cheaply against the registry. This also means the LLM's suggestions tell us exactly which tools are missing — useful for the error message.

**Error propagation**: `ToolNotAvailableError(intent_type, required_tools)` propagates up to Intake, which catches it and returns HTTP 422 `TOOL_NOT_AVAILABLE` with a message like "I can't help with 'book_flight' — the required tool(s) [airline.booking] are not available."

**Performance**: 1 LLM call + 1 PluginRegistry lookup (in sequence). Target: < 2s p95 (LLM-dominated). Circuit breaker from Planner applies to the LLM call.

### 7.4 ProfileStore Integration (Consent-Gated Defaults)

Intake calls ProfileStore's `PreferenceService.get_preference()` for missing entities that have a `default_preference_key`:

```python
# In IntakeService.process_message():
if context_tier >= 2 and missing_entity.default_preference_key:
    try:
        evidence = await self._preference_service.get_preference(
            user_id=UUID(user_id),
            preference_key=missing_entity.default_preference_key,
            context_tier=context_tier,
            plan_id=None,  # No plan yet — this is pre-planning
        )
        # Offer the default to the user
        default_value = evidence.value
    except ConsentDeniedError:
        pass  # Tier check failed — ask user directly
    except Exception:
        pass  # ProfileStore down — ask user directly
```

**Consent tier logic** (GLOBAL_SPEC §7):
- **Tier 1** (session-only): No profile lookups. Ask user for all missing entities directly.
- **Tier 2+** (stable preferences): Check ProfileStore for stored defaults. For each hit, include in follow-up: "I see you usually use {default}. Use that, or specify a different value?"

**Follow-up prompt generation**: When profile defaults are available, the follow-up prompt includes them:
```
I still need a few details:
- Duration: I see you usually use 30 minutes. Use that, or specify a different value?
- Time: When would you like to schedule?
```

### 7.5 Shared Infrastructure Usage

| Shared Utility | Usage |
|----------------|-------|
| `shared/api/auth.py` → `get_auth_context()` | Extract user_id, context_tier from JWT |
| `shared/api/error_handlers.py` → `ErrorResponse` | All error response formatting |
| `shared/api/error_handlers.py` → `APIErrorHandler.handle_generic_error()` | Fallback for unexpected errors |
| `shared/schemas/intent.py` → `Intent` | Output contract — imported, not redefined |
| `shared/dependencies.py` → `get_intake_service()` | DI for route handlers |
| `shared/app.py` lifespan | Service initialization with Redis + LLM + Planner + ProfileStore |
| `components/Planner/adapters/llm_adapter.py` → `LLMAdapter`, `AnthropicAdapter` | Shared LLM client |

**DI wiring**:
1. `shared/app.py` lifespan:
   - Create `redis.asyncio.Redis` from `REDIS_URL` env var
   - Reuse `AnthropicAdapter` instance (already created for Planner) or create a shared one
   - Call `create_intake_service(redis_client, llm_adapter, planner_service, preference_service)`
   - Store on `app.state.intake_service`
2. `shared/dependencies.py`: Add `get_intake_service(request) → request.app.state.intake_service`
3. Routes: `service: IntakeService = Depends(get_intake_service)`

**Error handling pattern**: Local `_handle_domain_error(exc)` in routes maps domain exceptions to HTTP status codes + `ErrorResponse`. Shared `APIErrorHandler.handle_generic_error()` as fallback.

---

## 8. Sequences

### 8.1 Happy Path — Single-Turn Intent (Full Flow)

```
Client                    routes.py              IntakeService          SessionStore    IntentParser(LLM)    Planner               ProfileStore
  │ POST /intake/message    │                        │                      │               │                    │                      │
  ├────────────────────────►│ get_auth_context()     │                      │               │                    │                      │
  │                         │ (user_id, tier, tz)    │                      │               │                    │                      │
  │                         ├───────────────────────►│ process_message()    │               │                    │                      │
  │                         │                        │                      │               │                    │                      │
  │                         │                        ├─ session_id=None ───►│ get() → None  │                    │                      │
  │                         │                        │  create new Session  │               │                    │                      │
  │                         │                        │                      │               │                    │                      │
  │                         │                        ├─────────────────────────────────────►│ parse(msg, None)   │                      │
  │                         │                        │                      │               │  LLM call          │                      │
  │                         │                        │                      │               │  ParseResult       │                      │
  │                         │                        │  update session      │               │                    │                      │
  │                         │                        │                      │               │                    │                      │
  │                         │                        ├─────────────────────────────────────────────────────────►│ get_required_entities │
  │                         │                        │                      │               │                    │  RequiredEntities     │
  │                         │                        │                      │               │                    │  (all satisfied)      │
  │                         │                        │                      │               │                    │                      │
  │                         │                        ├─────────────────────►│ save(session) │                    │                      │
  │                         │                        │  build Intent        │               │                    │                      │
  │                         │◄───────────────────────┤ IntakeResponse(ready)│               │                    │                      │
  │◄────────────────────────┤ 200 {status:"ready"}   │                      │               │                    │                      │
```

### 8.2 Happy Path — Multi-Turn with Profile Defaults

```
Turn 1: "I need to meet with Alice"

Client → POST → IntakeService:
  1. Create session
  2. LLM parse → intent=schedule_meeting, entities={attendee: Alice}
  3. Planner.get_required_entities("schedule_meeting", {attendee: Alice})
     → required: [attendee, time, duration_min]
     → missing: [time(no pref key), duration_min(pref_key="default_meeting_duration")]
  4. context_tier=2 → check ProfileStore for "default_meeting_duration"
     → found: 30 minutes
  5. Build follow-up:
     "I still need a few details:
      - Duration: I see you usually use 30 minutes. Use that, or specify a different value?
      - Time: When would you like to schedule?"
  6. Save session (profile_defaults_offered: {duration_min: 30})
  7. Return {status: "collecting", follow_up: "...", missing_fields: ["time", "duration_min"]}

Turn 2: "Tuesday at 10 AM, and yes use the default duration"

Client → POST {msg, session_id} → IntakeService:
  1. Load session (with prior entities + profile_defaults_offered)
  2. LLM parse (with context) → entities={time: "10 AM", date: "Tuesday"}
     + user accepted default → entities += {duration_min: 30}
  3. Planner.get_required_entities("schedule_meeting", {attendee, time, date, duration_min})
     → missing: []
  4. Build Intent from shared/schemas/intent.py
  5. Save session, return {status: "ready", intent: {...}}
```

### 8.3 Tier 1 User — No Profile Defaults

```
Turn 1: "Meet with Alice"

Client → POST → IntakeService:
  1. Create session
  2. LLM parse → intent=schedule_meeting, entities={attendee: Alice}
  3. Planner.get_required_entities() → missing: [time, duration_min]
  4. context_tier=1 → SKIP ProfileStore lookup
  5. Build follow-up: "When would you like to schedule, and for how long?"
  6. Return {status: "collecting", missing_fields: ["time", "duration_min"]}
```

### 8.4 Graceful Degradation — Planner Unavailable

```
Client → POST → IntakeService:
  1. Create/load session
  2. LLM parse → intent=schedule_meeting, entities={attendee: Alice, time: 10 AM}
  3. Planner.get_required_entities() → EXCEPTION (Planner down)
  4. Fallback heuristic: intent detected + ≥1 entity → ready=True
  5. Build Intent, return {status: "ready", intent: {...}}
```

### 8.5 Graceful Degradation — LLM Unavailable

```
Client → POST → IntakeService:
  1. Create/load session
  2. IntentParser.parse() → IntentParserError (LLM timeout/error)
  3. Fallback: ParseResult(intent=None, entities={})
  4. Session has no intent → return {status: "collecting", follow_up: "What would you like me to help you with?"}
```

### 8.6 Error — Tool Not Available

```
Client → POST {msg: "Book a flight to Paris"} → IntakeService:
  1. Create session
  2. LLM parse → intent=book_flight, entities={destination: Paris}
  3. Planner.get_required_entities("book_flight", {destination: Paris})
     Step A: LLM suggests tools_needed=["airline.booking"], entities=[destination, date, ...]
     Step B: Check PluginRegistry → "airline.booking" NOT in registered tools
     → raises ToolNotAvailableError(intent_type="book_flight", required_tools=["airline.booking"])
  4. Intake catches ToolNotAvailableError
  → routes._handle_domain_error() → HTTP 422 {
       "error_code": "TOOL_NOT_AVAILABLE",
       "message": "I can't help with 'book_flight' — the required tool(s) [airline.booking] are not available."
     }
```

### 8.7 Error — Redis Down

```
Client → POST → IntakeService → SessionStore.get() → redis.RedisError
  → SessionStoreUnavailableError
  → routes._handle_domain_error() → HTTP 503 {"error_code": "SESSION_STORE_UNAVAILABLE"}
```

### 8.8 Error — Max Turns Exceeded

```
Client → POST {msg, session_id} → IntakeService:
  1. Load session → len(turns) >= 20 → MaxTurnsExceededError
  → routes._handle_domain_error() → HTTP 400 {"error_code": "MAX_TURNS_EXCEEDED"}
```

### 8.9 Error — Session Not Found on Reset

```
Client → DELETE /intake/session/{id} → IntakeService.reset_session():
  1. SessionStore.delete() returns False → SessionNotFoundError
  → routes._handle_domain_error() → HTTP 404 {"error_code": "SESSION_NOT_FOUND"}
```

### 8.10 Retry / Idempotency

Message submission is **NOT idempotent** — each POST creates a new turn in the session. This is by design: user messages are sequential conversation turns, not retryable operations. If a client retries after a network failure:
- If the first request succeeded (session saved): the retry adds a duplicate turn. Mitigation: client should check response before retrying.
- If the first request failed before save: the retry is the first successful attempt — no duplicate.

Session reset (`DELETE`) IS idempotent from a client perspective: deleting an already-deleted session returns 404, which the client can treat as success.

---

## 9. Dependencies & External Integrations

### Python Packages

| Package | Version | Justification |
|---------|---------|---------------|
| `fastapi` | >=0.109.0 | HTTP framework (existing dep) |
| `pydantic` | >=2.0 | Data validation (existing dep) |
| `redis[hiredis]` | >=5.0 | Async Redis client (existing dep) |
| `ulid-py` | >=1.1.0 | Session ID generation (existing dep) |
| `anthropic` | >=0.39.0 | LLM API client for intent parsing (existing dep — shared with Planner) |

No new dependencies required — all packages already in `pyproject.toml`.

### Internal Infrastructure

| Dependency | Module | Purpose |
|------------|--------|---------|
| Auth middleware | `shared/middleware/auth.py` | JWT validation → `request.state` |
| Auth utilities | `shared/api/auth.py` | `get_auth_context()` → user_id, context_tier |
| Error handlers | `shared/api/error_handlers.py` | `ErrorResponse`, `APIErrorHandler` |
| Intent schema | `shared/schemas/intent.py` | `Intent` model (§2.1) |
| LLMAdapter | `components/Planner/adapters/llm_adapter.py` | Shared LLM protocol + Anthropic adapter |
| DI wiring | `shared/app.py`, `shared/dependencies.py` | Service initialization |

### Component Dependencies

| Component | Method | Purpose | Fallback |
|-----------|--------|---------|----------|
| **Planner** | `get_required_entities(intent_type, collected_entities)` | Determine which entities are needed and which are missing | Heuristic: intent + ≥1 entity → ready |
| **ProfileStore** | `get_preference(user_id, key, tier)` | Fetch stored user defaults for missing entities | Skip defaults, ask user directly |

**MODULAR_ARCHITECTURE update needed**: §4 dependency matrix currently shows `Intake → (none — entry point)`. Must be updated to `Intake → Planner (lightweight query), ProfileStore (consent-gated defaults)`.

### External Services

| Service | Purpose | SLA |
|---------|---------|-----|
| Redis 7 | Session storage | Required for operation; 503 on failure |
| Anthropic Claude API | Intent parsing (via LLMAdapter) | Degrades gracefully; returns "collecting" on failure |

---

## 10. Observability & Safety

### Structured Logging

| Event | Level | Fields | PII |
|-------|-------|--------|-----|
| New session created | INFO | `session_id`, `user_id` | ❌ |
| LLM parse completed | INFO | `session_id`, `intent` (type only), `entity_count` | ❌ |
| Required entities queried | DEBUG | `session_id`, `intent_type`, `missing_count` | ❌ |
| Profile default offered | INFO | `session_id`, `preference_key` (not value) | ❌ |
| Intent ready | INFO | `session_id`, `intent` (type only), `turn_count` | ❌ |
| Session reset | INFO | `session_id`, `user_id` | ❌ |
| Redis error | ERROR | `session_id`, error message | ❌ |
| LLM parse error | WARNING | `session_id`, error type (not message content) | ❌ |
| Max turns exceeded | WARNING | `session_id`, `max_turns` | ❌ |
| Planner query failed | WARNING | `session_id`, `intent_type`, error message | ❌ |

**FR-010 compliance**: User message content (`message` field) MUST NEVER appear in logs. Only `session_id`, `user_id`, and `detected_intent` (type string, not message content) are logged.

### Error Classes

Domain exceptions in `domain/models.py`. Routes format them via `shared/api/error_handlers.ErrorResponse`. The shared module does NOT import Intake internals.

### Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `intake_message_duration_seconds` | Histogram | `status` (collecting/ready) | Message processing latency |
| `intake_messages_total` | Counter | `status` (collecting/ready/error) | Total messages processed |
| `intake_sessions_created_total` | Counter | — | New sessions created |
| `intake_sessions_reset_total` | Counter | — | Sessions explicitly reset |
| `intake_errors_total` | Counter | `error_code` | Errors by type |
| `intake_session_turns` | Histogram | — | Turns per session at completion |
| `intake_llm_parse_duration_seconds` | Histogram | `model` | LLM intent parsing latency |
| `intake_llm_parse_errors_total` | Counter | `error_type` | LLM parsing failures |
| `intake_planner_query_duration_seconds` | Histogram | — | Planner get_required_entities latency |
| `intake_profile_defaults_offered_total` | Counter | — | Times profile defaults were offered |

---

## 11. Caching Strategy

### Redis Session Cache

| Aspect | Design |
|--------|--------|
| **What is cached** | Session state (turns, entities, intent, constraints, profile_defaults_offered) |
| **Key structure** | `session:{user_id}:{session_id}` |
| **TTL** | 3600s (1h), refreshed on every `save()` |
| **Max size** | 50KB per session |
| **Serialization** | JSON via Pydantic `model_dump_json()` |
| **Invalidation** | Explicit DELETE (session reset) or TTL expiry |
| **Redis unavailable** | HTTP 503 — no fallback cache (sessions are primary store, not cache) |

Note: Redis is the **primary store** for sessions, not a cache layer over PostgreSQL. There is no read-through/write-through cache pattern — sessions exist only in Redis.

---

## 12. Non-Functional Requirements

### Performance

| Metric | Target (local) | Target (cloud) | Notes |
|--------|----------------|----------------|-------|
| Message → response p95 | < 2s | < 1.5s | Includes LLM parse + Planner query |
| Message → response p99 | < 4s | < 3s | Worst case with LLM + ProfileStore |
| LLM parse (Haiku) p95 | < 1s | < 800ms | Intent extraction is a simple task |
| Planner query (cached tools) p95 | < 100ms | < 50ms | PluginRegistry catalog lookup |
| Planner query (LLM fallback) p95 | < 2s | < 1.5s | When tool not in catalog |
| Redis GET/SET | < 5ms | < 2ms | Local network |
| ProfileStore lookup p95 | < 50ms | < 20ms | DB query for single preference |

**Note**: Performance targets are significantly relaxed from the original spec (< 200ms). The LLM-based parsing adds ~500ms-1s of latency. This is acceptable because Intake is a conversational interface where users expect a brief response time, not real-time. The spec's < 200ms target applied to the rules-based parser and should be updated.

### Availability

| Environment | Target | Notes |
|-------------|--------|-------|
| Cloud | 99.9% (< 43min/month) | Per GLOBAL_SPEC §3 |
| Local | Best-effort | Redis + LLM dependency |

### Throughput

| Scenario | Target |
|----------|--------|
| Single-user | 10 msg/s (LLM-limited) |
| Multi-user (10 concurrent) | 5 msg/s per user |

### Limits

| Limit | Value | Enforcement |
|-------|-------|-------------|
| Max message length | 10,000 chars | Pydantic `max_length` |
| Max turns per session | 20 | Service-layer check |
| Max session state size | 50KB | Warning log on save |
| Session TTL | 1 hour | Redis SETEX |

### Testing Strategy

| Test File | Scope | Coverage |
|-----------|-------|----------|
| `test_unit.py` | LLM parser (mocked LLM), session store (mocked Redis) | Entity extraction, intent detection, context merging |
| `test_service.py` | IntakeService with mocked adapters | Multi-turn flow, session lifecycle, readiness via Planner, profile defaults, consent tier gating, error paths |
| `test_contract.py` | Intent §2.1 conformance | All emitted Intents pass `Intent.model_validate()` |
| `test_observability.py` | No PII in logs | Capture log output, assert no message content |

---

## 13. Architectural Considerations

### Blast Radius Containment

Intake has three external dependencies (Redis, LLM API, Planner/ProfileStore). Failure modes:
- **Redis down**: HTTP 503 for all session operations. No cascade to other components.
- **LLM down**: Intent parsing fails → empty ParseResult → "collecting" with generic prompt. No cascade.
- **Planner down**: Entity requirements unknown → fallback to heuristic (intent + ≥1 entity → ready). May emit incomplete Intents, but downstream validation catches this.
- **ProfileStore down**: Cannot offer stored defaults → skip defaults, ask user for all missing entities. No cascade.
- **Session data corruption**: 1h TTL bounds exposure. User can reset session.

### Fault Isolation

- **LLM calls**: Reuse Planner's `CircuitBreaker` for the LLM adapter. Intake uses Haiku model (separate circuit from Planner's primary/fallback models). Circuit opens after 5 consecutive failures, closes after 60s.
- **Planner query**: If Planner service throws, catch and fall back to heuristic. No circuit breaker needed — the Planner's own breakers handle LLM failures internally.
- **ProfileStore**: If preference lookup throws (including `ConsentDeniedError`), skip the default for that entity. No circuit breaker needed — single DB query, fast failure.
- **Redis**: Binary fail/succeed. `SessionStoreUnavailableError` is the single fault boundary.

### Cross-Component Interactions

| Interaction | Type | Direction | Notes |
|-------------|------|-----------|-------|
| Intake → Planner | Library call | Sync (await) | `get_required_entities()` — lightweight, no plan generation |
| Intake → ProfileStore | Library call | Sync (await) | `get_preference()` — consent-gated, single DB query |
| Intake → LLM | HTTP API | Async | Via shared `AnthropicAdapter` — circuit-breakered |
| Intake → Redis | TCP | Async | Session CRUD — binary success/fail |

### Determinism Guarantees

The LLM-based parser is **NOT deterministic** — same message may produce slightly different ParseResults across calls (despite temperature=0.0, LLM outputs are not perfectly reproducible). This is acceptable because:
1. Intake is a conversational interface — slight variation in entity extraction is fine.
2. The Planner's `get_required_entities()` provides a deterministic check on completeness.
3. The final Intent is validated against the shared schema regardless.

### State Management

- **Stateful**: Session state in Redis (ephemeral, 1h TTL).
- **No persistent state**: No PostgreSQL tables.
- **Data loss risk**: Redis restart loses all active sessions. Acceptable — sessions are ephemeral and users can start new ones.

### Background Task Durability

No background tasks. All operations are synchronous request-response.

---

## 14. Architecture Decision Records

### Referenced ADRs

| ADR | Relevance |
|-----|-----------|
| ADR-0001: Component-first architecture | Intake follows `components/Intake/` structure with api/, service/, domain/, adapters/, tests/ |

### New Decisions (not requiring ADR)

1. **Redis key format**: `session:{user_id}:{session_id}` (deviation from MODULAR_ARCHITECTURE `session:{user_id}`). Rationale: supports multiple concurrent sessions per user. MODULAR_ARCHITECTURE should be updated to reflect this pattern.

2. **Open intent taxonomy**: Intake accepts any intent string. Downstream validation by Planner/PluginRegistry. No ADR needed — this was a spec-level decision.

3. **LLM-based parser**: Uses Anthropic Claude (Haiku) via shared `LLMAdapter` protocol. Chosen over rules-based MVP because:
   - Rules-based parser cannot accurately judge entity completeness for arbitrary intent types (open taxonomy).
   - LLM naturally handles synonyms, implicit entities, and multi-language input.
   - Cost per parse is minimal (~$0.001 per Haiku call for short extraction tasks).

4. **Planner-driven readiness**: Readiness is determined by Planner's `get_required_entities()` instead of a local heuristic. Chosen because:
   - Planner has access to tool definitions (via PluginRegistry) and knows exactly what entities each tool needs.
   - Avoids duplicating tool knowledge in Intake.
   - Graceful fallback to heuristic if Planner is unavailable.

5. **Consent-gated profile defaults**: ProfileStore integration gated by consent tier from JWT. Chosen because:
   - GLOBAL_SPEC §7 requires consent enforcement for profile data access.
   - Better UX for Tier 2+ users — fewer follow-up prompts when defaults match intent.
   - Tier 1 users are unaffected — same behavior as if ProfileStore didn't exist.

---

## 15. Risks & Open Questions

### Risks

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| R-001 | LLM parsing adds 500ms-1s latency per message | Medium | Use Haiku (fastest model); circuit breaker for fast failure; update spec targets |
| R-002 | Redis unavailability causes all sessions to fail | Medium | HTTP 503 with clear error; sessions are ephemeral (1h TTL) |
| R-003 | Session state grows unbounded with many turns | Low | Capped at 20 turns, 50KB max state, 10K char message limit |
| R-004 | Redis key pattern deviation from MODULAR_ARCHITECTURE | Low | Document deviation; update MODULAR_ARCHITECTURE to `session:{user_id}:{session_id}` |
| R-005 | Planner query adds cross-component coupling | Medium | Lightweight method with no plan generation; graceful fallback to heuristic |
| R-006 | LLM may extract incorrect entities | Medium | Planner validates completeness; user can correct in subsequent turns |
| R-007 | ANTHROPIC_API_KEY must be set for Intake to parse | High | Shared with Planner — already a deployment requirement; clear error on startup |

### Open Questions

1. **Follow-up prompts**: Static templates vs LLM-generated for the "missing fields" prompt. Decision: LLM-generated for clarity when profile defaults are offered (template cannot express "Use X or specify different?"), static fallback when LLM unavailable.
2. **Timezone source**: `X-Timezone` header (current) vs ProfileStore lookup. Decision: header for now, deferred.
3. **MODULAR_ARCHITECTURE update**: Need to add `Intake → Planner, ProfileStore` to dependency matrix. Deferred to PR review.
