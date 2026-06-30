# Feature Specification: Intake

**Feature Branch**: `feat/intake`
**Created**: 2026-03-20
**Status**: Draft
**Input**: User description: "Intake — multi-turn intent collection and session management"

---

## Overview

The Intake component is the system's HTTP entry point (API/Interface Layer). It receives raw user messages, manages multi-turn conversation state via Redis-backed sessions, and progressively accumulates extracted entities and constraints across turns. Intake accepts **any** user intent — it does not restrict to a fixed taxonomy. If downstream components (Planner, PluginRegistry) determine that a required tool is missing or unauthorized, they return errors — Intake does not gatekeep intent types. Intake auto-detects readiness using heuristic rules (does it have an intent type + at least one entity?). Users can start a new session at any time (via a "new request" action) to abandon an in-progress collection and begin fresh.

---

## User Scenarios & Testing

### User Story 1 — Single-Turn Intent Submission (Priority: P1)

A user sends a fully specified message in one turn.

**Why this priority**: The simplest happy path — validates the core message-to-Intent pipeline.

**Independent Test**: POST a single message with all required fields; receive an Intent JSON back with `status: "ready"`.

**Acceptance Scenarios**:

1. **Given** an authenticated user, **When** they POST `{"message": "Book a 30-min meeting with Alice on Tuesday at 10 AM"}`, **Then** the response contains `status: "ready"` and a valid Intent JSON with `intent: "schedule_meeting"`, entities `{attendee, time, duration_min}`, and constraints.
2. **Given** an authenticated user, **When** they POST a message without a `message` field, **Then** the response is HTTP 422 with an ErrorResponse body.

---

### User Story 2 — Multi-Turn Intent Collection (Priority: P1)

A user's intent requires multiple messages to fully specify.

**Why this priority**: Core differentiator — Intake must track state across turns and prompt for missing information.

**Independent Test**: Send a vague first message, receive `status: "collecting"` with a follow-up prompt. Send a second message with the missing info, receive `status: "ready"` with the finalized Intent.

**Acceptance Scenarios**:

1. **Given** a new session, **When** the user sends `"I need to meet with Alice"`, **Then** the response is `status: "collecting"` with a `follow_up` prompt asking for time/date.
2. **Given** an active session with prior context `"meet with Alice"`, **When** the user sends `"Tuesday at 10 AM for 30 minutes"`, **Then** the response is `status: "ready"` with a complete Intent containing all extracted entities.
3. **Given** an active session, **When** the user sends a message that contradicts a prior entity (e.g., changes the attendee), **Then** the session state is updated and the latest entity value is used.

---

### User Story 3 — Session Management (Priority: P2)

Sessions persist across messages and expire after inactivity.

**Why this priority**: Enables multi-turn — without sessions, Intake is stateless and can't collect incrementally.

**Independent Test**: Create a session, verify it persists across two messages, verify it expires after TTL.

**Acceptance Scenarios**:

1. **Given** a user's first message, **When** no prior session exists, **Then** a new session is created with a `session_id` and the response includes the `session_id`.
2. **Given** an existing session, **When** the user sends a subsequent message with the same `session_id`, **Then** the session state is loaded and extended.
3. **Given** a session older than the TTL (1 hour), **When** the user sends a new message, **Then** a fresh session is created (old state is discarded).

---

### User Story 4 — Session Reset (Priority: P3)

A user explicitly starts over.

**Why this priority**: Quality-of-life — users may want to abandon a partially collected intent and start fresh.

**Independent Test**: POST a reset request with an active session_id; verify session is deleted and next message creates a new session.

**Acceptance Scenarios**:

1. **Given** an active session with collected entities, **When** the user DELETEs the session endpoint with `session_id`, **Then** the session is deleted from Redis and the response confirms reset.
2. **Given** a non-existent session_id, **When** the user DELETEs the session, **Then** the response is HTTP 404.

---

### Edge Cases

