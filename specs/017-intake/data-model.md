# Data Model: Intake

**Date**: 2026-03-26

## Entities

### Session (Redis — ephemeral state)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| session_id | str | ✅ | `ses_<26-char ULID>` |
| user_id | str | ✅ | UUID string from JWT `sub` claim |
| turns | list[SessionTurn] | ✅ | Ordered list of conversation turns |
| detected_intent | str \| None | | Best-fit intent type accumulated across turns |
| extracted_entities | dict[str, Any] | ✅ | Merged entities from all turns (latest wins) |
| extracted_constraints | dict[str, Any] | ✅ | Merged constraints from all turns |
| created_at | datetime (UTC) | ✅ | Session creation timestamp |
| updated_at | datetime (UTC) | ✅ | Last message timestamp |

**Storage**: Redis key `session:{user_id}:{session_id}`, TTL 3600s, JSON serialized via Pydantic `model_dump_json()`.

**Limits**: 50KB max session state, 20 max turns.

### SessionTurn (embedded in Session)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| message | str | ✅ | Raw user message text |
| timestamp | datetime (UTC) | ✅ | Turn timestamp |
| extracted_intent | str \| None | | Intent detected in this turn |
| extracted_entities | dict[str, Any] | ✅ | Entities extracted from this turn |
| extracted_constraints | dict[str, Any] | ✅ | Constraints extracted from this turn |

### IntakeMessage (request body)

| Field | Type | Required | Validation | Description |
|-------|------|----------|------------|-------------|
| message | str | ✅ | min_length=1, max_length=10,000 | User message text |
| session_id | str \| None | | | Existing session to continue |

### IntakeResponse (response body)

| Field | Type | Present When | Description |
|-------|------|-------------|-------------|
| status | Literal["collecting", "ready"] | always | Current session state |
| session_id | str | always | Active session ID |
| detected_intent | str \| None | always | Current best-fit intent |
| collected_entities | dict[str, Any] | always | All extracted entities |
| missing_fields | list[str] | collecting | Fields still needed |
| follow_up | str \| None | collecting | Prompt for missing info |
| turn_count | int | always | Total turns in session |
| intent | Intent \| None | ready | Finalized GLOBAL_SPEC §2.1 Intent |

### Intent (GLOBAL_SPEC §2.1 — output contract)

**Source**: `shared/schemas/intent.py` — imported, NOT redefined.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| intent | str | ✅ | Action type (min_length=1) |
| entities | dict[str, Any] | ✅ | Extracted entities |
| constraints | dict[str, Any] | ✅ | User-specified constraints |
| tz | str | ✅ | IANA timezone (default: "America/Chicago") |
| user_id | str | ✅ | UUID string |
| context_budget | int \| None | | 1-5, None = system decides |
| session_id | str \| None | | Session identifier |
| trace_id | str \| None | | 32-char hex distributed trace ID |

### ParseResult (internal value object)

| Field | Type | Description |
|-------|------|-------------|
| intent | str \| None | Detected intent type |
| entities | dict[str, Any] | Extracted entities |
| constraints | dict[str, Any] | Extracted constraints |

### ReadinessResult (internal value object)

| Field | Type | Description |
|-------|------|-------------|
| ready | bool | Whether session has enough info |
| missing_fields | list[str] | What's still needed |
| follow_up | str \| None | Prompt template for user |

## Relationships

```
IntakeMessage ──POST──→ IntakeService ──creates/loads──→ Session (Redis)
                              │
                              ├──parse──→ IntentParser → ParseResult
                              ├──check──→ ReadinessChecker → ReadinessResult
                              │
                              └──emit──→ IntakeResponse
                                           └──(if ready)──→ Intent (§2.1)
```

## Error Hierarchy

```
IntakeError (base)
├── SessionNotFoundError(session_id)
├── SessionOwnershipError(session_id, user_id)
├── MaxTurnsExceededError(session_id, max_turns=20)
└── SessionStoreUnavailableError(reason)
```

## State Transitions

```
[No Session] ──first message──→ Session(collecting)
                                     │
              ┌──more info needed────┘
              │                      │
              ▼                      ▼
    Session(collecting)     Session(ready) ──emit Intent──→ Done
         │
         └──max turns──→ MaxTurnsExceededError
```
