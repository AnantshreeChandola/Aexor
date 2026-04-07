"""
Tests for UserToolCache and ToolCatalog.refresh_user().
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from shared.mcp.user_tool_cache import UserToolCache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock()
    r.delete = AsyncMock(return_value=1)
    return r


@pytest.fixture
def cache(mock_redis):
    return UserToolCache(mock_redis, ttl_seconds=3600)


_SAMPLE_TOOLS = [
    {"name": "GMAIL_SEND_EMAIL", "server_name": "composio", "provider_name": "gmail", "description": "Send email"},
    {"name": "GOOGLECALENDAR_CREATE_EVENT", "server_name": "composio", "provider_name": "google_calendar", "description": "Create event"},
]


# ---------------------------------------------------------------------------
# get / set
# ---------------------------------------------------------------------------


class TestGetSet:
    async def test_cache_miss_returns_none(self, cache, mock_redis):
        result = await cache.get("user-1")
        assert result is None

    async def test_cache_hit(self, cache, mock_redis):
        mock_redis.get.return_value = json.dumps(_SAMPLE_TOOLS)
        result = await cache.get("user-1")
        assert len(result) == 2
        assert result[0]["name"] == "GMAIL_SEND_EMAIL"

    async def test_set_stores_json(self, cache, mock_redis):
        await cache.set("user-1", _SAMPLE_TOOLS)
        mock_redis.setex.assert_awaited_once()
        args = mock_redis.setex.call_args[0]
        assert args[0] == "user_tools:user-1"
        assert args[1] == 3600
        stored = json.loads(args[2])
        assert len(stored) == 2

    async def test_redis_error_on_get(self, cache, mock_redis):
        import redis.exceptions

        mock_redis.get.side_effect = redis.exceptions.RedisError("down")
        result = await cache.get("user-1")
        assert result is None

    async def test_redis_error_on_set(self, cache, mock_redis):
        import redis.exceptions

        mock_redis.setex.side_effect = redis.exceptions.RedisError("down")
        # Should not raise
        await cache.set("user-1", _SAMPLE_TOOLS)


# ---------------------------------------------------------------------------
# get_tool_names
# ---------------------------------------------------------------------------


class TestGetToolNames:
    async def test_returns_set_of_names(self, cache, mock_redis):
        mock_redis.get.return_value = json.dumps(_SAMPLE_TOOLS)
        names = await cache.get_tool_names("user-1")
        assert names == {"GMAIL_SEND_EMAIL", "GOOGLECALENDAR_CREATE_EVENT"}

    async def test_cache_miss_returns_none(self, cache, mock_redis):
        result = await cache.get_tool_names("user-1")
        assert result is None


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------


class TestInvalidate:
    async def test_deletes_key(self, cache, mock_redis):
        await cache.invalidate("user-1")
        mock_redis.delete.assert_awaited_once_with("user_tools:user-1")

    async def test_redis_error_swallowed(self, cache, mock_redis):
        import redis.exceptions

        mock_redis.delete.side_effect = redis.exceptions.RedisError("down")
        await cache.invalidate("user-1")
