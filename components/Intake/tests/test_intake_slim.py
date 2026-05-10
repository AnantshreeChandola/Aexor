"""
Tests for slimmed IntakeService — intent detection and clarification only.

Covers: parse_once, process_message (simplified), session tracking,
max turns, rate limiting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from components.Intake.adapters.intent_parser import LLMBasedParser
from components.Intake.domain.models import (
    IntentParserError,
    MaxTurnsExceededError,
    ParseResult,
    RateLimitedError,
    Session,
    SessionTurn,
)
from components.Intake.service.intake_service import IntakeService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_session_store():
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    store.save = AsyncMock()
    store.delete = AsyncMock(return_value=True)
    return store


@pytest.fixture()
def mock_parser():
    parser = AsyncMock(spec=LLMBasedParser)
    parser.parse = AsyncMock(
        return_value=ParseResult(
            intent="schedule_meeting",
            entities={"attendee": "Alice"},
        )
    )
    return parser


@pytest.fixture()
def service(mock_session_store, mock_parser):
    return IntakeService(
        session_store=mock_session_store,
        intent_parser=mock_parser,
        max_turns=5,
    )


# ---------------------------------------------------------------------------
# parse_once
# ---------------------------------------------------------------------------


class TestParseOnce:
    """parse_once: single-turn intent detection for skeleton flow."""

    @pytest.mark.asyncio
    async def test_returns_intent_and_entities(self, service, mock_parser):
        result = await service.parse_once("schedule a meeting with Alice", "user-1")
        assert result.intent == "schedule_meeting"
        assert result.entities["attendee"] == "Alice"
        mock_parser.parse.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_intent_detected(self, service, mock_parser):
        mock_parser.parse = AsyncMock(return_value=ParseResult())
        result = await service.parse_once("hello", "user-1")
        assert result.intent is None
        assert result.entities == {}

    @pytest.mark.asyncio
    async def test_does_not_persist_session(self, service, mock_session_store):
        await service.parse_once("test", "user-1")
        mock_session_store.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_surfaces(self, service, mock_parser):
        mock_parser.parse = AsyncMock(
            side_effect=IntentParserError("rate limit exceeded (429)")
        )
        with pytest.raises(RateLimitedError):
            await service.parse_once("test", "user-1")

    @pytest.mark.asyncio
    async def test_parser_error_returns_empty(self, service, mock_parser):
        mock_parser.parse = AsyncMock(
            side_effect=IntentParserError("JSON parse failed")
        )
        result = await service.parse_once("test", "user-1")
        assert result.intent is None


# ---------------------------------------------------------------------------
# process_message — intent detection
# ---------------------------------------------------------------------------


class TestProcessMessage:
    """process_message: multi-turn intent clarification."""

    @pytest.mark.asyncio
    async def test_intent_detected_returns_ready(self, service):
        resp = await service.process_message("user-1", "schedule a meeting")
        assert resp.status == "ready"
        assert resp.detected_intent == "schedule_meeting"
        assert resp.collected_entities.get("attendee") == "Alice"
        assert resp.follow_up is None

    @pytest.mark.asyncio
    async def test_no_intent_returns_collecting(self, service, mock_parser):
        mock_parser.parse = AsyncMock(return_value=ParseResult())
        resp = await service.process_message("user-1", "hello there")
        assert resp.status == "collecting"
        assert resp.detected_intent is None
        assert resp.follow_up is not None
        assert "help" in resp.follow_up.lower()

    @pytest.mark.asyncio
    async def test_session_persisted(self, service, mock_session_store):
        await service.process_message("user-1", "test")
        mock_session_store.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_appended(self, service, mock_session_store):
        await service.process_message("user-1", "schedule meeting")
        saved_session = mock_session_store.save.call_args[0][0]
        assert len(saved_session.turns) == 1
        assert saved_session.turns[0].message == "schedule meeting"

    @pytest.mark.asyncio
    async def test_existing_session_loaded(self, service, mock_session_store):
        existing = Session(
            session_id="ses_existing",
            user_id="user-1",
            turns=[
                SessionTurn(
                    message="hi",
                    timestamp=datetime.now(UTC),
                )
            ],
        )
        mock_session_store.get = AsyncMock(return_value=existing)
        resp = await service.process_message(
            "user-1", "schedule meeting", session_id="ses_existing"
        )
        assert resp.session_id == "ses_existing"
        # Should have 2 turns now (existing + new)
        saved = mock_session_store.save.call_args[0][0]
        assert len(saved.turns) == 2


# ---------------------------------------------------------------------------
# Max turns
# ---------------------------------------------------------------------------


class TestMaxTurns:
    @pytest.mark.asyncio
    async def test_max_turns_raises(self, service, mock_session_store):
        full_session = Session(
            session_id="ses_full",
            user_id="user-1",
            turns=[
                SessionTurn(message=f"msg {i}", timestamp=datetime.now(UTC))
                for i in range(5)  # max_turns = 5
            ],
        )
        mock_session_store.get = AsyncMock(return_value=full_session)
        with pytest.raises(MaxTurnsExceededError):
            await service.process_message(
                "user-1", "one more", session_id="ses_full"
            )


# ---------------------------------------------------------------------------
# Entity key normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_alias_mapping(self):
        entities = {"time": "2pm", "date": "tomorrow"}
        result = IntakeService._normalize_entity_keys(entities, "schedule_meeting")
        assert "date_time" in result
        assert "tomorrow" in result["date_time"]
        assert "2pm" in result["date_time"]

    def test_unknown_intent_passes_through(self):
        entities = {"foo": "bar"}
        result = IntakeService._normalize_entity_keys(entities, "unknown_intent")
        assert result == {"foo": "bar"}


# ---------------------------------------------------------------------------
# Reset session
# ---------------------------------------------------------------------------


class TestResetSession:
    @pytest.mark.asyncio
    async def test_reset_deletes(self, service, mock_session_store):
        await service.reset_session("user-1", "ses_123")
        mock_session_store.delete.assert_called_once_with("user-1", "ses_123")

    @pytest.mark.asyncio
    async def test_reset_not_found(self, service, mock_session_store):
        mock_session_store.delete = AsyncMock(return_value=False)
        from components.Intake.domain.models import SessionNotFoundError

        with pytest.raises(SessionNotFoundError):
            await service.reset_session("user-1", "ses_missing")
