# API Contract: Intake

**Date**: 2026-03-26
**Router prefix**: `/intake`
**Auth**: Bearer JWT → `request.state.user_id`, `request.state.context_tier`, `request.state.email`

---

## POST /intake/message

Submit a user message for intent collection.

### Request

```json
{
  "message": "Book a 30-min meeting with Alice on Tuesday at 10 AM",
  "session_id": "ses_01JXYZ..."  // optional — omit for new session
}
```

**Headers**:
- `Authorization: Bearer <jwt>` (required)
- `X-Timezone: America/New_York` (optional, default: "America/Chicago")

**Validation**:
- `message`: min 1 char, max 10,000 chars
- `session_id`: optional string

### Response — Collecting (HTTP 200)

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

### Response — Ready (HTTP 200)

```json
{
  "status": "ready",
  "session_id": "ses_01JXYZ...",
  "detected_intent": "schedule_meeting",
  "collected_entities": {"attendee": "Alice", "time": "10 AM", "date": "Tuesday", "duration_min": 30},
  "turn_count": 1,
  "intent": {
    "intent": "schedule_meeting",
    "entities": {"attendee": "Alice", "time": "10 AM", "date": "Tuesday", "duration_min": 30},
    "constraints": {},
    "tz": "America/Chicago",
    "user_id": "550e8400-e29b-41d4-a716-446655440000",
    "context_budget": null,
    "session_id": "ses_01JXYZ...",
    "trace_id": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
  }
}
```

### Error Responses

| Status | Error Code | Condition |
|--------|------------|-----------|
| 401 | — | Missing/invalid JWT |
| 422 | VALIDATION_ERROR | Missing `message`, message too long, etc. |
| 400 | MAX_TURNS_EXCEEDED | Session has 20+ turns |
| 503 | SESSION_STORE_UNAVAILABLE | Redis down |

---

## DELETE /intake/session/{session_id}

Reset (delete) an active session.

### Request

**Path**: `session_id` — the session to delete
**Headers**: `Authorization: Bearer <jwt>` (required)

### Response — Success (HTTP 200)

```json
{
  "status": "reset",
  "session_id": "ses_01JXYZ..."
}
```

### Error Responses

| Status | Error Code | Condition |
|--------|------------|-----------|
| 401 | — | Missing/invalid JWT |
| 404 | SESSION_NOT_FOUND | Session doesn't exist or expired |
| 503 | SESSION_STORE_UNAVAILABLE | Redis down |

---

## GET /intake/health

Health check (no auth required).

### Response (HTTP 200)

```json
{
  "status": "ok",
  "service": "intake"
}
```
