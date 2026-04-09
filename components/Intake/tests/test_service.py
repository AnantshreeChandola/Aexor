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
            entities={
                "attendee": "Alice",
                "attendee_email": "alice@example.com",
                "date": "Tuesday",
                "time": "10 AM",
                "duration": "30m",
            },
        )
        mock_planner_service.get_required_entities.return_value = RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["google.calendar"],
            required_entities=[
                EntityRequirement(name="attendee", description="Who?"),
                EntityRequirement(name="attendee_email", description="Email?"),
                EntityRequirement(name="date", description="Date?"),
                EntityRequirement(name="time", description="When?"),
                EntityRequirement(name="duration", description="How long?"),
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
            extracted_entities={
                "attendee": "Alice",
                "attendee_email": "alice@example.com",
            },
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

        # Second turn adds remaining fields
        mock_parser.parse.return_value = ParseResult(
            intent="schedule_meeting",
            entities={"date": "Tuesday", "time": "10 AM", "duration": "30m"},
        )
        mock_planner_service.get_required_entities.return_value = RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["google.calendar"],
            required_entities=[
                EntityRequirement(name="attendee", description="Who?"),
                EntityRequirement(name="attendee_email", description="Email?"),
                EntityRequirement(name="date", description="Date?"),
                EntityRequirement(name="time", description="When?"),
                EntityRequirement(name="duration", description="How long?"),
            ],
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


# ------------------------------------------------------------------
# Planner-driven entity readiness (schema overrides removed)
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Contact resolution
# ------------------------------------------------------------------


class TestContactResolution:
    """Tests for contact_suggestions and LLM-driven confirmation flow."""

    def test_apply_confirmed_suggestions_clears_resolved(self):
        """When LLM emits the suggested value, the suggestion is cleared."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Utkarsh"},
            contact_suggestions={"attendee_email": "utkarsh@example.com"},
        )
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"attendee_email": "utkarsh@example.com", "date": "tomorrow"},
        )
        IntakeService._apply_confirmed_suggestions(session, parse_result)
        assert session.contact_suggestions == {}
        assert parse_result.entities["attendee_email"] == "utkarsh@example.com"

    def test_apply_confirmed_suggestions_fixes_literal_yes(self):
        """When LLM emits 'yes' for a pending suggestion, replace with actual value."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Utkarsh"},
            contact_suggestions={"attendee_email": "utkarsh@example.com"},
        )
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"attendee_email": "yes", "date": "tomorrow"},
        )
        IntakeService._apply_confirmed_suggestions(session, parse_result)
        assert session.contact_suggestions == {}
        # "yes" replaced with the actual suggested email
        assert parse_result.entities["attendee_email"] == "utkarsh@example.com"

    def test_apply_confirmed_suggestions_keeps_unresolved(self):
        """When LLM does NOT emit anything for the entity, suggestion stays."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Utkarsh"},
            contact_suggestions={"attendee_email": "utkarsh@example.com"},
        )
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"date": "tomorrow"},
        )
        IntakeService._apply_confirmed_suggestions(session, parse_result)
        assert "attendee_email" in session.contact_suggestions

    def test_apply_confirmed_suggestions_user_corrects(self):
        """LLM emits a different real email -> user corrected, suggestion cleared."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Utkarsh"},
            contact_suggestions={"attendee_email": "utkarsh@example.com"},
        )
        parse_result = ParseResult(
            intent="schedule_meeting",
            entities={"attendee_email": "other@example.com"},
        )
        IntakeService._apply_confirmed_suggestions(session, parse_result)
        # User provided a different value — accepted, suggestion cleared
        assert session.contact_suggestions == {}
        assert parse_result.entities["attendee_email"] == "other@example.com"

    def test_apply_confirmed_suggestions_empty_noop(self):
        """No pending suggestions -> no-op."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Alice"},
        )
        parse_result = ParseResult(intent="schedule_meeting", entities={})
        IntakeService._apply_confirmed_suggestions(session, parse_result)
        assert session.contact_suggestions == {}

    async def test_resolve_contact_email_no_db(self):
        """No db_adapter -> returns None."""
        svc = IntakeService(
            session_store=AsyncMock(),
            intent_parser=AsyncMock(),
            planner_service=AsyncMock(),
            preference_service=AsyncMock(),
            db_adapter=None,
        )
        result = await svc._resolve_contact_email("Alice")
        assert result is None

    async def test_contact_suggestion_in_follow_up(
        self, service, mock_parser, mock_planner_service
    ):
        """When Planner reports attendee_email missing and contact resolves, suggest it."""
        # Patch service to have a mock db_adapter that resolves the contact
        service._db_adapter = AsyncMock()

        # Mock the _resolve_contact_email directly for simplicity
        async def mock_resolve(name):
            if name == "Utkarsh":
                return "utkarsh@example.com"
            return None

        service._resolve_contact_email = mock_resolve

        mock_parser.parse.return_value = ParseResult(
            intent="schedule_meeting",
            entities={
                "attendee": "Utkarsh",
                "date": "Monday",
                "time": "3 PM",
                "duration": "1h",
            },
        )
        mock_planner_service.get_required_entities.return_value = RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["GOOGLECALENDAR_CREATE_EVENT"],
            required_entities=[
                EntityRequirement(name="attendee", description="Who?"),
                EntityRequirement(name="attendee_email", description="Attendee email address"),
                EntityRequirement(name="date", description="Date?"),
                EntityRequirement(name="time", description="When?"),
                EntityRequirement(name="duration", description="How long?"),
            ],
            missing_entities=[
                EntityRequirement(name="attendee_email", description="Attendee email address"),
            ],
        )
        resp = await service.process_message(
            user_id=USER_ID, message="Schedule a meeting with Utkarsh", context_tier=2
        )
        assert resp.status == "collecting"
        assert "attendee_email" in resp.missing_fields
        assert "utkarsh@example.com" in resp.follow_up


# ------------------------------------------------------------------
# Follow-up guard: skip already-collected entities
# ------------------------------------------------------------------


class TestBuildFollowUpGuard:
    """Verify _build_follow_up skips entities already in extracted_entities."""

    async def test_build_follow_up_skips_already_collected_entities(
        self, service, mock_planner_service
    ):
        """If Planner marks an entity as missing but it's already collected, skip it."""
        session = Session(
            session_id="ses_guard_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={
                "attendee": "Utkarsh",
                "attendee_email": "utkarsh@example.com",
                "date": "Monday",
                "time": "3 PM",
            },
        )
        # Planner incorrectly reports attendee_email as missing
        planner_result = RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["GOOGLECALENDAR_CREATE_EVENT"],
            required_entities=[
                EntityRequirement(name="attendee_email", description="Attendee email"),
                EntityRequirement(name="duration", description="How long?"),
            ],
            missing_entities=[
                EntityRequirement(name="attendee_email", description="Attendee email"),
                EntityRequirement(name="duration", description="How long?"),
            ],
        )
        follow_up = await service._build_follow_up(
            session,
            missing_fields=["attendee_email", "duration"],
            planner_result=planner_result,
            context_tier=2,
            user_id=USER_ID,
        )
        # attendee_email is already collected — should NOT appear in follow-up
        assert "attendee_email" not in follow_up.lower().replace(" ", "_")
        assert "Attendee email" not in follow_up
        # duration IS still missing — should appear
        assert "How long?" in follow_up


