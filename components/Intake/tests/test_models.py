"""
Intake Domain Model Unit Tests

Tests for Session, IntakeMessage, IntakeResponse, ParseResult,
SessionResetResponse, and the error hierarchy.

Reference: T101
"""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError

from components.Intake.domain.models import (
    IntakeError,
    IntakeMessage,
    IntakeResponse,
    IntentParserError,
    MaxTurnsExceededError,
    ParseResult,
    Session,
    SessionNotFoundError,
    SessionOwnershipError,
    SessionResetResponse,
    SessionStoreUnavailableError,
    SessionTurn,
)

# ------------------------------------------------------------------
# SessionTurn
# ------------------------------------------------------------------


class TestSessionTurn:
    def test_session_turn_creation(self):
        from datetime import datetime

        turn = SessionTurn(
            message="Hello",
            timestamp=datetime.now(UTC),
        )
        assert turn.message == "Hello"
        assert turn.extracted_intent is None
        assert turn.extracted_entities == {}
        assert turn.extracted_constraints == {}


# ------------------------------------------------------------------
# Session
# ------------------------------------------------------------------


class TestSession:
    def test_session_creation_defaults(self):
        session = Session(user_id="user-123")
        assert session.session_id.startswith("ses_")
        assert session.user_id == "user-123"
        assert session.turns == []
        assert session.detected_intent is None
        assert session.extracted_entities == {}
        assert session.extracted_constraints == {}
        assert session.created_at is not None
        assert session.updated_at is not None

    def test_session_id_format(self):
        session = Session(user_id="user-123")
        assert session.session_id.startswith("ses_")
        assert len(session.session_id) == 30  # ses_ (4) + ULID (26)


# ------------------------------------------------------------------
# IntakeMessage
# ------------------------------------------------------------------


class TestIntakeMessage:
    def test_intake_message_valid(self):
        msg = IntakeMessage(message="Book a meeting")
        assert msg.message == "Book a meeting"
        assert msg.session_id is None

    def test_intake_message_empty_rejects(self):
        with pytest.raises(ValidationError):
            IntakeMessage(message="")

    def test_intake_message_too_long_rejects(self):
        with pytest.raises(ValidationError):
            IntakeMessage(message="x" * 10_001)

    def test_intake_message_with_session_id(self):
        msg = IntakeMessage(message="hello", session_id="ses_abc")
        assert msg.session_id == "ses_abc"


# ------------------------------------------------------------------
# IntakeResponse
# ------------------------------------------------------------------


class TestIntakeResponse:
    def test_intake_response_collecting(self):
        resp = IntakeResponse(
            status="collecting",
            session_id="ses_123",
            follow_up="What time?",
            turn_count=1,
        )
        assert resp.status == "collecting"
        assert resp.follow_up == "What time?"
        assert resp.intent is None

    def test_intake_response_ready(self):
        resp = IntakeResponse(
            status="ready",
            session_id="ses_123",
            detected_intent="schedule_meeting",
            intent={"intent": "schedule_meeting", "entities": {}},
            turn_count=2,
        )
        assert resp.status == "ready"
        assert resp.intent is not None


# ------------------------------------------------------------------
# SessionResetResponse
# ------------------------------------------------------------------


class TestSessionResetResponse:
    def test_session_reset_response(self):
        resp = SessionResetResponse(session_id="ses_abc")
        assert resp.status == "reset"
        assert resp.session_id == "ses_abc"


# ------------------------------------------------------------------
# ParseResult
# ------------------------------------------------------------------


class TestParseResult:
    def test_parse_result_defaults(self):
        result = ParseResult()
        assert result.intent is None
        assert result.entities == {}
        assert result.constraints == {}

    def test_parse_result_with_values(self):
        result = ParseResult(
            intent="book_flight",
            entities={"destination": "Paris"},
            constraints={"direct_only": True},
        )
        assert result.intent == "book_flight"


# ------------------------------------------------------------------
# Error hierarchy
# ------------------------------------------------------------------


class TestErrorHierarchy:
    def test_all_errors_are_subclass_of_intake_error(self):
        errors = [
            SessionNotFoundError("ses_1"),
            SessionOwnershipError("ses_1", "user_1"),
            MaxTurnsExceededError("ses_1", 20),
            SessionStoreUnavailableError("Redis down"),
            IntentParserError("LLM failed"),
        ]
        for err in errors:
            assert isinstance(err, IntakeError)

    def test_session_not_found_attrs(self):
        err = SessionNotFoundError("ses_abc")
        assert err.session_id == "ses_abc"
        assert "ses_abc" in str(err)

    def test_session_ownership_attrs(self):
        err = SessionOwnershipError("ses_abc", "user_1")
        assert err.session_id == "ses_abc"
        assert err.user_id == "user_1"

    def test_max_turns_exceeded_attrs(self):
        err = MaxTurnsExceededError("ses_abc", 20)
        assert err.max_turns == 20

    def test_intent_parser_error_attrs(self):
        err = IntentParserError("timeout")
        assert err.reason == "timeout"

    def test_session_store_unavailable_attrs(self):
        err = SessionStoreUnavailableError("connection refused")
        assert err.reason == "connection refused"
