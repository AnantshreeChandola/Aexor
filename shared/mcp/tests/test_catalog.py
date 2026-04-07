"""Tests for shared.mcp.catalog — in-memory tool catalog."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from shared.mcp.catalog import (
    ToolCatalog,
    ToolNotFoundError,
    _extract_provider_name,
)
from shared.mcp.config import ComposioConfig, MCPConfigRegistry, MCPServerConfig
from shared.mcp.session import MCPSession, MCPSessionManager
from shared.mcp.url_manager import MCPUrlManager


def _make_tools_list_response(tools: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": tools},
        },
    )


@pytest.fixture()
def composio_config():
    return MCPServerConfig(
        name="composio",
        url="https://backend.composio.dev/v3/mcp/abc",
        api_key="sk-test",
    )


@pytest.fixture()
def config_registry(composio_config):
    return MCPConfigRegistry({"composio": composio_config})


@pytest.fixture()
def mock_http():
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture()
def mock_session_manager():
    mgr = AsyncMock(spec=MCPSessionManager)
    mgr.get_session = AsyncMock(
        return_value=MCPSession(server_name="composio", session_id="ses-1")
    )
    return mgr


@pytest.fixture()
def catalog(config_registry, mock_http, mock_session_manager):
    return ToolCatalog(
        config=config_registry,
        http_client=mock_http,
        session_manager=mock_session_manager,
    )


class TestExtractProviderName:
    def test_composio_style(self):
        assert _extract_provider_name("GOOGLECALENDAR_CREATE_EVENT") == "googlecalendar"

    def test_single_word(self):
        assert _extract_provider_name("SLACK_SEND_MESSAGE") == "slack"

    def test_no_underscore(self):
        assert _extract_provider_name("TOOL") == "tool"

    def test_camel_case_splitting(self):
        assert _extract_provider_name("GoogleCalendar_create") == "google_calendar"


class TestToolCatalog:
    @pytest.mark.asyncio()
    async def test_refresh_populates_tools(self, catalog, mock_http):
        mock_http.post.return_value = _make_tools_list_response([
            {
                "name": "GOOGLECALENDAR_CREATE_EVENT",
                "description": "Create a calendar event",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "GMAIL_SEND_EMAIL",
                "description": "Send an email",
                "inputSchema": {},
            },
        ])

        await catalog.refresh()

        assert len(catalog.get_all_tools()) == 2
        tool = catalog.get_tool("GOOGLECALENDAR_CREATE_EVENT")
        assert tool is not None
        assert tool.server_name == "composio"
        assert tool.description == "Create a calendar event"

    @pytest.mark.asyncio()
    async def test_get_tool_or_raise_missing(self, catalog, mock_http):
        mock_http.post.return_value = _make_tools_list_response([])
        await catalog.refresh()

        with pytest.raises(ToolNotFoundError, match="NONEXISTENT"):
            catalog.get_tool_or_raise("NONEXISTENT")

    @pytest.mark.asyncio()
    async def test_get_tools_for_server(self, catalog, mock_http):
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "TOOL_A", "description": ""},
            {"name": "TOOL_B", "description": ""},
        ])
        await catalog.refresh()

        tools = catalog.get_tools_for_server("composio")
        assert len(tools) == 2

    @pytest.mark.asyncio()
    async def test_get_tools_for_provider(self, catalog, mock_http):
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "SLACK_SEND", "description": ""},
            {"name": "SLACK_READ", "description": ""},
            {"name": "GMAIL_SEND", "description": ""},
        ])
        await catalog.refresh()

        slack_tools = catalog.get_tools_for_provider("slack")
        assert len(slack_tools) == 2

    @pytest.mark.asyncio()
    async def test_allowlist_filters(self, catalog, mock_http, monkeypatch):
        monkeypatch.setenv("TOOL_ALLOWLIST", "GMAIL_SEND")
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "GMAIL_SEND", "description": ""},
            {"name": "SLACK_POST", "description": ""},
        ])
        await catalog.refresh()

        assert catalog.get_tool("GMAIL_SEND") is not None
        assert catalog.get_tool("SLACK_POST") is None

    @pytest.mark.asyncio()
    async def test_blocklist_filters(self, catalog, mock_http, monkeypatch):
        monkeypatch.setenv("TOOL_BLOCKLIST", "DANGEROUS_TOOL")
        monkeypatch.delenv("TOOL_ALLOWLIST", raising=False)
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "SAFE_TOOL", "description": ""},
            {"name": "DANGEROUS_TOOL", "description": ""},
        ])
        await catalog.refresh()

        assert catalog.get_tool("SAFE_TOOL") is not None
        assert catalog.get_tool("DANGEROUS_TOOL") is None

    @pytest.mark.asyncio()
    async def test_refresh_failure_preserves_previous_tools(
        self, config_registry, mock_session_manager,
    ):
        """If refresh fails for a server, keep previously loaded tools."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        catalog = ToolCatalog(
            config=config_registry,
            http_client=mock_http,
            session_manager=mock_session_manager,
        )

        # First refresh succeeds
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "TOOL_A", "description": "first load"},
        ])
        await catalog.refresh()
        assert catalog.get_tool("TOOL_A") is not None

        # Second refresh fails
        mock_http.post.side_effect = httpx.ConnectError("connection refused")
        await catalog.refresh()

        # Tool should still be available
        assert catalog.get_tool("TOOL_A") is not None

    @pytest.mark.asyncio()
    async def test_needs_refresh(self, catalog, mock_http):
        assert catalog.needs_refresh() is True

        mock_http.post.return_value = _make_tools_list_response([])
        await catalog.refresh()

        assert catalog.needs_refresh(ttl_seconds=3600) is False
        assert catalog.needs_refresh(ttl_seconds=0) is True

    @pytest.mark.asyncio()
    async def test_refresh_server(self, catalog, mock_http):
        # Initial full refresh
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "OLD_TOOL", "description": ""},
        ])
        await catalog.refresh()
        assert catalog.get_tool("OLD_TOOL") is not None

        # Refresh single server with new tools
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "NEW_TOOL", "description": ""},
        ])
        await catalog.refresh_server("composio")

        assert catalog.get_tool("NEW_TOOL") is not None
        assert catalog.get_tool("OLD_TOOL") is None  # Replaced

    @pytest.mark.asyncio()
    async def test_tools_list_error_response(self, catalog, mock_http):
        mock_http.post.return_value = httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32600, "message": "Invalid request"},
            },
        )

        # Should not crash, just log warning and keep empty catalog
        await catalog.refresh()
        assert len(catalog.get_all_tools()) == 0


