# Quickstart: Intake

**Date**: 2026-03-26

## Scenario 1: Single-Turn Intent (Happy Path)

```bash
# User sends a fully specified message
curl -X POST http://localhost:8000/intake/message \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Timezone: America/Chicago" \
  -d '{"message": "Book a 30-min meeting with Alice on Tuesday at 10 AM"}'

# Response: status "ready" with finalized Intent
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
    "user_id": "550e8400-...",
    "session_id": "ses_01JXYZ...",
    "trace_id": "a1b2c3d4..."
  }
}
```

## Scenario 2: Multi-Turn Collection

```bash
# Turn 1: Vague message
curl -X POST http://localhost:8000/intake/message \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "I need to meet with Alice"}'

# Response: collecting
{
  "status": "collecting",
  "session_id": "ses_01JABC...",
  "detected_intent": "schedule_meeting",
  "collected_entities": {"attendee": "Alice"},
  "missing_fields": ["entities"],
  "follow_up": "When would you like to schedule the meeting, and for how long?",
  "turn_count": 1
}

# Turn 2: Provide missing info (same session)
curl -X POST http://localhost:8000/intake/message \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Tuesday at 10 AM for 30 minutes", "session_id": "ses_01JABC..."}'

# Response: ready
{
  "status": "ready",
  "session_id": "ses_01JABC...",
  "detected_intent": "schedule_meeting",
  "collected_entities": {"attendee": "Alice", "time": "10 AM", "date": "Tuesday", "duration_min": 30},
  "turn_count": 2,
  "intent": { ... }
}
```

## Scenario 3: Session Reset

```bash
# Reset an active session
curl -X DELETE http://localhost:8000/intake/session/ses_01JABC... \
  -H "Authorization: Bearer $JWT_TOKEN"

# Response
{"status": "reset", "session_id": "ses_01JABC..."}

# Next message creates a fresh session
curl -X POST http://localhost:8000/intake/message \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Send an email to Bob"}'

# Response: new session
{"status": "collecting", "session_id": "ses_01JDEF...", ...}
```

## Scenario 4: Redis Down

```bash
curl -X POST http://localhost:8000/intake/message \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'

# Response: 503
{
  "status": "error",
  "error_code": "SESSION_STORE_UNAVAILABLE",
  "message": "Session service unavailable"
}
```

## Integration Test Prerequisites

1. Redis running on `localhost:6379`
2. JWT_SECRET set in environment
3. Valid JWT token with `sub` (UUID), `email`, `context_tier` claims
