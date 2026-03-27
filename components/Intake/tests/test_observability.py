"""
Intake Observability Tests

FR-010 compliance: no PII (user message content) in logs.
Verifies structured logging fields and absence of message text.

Reference: T601
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from components.Intake.domain.models import ParseResult
from components.Intake.service.intake_service import IntakeService
from components.Planner.domain.models import (
    EntityRequirement,
    RequiredEntitiesResult,
)

USER_ID = "550e8400-e29b-41d4-a716-446655440000"
SECRET_MESSAGE = "Book a meeting with Alice at 10 AM on Tuesday for 30 minutes"


@pytest.fixture()
def svc_with_mocks(
    mock_planner_service,
    mock_preference_service,
) -> IntakeService:
    """IntakeService with all mocked adapters."""
    mock_planner_service.get_required_entities.return_value = (
        RequiredEntitiesResult(
            intent_type="schedule_meeting",
            resolved_tools=["google.calendar"],
            required_entities=[
                EntityRequirement(name="attendee", description="Who?"),
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
    return IntakeService(
        session_store=store,
        intent_parser=parser,
        planner_service=mock_planner_service,
        preference_service=mock_preference_service,
    )


class TestNoPIIInLogs:
    async def test_message_content_absent_from_logs(
        self, svc_with_mocks, caplog
    ):
        """FR-010: user message content must NOT appear in any log record."""
        with caplog.at_level(logging.DEBUG):
            await svc_with_mocks.process_message(
                user_id=USER_ID,
                message=SECRET_MESSAGE,
                context_tier=1,
            )

        full_log = caplog.text
        # The secret message must not appear anywhere in logs
        assert SECRET_MESSAGE not in full_log
        # Partial unique phrases from the message
        assert "Book a meeting with Alice" not in full_log
        assert "30 minutes" not in full_log

    async def test_structured_fields_present(
        self, svc_with_mocks, caplog
    ):
        """Logs contain session_id and user_id in extra fields."""
        with caplog.at_level(logging.DEBUG):
            resp = await svc_with_mocks.process_message(
                user_id=USER_ID,
                message=SECRET_MESSAGE,
                context_tier=1,
            )

        # Check extra fields on log records (structured logging)
        session_ids_logged = [
            r.__dict__.get("session_id")
            for r in caplog.records
            if hasattr(r, "session_id")
        ]
        assert resp.session_id in session_ids_logged

        user_ids_logged = [
            r.__dict__.get("user_id")
            for r in caplog.records
            if hasattr(r, "user_id")
        ]
        assert USER_ID in user_ids_logged

    async def test_parser_error_logs_no_message(
        self, svc_with_mocks, caplog
    ):
        """Parser failure log must not contain message content."""
        from components.Intake.domain.models import IntentParserError

        svc_with_mocks._intent_parser.parse.side_effect = IntentParserError(
            "LLM timeout"
        )
        with caplog.at_level(logging.DEBUG):
            await svc_with_mocks.process_message(
                user_id=USER_ID,
                message=SECRET_MESSAGE,
                context_tier=1,
            )

        assert SECRET_MESSAGE not in caplog.text

    async def test_reset_session_logs_no_pii(
        self, svc_with_mocks, caplog
    ):
        """Session reset logs session_id in extra but no PII in message."""
        svc_with_mocks._session_store.delete.return_value = True
        with caplog.at_level(logging.DEBUG):
            await svc_with_mocks.reset_session(USER_ID, "ses_test123")

        # session_id should be in extra fields
        session_ids = [
            r.__dict__.get("session_id")
            for r in caplog.records
            if hasattr(r, "session_id")
        ]
        assert "ses_test123" in session_ids

        # No user message content in log text
        assert SECRET_MESSAGE not in caplog.text
