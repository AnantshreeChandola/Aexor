"""Tests for ComposioClient REST API adapter."""

from __future__ import annotations

import httpx
import pytest

from components.IntegrationManager.adapters.composio_client import ComposioClient
from components.IntegrationManager.domain.models import (
    ComposioApiError,
    ComposioUnreachableError,
)

API_KEY = "sk-composio-test-key"
BASE_URL = "https://backend.composio.dev"


def _mock_transport(
    *,
    status_code: int = 200,
    json_body: dict | list | None = None,
    text_body: str = "",
    raise_error: Exception | None = None,
) -> httpx.MockTransport:
    """Create an httpx MockTransport that returns a fixed response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if raise_error is not None:
            raise raise_error
        body = json_body if json_body is not None else {}
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def _make_client(transport: httpx.MockTransport) -> ComposioClient:
    http = httpx.AsyncClient(transport=transport)
    return ComposioClient(http_client=http, api_key=API_KEY, base_url=BASE_URL)


# ------------------------------------------------------------------
# Header injection
# ------------------------------------------------------------------


class TestHeaders:
    @pytest.mark.asyncio()
    async def test_api_key_header_sent(self):
        captured_headers = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"allowed_tools": []})

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        await client.get_mcp_config("cfg-123")

        assert captured_headers.get("x-api-key") == API_KEY


# ------------------------------------------------------------------
# System-level: MCP config
# ------------------------------------------------------------------


class TestGetMcpConfig:
    @pytest.mark.asyncio()
    async def test_returns_config(self):
        transport = _mock_transport(
            json_body={"allowed_tools": ["TOOL_A", "TOOL_B"], "id": "cfg-123"}
        )
        client = _make_client(transport)
        result = await client.get_mcp_config("cfg-123")
        assert result["allowed_tools"] == ["TOOL_A", "TOOL_B"]

    @pytest.mark.asyncio()
    async def test_raises_on_404(self):
        transport = _mock_transport(status_code=404, text_body="Not found")
        client = _make_client(transport)
        with pytest.raises(ComposioApiError) as exc_info:
            await client.get_mcp_config("nonexistent")
        assert exc_info.value.status_code == 404


class TestUpdateAllowedTools:
    @pytest.mark.asyncio()
    async def test_sends_patch(self):
        captured_request = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_request["method"] = request.method
            captured_request["url"] = str(request.url)
            captured_request["body"] = request.content.decode()
            return httpx.Response(200, json={"allowed_tools": ["TOOL_A", "TOOL_B"]})

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        await client.update_allowed_tools("cfg-123", ["TOOL_A", "TOOL_B"])

        assert captured_request["method"] == "PATCH"
        assert "/api/v3/mcp/cfg-123" in captured_request["url"]
        assert '"allowed_tools"' in captured_request["body"]

    @pytest.mark.asyncio()
    async def test_raises_on_server_error(self):
        transport = _mock_transport(status_code=500, text_body="Internal error")
        client = _make_client(transport)
        with pytest.raises(ComposioApiError) as exc_info:
            await client.update_allowed_tools("cfg-123", ["TOOL_A"])
        assert exc_info.value.status_code == 500


# ------------------------------------------------------------------
# Per-user: Connection management
# ------------------------------------------------------------------


class TestInitiateConnection:
    @pytest.mark.asyncio()
    async def test_returns_redirect_url(self):
        transport = _mock_transport(
            json_body={"redirectUrl": "https://oauth.example.com/auth?state=xyz"}
        )
        client = _make_client(transport)
        url = await client.initiate_connection("user-1", "ac_gcal")
        assert url == "https://oauth.example.com/auth?state=xyz"

    @pytest.mark.asyncio()
    async def test_sends_callback_url(self):
        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured_body.update(json.loads(request.content))
            return httpx.Response(
                200, json={"redirectUrl": "https://oauth.example.com"}
            )

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        await client.initiate_connection(
            "user-1", "ac_gcal", redirect_url="https://myapp.com/cb"
        )

        assert captured_body["connection"]["callback_url"] == "https://myapp.com/cb"
        assert captured_body["connection"]["user_id"] == "user-1"
        assert captured_body["auth_config"]["id"] == "ac_gcal"

    @pytest.mark.asyncio()
    async def test_no_callback_url_when_none(self):
        captured_body = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured_body.update(json.loads(request.content))
            return httpx.Response(
                200, json={"redirectUrl": "https://oauth.example.com"}
            )

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        await client.initiate_connection("user-1", "ac_gcal")

        assert "callback_url" not in captured_body.get("connection", {})

    @pytest.mark.asyncio()
    async def test_fallback_redirect_url_key(self):
        transport = _mock_transport(
            json_body={"redirect_url": "https://oauth.example.com/alt"}
        )
        client = _make_client(transport)
        url = await client.initiate_connection("user-1", "ac_gcal")
        assert url == "https://oauth.example.com/alt"


class TestListConnections:
    @pytest.mark.asyncio()
    async def test_returns_items(self):
        transport = _mock_transport(
            json_body={
                "items": [
                    {"id": "ca-1", "appName": "google_calendar", "status": "ACTIVE"},
                    {"id": "ca-2", "appName": "slack", "status": "ACTIVE"},
                ]
            }
        )
        client = _make_client(transport)
        result = await client.list_connections("user-1")
        assert len(result) == 2
        assert result[0]["appName"] == "google_calendar"

    @pytest.mark.asyncio()
    async def test_sends_query_params(self):
        captured_url = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_url["url"] = str(request.url)
            return httpx.Response(200, json={"items": []})

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        await client.list_connections("user-1")

        assert "user_ids=user-1" in captured_url["url"]
        assert "statuses=ACTIVE" in captured_url["url"]

    @pytest.mark.asyncio()
    async def test_empty_items_when_no_key(self):
        transport = _mock_transport(json_body={})
        client = _make_client(transport)
        result = await client.list_connections("user-1")
        assert result == []


class TestRevokeConnection:
    @pytest.mark.asyncio()
    async def test_sends_delete(self):
        captured_request = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_request["method"] = request.method
            captured_request["url"] = str(request.url)
            return httpx.Response(204)

        transport = httpx.MockTransport(handler)
        client = _make_client(transport)
        await client.revoke_connection("ca-123")

        assert captured_request["method"] == "DELETE"
        assert "/api/v3/connected_accounts/ca-123" in captured_request["url"]

    @pytest.mark.asyncio()
    async def test_raises_on_not_found(self):
        transport = _mock_transport(status_code=404, text_body="Not found")
        client = _make_client(transport)
        with pytest.raises(ComposioApiError) as exc_info:
            await client.revoke_connection("nonexistent")
        assert exc_info.value.status_code == 404


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio()
    async def test_connect_error_raises_unreachable(self):
        transport = _mock_transport(raise_error=httpx.ConnectError("refused"))
        client = _make_client(transport)
        with pytest.raises(ComposioUnreachableError):
            await client.get_mcp_config("cfg-123")

    @pytest.mark.asyncio()
    async def test_timeout_raises_unreachable(self):
        transport = _mock_transport(raise_error=httpx.ReadTimeout("timed out"))
        client = _make_client(transport)
        with pytest.raises(ComposioUnreachableError):
            await client.get_mcp_config("cfg-123")

    @pytest.mark.asyncio()
    async def test_4xx_raises_api_error(self):
        transport = _mock_transport(status_code=422, text_body="Validation error")
        client = _make_client(transport)
        with pytest.raises(ComposioApiError) as exc_info:
            await client.update_allowed_tools("cfg-123", [])
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio()
    async def test_5xx_raises_api_error(self):
        transport = _mock_transport(status_code=503, text_body="Unavailable")
        client = _make_client(transport)
        with pytest.raises(ComposioApiError) as exc_info:
            await client.list_connections("user-1")
        assert exc_info.value.status_code == 503
