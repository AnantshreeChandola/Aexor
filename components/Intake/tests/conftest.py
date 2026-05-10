"""
Intake Test Fixtures

Shared fixtures for mocked Redis, LLM, and sample domain objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from components.Intake.domain.models import (
    IntakeMessage,
    ParseResult,
    Session,
    SessionTurn,
)


@pytest.fixture()
def sample_session() -> Session:
    """Session with 1 turn and detected intent."""
    return Session(
        session_id="ses_01JXYZ12345678901234567890",
        user_id="550e8400-e29b-41d4-a716-446655440000",
        turns=[
            SessionTurn(
                message="Meet with Alice",
                timestamp=datetime.now(UTC),
                extracted_intent="schedule_meeting",
                extracted_entities={"attendee": "Alice"},
            ),
        ],
        detected_intent="schedule_meeting",
        extracted_entities={"attendee": "Alice"},
    )


@pytest.fixture()
def empty_session() -> Session:
    """Session with 0 turns."""
    return Session(
        session_id="ses_01JXYZ00000000000000000000",
        user_id="550e8400-e29b-41d4-a716-446655440000",
    )


@pytest.fixture()
def sample_parse_result() -> ParseResult:
    """ParseResult with meeting intent."""
    return ParseResult(
        intent="schedule_meeting",
        entities={"attendee": "Alice"},
        constraints={},
    )


@pytest.fixture()
def sample_intake_message() -> IntakeMessage:
    """IntakeMessage for a meeting booking."""
    return IntakeMessage(
        message="Book a 30-min meeting with Alice on Tuesday at 10 AM",
    )


@pytest.fixture()
def mock_redis_client() -> AsyncMock:
    """AsyncMock of redis.asyncio.Redis."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    return client


@pytest.fixture()
def mock_llm_adapter() -> AsyncMock:
    """AsyncMock implementing LLMAdapter protocol."""
    adapter = AsyncMock()
    adapter.generate = AsyncMock(
        return_value='{"intent": "schedule_meeting", "entities": {"attendee": "Alice"}, "constraints": {}}'
    )
    return adapter


@pytest.fixture()
def sample_auth_context() -> dict:
    """Auth context dict with user_id, context_tier, email."""
    return {
        "user_id": "550e8400-e29b-41d4-a716-446655440000",
        "context_tier": 2,
        "email": "test@example.com",
    }
