"""
Composio REST API Client

Thin httpx wrapper for Composio v3 API endpoints.
Handles system-level tool management and per-user OAuth connections.
"""

from __future__ import annotations

import logging

import httpx

from ..domain.models import ComposioApiError, ComposioUnreachableError

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://backend.composio.dev"


class ComposioClient:
    """Async client for Composio REST API v3."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._http = http_client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key}

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Send an HTTP request and return the JSON response.

        Raises:
            ComposioApiError: On non-2xx responses.
            ComposioUnreachableError: On connection/timeout errors.
        """
        try:
            resp = await self._http.request(
                method,
                self._url(path),
                headers=self._headers(),
                json=json,
                params=params,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ComposioUnreachableError(str(exc)) from exc

        if not resp.is_success:
            detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
            raise ComposioApiError(resp.status_code, detail)

        if resp.status_code == 204:
            return {}
        return resp.json()

    # ------------------------------------------------------------------
    # System-level: MCP config tool management
    # ------------------------------------------------------------------

    async def get_mcp_config(self, mcp_config_id: str) -> dict:
        """GET /api/v3/mcp/{mcp_config_id} — read current MCP config."""
        return await self._request("GET", f"/api/v3/mcp/{mcp_config_id}")

    async def update_allowed_tools(self, mcp_config_id: str, tools: list[str]) -> dict:
        """PATCH /api/v3/mcp/{mcp_config_id} — update allowed_tools list."""
        return await self._request(
            "PATCH",
            f"/api/v3/mcp/{mcp_config_id}",
            json={"allowed_tools": tools},
        )

    # ------------------------------------------------------------------
    # Per-user: OAuth connection management
    # ------------------------------------------------------------------

    async def initiate_connection(
        self,
        user_id: str,
        auth_config_id: str,
        redirect_url: str | None = None,
    ) -> str:
        """POST /api/v3/connected_accounts — initiate OAuth flow.

        Returns the redirect URL from the Composio response.
        """
        connection: dict = {"user_id": user_id}
        if redirect_url is not None:
            connection["callback_url"] = redirect_url

        body: dict = {
            "auth_config": {"id": auth_config_id},
            "connection": connection,
        }

        data = await self._request("POST", "/api/v3/connected_accounts", json=body)
        return data.get("redirectUrl") or data.get("redirect_url") or str(data)

    async def create_integration(self, app_name: str) -> dict:
        """POST /api/v3/auth_configs — create an auth config for an app.

        Uses Composio's managed OAuth credentials.
        Returns a dict with ``id`` set to the auth_config_id (e.g. ``ac_xxx``).
        """
        data = await self._request(
            "POST",
            "/api/v3/auth_configs",
            json={
                "toolkit": {"slug": app_name},
                "auth_config": {"type": "use_composio_managed_auth"},
            },
        )
        # v3 nests the id: {"auth_config": {"id": "ac_xxx", ...}}
        auth_config = data.get("auth_config", {})
        return {"id": auth_config.get("id"), **auth_config}

    async def get_auth_config(self, toolkit_slug: str) -> str | None:
        """GET /api/v3/auth_configs — find existing auth_config_id for a toolkit.

        Returns the first enabled auth_config id (``ac_xxx``) or None.
        """
        data = await self._request(
            "GET",
            "/api/v3/auth_configs",
            params={"toolkit_slug": toolkit_slug},
        )
        items = data.get("items", [])
        for item in items:
            ac_id = item.get("id")
            if ac_id and item.get("status", "ENABLED") != "DISABLED":
                return ac_id
        return None

    async def list_connections(self, user_id: str) -> list[dict]:
        """GET /api/v3/connected_accounts — list active connections for user."""
        data = await self._request(
            "GET",
            "/api/v3/connected_accounts",
            params={"user_ids": user_id, "statuses": "ACTIVE"},
        )
        return data.get("items", [])

    async def revoke_connection(self, connected_account_id: str) -> None:
        """DELETE /api/v3/connected_accounts/{id} — revoke a connection."""
        await self._request("DELETE", f"/api/v3/connected_accounts/{connected_account_id}")
