"""
Idempotency Adapter Tests

Tests for the 3-state Redis idempotency mechanism (IN_FLIGHT, SUCCEEDED, FAILED).
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from ..adapters.idempotency import IdempotencyAdapter
from ..domain.models import IdempotencyConflict


@pytest.fixture()
def mock_redis():
    r = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hset = AsyncMock(return_value=True)
    r.expire = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    return r


@pytest.fixture()
def adapter(mock_redis):
    return IdempotencyAdapter(mock_redis)


class TestBuildKey:
    def test_deterministic_hash(self, adapter):
        key1 = adapter.build_key("u1", "tool", "plan1", 1, "create", {"a": 1})
        key2 = adapter.build_key("u1", "tool", "plan1", 1, "create", {"a": 1})
        assert key1 == key2

    def test_different_args_different_hash(self, adapter):
        key1 = adapter.build_key("u1", "tool", "plan1", 1, "create", {"a": 1})
        key2 = adapter.build_key("u1", "tool", "plan1", 1, "create", {"a": 2})
        assert key1 != key2

    def test_key_format(self, adapter):
        key = adapter.build_key("u1", "tool", "plan1", 1, "create", {"a": 1})
        assert key.startswith("idem:u1:tool:plan1:1:create:")
        parts = key.split(":")
        assert len(parts) == 7


class TestCheckAndClaim:
    async def test_no_prior_record(self, adapter, mock_redis):
        """No prior record: returns None, creates IN_FLIGHT."""
        mock_redis.hgetall = AsyncMock(return_value={})
        result = await adapter.check_and_claim("key", "exec-1")
        assert result is None
        mock_redis.hset.assert_called()

    async def test_succeeded_returns_cached(self, adapter, mock_redis):
        """SUCCEEDED record: returns cached StepResult."""
        mock_redis.hgetall = AsyncMock(
            return_value={
                "state": "SUCCEEDED",
                "result_json": json.dumps({"id": "evt-1"}),
                "step": "4",
            }
        )
        result = await adapter.check_and_claim("key", "exec-1")
        assert result is not None
        assert result.status == "completed"
        assert result.result == {"id": "evt-1"}

    async def test_in_flight_recent_raises(self, adapter, mock_redis):
        """IN_FLIGHT recent: raises IdempotencyConflict."""
        mock_redis.hgetall = AsyncMock(
            return_value={
                "state": "IN_FLIGHT",
                "execution_id": "exec-other",
                "claimed_at": str(time.time()),
            }
        )
        with pytest.raises(IdempotencyConflict):
            await adapter.check_and_claim("key", "exec-1")

    async def test_in_flight_stale_takes_over(self, adapter, mock_redis):
        """IN_FLIGHT stale (> 5 min): takes over, returns None."""
        stale_time = time.time() - 400  # 6+ minutes ago
        mock_redis.hgetall = AsyncMock(
            return_value={
                "state": "IN_FLIGHT",
                "execution_id": "exec-old",
                "claimed_at": str(stale_time),
            }
        )
        result = await adapter.check_and_claim("key", "exec-new")
        assert result is None
        mock_redis.hset.assert_called()

    async def test_failed_allows_retry(self, adapter, mock_redis):
        """FAILED record: delete and return None."""
        mock_redis.hgetall = AsyncMock(
            return_value={
                "state": "FAILED",
                "error": "prev error",
            }
        )
        result = await adapter.check_and_claim("key", "exec-1")
        assert result is None
        mock_redis.delete.assert_called_with("key")


class TestMarkSucceeded:
    async def test_sets_state(self, adapter, mock_redis):
        await adapter.mark_succeeded("key", {"id": "x"})
        call_args = mock_redis.hset.call_args
        mapping = call_args.kwargs.get("mapping", call_args[1].get("mapping"))
        assert mapping["state"] == "SUCCEEDED"
        assert "id" in mapping["result_json"]

    async def test_sets_ttl(self, adapter, mock_redis):
        await adapter.mark_succeeded("key", {"id": "x"})
        mock_redis.expire.assert_called_with("key", 86400)


class TestMarkFailed:
    async def test_sets_failed_state(self, adapter, mock_redis):
        await adapter.mark_failed("key", "timeout error")
        call_args = mock_redis.hset.call_args
        mapping = call_args.kwargs.get("mapping", call_args[1].get("mapping"))
        assert mapping["state"] == "FAILED"
        assert mapping["error"] == "timeout error"
