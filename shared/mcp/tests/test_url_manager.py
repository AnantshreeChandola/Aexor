"""Tests for shared.mcp.url_manager — per-user MCP URL generation."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from shared.mcp.config import ComposioConfig
from shared.mcp.url_manager import MCPUrlManager


def _make_config(**overrides) -> ComposioConfig:
    defaults = {
        "api_key": "sk-test",
        "mcp_config_id": "cfg-abc",
        "user_url_cache_ttl": 3600,
        "auth_configs": {},
        "system_user_id": "__system__",
    }
    defaults.update(overrides)
    return ComposioConfig(**defaults)


@pytest.fixture()
def composio_config():
    return _make_config()


@pytest.fixture()
def manager(composio_config):
    return MCPUrlManager(composio_config)


class TestGetUrl:
    @pytest.mark.asyncio()
    async def test_generates_url_on_first_call(self, manager):
        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()
            mock_composio.mcp.generate.return_value = "https://composio.dev/mcp/user-1"
            mock_import.return_value = mock_composio

            url = await manager.get_url("user-1")

            assert url == "https://composio.dev/mcp/user-1"
            mock_composio.mcp.generate.assert_called_once_with("user-1", "cfg-abc")

    @pytest.mark.asyncio()
    async def test_returns_cached_url(self, manager):
        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()
            mock_composio.mcp.generate.return_value = "https://composio.dev/mcp/user-1"
            mock_import.return_value = mock_composio

            url1 = await manager.get_url("user-1")
            url2 = await manager.get_url("user-1")

            assert url1 == url2
            # SDK called only once (second was cached)
            assert mock_composio.mcp.generate.call_count == 1

    @pytest.mark.asyncio()
    async def test_cache_expires_after_ttl(self):
        config = _make_config(user_url_cache_ttl=0)  # 0 = always expire
        mgr = MCPUrlManager(config)

        call_count = 0

        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()

            def gen(user_id, config_id):
                nonlocal call_count
                call_count += 1
                return f"https://url-{call_count}"

            mock_composio.mcp.generate.side_effect = gen
            mock_import.return_value = mock_composio

            url1 = await mgr.get_url("user-1")
            url2 = await mgr.get_url("user-1")

            assert url1 != url2  # TTL=0 so cache expired
            assert call_count == 2

    @pytest.mark.asyncio()
    async def test_different_users_get_different_urls(self, manager):
        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()
            mock_composio.mcp.generate.side_effect = lambda uid, cid: (
                f"https://composio.dev/mcp/{uid}"
            )
            mock_import.return_value = mock_composio

            url1 = await manager.get_url("user-1")
            url2 = await manager.get_url("user-2")

            assert url1 != url2
            assert "user-1" in url1
            assert "user-2" in url2


class TestGetSystemUrl:
    @pytest.mark.asyncio()
    async def test_uses_system_user_id(self, manager):
        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()
            mock_composio.mcp.generate.return_value = "https://composio.dev/system"
            mock_import.return_value = mock_composio

            url = await manager.get_system_url()

            assert url == "https://composio.dev/system"
            mock_composio.mcp.generate.assert_called_once_with("__system__", "cfg-abc")

    @pytest.mark.asyncio()
    async def test_custom_system_user_id(self):
        config = _make_config(system_user_id="platform-admin")
        mgr = MCPUrlManager(config)

        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()
            mock_composio.mcp.generate.return_value = "https://url"
            mock_import.return_value = mock_composio

            await mgr.get_system_url()
            mock_composio.mcp.generate.assert_called_once_with("platform-admin", "cfg-abc")


class TestInvalidation:
    @pytest.mark.asyncio()
    async def test_invalidate_forces_regeneration(self, manager):
        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()
            call_count = 0

            def gen(uid, cid):
                nonlocal call_count
                call_count += 1
                return f"https://url-{call_count}"

            mock_composio.mcp.generate.side_effect = gen
            mock_import.return_value = mock_composio

            url1 = await manager.get_url("user-1")
            manager.invalidate("user-1")
            url2 = await manager.get_url("user-1")

            assert url1 != url2
            assert call_count == 2

    @pytest.mark.asyncio()
    async def test_invalidate_all_clears_all(self, manager):
        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()
            mock_composio.mcp.generate.return_value = "https://url"
            mock_import.return_value = mock_composio

            await manager.get_url("user-1")
            await manager.get_url("user-2")
            manager.invalidate_all()

            # Both should re-generate
            await manager.get_url("user-1")
            await manager.get_url("user-2")

            # 2 initial + 2 after invalidation = 4 total
            assert mock_composio.mcp.generate.call_count == 4


class TestConcurrency:
    @pytest.mark.asyncio()
    async def test_concurrent_gets_single_generate(self, manager):
        """Multiple concurrent get_url calls for same user produce one SDK call."""
        call_count = 0

        with patch("shared.mcp.url_manager._lazy_import_composio") as mock_import:
            mock_composio = MagicMock()

            def gen(uid, cid):
                nonlocal call_count
                call_count += 1
                return f"https://url-{uid}"

            mock_composio.mcp.generate.side_effect = gen
            mock_import.return_value = mock_composio

            urls = await asyncio.gather(
                manager.get_url("user-1"),
                manager.get_url("user-1"),
                manager.get_url("user-1"),
            )

            assert all(u == urls[0] for u in urls)
            assert call_count == 1


class TestComposioNotInstalled:
    @pytest.mark.asyncio()
    async def test_import_error_raises_runtime(self, manager):
        with (
            patch(
                "shared.mcp.url_manager._lazy_import_composio",
                side_effect=ImportError("no composio"),
            ),
            pytest.raises(RuntimeError, match="composio package not installed"),
        ):
            await manager.get_url("user-1")
