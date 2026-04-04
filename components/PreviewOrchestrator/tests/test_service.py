"""
PreviewOrchestrator service tests -- cache interaction, MCP dispatch,
template resolution, factory function.

Tests adapter-level behavior and service integration with Redis cache.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from components.PreviewOrchestrator.adapters.preview_cache import (
    PreviewCacheAdapter,
)
from components.PreviewOrchestrator.adapters.previewability_checker import (
    PreviewabilityChecker,
)
from components.PreviewOrchestrator.domain.models import (
    PreviewRequest,
)
from components.PreviewOrchestrator.service.preview_service import (
    create_preview_service,
)
from components.PreviewOrchestrator.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
)

# ---------------------------------------------------------------------------
# PreviewCacheAdapter tests
# ---------------------------------------------------------------------------


class TestPreviewCacheAdapter:
    """Tests for PreviewCacheAdapter Redis operations."""

    async def test_store_returns_cache_key(self, mock_redis_client):
        """store() returns cache key when Redis available."""
        cache = PreviewCacheAdapter(mock_redis_client, ttl_s=900)
        state = {1: {"step": 1, "status": "completed"}}
        key = await cache.store("plan-123", "user-456", state)
        assert key == "preview:user-456:plan-123"

    async def test_store_returns_none_when_no_redis(self):
        """store() returns None when Redis is None."""
        cache = PreviewCacheAdapter(None, ttl_s=900)
        state = {1: {"step": 1, "status": "completed"}}
        key = await cache.store("plan-123", "user-456", state)
        assert key is None

    async def test_store_returns_none_on_redis_error(self):
        """store() returns None and logs warning on Redis error."""
        broken_redis = AsyncMock()
        broken_redis.set = AsyncMock(side_effect=ConnectionError("down"))
        cache = PreviewCacheAdapter(broken_redis, ttl_s=900)
        state = {1: {"step": 1, "status": "completed"}}
        key = await cache.store("plan-123", "user-456", state)
        assert key is None

    async def test_retrieve_returns_state_on_hit(self, mock_redis_client):
        """retrieve() returns deserialized state on cache hit."""
        cache = PreviewCacheAdapter(mock_redis_client, ttl_s=900)
        state = {1: {"step": 1, "status": "completed"}}
        await cache.store("plan-123", "user-456", state)
        result = await cache.retrieve("plan-123", "user-456")
        assert result is not None
        assert 1 in result
        assert result[1]["status"] == "completed"

    async def test_retrieve_returns_none_on_miss(self, mock_redis_client):
        """retrieve() returns None on cache miss."""
        cache = PreviewCacheAdapter(mock_redis_client, ttl_s=900)
        result = await cache.retrieve("nonexistent", "user")
        assert result is None

    async def test_retrieve_returns_none_when_no_redis(self):
        """retrieve() returns None when Redis is None."""
        cache = PreviewCacheAdapter(None, ttl_s=900)
        result = await cache.retrieve("plan-123", "user-456")
        assert result is None

    async def test_retrieve_returns_none_on_redis_error(self):
        """retrieve() returns None on Redis error."""
        broken_redis = AsyncMock()
        broken_redis.get = AsyncMock(side_effect=ConnectionError("down"))
        cache = PreviewCacheAdapter(broken_redis, ttl_s=900)
        result = await cache.retrieve("plan-123", "user-456")
        assert result is None

    async def test_store_retrieve_roundtrip_int_keys(self, mock_redis_client):
        """store() then retrieve() preserves int keys."""
        cache = PreviewCacheAdapter(mock_redis_client, ttl_s=900)
        state = {
            1: {"step": 1, "status": "completed"},
            2: {"step": 2, "status": "deferred"},
        }
        await cache.store("plan-123", "user-456", state)
        result = await cache.retrieve("plan-123", "user-456")
        assert result is not None
        assert set(result.keys()) == {1, 2}


# ---------------------------------------------------------------------------
# PreviewabilityChecker tests
# ---------------------------------------------------------------------------


class TestPreviewabilityChecker:
    """Tests for PreviewabilityChecker PluginRegistry integration."""

    async def test_previewable_operation_returns_true(self, mock_registry_service):
        """Returns True for previewable operations."""
        checker = PreviewabilityChecker(mock_registry_service)
        result = await checker.is_previewable("google.calendar", "list_events")
        assert result is True

    async def test_non_previewable_operation_returns_false(self, mock_registry_service):
        """Returns False for non-previewable operations."""
        checker = PreviewabilityChecker(mock_registry_service)
        result = await checker.is_previewable("google.calendar", "create_event")
        assert result is False

    async def test_tool_not_found_returns_false(self, mock_registry_service):
        """Returns False when get_tool() raises ToolNotFoundError."""
        checker = PreviewabilityChecker(mock_registry_service)
        result = await checker.is_previewable("nonexistent.tool", "any_op")
        assert result is False

    async def test_operation_not_found_returns_false(self, mock_registry_service):
        """Returns False when operation_id not in tool.operations."""
        checker = PreviewabilityChecker(mock_registry_service)
        result = await checker.is_previewable("google.calendar", "nonexistent_op")
        assert result is False


# ---------------------------------------------------------------------------
# Service-level cache integration
# ---------------------------------------------------------------------------


class TestServiceCacheIntegration:
    """Tests for preview + cache interaction."""

    async def test_preview_caches_state(self, preview_service, sample_plan, mock_redis_client):
        """US4 / FR-007: Preview caches state in Redis."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)
        assert result.cached_state_key is not None
        assert "preview:" in result.cached_state_key

    async def test_get_preview_state_returns_cached(
        self, preview_service, sample_plan, mock_redis_client
    ):
        """FR-012: get_preview_state() returns cached results."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        await preview_service.preview(request)

        state = await preview_service.get_preview_state(SAMPLE_PLAN_ID, SAMPLE_USER_ID)
        assert state is not None
        assert len(state) == 5  # All 5 steps in cache

    async def test_cache_ttl_set_correctly(self, preview_service, sample_plan, mock_redis_client):
        """US4: Verify key set with correct TTL."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)
        ttl = mock_redis_client.get_ttl(result.cached_state_key)
        assert ttl == 900

    async def test_re_preview_replaces_cache(self, preview_service, sample_plan, mock_redis_client):
        """US4: Re-running preview replaces old cache entry."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result1 = await preview_service.preview(request)
        result2 = await preview_service.preview(request)

        assert result1.cached_state_key == result2.cached_state_key

    async def test_get_preview_state_returns_none_for_missing(self, preview_service):
        """US4: get_preview_state() returns None for missing cache."""
        state = await preview_service.get_preview_state(
            "nonexistent-plan-id-26chars!!", SAMPLE_USER_ID
        )
        assert state is None

    async def test_preview_completes_without_redis(self, preview_service_no_redis, sample_plan):
        """FR-007: Preview completes when Redis unavailable."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service_no_redis.preview(request)

        assert result.cached_state_key is None
        assert result.can_execute is True
        assert result.source == "preview"


# ---------------------------------------------------------------------------
# Factory function tests
# ---------------------------------------------------------------------------


class TestFactory:
    """Tests for create_preview_service() factory."""

    def test_factory_creates_valid_service(self, mock_mcp_client, mock_registry_service):
        """create_preview_service() creates valid PreviewService."""
        from components.PreviewOrchestrator.service.preview_service import (
            PreviewService,
        )

        service = create_preview_service(
            mcp_client=mock_mcp_client,
            registry_service=mock_registry_service,
            redis_client=None,
        )
        assert isinstance(service, PreviewService)

    def test_factory_reads_ttl_from_env(self, mock_mcp_client, mock_registry_service, monkeypatch):
        """create_preview_service() reads PREVIEW_CACHE_TTL_S from env."""
        monkeypatch.setenv("PREVIEW_CACHE_TTL_S", "300")
        service = create_preview_service(
            mcp_client=mock_mcp_client,
            registry_service=mock_registry_service,
            redis_client=None,
        )
        assert service._cache._ttl_s == 300