- What happens when Redis is unavailable? → Intake returns HTTP 503 with `"Session service unavailable"`.
- What happens when a message exceeds the max length (10,000 chars)? → HTTP 422 validation error.
- What happens when `user_id` from auth does not match `session_id` ownership? → HTTP 403 Forbidden.
- What happens when intent type cannot be determined? → `status: "collecting"` with a generic clarification prompt ("What would you like me to help you with?").
- What happens when a user's intent requires an unavailable tool? → Intake emits the Intent anyway; Planner/PluginRegistry return the error downstream.

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST accept HTTP POST requests with a JSON body containing a `message` string field and an optional `session_id`.
- **FR-002**: System MUST create and manage sessions in Redis under key `session:{user_id}:{session_id}` with 1-hour TTL (per MODULAR_ARCHITECTURE Table Ownership Map).
- **FR-003**: System MUST parse user messages via LLM (Anthropic Claude) behind an `IntentParser` protocol to extract `intent`, `entities`, and `constraints` for the Intent contract (GLOBAL_SPEC §2.1). The parser MUST NOT restrict to a fixed intent taxonomy — open taxonomy, downstream validation.
- **FR-004**: System MUST determine session readiness by querying Planner's `get_required_entities(intent_type, collected_entities)` lightweight method. Planner uses its tool knowledge (via PluginRegistry) to determine what entities are needed for a given intent and which are still missing. Readiness = all required entities collected.
- **FR-005**: System MUST return either `status: "collecting"` (with `follow_up` prompt and partial entities) or `status: "ready"` (with finalized Intent JSON).
- **FR-006**: System MUST populate `trace_id` (32-char hex) on the Intent for distributed tracing correlation.
- **FR-007**: System MUST populate `session_id` on the Intent from the active session.
- **FR-008**: System MUST support explicit session reset via DELETE endpoint.
- **FR-009**: System MUST enforce that sessions are scoped to the authenticated `user_id` — a user cannot access another user's session.
- **FR-010**: System MUST use structured logging with no PII (user message content) in logs.
- **FR-011**: System MUST be consent-tier-aware. When entities are missing and the user's consent tier is ≥ 2 (Tier 2: stable preferences), Intake MUST check ProfileStore for stored defaults. For each missing entity with a profile default, Intake prompts: "I see you usually use X. Use that, or specify a different value?" For Tier 1 (session-only) users, Intake asks directly without offering defaults.
- **FR-012**: The Planner component MUST expose a lightweight `get_required_entities(intent_type, collected_entities)` method that returns required entity names, descriptions, whether they're optional, and which ProfileStore preference key (if any) maps to each entity. This avoids full plan generation for entity discovery.

### Key Entities

- **Session**: Conversation state (session_id, user_id, turns[], extracted_entities, extracted_constraints, detected_intent, created_at, updated_at)
- **IntakeMessage**: Incoming user message (message text, optional session_id)
- **IntakeResponse**: Response to user (status, session_id, follow_up prompt or finalized Intent)
- **Intent**: Output contract (GLOBAL_SPEC §2.1) — `intent`, `entities`, `constraints`, `tz`, `user_id`, `session_id`, `trace_id`

---

## Interfaces & Contracts (conform to GLOBAL_SPEC v2.2)

### Input: HTTP POST `/api/intake/message`

```json
{
  "message": "Book a meeting with Alice next Tuesday at 10 AM",
  "session_id": "optional-existing-session-id"
}
```

**Auth**: Bearer JWT → `request.state.user_id`, `request.state.context_tier`, `request.state.email`

### Output: IntakeResponse

**When collecting (incomplete):**
```json
{
  "status": "collecting",
  "session_id": "ses_01JXYZ...",
  "detected_intent": "schedule_meeting",
  "collected_entities": {"attendee": "Alice"},
  "missing_fields": ["time", "duration_min"],
  "follow_up": "When would you like to schedule the meeting, and for how long?",
  "turn_count": 1
}
```

**When ready (complete):**
```json
{
  "status": "ready",
  "session_id": "ses_01JXYZ...",
  "intent": {
    "intent": "schedule_meeting",
    "entities": {"attendee": "Alice", "time": "Tuesday 10 AM", "duration_min": 30},
    "constraints": {"prefer_afternoon": false},
    "tz": "America/Chicago",
    "user_id": "550e8400-e29b-41d4-a716-446655440000",
    "context_budget": null,
    "session_id": "ses_01JXYZ...",
    "trace_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
  }
}
```

### Session Reset: DELETE `/api/intake/session/{session_id}`

```json
{
  "status": "reset",
  "session_id": "ses_01JXYZ..."
}
```

**Note**: Intake is an internal component — the safety model (Preview/Execute wrappers) does NOT apply. Per GLOBAL_SPEC §1: "This safety model applies to user-facing plans (Intent → Plan → Preview → Execute), NOT to internal component operations."

Reference: docs/architecture/GLOBAL_SPEC.md (v2.2)

