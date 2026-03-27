"""
Intake Service Tests

Tests for IntakeService: single-turn, multi-turn, readiness via Planner,
consent-gated profile defaults, tool not available, degradation paths.

Reference: T301
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock

import pytest

from components.Intake.domain.models import (
    IntakeResponse,
    MaxTurnsExceededError,
    ParseResult,
    Session,
    SessionNotFoundError,
    SessionTurn,
    ToolNotAvailableError,
)
from components.Intake.service.intake_service import IntakeService
from components.Planner.domain.models import (
    EntityRequirement,
    RequiredEntitiesResult,
)
from components.Planner.domain.models import (
    ToolNotAvailableError as PlannerToolNotAvailableError,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def mock_session_store() -> AsyncMock:
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    store.save = AsyncMock()
    store.delete = AsyncMock(return_value=True)
    return store


@pytest.fixture()
def mock_parser() -> AsyncMock:
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice"},
            constraints={},
        )
    )
    return parser


@pytest.fixture()
def service(
    mock_session_store,
    mock_parser,
    mock_planner_service,
    mock_preference_service,
) -> IntakeService:
    return IntakeService(
        session_store=mock_session_store,
        intent_parser=mock_parser,
        planner_service=mock_planner_service,
        preference_service=mock_preference_service,
    )


USER_ID = "550e8400-e29b-41d4-a716-446655440000"


# ------------------------------------------------------------------
# Single-turn
# ------------------------------------------------------------------


class TestSingleTurn:
    async def test_single_turn_collecting(self, service):
        """First message with partial entities -> collecting."""
        resp = await service.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=2,
        )
        assert isinstance(resp, IntakeResponse)
        assert resp.status == "collecting"
        assert resp.session_id.startswith("ses_")
        assert resp.detected_intent == "schedule_meeting"
        assert resp.turn_count == 1
        assert resp.intent is None

    async def test_single_turn_ready_all_entities(self, service, mock_parser, mock_planner_service):
        """All entities provided -> ready with Intent."""
        mock_parser.parse.return_value = ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice", "time": "10 AM", "duration_min": 30},
        )
        mock_planner_service.get_required_entities.return_value = RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["google.calendar"],
            required_entities=[
                EntityRequirement(name="attendee", description="Who?"),
                EntityRequirement(name="time", description="When?"),
                EntityRequirement(name="duration_min", description="How long?"),
            ],
            missing_entities=[],
        )
        resp = await service.process_message(
            user_id=USER_ID,
            message="Book a 30-min meeting with Alice at 10 AM",
            context_tier=1,
        )
        assert resp.status == "ready"
        assert resp.intent is not None
        assert resp.intent["intent"] == "schedule_meeting"
        assert resp.intent["entities"]["attendee"] == "Alice"
        assert resp.intent["trace_id"] is not None
        assert len(resp.intent["trace_id"]) == 32
        assert resp.intent["session_id"] == resp.session_id
        assert resp.intent["user_id"] == USER_ID


# ------------------------------------------------------------------
# Multi-turn
# ------------------------------------------------------------------


class TestMultiTurn:
    async def test_multi_turn_collects_across_turns(
        self, service, mock_session_store, mock_parser, mock_planner_service
    ):
        """Second message loads session and merges entities."""
        # Pre-existing session
        existing = Session(
            session_id="ses_01JXYZ12345678901234567890",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Alice"},
            turns=[
                SessionTurn(
                    message="prev",
                    timestamp="2026-01-01T00:00:00Z",
                    extracted_intent="schedule_meeting",
                    extracted_entities={"attendee": "Alice"},
                )
            ],
        )
        mock_session_store.get.return_value = existing

        # Second turn adds time
        mock_parser.parse.return_value = ParseResult(
            intent="schedule_meeting",
            entities={"time": "10 AM", "duration_min": 30},
        )
        mock_planner_service.get_required_entities.return_value = RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["google.calendar"],
            required_entities=[],
            missing_entities=[],
        )
        resp = await service.process_message(
            user_id=USER_ID,
            message="Tuesday at 10 AM for 30 min",
            context_tier=1,
            session_id="ses_01JXYZ12345678901234567890",
        )
        assert resp.status == "ready"
        assert resp.turn_count == 2


# ------------------------------------------------------------------
# Consent-gated profile defaults
# ------------------------------------------------------------------


class TestConsentTierDefaults:
    async def test_tier2_offers_profile_defaults(self, service, mock_preference_service):
        """Tier 2 user gets ProfileStore defaults in follow_up."""
        resp = await service.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=2,
        )
        assert resp.status == "collecting"
        assert "usually use" in resp.follow_up
        assert "30" in resp.follow_up

    async def test_tier1_skips_profile_defaults(self, service, mock_preference_service):
        """Tier 1 user does NOT get ProfileStore defaults."""
        resp = await service.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=1,
        )
        assert resp.status == "collecting"
        assert "usually use" not in resp.follow_up
        mock_preference_service.get_preference.assert_not_called()

    async def test_profile_store_down_skips_defaults(self, service, mock_preference_service):
        """ProfileStore failure -> no defaults, still works."""
        mock_preference_service.get_preference.side_effect = RuntimeError("DB down")
        resp = await service.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=2,
        )
        assert resp.status == "collecting"
        assert resp.follow_up is not None


# ------------------------------------------------------------------
# Tool not available
# ------------------------------------------------------------------


class TestToolNotAvailable:
    async def test_tool_not_available_raises(self, service, mock_planner_service):
        """Planner ToolNotAvailableError -> Intake ToolNotAvailableError."""
        mock_planner_service.get_required_entities.side_effect = PlannerToolNotAvailableError(
            intent_type="book_flight",
            required_tools=["airline.booking"],
        )
        with pytest.raises(ToolNotAvailableError) as exc_info:
            await service.process_message(
                user_id=USER_ID,
                message="Book a flight to Paris",
                context_tier=1,
            )
        assert exc_info.value.intent_type == "book_flight"
        assert "airline.booking" in exc_info.value.required_tools


# ------------------------------------------------------------------
# Graceful degradation
# ------------------------------------------------------------------


class TestGracefulDegradation:
    async def test_planner_down_heuristic_ready(self, service, mock_planner_service):
        """Planner unavailable + intent + entities -> ready (heuristic)."""
        mock_planner_service.get_required_entities.side_effect = RuntimeError("Planner down")
        resp = await service.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=1,
        )
        assert resp.status == "ready"

    async def test_planner_down_no_entities_collecting(
        self, service, mock_parser, mock_planner_service
    ):
        """Planner unavailable + no entities -> collecting."""
        mock_parser.parse.return_value = ParseResult(intent="unknown_intent", entities={})
        mock_planner_service.get_required_entities.side_effect = RuntimeError("Planner down")
        resp = await service.process_message(
            user_id=USER_ID,
            message="Help me with something",
            context_tier=1,
        )
        assert resp.status == "collecting"

    async def test_llm_parser_down_collecting(self, service, mock_parser):
        """Parser fails -> empty ParseResult -> collecting."""
        from components.Intake.domain.models import IntentParserError

        mock_parser.parse.side_effect = IntentParserError("LLM timeout")
        resp = await service.process_message(
            user_id=USER_ID,
            message="Hello",
            context_tier=1,
        )
        assert resp.status == "collecting"
        assert resp.detected_intent is None


# ------------------------------------------------------------------
# Max turns / session reset
# ------------------------------------------------------------------


class TestSessionLifecycle:
    async def test_max_turns_exceeded(self, service, mock_session_store):
        """20+ turns raises MaxTurnsExceededError."""
        from datetime import datetime

        session = Session(
            session_id="ses_full",
            user_id=USER_ID,
            turns=[
                SessionTurn(
                    message=f"msg {i}",
                    timestamp=datetime.now(UTC),
                )
                for i in range(20)
            ],
        )
        mock_session_store.get.return_value = session

        with pytest.raises(MaxTurnsExceededError):
            await service.process_message(
                user_id=USER_ID,
                message="One more",
                context_tier=1,
                session_id="ses_full",
            )

    async def test_reset_session_success(self, service, mock_session_store):
        """Deleting existing session succeeds."""
        mock_session_store.delete.return_value = True
        await service.reset_session(USER_ID, "ses_abc")
        mock_session_store.delete.assert_called_once_with(USER_ID, "ses_abc")

    async def test_reset_session_not_found(self, service, mock_session_store):
        """Deleting missing session raises SessionNotFoundError."""
        mock_session_store.delete.return_value = False
        with pytest.raises(SessionNotFoundError):
            await service.reset_session(USER_ID, "ses_gone")


# ------------------------------------------------------------------
# Session creation
# ------------------------------------------------------------------


class TestSessionCreation:
    async def test_new_session_when_no_session_id(self, service):
        """No session_id -> new session created."""
        resp = await service.process_message(
            user_id=USER_ID,
            message="Hello",
            context_tier=1,
        )
        assert resp.session_id.startswith("ses_")

    async def test_new_session_when_session_not_found(self, service, mock_session_store):
        """Provided session_id not in store -> new session."""
        mock_session_store.get.return_value = None
        resp = await service.process_message(
            user_id=USER_ID,
            message="Hello",
            context_tier=1,
            session_id="ses_expired",
        )
        assert resp.session_id.startswith("ses_")
        assert resp.session_id != "ses_expired"