# ------------------------------------------------------------------
# last_follow_up persistence
# ------------------------------------------------------------------


class TestLastFollowUp:
    """Verify last_follow_up is stored in session after each turn."""

    async def test_last_follow_up_stored_when_collecting(self, service, mock_session_store):
        """When status=collecting, last_follow_up is set to the follow-up text."""
        resp = await service.process_message(
            user_id=USER_ID,
            message="Meet with Alice",
            context_tier=2,
        )
        assert resp.status == "collecting"
        # Verify session was saved with last_follow_up set
        saved_session = mock_session_store.save.call_args[0][0]
        assert saved_session.last_follow_up is not None
        assert saved_session.last_follow_up == resp.follow_up

    async def test_last_follow_up_cleared_when_ready(
        self, service, mock_session_store, mock_parser, mock_planner_service
    ):
        """When status=ready, last_follow_up is None."""
        mock_parser.parse.return_value = ParseResult(
            intent="schedule_meeting",
            entities={
                "attendee": "Alice",
                "attendee_email": "alice@example.com",
                "date": "Tuesday",
                "time": "10 AM",
                "duration": "30m",
            },
        )
        mock_planner_service.get_required_entities.return_value = RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["google.calendar"],
            required_entities=[],
            missing_entities=[],
        )
        resp = await service.process_message(
            user_id=USER_ID,
            message="All details provided",
            context_tier=1,
        )
        assert resp.status == "ready"
        saved_session = mock_session_store.save.call_args[0][0]
        assert saved_session.last_follow_up is None
