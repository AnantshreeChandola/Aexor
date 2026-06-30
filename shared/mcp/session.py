"""
MCP Session Manager

Handles the MCP Streamable HTTP ``initialize`` handshake and
``Mcp-Session-Id`` tracking per server.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2025-03-26"
_CLIENT_INFO = {"name": "personal-agent", "version": "1.0.0"}


class MCPSessionError(Exception):
    """MCP initialize handshake failed."""

    def __init__(self, server_name: str, reason: str) -> None:
        self.server_name = server_name
        super().__init__(f"MCP session init failed for '{server_name}': {reason}")


@dataclass
class MCPSession:
    """Cached session state for a single MCP server."""

    server_name: str
    session_id: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)


class MCPSessionManager:
    """Manages MCP sessions: initialize handshake + session ID caching.

    One session per logical server name.  Per-server locks prevent
    duplicate concurrent initialize calls.
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client
        self._sessions: dict[str, MCPSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, server_name: str) -> asyncio.Lock:
        if server_name not in self._locks:
            self._locks[server_name] = asyncio.Lock()
        return self._locks[server_name]

    async def get_session(
        self,
        server_name: str,
        url: str,
        headers: dict[str, str],
        *,
        cache_key: str | None = None,
    ) -> MCPSession:
        """Return cached session or create one via initialize handshake.

        Args:
            server_name: Logical MCP server name.
            url: MCP server URL.
            headers: Auth/extra headers.
            cache_key: Override for the session cache key. When provided,
                uses this instead of ``server_name`` for cache lookup/storage.
                This enables per-user-URL session isolation in Composio mode.
        """
        key = cache_key if cache_key is not None else server_name
        existing = self._sessions.get(key)
        if existing is not None:
            return existing

        lock = self._get_lock(key)
        async with lock:
            # Double-check after acquiring lock
            existing = self._sessions.get(key)
            if existing is not None:
                return existing

            session = await self._initialize(server_name, url, headers)
            self._sessions[key] = session
            return session

    async def _initialize(
        self,
        server_name: str,
        url: str,
        headers: dict[str, str],
    ) -> MCPSession:
        """Send the MCP ``initialize`` JSON-RPC request."""
        payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        }

        init_headers = {
            **headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        try:
            response = await self._http.post(url, json=payload, headers=init_headers)
        except httpx.HTTPError as exc:
            raise MCPSessionError(server_name, f"HTTP error: {exc}") from exc

        if response.status_code >= 400:
            raise MCPSessionError(
                server_name,
                f"HTTP {response.status_code}: {response.text[:200]}",
            )

        # Extract session ID from response headers
        session_id = response.headers.get("mcp-session-id")

        # Parse capabilities from JSON-RPC response (may be SSE or JSON)
        capabilities: dict[str, Any] = {}
        try:
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                # SSE: extract JSON from "data:" line
                data = {}
                for line in response.text.splitlines():
                    if line.startswith("data: "):
                        import json

                        data = json.loads(line[6:])
                        break
            else:
                data = response.json()
            result = data.get("result", {})
            capabilities = result.get("capabilities", {})

            if "tools" not in capabilities:
                logger.warning(
                    "MCP server '%s' capabilities do not include 'tools'",
                    server_name,
                )
        except Exception:
            logger.warning(
                "Failed to parse initialize response from '%s'",
                server_name,
            )

        logger.info(
            "MCP session initialized",
            extra={
                "server": server_name,
                "has_session_id": session_id is not None,
                "capabilities": list(capabilities.keys()),
            },
        )

        return MCPSession(
            server_name=server_name,
            session_id=session_id,
            capabilities=capabilities,
        )

    def invalidate(self, server_name: str) -> None:
        """Remove cached session, forcing re-init on next call."""
        self._sessions.pop(server_name, None)

    def invalidate_all(self) -> None:
        """Clear all cached sessions (for shutdown)."""
        self._sessions.clear()
