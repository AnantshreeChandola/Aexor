"""
In-Memory Tool Catalog

Replaces the database-backed PluginRegistry for tool definitions.
Populated from MCP servers' ``tools/list`` at startup and refreshed
periodically.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import ComposioConfig, MCPConfigRegistry
from .session import MCPSessionManager
from .url_manager import MCPUrlManager
from .user_tool_cache import UserToolCache

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_request_id_counter = itertools.count(1)


class ToolNotFoundError(Exception):
    """Tool name does not exist in the catalog."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' not found in catalog")


@dataclass
class ToolDefinition:
    """A tool discovered from an MCP server's ``tools/list`` response."""

    name: str
    server_name: str
    provider_name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


# Tools with these app prefixes use a different provider's OAuth connection.
# e.g. GOOGLEMEET_* tools authenticate via Google Calendar, not a separate app.
_PROVIDER_ALIASES: dict[str, str] = {
    "googlemeet": "googlecalendar",
}


def _extract_provider_name(tool_name: str) -> str:
    """Extract provider name from MCP tool name.

    Composio convention: ``GOOGLECALENDAR_CREATE_EVENT`` → ``googlecalendar``.
    Splits on first ``_``, lowercases, and applies provider aliases
    (e.g. ``GOOGLEMEET`` → ``googlecalendar``).
    """
    prefix = tool_name.split("_", 1)[0] if "_" in tool_name else tool_name
    # Insert underscore before uppercase-to-lowercase transitions
    # GOOGLECALENDAR -> GOOGLE_CALENDAR -> google_calendar
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", prefix)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spaced)
    name = spaced.lower()
    # Apply aliases: googlemeet -> googlecalendar (same OAuth connection)
    return _PROVIDER_ALIASES.get(name, name)


