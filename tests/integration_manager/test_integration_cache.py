"""
Tests for IntegrationManager cache methods:
warm_connection_cache, is_user_connected_cached, cache invalidation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from components.IntegrationManager.domain.models import UserConnection
from components.IntegrationManager.service.integration_service import (
    IntegrationManager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_user_connections = AsyncMock(return_value=[])
    db.is_user_connected = AsyncMock(return_value=False)
    db.upsert_connection = AsyncMock()
    return db


@pytest.fixture
def mock_cache():
    cache = AsyncMock()
    cache.is_cached = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    cache.invalidate = AsyncMock()
    return cache


@pytest.fixture
def manager(mock_db, mock_cache):
    return IntegrationManager(
        db_adapter=mock_db,
        connection_cache=mock_cache,
    )


@pytest.fixture
def manager_no_cache(mock_db):
    return IntegrationManager(db_adapter=mock_db, connection_cache=None)


def _make_connection(provider: str, connected: bool = True) -> UserConnection:
    return UserConnection(
        user_id="user-1",
        provider_name=provider,
        is_connected=connected,
    )


# ---------------------------------------------------------------------------
# warm_connection_cache
# ---------------------------------------------------------------------------


class TestWarmConnectionCache:
    async def test_fetches_db_and_populates_cache(self, manager, mock_db, mock_cache):
        mock_db.get_user_connections.return_value = [
            _make_connection("github", True),
            _make_connection("gmail", True),
            _make_connection("slack", False),  # disconnected — should be excluded
        ]

        await manager.warm_connection_cache("user-1")

        mock_db.get_user_connections.assert_awaited_once_with("user-1")
        mock_cache.set.assert_awaited_once_with("user-1", {"github", "gmail"})

    async def test_empty_connections(self, manager, mock_db, mock_cache):
        mock_db.get_user_connections.return_value = []

        await manager.warm_connection_cache("user-1")

        mock_cache.set.assert_awaited_once_with("user-1", set())

    async def test_noop_without_cache(self, manager_no_cache, mock_db):
        await manager_no_cache.warm_connection_cache("user-1")
        mock_db.get_user_connections.assert_not_awaited()


# ---------------------------------------------------------------------------
# is_user_connected_cached
# ---------------------------------------------------------------------------


class TestIsUserConnectedCached:
    async def test_cache_hit_true(self, manager, mock_cache, mock_db):
        mock_cache.is_cached.return_value = True

        result = await manager.is_user_connected_cached("user-1", "github")

        assert result is True
        mock_cache.is_cached.assert_awaited_once_with("user-1", "github")
        mock_db.is_user_connected.assert_not_awaited()

    async def test_cache_hit_false(self, manager, mock_cache, mock_db):
        mock_cache.is_cached.return_value = False

        result = await manager.is_user_connected_cached("user-1", "github")

        assert result is False
        mock_db.is_user_connected.assert_not_awaited()

    async def test_cache_miss_falls_back_to_db(self, manager, mock_cache, mock_db):
        mock_cache.is_cached.return_value = None
        mock_db.is_user_connected.return_value = True

        result = await manager.is_user_connected_cached("user-1", "github")

        assert result is True
        mock_db.is_user_connected.assert_awaited_once_with("user-1", "github")

    async def test_no_cache_always_uses_db(self, manager_no_cache, mock_db):
        mock_db.is_user_connected.return_value = True

        result = await manager_no_cache.is_user_connected_cached("user-1", "github")

        assert result is True
        mock_db.is_user_connected.assert_awaited_once_with("user-1", "github")


# ---------------------------------------------------------------------------
# Cache invalidation on state changes
# ---------------------------------------------------------------------------


class TestCacheInvalidation:
    async def test_handle_callback_invalidates(self, manager, mock_db, mock_cache):
        mock_db.upsert_connection.return_value = _make_connection("github")

        await manager.handle_callback("user-1", "github", {"status": "connected"})

        mock_cache.invalidate.assert_awaited_once_with("user-1")

    async def test_disconnect_invalidates(self, manager, mock_db, mock_cache):
        mock_db.upsert_connection.return_value = _make_connection("github", False)

        await manager.disconnect("user-1", "github")

        mock_cache.invalidate.assert_awaited_once_with("user-1")

    async def test_mark_connected_invalidates(self, manager, mock_db, mock_cache):
        mock_db.upsert_connection.return_value = _make_connection("github")

        await manager.mark_connected("user-1", "github")

        mock_cache.invalidate.assert_awaited_once_with("user-1")

    async def test_no_cache_no_error(self, manager_no_cache, mock_db):
        mock_db.upsert_connection.return_value = _make_connection("github")

        # Should not raise even without cache
        await manager_no_cache.disconnect("user-1", "github")
        await manager_no_cache.mark_connected("user-1", "github")
