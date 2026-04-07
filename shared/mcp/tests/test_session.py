"""Tests for shared.mcp.session — MCP session management."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from shared.mcp.session import MCPSessionError, MCPSessionManager


def _make_init_response(
    session_id: str | None = "ses-abc-123",
    capabilities: dict | None = None,
    status_code: int = 200,
) -> httpx.Response:
    """Build a mock httpx.Response for the initialize handshake."""
    caps = capabilities or {"tools": {"listChanged": True}}
    body = {
        "jsonrpc": "2.0",
        "id": 0,
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": caps,
            "serverInfo": {"name": "test-server", "version": "1.0"},
        },
    }
    headers = {}
    if session_id is not None:
        headers["mcp-session-id"] = session_id

    resp = httpx.Response(status_code, json=body, headers=headers)
    return resp


@pytest.fixture()
def mock_http_client():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=_make_init_response())
    return client


@pytest.fixture()
def session_manager(mock_http_client):
    return MCPSessionManager(mock_http_client)


class TestMCPSessionManager:
    @pytest.mark.asyncio()
    async def test_initialize_sends_correct_payload(self, session_manager, mock_http_client):
        await session_manager.get_session("test-server", "https://mcp.test/", {})

        mock_http_client.post.assert_called_once()
        _, kwargs = mock_http_client.post.call_args
        json_payload = kwargs.get("json")

        assert json_payload["jsonrpc"] == "2.0"
        assert json_payload["method"] == "initialize"
        assert json_payload["params"]["protocolVersion"] == "2025-03-26"
        assert json_payload["params"]["clientInfo"]["name"] == "personal-agent"

    @pytest.mark.asyncio()
    async def test_session_cached_after_init(self, session_manager, mock_http_client):
        s1 = await session_manager.get_session("srv", "https://mcp.test/", {})
        s2 = await session_manager.get_session("srv", "https://mcp.test/", {})

        assert s1 is s2
        assert mock_http_client.post.call_count == 1  # Only one init call

    @pytest.mark.asyncio()
    async def test_session_id_extracted(self, session_manager, mock_http_client):
        mock_http_client.post.return_value = _make_init_response(session_id="my-session-42")

        session = await session_manager.get_session("srv", "https://mcp.test/", {})
        assert session.session_id == "my-session-42"

    @pytest.mark.asyncio()
    async def test_session_id_none_when_absent(self, session_manager, mock_http_client):
        mock_http_client.post.return_value = _make_init_response(session_id=None)

        session = await session_manager.get_session("srv", "https://mcp.test/", {})
        assert session.session_id is None

    @pytest.mark.asyncio()
    async def test_capabilities_stored(self, session_manager, mock_http_client):
        caps = {"tools": {"listChanged": True}, "resources": {}}
        mock_http_client.post.return_value = _make_init_response(capabilities=caps)

        session = await session_manager.get_session("srv", "https://mcp.test/", {})
        assert session.capabilities == caps

    @pytest.mark.asyncio()
    async def test_invalidate_forces_reinit(self, session_manager, mock_http_client):
        await session_manager.get_session("srv", "https://mcp.test/", {})
        assert mock_http_client.post.call_count == 1

        session_manager.invalidate("srv")
        await session_manager.get_session("srv", "https://mcp.test/", {})
        assert mock_http_client.post.call_count == 2

    @pytest.mark.asyncio()
    async def test_invalidate_all(self, session_manager, mock_http_client):
        await session_manager.get_session("a", "https://a.test/", {})
        await session_manager.get_session("b", "https://b.test/", {})

        session_manager.invalidate_all()

        await session_manager.get_session("a", "https://a.test/", {})
        # 2 initial + 1 re-init for "a"
        assert mock_http_client.post.call_count == 3

    @pytest.mark.asyncio()
    async def test_http_error_raises_session_error(self, session_manager, mock_http_client):
        mock_http_client.post.side_effect = httpx.ConnectError("connection refused")

        with pytest.raises(MCPSessionError, match="test-server"):
            await session_manager.get_session("test-server", "https://mcp.test/", {})

    @pytest.mark.asyncio()
    async def test_http_4xx_raises_session_error(self, session_manager, mock_http_client):
        mock_http_client.post.return_value = httpx.Response(401, text="Unauthorized")

        with pytest.raises(MCPSessionError, match="401"):
            await session_manager.get_session("srv", "https://mcp.test/", {})

    @pytest.mark.asyncio()
    async def test_concurrent_gets_single_initialize(self, mock_http_client):
        """Multiple concurrent get_session calls should only trigger one initialize."""
        call_count = 0

        async def slow_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return _make_init_response()

        mock_http_client.post = AsyncMock(side_effect=slow_post)
        manager = MCPSessionManager(mock_http_client)

        sessions = await asyncio.gather(
            manager.get_session("srv", "https://mcp.test/", {}),
            manager.get_session("srv", "https://mcp.test/", {}),
            manager.get_session("srv", "https://mcp.test/", {}),
        )

        # All should be the same object
        assert sessions[0] is sessions[1] is sessions[2]
        # Only one initialize call
        assert call_count == 1

    @pytest.mark.asyncio()
    async def test_auth_headers_forwarded(self, session_manager, mock_http_client):
        headers = {"x-api-key": "sk-test-key"}
        await session_manager.get_session("srv", "https://mcp.test/", headers)

        _, kwargs = mock_http_client.post.call_args
        sent_headers = kwargs["headers"]
        assert sent_headers["x-api-key"] == "sk-test-key"

    @pytest.mark.asyncio()
    async def test_cache_key_overrides_server_name(self, session_manager, mock_http_client):
        """When cache_key is provided, it should be used for cache lookup instead of server_name."""
        s1 = await session_manager.get_session(
            "composio", "https://user1.url/", {}, cache_key="https://user1.url/"
        )
        s2 = await session_manager.get_session(
            "composio", "https://user1.url/", {}, cache_key="https://user1.url/"
        )

        assert s1 is s2
        assert mock_http_client.post.call_count == 1  # Only one init

    @pytest.mark.asyncio()
    async def test_different_cache_keys_get_different_sessions(
        self, session_manager, mock_http_client,
    ):
        """Different cache_keys (different user URLs) should produce separate sessions."""
        s1 = await session_manager.get_session(
            "composio", "https://user1.url/", {}, cache_key="https://user1.url/"
        )
        s2 = await session_manager.get_session(
            "composio", "https://user2.url/", {}, cache_key="https://user2.url/"
        )

        assert s1 is not s2
        assert mock_http_client.post.call_count == 2