# ------------------------------------------------------------------
# Composio mode
# ------------------------------------------------------------------


def _make_composio_config(**overrides) -> ComposioConfig:
    defaults = {
        "api_key": "sk-composio-test",
        "mcp_config_id": "cfg-abc",
        "auth_configs": {},
        "system_user_id": "__system__",
    }
    defaults.update(overrides)
    return ComposioConfig(**defaults)


class TestToolCatalogComposioMode:
    @pytest.mark.asyncio()
    async def test_refresh_uses_composio_path(self, mock_http, mock_session_manager):
        """When url_manager is set, refresh() should use _refresh_composio path."""
        composio_cfg = _make_composio_config()
        url_mgr = MagicMock(spec=MCPUrlManager)
        url_mgr.get_system_url = AsyncMock(return_value="https://composio.dev/mcp/system")

        mock_http.post.return_value = _make_tools_list_response([
            {"name": "GCAL_CREATE_EVENT", "description": "Create event"},
            {"name": "GMAIL_SEND", "description": "Send email"},
        ])

        catalog = ToolCatalog(
            config=MCPConfigRegistry({}),
            http_client=mock_http,
            session_manager=mock_session_manager,
            composio_config=composio_cfg,
            url_manager=url_mgr,
        )

        await catalog.refresh()

        url_mgr.get_system_url.assert_awaited_once()
        assert len(catalog.get_all_tools()) == 2
        tool = catalog.get_tool("GCAL_CREATE_EVENT")
        assert tool is not None
        assert tool.server_name == "composio"

    @pytest.mark.asyncio()
    async def test_composio_refresh_failure_preserves_stale(
        self, mock_http, mock_session_manager,
    ):
        """If Composio refresh fails, previously loaded tools are preserved."""
        composio_cfg = _make_composio_config()
        url_mgr = MagicMock(spec=MCPUrlManager)
        url_mgr.get_system_url = AsyncMock(return_value="https://composio.dev/mcp/system")

        # First refresh succeeds
        mock_http.post.return_value = _make_tools_list_response([
            {"name": "TOOL_A", "description": "tool A"},
        ])

        catalog = ToolCatalog(
            config=MCPConfigRegistry({}),
            http_client=mock_http,
            session_manager=mock_session_manager,
            composio_config=composio_cfg,
            url_manager=url_mgr,
        )

        await catalog.refresh()
        assert catalog.get_tool("TOOL_A") is not None

        # Second refresh fails
        mock_http.post.side_effect = httpx.ConnectError("connection refused")
        await catalog.refresh()

        # Stale tools preserved
        assert catalog.get_tool("TOOL_A") is not None

    @pytest.mark.asyncio()
    async def test_composio_api_key_in_headers(self, mock_http, mock_session_manager):
        """Verify the Composio API key is sent in headers during tools/list."""
        composio_cfg = _make_composio_config(api_key="sk-my-key")
        url_mgr = MagicMock(spec=MCPUrlManager)
        url_mgr.get_system_url = AsyncMock(return_value="https://composio.dev/mcp/system")

        mock_http.post.return_value = _make_tools_list_response([])

        catalog = ToolCatalog(
            config=MCPConfigRegistry({}),
            http_client=mock_http,
            session_manager=mock_session_manager,
            composio_config=composio_cfg,
            url_manager=url_mgr,
        )

        await catalog.refresh()

        _, kwargs = mock_http.post.call_args
        assert kwargs["headers"]["x-api-key"] == "sk-my-key"
