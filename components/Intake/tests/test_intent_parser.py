"""
Intent Parser Tests

Tests for LLMBasedParser: prompt construction, confidence parsing, escalation.

Reference: T301
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from components.Intake.adapters.intent_parser import LLMBasedParser
from components.Intake.domain.models import Session, SessionTurn

USER_ID = "550e8400-e29b-41d4-a716-446655440000"


class TestBuildUserPrompt:
    """Verify _build_user_prompt includes conversation history and state."""

    def test_includes_conversation_history(self):
        """Prior turns appear as conversation history with assistant responses."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Utkarsh"},
            turns=[
                SessionTurn(
                    message="Schedule a meeting with Utkarsh",
                    assistant_response="I still need a few details:\n- Attendee email address: Is this utkarsh@example.com?",
                    timestamp=datetime.now(UTC),
                    extracted_intent="schedule_meeting",
                ),
            ],
        )
        prompt = LLMBasedParser._build_user_prompt("yes", session)
        assert "Conversation so far:" in prompt
        assert "Schedule a meeting with Utkarsh" in prompt
        assert "Assistant:" in prompt
        assert "utkarsh@example.com" in prompt

    def test_multi_turn_conversation_history(self):
        """Each turn's assistant response appears in the conversation."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Utkarsh", "date": "Monday"},
            turns=[
                SessionTurn(
                    message="Schedule a meeting with Utkarsh",
                    assistant_response="When should I schedule it?",
                    timestamp=datetime.now(UTC),
                    extracted_intent="schedule_meeting",
                ),
                SessionTurn(
                    message="Monday at 3pm",
                    assistant_response="Is the attendee email utkarsh@example.com?",
                    timestamp=datetime.now(UTC),
                ),
            ],
        )
        prompt = LLMBasedParser._build_user_prompt("yes", session)
        # Both user messages present
        assert "Schedule a meeting with Utkarsh" in prompt
        assert "Monday at 3pm" in prompt
        # Both assistant responses present
        assert "When should I schedule it?" in prompt
        assert "utkarsh@example.com" in prompt

    def test_omits_follow_up_when_none(self):
        """No last_follow_up -> no Assistant line in prompt."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Alice"},
        )
        prompt = LLMBasedParser._build_user_prompt("hello", session)
        assert "Assistant:" not in prompt

    def test_no_context_still_works(self):
        """No session context -> plain user message only."""
        prompt = LLMBasedParser._build_user_prompt("hello", None)
        assert "New user message: hello" in prompt
        assert "Conversation so far" not in prompt

    def test_new_message_always_present(self):
        """Current user message always at the end."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Alice"},
            turns=[
                SessionTurn(
                    message="Meet with Alice",
                    timestamp=datetime.now(UTC),
                ),
            ],
        )
        prompt = LLMBasedParser._build_user_prompt("tomorrow at 3pm", session)
        assert prompt.endswith("New user message: tomorrow at 3pm")


class TestConfidenceParsing:
    """Verify confidence extraction and normalization in _parse_response."""

    def test_confidence_parsed_from_json(self):
        """Confidence field is extracted correctly from LLM JSON."""
        raw = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 0.85,
        })
        result = LLMBasedParser._parse_response(raw)
        assert result.confidence == pytest.approx(0.85)

    def test_confidence_missing_defaults_to_none(self):
        """No confidence field → None."""
        raw = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
        })
        result = LLMBasedParser._parse_response(raw)
        assert result.confidence is None

    def test_confidence_percentage_normalized(self):
        """LLM returning 85 instead of 0.85 → normalized to 0.85."""
        raw = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 85,
        })
        result = LLMBasedParser._parse_response(raw)
        assert result.confidence == pytest.approx(0.85)

    def test_confidence_clamped(self):
        """Values > 1.0 after normalization are clamped to 1.0."""
        raw = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 150,
        })
        result = LLMBasedParser._parse_response(raw)
        assert result.confidence == pytest.approx(1.0)

    def test_confidence_zero(self):
        """Confidence 0 is preserved."""
        raw = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 0,
        })
        result = LLMBasedParser._parse_response(raw)
        assert result.confidence == pytest.approx(0.0)

    def test_confidence_invalid_string_defaults_to_none(self):
        """Non-numeric confidence → None."""
        raw = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": "high",
        })
        result = LLMBasedParser._parse_response(raw)
        assert result.confidence is None


class TestConfidenceEscalation:
    """Verify escalation logic when local LLM confidence is low."""

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_remote_escalation(self):
        """Low confidence (0.5) triggers remote LLM call."""
        local_response = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 0.5,
        })
        remote_response = json.dumps({
            "intent": "search_notion",
            "entities": {"query": "meetings"},
            "constraints": {},
            "confidence": 0.92,
        })
        local_llm = AsyncMock()
        local_llm.generate = AsyncMock(return_value=local_response)
        remote_llm = AsyncMock()
        remote_llm.generate = AsyncMock(return_value=remote_response)

        parser = LLMBasedParser(local_llm, remote_llm=remote_llm)
        result = await parser.parse("meetings from notion")

        assert result.intent == "search_notion"
        assert result.escalated is True
        assert result.confidence == pytest.approx(0.92)
        remote_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_high_confidence_skips_remote(self):
        """High confidence (0.95) does not call remote LLM."""
        local_response = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 0.95,
        })
        local_llm = AsyncMock()
        local_llm.generate = AsyncMock(return_value=local_response)
        remote_llm = AsyncMock()
        remote_llm.generate = AsyncMock()

        parser = LLMBasedParser(local_llm, remote_llm=remote_llm)
        result = await parser.parse("list my meetings")

        assert result.intent == "list_meetings"
        assert result.escalated is False
        remote_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_confidence_triggers_remote(self):
        """None confidence triggers remote LLM call."""
        local_response = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
        })
        remote_response = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 0.90,
        })
        local_llm = AsyncMock()
        local_llm.generate = AsyncMock(return_value=local_response)
        remote_llm = AsyncMock()
        remote_llm.generate = AsyncMock(return_value=remote_response)

        parser = LLMBasedParser(local_llm, remote_llm=remote_llm)
        result = await parser.parse("list my meetings")

        assert result.escalated is True
        remote_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_remote_adapter_skips_check(self):
        """When remote_llm=None, no escalation happens regardless of confidence."""
        local_response = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 0.3,
        })
        local_llm = AsyncMock()
        local_llm.generate = AsyncMock(return_value=local_response)

        parser = LLMBasedParser(local_llm, remote_llm=None)
        result = await parser.parse("list meetings")

        assert result.intent == "list_meetings"
        assert result.escalated is False
        assert result.confidence == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_escalation_failure_falls_back_to_local(self):
        """If remote LLM fails during escalation, local result is returned."""
        local_response = json.dumps({
            "intent": "list_meetings",
            "entities": {},
            "constraints": {},
            "confidence": 0.4,
        })
        local_llm = AsyncMock()
        local_llm.generate = AsyncMock(return_value=local_response)
        remote_llm = AsyncMock()
        remote_llm.generate = AsyncMock(side_effect=Exception("Remote down"))

        parser = LLMBasedParser(local_llm, remote_llm=remote_llm)
        result = await parser.parse("list meetings")

        assert result.intent == "list_meetings"
        assert result.escalated is False
        assert result.confidence == pytest.approx(0.4)