def _parse_sse_or_json(response) -> dict:
    """Parse a response that may be plain JSON or SSE (text/event-stream).

    Composio MCP endpoints return SSE with ``event: message\\ndata: {json}``.
    Other MCP servers return plain JSON.
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        # Extract JSON from SSE "data:" line
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise RuntimeError("No data: line found in SSE response")
    return response.json()


class ToolCatalog:
    """In-memory tool catalog populated from MCP servers' ``tools/list``."""

    def __init__(
        self,
        config: MCPConfigRegistry,
        http_client: httpx.AsyncClient,
        session_manager: MCPSessionManager,
        *,
        composio_config: ComposioConfig | None = None,
        url_manager: MCPUrlManager | None = None,
        user_tool_cache: UserToolCache | None = None,
    ) -> None:
        self._config = config
        self._http = http_client
        self._sessions = session_manager
        self._composio_config = composio_config
        self._url_manager = url_manager
        self._user_tool_cache = user_tool_cache
        self._tools: dict[str, ToolDefinition] = {}
        self._last_refresh: float = 0.0

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Refresh tools from all configured MCP servers.

        When Composio mode is active (``url_manager`` is set), fetches tools
        via the system-level Composio URL. Otherwise iterates individual
        server configs.  Errors on individual servers are logged but do not
        prevent loading tools from healthy servers.
        """
        if self._url_manager is not None:
            await self._refresh_composio()
            return

        new_tools: dict[str, ToolDefinition] = {}

        for server_name in self._config.list_servers():
            try:
                server_tools = await self._fetch_tools(server_name)
                for tool in server_tools:
                    new_tools[tool.name] = tool
            except Exception:
                logger.warning(
                    "Failed to refresh tools from '%s'",
                    server_name,
                    exc_info=True,
                )
                # Preserve previously loaded tools for this server
                for name, td in self._tools.items():
                    if td.server_name == server_name and name not in new_tools:
                        new_tools[name] = td

        # Apply allowlist/blocklist
        new_tools = self._apply_filters(new_tools)

        self._tools = new_tools
        self._last_refresh = time.monotonic()
        logger.info(
            "Tool catalog refreshed",
            extra={"tool_count": len(self._tools)},
        )

    async def _refresh_composio(self) -> None:
        """Refresh tools via Composio system URL."""
        assert self._url_manager is not None
        assert self._composio_config is not None

        try:
            tools = await self._fetch_tools_composio()
            new_tools = {t.name: t for t in tools}
            new_tools = self._apply_filters(new_tools)
            self._tools = new_tools
            self._last_refresh = time.monotonic()
            logger.info(
                "Tool catalog refreshed (Composio)",
                extra={"tool_count": len(self._tools)},
            )
        except Exception:
            logger.warning(
                "Composio tool catalog refresh failed, preserving stale tools",
                exc_info=True,
            )

    async def _fetch_tools_composio(self) -> list[ToolDefinition]:
        """Call ``tools/list`` on the Composio system MCP URL."""
        assert self._url_manager is not None
        assert self._composio_config is not None

        url = await self._url_manager.get_system_url()
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-api-key": self._composio_config.api_key,
        }

        session = await self._sessions.get_session(
            "composio", url, headers, cache_key=url
        )
        if session.session_id:
            headers["Mcp-Session-Id"] = session.session_id

        payload = {
            "jsonrpc": "2.0",
            "id": next(_request_id_counter),
            "method": "tools/list",
            "params": {},
        }

        response = await self._http.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise RuntimeError(
                f"tools/list failed for Composio: HTTP {response.status_code}"
            )

        data = _parse_sse_or_json(response)
        if "error" in data:
            raise RuntimeError(
                f"tools/list JSON-RPC error for Composio: {data['error']}"
            )

        raw_tools = data.get("result", {}).get("tools", [])
        tools: list[ToolDefinition] = []
        for raw in raw_tools:
            name = raw.get("name", "")
            if not name:
                continue
            tools.append(
                ToolDefinition(
                    name=name,
                    server_name="composio",
                    provider_name=_extract_provider_name(name),
                    description=raw.get("description", ""),
                    input_schema=raw.get("inputSchema", {}),
                )
            )

        logger.info(
            "Fetched tools from Composio",
            extra={"count": len(tools)},
        )
        return tools

    async def refresh_server(self, server_name: str) -> None:
        """Refresh tools from a single server."""
        try:
            server_tools = await self._fetch_tools(server_name)
        except Exception:
            logger.warning(
                "Failed to refresh tools from '%s'",
                server_name,
                exc_info=True,
            )
            return

        # Remove old tools for this server
        self._tools = {
            name: td
            for name, td in self._tools.items()
            if td.server_name != server_name
        }

        # Add new tools
        for tool in server_tools:
            self._tools[tool.name] = tool

        # Re-apply filters
        self._tools = self._apply_filters(self._tools)
        self._last_refresh = time.monotonic()

    # ------------------------------------------------------------------
    # Per-user tool discovery (Composio mode)
    # ------------------------------------------------------------------

    async def refresh_user(self, user_id: str) -> list[ToolDefinition]:
        """Fetch tools available to a specific user via their Composio MCP URL.

        Calls ``tools/list`` on the per-user URL (which reflects the user's
        connected apps) and caches the result in Redis.  Returns the
        discovered tools.  Requires Composio mode (url_manager set).

        Falls back to the global catalog tools if Composio mode is not active
        or if the call fails.
        """
        if self._url_manager is None or self._composio_config is None:
            return list(self._tools.values())

        try:
            tools = await self._fetch_tools_for_user(user_id)
        except Exception:
            logger.warning(
                "Per-user tool refresh failed, using global catalog",
                extra={"user_id": user_id},
                exc_info=True,
            )
            return list(self._tools.values())

        # Cache in Redis if available
        if self._user_tool_cache is not None:
            serialised = [
                {
                    "name": t.name,
                    "server_name": t.server_name,
                    "provider_name": t.provider_name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]
            await self._user_tool_cache.set(user_id, serialised)

        logger.info(
            "Per-user tool catalog refreshed",
            extra={"user_id": user_id, "tool_count": len(tools)},
        )
        return tools

    async def get_user_tools(self, user_id: str) -> list[ToolDefinition] | None:
        """Return cached per-user tools, or None if not cached."""
        if self._user_tool_cache is None:
            return None

        cached = await self._user_tool_cache.get(user_id)
        if cached is None:
            return None

        return [
            ToolDefinition(
                name=t["name"],
                server_name=t.get("server_name", "composio"),
                provider_name=t.get("provider_name", ""),
                description=t.get("description", ""),
                input_schema=t.get("input_schema", {}),
            )
            for t in cached
        ]

    async def get_user_tool_names(self, user_id: str) -> set[str] | None:
        """Return just the tool names for a user from cache."""
        if self._user_tool_cache is None:
            return None
        return await self._user_tool_cache.get_tool_names(user_id)

    async def _fetch_tools_for_user(self, user_id: str) -> list[ToolDefinition]:
        """Call ``tools/list`` on a user's Composio MCP URL."""
        assert self._url_manager is not None
        assert self._composio_config is not None

        url = await self._url_manager.get_url(user_id)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-api-key": self._composio_config.api_key,
        }

        session = await self._sessions.get_session(
            "composio", url, headers, cache_key=f"user:{user_id}"
        )
        if session.session_id:
            headers["Mcp-Session-Id"] = session.session_id

        payload = {
            "jsonrpc": "2.0",
            "id": next(_request_id_counter),
            "method": "tools/list",
            "params": {},
        }

        response = await self._http.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise RuntimeError(
                f"tools/list failed for user '{user_id}': HTTP {response.status_code}"
            )

        data = _parse_sse_or_json(response)
        if "error" in data:
            raise RuntimeError(
                f"tools/list JSON-RPC error for user '{user_id}': {data['error']}"
            )

        raw_tools = data.get("result", {}).get("tools", [])
        tools: list[ToolDefinition] = []
        for raw in raw_tools:
            name = raw.get("name", "")
            if not name:
                continue
            tools.append(
                ToolDefinition(
                    name=name,
                    server_name="composio",
                    provider_name=_extract_provider_name(name),
                    description=raw.get("description", ""),
                    input_schema=raw.get("inputSchema", {}),
                )
            )

        return tools

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_tool(self, tool_name: str) -> ToolDefinition | None:
        return self._tools.get(tool_name)

    def get_tool_or_raise(self, tool_name: str) -> ToolDefinition:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(tool_name)
        return tool

    def get_all_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_tools_for_server(self, server_name: str) -> list[ToolDefinition]:
        return [td for td in self._tools.values() if td.server_name == server_name]

    def get_tools_for_provider(self, provider_name: str) -> list[ToolDefinition]:
        return [td for td in self._tools.values() if td.provider_name == provider_name]

    def needs_refresh(self, ttl_seconds: int = 3600) -> bool:
        if self._last_refresh == 0.0:
            return True
        return (time.monotonic() - self._last_refresh) > ttl_seconds

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_tools(self, server_name: str) -> list[ToolDefinition]:
        """Call ``tools/list`` on a single MCP server."""
        cfg = self._config.get_or_raise(server_name)

        # Build auth headers
        headers: dict[str, str] = {}
        if cfg.api_key:
            headers[cfg.api_key_header] = cfg.api_key
        headers.update(cfg.extra_headers)

        # Get/create session (handles initialize handshake)
        session = await self._sessions.get_session(server_name, cfg.url, headers)

        # Build tools/list request
        request_headers = {
            **headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session.session_id:
            request_headers["Mcp-Session-Id"] = session.session_id

        payload = {
            "jsonrpc": "2.0",
            "id": next(_request_id_counter),
            "method": "tools/list",
            "params": {},
        }

        response = await self._http.post(
            cfg.url, json=payload, headers=request_headers
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f"tools/list failed for '{server_name}': HTTP {response.status_code}"
            )

        data = response.json()
        if "error" in data:
            raise RuntimeError(
                f"tools/list JSON-RPC error for '{server_name}': {data['error']}"
            )

        raw_tools = data.get("result", {}).get("tools", [])

        tools: list[ToolDefinition] = []
        for raw in raw_tools:
            name = raw.get("name", "")
            if not name:
                continue
            tools.append(
                ToolDefinition(
                    name=name,
                    server_name=server_name,
                    provider_name=_extract_provider_name(name),
                    description=raw.get("description", ""),
                    input_schema=raw.get("inputSchema", {}),
                )
            )

        logger.info(
            "Fetched tools from '%s'",
            server_name,
            extra={"count": len(tools)},
        )
        return tools

    @staticmethod
    def _apply_filters(tools: dict[str, ToolDefinition]) -> dict[str, ToolDefinition]:
        """Apply TOOL_ALLOWLIST / TOOL_BLOCKLIST env var filters."""
        allowlist_raw = os.environ.get("TOOL_ALLOWLIST", "").strip()
        blocklist_raw = os.environ.get("TOOL_BLOCKLIST", "").strip()

        if allowlist_raw:
            allowed = {t.strip() for t in allowlist_raw.split(",") if t.strip()}
            tools = {name: td for name, td in tools.items() if name in allowed}
        elif blocklist_raw:
            blocked = {t.strip() for t in blocklist_raw.split(",") if t.strip()}
            tools = {name: td for name, td in tools.items() if name not in blocked}

        return tools
