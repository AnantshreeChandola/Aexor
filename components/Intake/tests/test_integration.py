"""
Intake Integration Tests

End-to-end multi-turn flows with mocked external dependencies.
Tests the full pipeline: routes → service → adapters → response.

Reference: T602
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from components.Intake.domain.models import ParseResult
from components.Intake.service.intake_service import IntakeService
from components.Planner.domain.models import (
    EntityRequirement,
    RequiredEntitiesResult,
)
from shared.schemas.intent import Intent

USER_ID = "550e8400-e29b-41d4-a716-446655440000"


def _make_service(
    parser_responses: list[ParseResult],
    planner_responses: list[RequiredEntitiesResult],
    preference_value: int | None = 30,
) -> IntakeService:
    """Build IntakeService with scripted mock responses."""
    parser = AsyncMock()
    parser.parse = AsyncMock(side_effect=parser_responses)

    planner = AsyncMock()
    planner.get_required_entities = AsyncMock(side_effect=planner_responses)

    preference = AsyncMock()
    if preference_value is not None:
        from shared.schemas.evidence import EvidenceItem

        preference.get_preference = AsyncMock(
            return_value=EvidenceItem(
                type="preference",
                key="default_meeting_duration",
                value=preference_value,
                confidence=1.0,
                source_ref="profilestore:prefs/default_meeting_duration",
                tier=2,
            )
        )
    else:
        preference.get_preference = AsyncMock(side_effect=RuntimeError("ProfileStore down"))

    # Use a real-ish in-memory store
    store = _InMemorySessionStore()

    return IntakeService(
        session_store=store,
        intent_parser=parser,
        planner_service=planner,
        preference_service=preference,
    )


class _InMemorySessionStore:
    """Simple in-memory session store for integration tests."""

    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, user_id: str, session_id: str):
        from components.Intake.domain.models import Session

        key = f"session:{user_id}:{session_id}"
        raw = self._data.get(key)
        if raw is None:
            return None
        return Session.model_validate_json(raw)

    async def save(self, session):
        key = f"session:{session.user_id}:{session.session_id}"
        self._data[key] = session.model_dump_json()

    async def delete(self, user_id: str, session_id: str) -> bool:
        key = f"session:{user_id}:{session_id}"
        if key in self._data:
            del self._data[key]
            return True
        return False


class TestMultiTurnIntegration:
    async def test_two_turn_meeting_booking(self):
        """Full 2-turn flow: collecting → ready."""
        svc = _make_service(
            parser_responses=[
                # Turn 1
                ParseResult(
                    intent="schedule_meeting",
                    entities={"attendee": "Alice"},
                ),
                # Turn 2
                ParseResult(
                    intent="schedule_meeting",
                    entities={"time": "10 AM", "date": "Tuesday", "duration_min": 30},
                ),
            ],
            planner_responses=[
                # Turn 1: missing time, duration_min
                RequiredEntitiesResult(
                    intent_type="schedule_meeting",
                    resolved_tools=["google.calendar"],
                    required_entities=[
                        EntityRequirement(name="attendee", description="Who?"),
                        EntityRequirement(name="time", description="When?"),
                        EntityRequirement(
                            name="duration_min",
                            description="How long?",
                            default_preference_key="default_meeting_duration",
                        ),
                    ],
                    missing_entities=[
                        EntityRequirement(name="time", description="When?"),
                        EntityRequirement(
                            name="duration_min",
                            description="How long?",
                            default_preference_key="default_meeting_duration",
                        ),
                    ],
                ),
                # Turn 2: nothing missing
                RequiredEntitiesResult(
                    intent_type="schedule_meeting",
                    resolved_tools=["google.calendar"],
                    required_entities=[
                        EntityRequirement(name="attendee", description="Who?"),
                        EntityRequirement(name="time", description="When?"),
                        EntityRequirement(name="duration_min", description="How long?"),
                    ],
                    missing_entities=[],
                ),
            ],
        )

        # Turn 1
        r1 = await svc.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=2,
        )
        assert r1.status == "collecting"
        assert r1.detected_intent == "schedule_meeting"
        assert r1.collected_entities["attendee"] == "Alice"
        assert r1.turn_count == 1
        assert "usually use" in r1.follow_up  # Profile default offered

        # Turn 2 (same session)
        r2 = await svc.process_message(
            user_id=USER_ID,
            message="Tuesday at 10 AM, 30 min",
            context_tier=2,
            session_id=r1.session_id,
        )
        assert r2.status == "ready"
        assert r2.turn_count == 2
        assert r2.session_id == r1.session_id

        # Validate the emitted Intent
        intent = Intent.model_validate(r2.intent)
        assert intent.intent == "schedule_meeting"
        assert intent.entities["attendee"] == "Alice"
        assert intent.entities["time"] == "10 AM"
        assert intent.user_id == USER_ID
        assert intent.session_id == r1.session_id

    async def test_tier1_user_no_defaults(self):
        """Tier 1 user gets no profile defaults."""
        svc = _make_service(
            parser_responses=[
                ParseResult(
                    intent="schedule_meeting",
                    entities={"attendee": "Alice"},
                ),
            ],
            planner_responses=[
                RequiredEntitiesResult(
                    intent_type="schedule_meeting",
                    resolved_tools=["google.calendar"],
                    required_entities=[
                        EntityRequirement(name="attendee", description="Who?"),
                        EntityRequirement(
                            name="duration_min",
                            description="How long?",
                            default_preference_key="default_meeting_duration",
                        ),
                    ],
                    missing_entities=[
                        EntityRequirement(
                            name="duration_min",
                            description="How long?",
                            default_preference_key="default_meeting_duration",
                        ),
                    ],
                ),
            ],
        )

        resp = await svc.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=1,
        )
        assert resp.status == "collecting"
        assert "usually use" not in resp.follow_up

    async def test_session_reset_then_new_session(self):
        """After reset, next message creates a fresh session."""
        svc = _make_service(
            parser_responses=[
                ParseResult(intent="schedule_meeting", entities={"attendee": "Alice"}),
                ParseResult(intent="send_email", entities={"to": "Bob"}),
            ],
            planner_responses=[
                RequiredEntitiesResult(
                    intent_type="schedule_meeting",
                    resolved_tools=["google.calendar"],
                    required_entities=[
                        EntityRequirement(name="attendee", description="Who?"),
                    ],
                    missing_entities=[
                        EntityRequirement(name="time", description="When?"),
                    ],
                ),
                RequiredEntitiesResult(
                    intent_type="send_email",
                    resolved_tools=["google.gmail"],
                    required_entities=[
                        EntityRequirement(name="to", description="Recipient"),
                    ],
                    missing_entities=[
                        EntityRequirement(name="subject", description="Subject?"),
                    ],
                ),
            ],
        )

        # Start a session
        r1 = await svc.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=1,
        )
        old_session = r1.session_id

        # Reset it
        await svc.reset_session(USER_ID, old_session)

        # New message creates fresh session
        r2 = await svc.process_message(
            user_id=USER_ID,
            message="Send email to Bob",
            context_tier=1,
        )
        assert r2.session_id != old_session
        assert r2.detected_intent == "send_email"

    async def test_planner_down_heuristic_fallback(self):
        """When Planner is down, heuristic kicks in."""
        parser = AsyncMock()
        parser.parse = AsyncMock(
            return_value=ParseResult(
                intent="schedule_meeting",
                entities={"attendee": "Alice"},
            )
        )
        planner = AsyncMock()
        planner.get_required_entities = AsyncMock(side_effect=RuntimeError("Planner down"))
        preference = AsyncMock()

        svc = IntakeService(
            session_store=_InMemorySessionStore(),
            intent_parser=parser,
            planner_service=planner,
            preference_service=preference,
        )

        resp = await svc.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=1,
        )
        # Heuristic: intent + entities -> ready
        assert resp.status == "ready"
