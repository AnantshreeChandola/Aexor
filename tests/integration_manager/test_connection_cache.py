"""
Tests for ConnectionCache — Redis-backed provider connection cache.

Covers: get/set/invalidate, is_cached hit/miss, Redis error handling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from components.IntegrationManager.adapters.connection_cache import ConnectionCache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """AsyncMock redis client."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock()
    r.delete = AsyncMock(return_value=1)
    return r


@pytest.fixture
def cache(mock_redis):
    return ConnectionCache(mock_redis, ttl_seconds=3600)


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


class TestGet:
    async def test_cache_miss_returns_none(self, cache, mock_redis):
        mock_redis.get.return_value = None
        result = await cache.get("user-1")
        assert result is None
        mock_redis.get.assert_awaited_once_with("connections:user-1")

    async def test_cache_hit_returns_set(self, cache, mock_redis):
        mock_redis.get.return_value = json.dumps(["github", "gmail"])
        result = await cache.get("user-1")
        assert result == {"github", "gmail"}

    async def test_redis_error_returns_none(self, cache, mock_redis):
        import redis.exceptions

        mock_redis.get.side_effect = redis.exceptions.RedisError("down")
        result = await cache.get("user-1")
        assert result is None


# ---------------------------------------------------------------------------
# set()
# ---------------------------------------------------------------------------


class TestSet:
    async def test_stores_sorted_json(self, cache, mock_redis):
        await cache.set("user-1", {"gmail", "github"})
        mock_redis.setex.assert_awaited_once()
        args = mock_redis.setex.call_args
        assert args[0][0] == "connections:user-1"
        assert args[0][1] == 3600
        stored = json.loads(args[0][2])
        assert stored == ["github", "gmail"]  # sorted

    async def test_empty_set(self, cache, mock_redis):
        await cache.set("user-1", set())
        args = mock_redis.setex.call_args
        stored = json.loads(args[0][2])
        assert stored == []

    async def test_redis_error_is_swallowed(self, cache, mock_redis):
        import redis.exceptions

        mock_redis.setex.side_effect = redis.exceptions.RedisError("down")
        # Should not raise
        await cache.set("user-1", {"github"})


# ---------------------------------------------------------------------------
# invalidate()
# ---------------------------------------------------------------------------


class TestInvalidate:
    async def test_deletes_key(self, cache, mock_redis):
        await cache.invalidate("user-1")
        mock_redis.delete.assert_awaited_once_with("connections:user-1")

    async def test_redis_error_is_swallowed(self, cache, mock_redis):
        import redis.exceptions

        mock_redis.delete.side_effect = redis.exceptions.RedisError("down")
        await cache.invalidate("user-1")


# ---------------------------------------------------------------------------
# is_cached()
# ---------------------------------------------------------------------------


class TestIsCached:
    async def test_cache_miss_returns_none(self, cache, mock_redis):
        mock_redis.get.return_value = None
        result = await cache.is_cached("user-1", "github")
        assert result is None

    async def test_provider_present_returns_true(self, cache, mock_redis):
        mock_redis.get.return_value = json.dumps(["github", "gmail"])
        result = await cache.is_cached("user-1", "github")
        assert result is True

    async def test_provider_absent_returns_false(self, cache, mock_redis):
        mock_redis.get.return_value = json.dumps(["gmail"])
        result = await cache.is_cached("user-1", "github")
        assert result is False