---

## Component Mapping

- **Target**: `components/Intake/`
- **Files expected to change:**
  - `components/Intake/api/routes.py` — FastAPI router (`POST /message`, `DELETE /session/{session_id}`)
  - `components/Intake/service/intake_service.py` — IntakeService (session orchestration, readiness check)
  - `components/Intake/domain/models.py` — IntakeMessage, IntakeResponse, Session, IntakeError hierarchy
  - `components/Intake/adapters/session_store.py` — Redis session adapter (get/set/delete/extend TTL)
  - `components/Intake/adapters/intent_parser.py` — IntentParser protocol + RulesBasedParser (MVP)
  - `components/Intake/adapters/readiness_checker.py` — ReadinessChecker protocol + RulesBasedReadinessChecker (MVP)
  - `components/Intake/tests/conftest.py` — Fixtures, mock Redis, sample messages
  - `components/Intake/tests/test_unit.py` — Parser, readiness checker, session store tests
  - `components/Intake/tests/test_service.py` — IntakeService integration tests
  - `components/Intake/tests/test_contract.py` — Intent §2.1 conformance, response schema
  - `components/Intake/tests/test_observability.py` — No PII in logs, structured fields
  - `shared/app.py` — DI wiring for IntakeService
  - `shared/dependencies.py` — `get_intake_service()` dependency

---

## Dependencies & Risks

### Dependencies
- **Redis 7**: Session storage (`session:{user_id}:{session_id}`, 1h TTL) — already in tech stack
- **FastAPI**: HTTP routes — already in use
- **Anthropic Claude API**: LLM-based intent parsing via `LLMAdapter` protocol (reused from Planner)
- **Planner component**: `get_required_entities()` lightweight query for entity completeness checking
- **ProfileStore component**: `get_preference()` / `get_all_preferences()` for consent-gated user defaults (Tier 2+)
- **shared/schemas/intent.py**: Intent Pydantic model — already exists
- **shared/middleware/auth.py**: JWT auth → `request.state.user_id`, `request.state.context_tier` — already exists
- **shared/api/error_handlers.py**: ErrorResponse for error formatting — already exists

### Risks
- **R-001**: Rules-based intent parsing (MVP) may have low accuracy for complex or ambiguous messages. Mitigated by the `IntentParser` protocol — swap to LLM-based parser in future without service layer changes.
- **R-002**: Redis unavailability causes session loss. Mitigated by returning HTTP 503 with clear error; sessions are ephemeral (1h TTL), so data loss is bounded.
- **R-003**: Session state grows unbounded if users send many turns. Mitigated by capping `max_turns` per session (default: 20) and `max_message_length` (10,000 chars).

---

## Non-Functional Requirements

### Baseline (inherited from GLOBAL_SPEC)
- **Intake p95**: < 200ms (message → response)
- **Availability**: 99.9% (< 43min downtime/month)
- **Structured logs**: Correlated by `session_id`, `user_id` (no PII — no message content in logs)
- **No secrets/PII in logs**

### Deltas
- **Session TTL**: 1 hour (per MODULAR_ARCHITECTURE Redis key pattern)
- **Max turns per session**: 20
- **Max message length**: 10,000 characters
- **Max session state size**: 50KB (Redis value limit for this key)

---

## Open Questions

1. ~~**Intent taxonomy**~~: **RESOLVED** — Open taxonomy. Intake accepts any intent; tool availability is validated downstream by Planner (PlanValidationError "Unknown tools") and PluginRegistry (ToolNotFoundError → 404).
2. **Follow-up prompt generation**: Should follow-up prompts be static templates or LLM-generated? Proposal: static templates for MVP (deterministic, no API cost).
3. **Timezone source**: Should `tz` come from user profile (ProfileStore) or client request? Proposal: client header `X-Timezone` with fallback to `America/Chicago`.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Single-turn messages produce a valid Intent in < 200ms (p95)
- **SC-002**: Multi-turn conversations correctly collect entities across 2-5 turns
- **SC-003**: Sessions expire after 1 hour of inactivity
- **SC-004**: All emitted Intents pass `Intent.model_validate()` (GLOBAL_SPEC §2.1 conformance)
- **SC-005**: No user message content appears in any log output

---

## Conformance

This work conforms to docs/architecture/GLOBAL_SPEC.md v2.2 and docs/architecture/MODULAR_ARCHITECTURE.md v1.3.
