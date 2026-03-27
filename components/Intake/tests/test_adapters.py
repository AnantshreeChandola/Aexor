"""
Intake Adapter Unit Tests

Tests for RedisSessionStore (mocked Redis) and LLMBasedParser (mocked LLM).

Reference: T202
"""

from __future__ import annotations

import pytest
import redis.exceptions

from components.Intake.adapters.intent_parser import LLMBasedParser
from components.Intake.adapters.session_store import RedisSessionStore
from components.Intake.domain.models import (
    IntentParserError,
    SessionStoreUnavailableError,
)

# ------------------------------------------------------------------
# RedisSessionStore
# ------------------------------------------------------------------


class TestRedisSessionStore:
    @pytest.fixture()
    def store(self, mock_redis_client) -> RedisSessionStore:
        return RedisSessionStore(mock_redis_client, ttl_seconds=3600)

    async def test_redis_session_store_save_and_get(
        self, store, mock_redis_client, sample_session
    ):
        """Save a session then retrieve it."""
        await store.save(sample_session)
        mock_redis_client.setex.assert_called_once()

        # Simulate get returning the saved data
        mock_redis_client.get.return_value = sample_session.model_dump_json()
        result = await store.get(
            sample_session.user_id, sample_session.session_id
        )
        assert result is not None
        assert result.session_id == sample_session.session_id

    async def test_redis_session_store_get_missing(
        self, store, mock_redis_client
    ):
        """Returns None for non-existent key."""
        mock_redis_client.get.return_value = None
        result = await store.get("user_1", "ses_nonexistent")
        assert result is None

    async def test_redis_session_store_delete_existing(
        self, store, mock_redis_client
    ):
        """Returns True when key deleted."""
        mock_redis_client.delete.return_value = 1
        assert await store.delete("user_1", "ses_abc") is True

    async def test_redis_session_store_delete_missing(
        self, store, mock_redis_client
    ):
        """Returns False when key did not exist."""
        mock_redis_client.delete.return_value = 0
        assert await store.delete("user_1", "ses_nope") is False

    async def test_redis_session_store_ttl_refresh(
        self, store, mock_redis_client, sample_session
    ):
        """Verify SETEX called with 3600 TTL."""
        await store.save(sample_session)
        args = mock_redis_client.setex.call_args
        assert args[0][1] == 3600

    async def test_redis_session_store_error_wrapping(
        self, store, mock_redis_client
    ):
        """RedisError wrapped as SessionStoreUnavailableError."""
        mock_redis_client.get.side_effect = redis.exceptions.RedisError(
            "conn refused"
        )
        with pytest.raises(SessionStoreUnavailableError):
            await store.get("user_1", "ses_abc")

    async def test_redis_key_format(self, store, mock_redis_client):
        """Key is session:{user_id}:{session_id}."""
        mock_redis_client.get.return_value = None
        await store.get("usr_42", "ses_xyz")
        mock_redis_client.get.assert_called_with("session:usr_42:ses_xyz")


# ------------------------------------------------------------------
# LLMBasedParser
# ------------------------------------------------------------------


class TestLLMBasedParser:
    @pytest.fixture()
    def parser(self, mock_llm_adapter) -> LLMBasedParser:
        return LLMBasedParser(mock_llm_adapter, model="test-model")

    async def test_llm_parser_single_message(
        self, parser, mock_llm_adapter
    ):
        """Returns ParseResult with intent + entities."""
        mock_llm_adapter.generate.return_value = (
            '{"intent": "schedule_meeting", '
            '"entities": {"attendee": "Alice"}, '
            '"constraints": {}}'
        )
        result = await parser.parse("Meet with Alice")
        assert result.intent == "schedule_meeting"
        assert result.entities["attendee"] == "Alice"

    async def test_llm_parser_with_context(
        self, parser, mock_llm_adapter, sample_session
    ):
        """Prior session context is included in prompt."""
        mock_llm_adapter.generate.return_value = (
            '{"intent": "schedule_meeting", '
            '"entities": {"attendee": "Alice", "time": "10 AM"}, '
            '"constraints": {}}'
        )
        result = await parser.parse("Tuesday at 10 AM", sample_session)
        assert result.entities["time"] == "10 AM"

        # Verify context was in the prompt
        call_kwargs = mock_llm_adapter.generate.call_args
        user_prompt = call_kwargs.kwargs.get(
            "user_prompt", call_kwargs[1].get("user_prompt", "")
        )
        assert "schedule_meeting" in user_prompt

    async def test_llm_parser_handles_markdown_fences(
        self, parser, mock_llm_adapter
    ):
        """Strips ``` fences from JSON."""
        mock_llm_adapter.generate.return_value = (
            "```json\n"
            '{"intent": "schedule_meeting", "entities": {}, "constraints": {}}'
            "\n```"
        )
        result = await parser.parse("Meet with Alice")
        assert result.intent == "schedule_meeting"

    async def test_llm_parser_handles_llm_error(
        self, parser, mock_llm_adapter
    ):
        """LLM exception -> IntentParserError."""
        mock_llm_adapter.generate.side_effect = RuntimeError("LLM timeout")
        with pytest.raises(IntentParserError):
            await parser.parse("Hello")

    async def test_llm_parser_handles_invalid_json(
        self, parser, mock_llm_adapter
    ):
        """Bad JSON -> IntentParserError."""
        mock_llm_adapter.generate.return_value = "not json {{"
        with pytest.raises(IntentParserError):
            await parser.parse("Hello")

    async def test_llm_parser_handles_partial_result(
        self, parser, mock_llm_adapter
    ):
        """Missing intent field -> intent=None."""
        mock_llm_adapter.generate.return_value = (
            '{"entities": {"attendee": "Bob"}, "constraints": {}}'
        )
        result = await parser.parse("Call Bob")
        assert result.intent is None
        assert result.entities["attendee"] == "Bob"
