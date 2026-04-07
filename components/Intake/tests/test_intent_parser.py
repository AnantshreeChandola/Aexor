"""
Intent Parser Tests

Tests for LLMBasedParser: prompt construction with conversation context.

Reference: T301
"""

from __future__ import annotations

from datetime import UTC, datetime

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

    def test_includes_contact_suggestions(self):
        """Parser context includes pending contact_suggestions."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Utkarsh"},
            contact_suggestions={"attendee_email": "utkarsh@example.com"},
        )
        prompt = LLMBasedParser._build_user_prompt("yes", session)
        assert "pending_suggestions" in prompt
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

    def test_omits_suggestions_when_empty(self):
        """Empty contact_suggestions -> key not in prompt."""
        session = Session(
            session_id="ses_test",
            user_id=USER_ID,
            detected_intent="schedule_meeting",
            extracted_entities={"attendee": "Alice"},
        )
        prompt = LLMBasedParser._build_user_prompt("hello", session)
        assert "pending_suggestions" not in prompt

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
