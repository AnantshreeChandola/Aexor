"""
Intake Contract Tests

Validates that all emitted Intents conform to GLOBAL_SPEC §2.1
via Intent.model_validate().

Reference: T600
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from components.Intake.domain.models import ParseResult
from components.Intake.service.intake_service import IntakeService
from components.Planner.domain.models import (
    EntityRequirement,
    RequiredEntitiesResult,
)
from shared.schemas.intent import Intent

USER_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture()
def ready_service(
    mock_planner_service,
    mock_preference_service,
    mock_redis_client,
) -> IntakeService:
    """IntakeService configured for ready (all entities satisfied)."""
    mock_planner_service.get_required_entities.return_value = (
        RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["google.calendar"],
            required_entities=[
                EntityRequirement(name="attendee", description="Who?"),
                EntityRequirement(name="time", description="When?"),
            ],
            missing_entities=[],
        )
    )
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice", "time": "10 AM"},
        )
    )
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    store.save = AsyncMock()
    return IntakeService(
        session_store=store,
        intent_parser=parser,
        planner_service=mock_planner_service,
        preference_service=mock_preference_service,
    )


class TestIntentContract:
    async def test_ready_intent_passes_model_validate(self, ready_service):
        """SC-004: emitted Intent passes Intent.model_validate()."""
        resp = await ready_service.process_message(
            user_id=USER_ID,
            message="Book meeting with Alice at 10 AM",
            context_tier=1,
        )
        assert resp.status == "ready"
        assert resp.intent is not None
        intent = Intent.model_validate(resp.intent)
        assert intent.intent == "schedule_meeting"
        assert intent.user_id == USER_ID
        assert intent.session_id is not None
        assert intent.trace_id is not None
        assert len(intent.trace_id) == 32

    async def test_intent_has_required_fields(self, ready_service):
        """All GLOBAL_SPEC §2.1 fields present."""
        resp = await ready_service.process_message(
            user_id=USER_ID,
            message="Book meeting with Alice at 10 AM",
            context_tier=1,
            tz="America/New_York",
        )
        intent = Intent.model_validate(resp.intent)
        assert intent.tz == "America/New_York"
        assert intent.entities["attendee"] == "Alice"
        assert isinstance(intent.constraints, dict)
        assert intent.context_budget is None  # Not set by Intake

    async def test_intent_trace_id_is_hex(self, ready_service):
        """trace_id is a 32-char hex string."""
        resp = await ready_service.process_message(
            user_id=USER_ID,
            message="Book meeting with Alice at 10 AM",
            context_tier=1,
        )
        trace_id = resp.intent["trace_id"]
        assert len(trace_id) == 32
        int(trace_id, 16)  # Should not raise

    async def test_intent_session_id_matches_response(self, ready_service):
        """session_id in Intent matches the top-level session_id."""
        resp = await ready_service.process_message(
            user_id=USER_ID,
            message="Book meeting with Alice at 10 AM",
            context_tier=1,
        )
        assert resp.intent["session_id"] == resp.session_id

    async def test_collecting_response_has_no_intent(
        self,
        mock_planner_service,
        mock_preference_service,
    ):
        """Collecting response must NOT have an intent dict."""
        mock_planner_service.get_required_entities.return_value = (
            RequiredEntitiesResult(
                intent_type="schedule_meeting",
                resolved_tools=["google.calendar"],
                required_entities=[
                    EntityRequirement(name="attendee", description="Who?"),
                    EntityRequirement(name="time", description="When?"),
                ],
                missing_entities=[
                    EntityRequirement(name="time", description="When?"),
                ],
            )
        )
        parser = AsyncMock()
        parser.parse = AsyncMock(
            return_value=ParseResult(
                intent="schedule_meeting",
                entities={"attendee": "Alice"},
            )
        )
        store = AsyncMock()
        store.get = AsyncMock(return_value=None)
        store.save = AsyncMock()

        svc = IntakeService(
            session_store=store,
            intent_parser=parser,
            planner_service=mock_planner_service,
            preference_service=mock_preference_service,
        )
        resp = await svc.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=1,
        )
        assert resp.status == "collecting"
        assert resp.intent is None
