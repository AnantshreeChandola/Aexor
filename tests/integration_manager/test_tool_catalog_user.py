"""
Tests for ToolCatalog.refresh_user() and get_user_tools().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.mcp.catalog import ToolCatalog, ToolDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jsonrpc_response(tools: list[dict]) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": tools},
    }


_RAW_TOOLS = [
    {"name": "GMAIL_SEND_EMAIL", "description": "Send email", "inputSchema": {}},
    {"name": "GOOGLECALENDAR_CREATE_EVENT", "description": "Create event", "inputSchema": {"type": "object"}},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_http():
    client = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = _jsonrpc_response(_RAW_TOOLS)
    client.post = AsyncMock(return_value=response)
    return client


@pytest.fixture
def mock_session_manager():
    mgr = AsyncMock()
    session = MagicMock()
    session.session_id = "test-session-123"
    mgr.get_session = AsyncMock(return_value=session)
    return mgr


@pytest.fixture
def mock_url_manager():
    mgr = AsyncMock()
    mgr.get_url = AsyncMock(return_value="https://composio.test/mcp/user-1")
    mgr.get_system_url = AsyncMock(return_value="https://composio.test/mcp/system")
    return mgr


@pytest.fixture
def mock_composio_config():
    cfg = MagicMock()
    cfg.api_key = "test-key"
    cfg.mcp_config_id = "cfg-123"
    cfg.system_user_id = "__system__"
    return cfg


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.list_servers.return_value = []
    return cfg


@pytest.fixture
def mock_user_tool_cache():
    cache = AsyncMock()
    cache.get.return_value = None
    cache.get_tool_names.return_value = None
    cache.set = AsyncMock()
    cache.invalidate = AsyncMock()
    return cache


@pytest.fixture
def catalog(
    mock_config,
    mock_http,
    mock_session_manager,
    mock_composio_config,
    mock_url_manager,
    mock_user_tool_cache,
):
    return ToolCatalog(
        config=mock_config,
        http_client=mock_http,
        session_manager=mock_session_manager,
        composio_config=mock_composio_config,
        url_manager=mock_url_manager,
        user_tool_cache=mock_user_tool_cache,
    )


# ---------------------------------------------------------------------------
# refresh_user
# ---------------------------------------------------------------------------


class TestRefreshUser:
    async def test_fetches_tools_for_user(self, catalog, mock_http, mock_url_manager):
        tools = await catalog.refresh_user("user-1")

        mock_url_manager.get_url.assert_awaited_once_with("user-1")
        mock_http.post.assert_awaited_once()
        assert len(tools) == 2
        assert tools[0].name == "GMAIL_SEND_EMAIL"
        assert tools[1].name == "GOOGLECALENDAR_CREATE_EVENT"

    async def test_caches_results(self, catalog, mock_user_tool_cache):
        await catalog.refresh_user("user-1")

        mock_user_tool_cache.set.assert_awaited_once()
        args = mock_user_tool_cache.set.call_args
        assert args[0][0] == "user-1"
        cached_tools = args[0][1]
        assert len(cached_tools) == 2
        assert cached_tools[0]["name"] == "GMAIL_SEND_EMAIL"

    async def test_extracts_provider_name(self, catalog):
        tools = await catalog.refresh_user("user-1")

        assert tools[0].provider_name == "gmail"
        # All-caps prefix GOOGLECALENDAR stays as one word (no camelCase transition)
        assert tools[1].provider_name == "googlecalendar"

    async def test_http_error_falls_back_to_global(self, catalog, mock_http):
        mock_http.post.side_effect = RuntimeError("network error")

        # Pre-populate global catalog
        catalog._tools = {
            "SLACK_SEND_MESSAGE": ToolDefinition(
                name="SLACK_SEND_MESSAGE",
                server_name="composio",
                provider_name="slack",
            )
        }

        tools = await catalog.refresh_user("user-1")
        assert len(tools) == 1
        assert tools[0].name == "SLACK_SEND_MESSAGE"

    async def test_no_composio_falls_back_to_global(
        self, mock_config, mock_http, mock_session_manager
    ):
        """Without url_manager, returns global tools."""
        catalog = ToolCatalog(
            config=mock_config,
            http_client=mock_http,
            session_manager=mock_session_manager,
        )
        catalog._tools = {
            "TEST_TOOL": ToolDefinition(
                name="TEST_TOOL",
                server_name="test",
                provider_name="test",
            )
        }

        tools = await catalog.refresh_user("user-1")
        assert len(tools) == 1
        assert tools[0].name == "TEST_TOOL"


# ---------------------------------------------------------------------------
# get_user_tools / get_user_tool_names
# ---------------------------------------------------------------------------


class TestGetUserTools:
    async def test_returns_cached_tools(self, catalog, mock_user_tool_cache):
        mock_user_tool_cache.get.return_value = [
            {
                "name": "GMAIL_SEND_EMAIL",
                "server_name": "composio",
                "provider_name": "gmail",
                "description": "Send email",
                "input_schema": {},
            },
        ]

        tools = await catalog.get_user_tools("user-1")
        assert tools is not None
        assert len(tools) == 1
        assert tools[0].name == "GMAIL_SEND_EMAIL"
        assert isinstance(tools[0], ToolDefinition)

    async def test_returns_none_on_miss(self, catalog, mock_user_tool_cache):
        mock_user_tool_cache.get.return_value = None
        result = await catalog.get_user_tools("user-1")
        assert result is None

    async def test_no_cache_returns_none(
        self, mock_config, mock_http, mock_session_manager
    ):
        catalog = ToolCatalog(
            config=mock_config,
            http_client=mock_http,
            session_manager=mock_session_manager,
        )
        result = await catalog.get_user_tools("user-1")
        assert result is None


class TestGetUserToolNames:
    async def test_returns_names(self, catalog, mock_user_tool_cache):
        mock_user_tool_cache.get_tool_names.return_value = {
            "GMAIL_SEND_EMAIL",
            "GOOGLECALENDAR_CREATE_EVENT",
        }

        names = await catalog.get_user_tool_names("user-1")
        assert names == {"GMAIL_SEND_EMAIL", "GOOGLECALENDAR_CREATE_EVENT"}

    async def test_cache_miss(self, catalog, mock_user_tool_cache):
        mock_user_tool_cache.get_tool_names.return_value = None
        result = await catalog.get_user_tool_names("user-1")
        assert result is None
