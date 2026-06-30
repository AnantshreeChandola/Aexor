"""
MCP Client Adapter

Protocol and httpx-based implementation for MCP tool invocations
against hosted MCP servers via Streamable HTTP.

Reference: LLD.md Section 6.1
"""

from __future__ import annotations

import itertools
import json
import logging
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from shared.mcp.config import ComposioConfig, MCPConfigRegistry, MCPServerConfig
from shared.mcp.session import MCPSessionManager
from shared.mcp.url_manager import MCPUrlManager

from ..domain.models import MCPInvocationError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {503, 504}
_SESSION_EXPIRED_CODES = {401, 403}

# Patterns in MCP tool result content that indicate the user's integration
# is not connected/authenticated.  Composio sometimes returns these as
# successful JSON-RPC responses (HTTP 200) instead of JSON-RPC errors.
_RESULT_NOT_CONNECTED_RE = re.compile(
    r"no connected\b.*\baccount|"
    r"not connected|"
    r"not authenticated|"
    r"account.*not.*found|"
    r"entity.*not.*found|"
    r"connection.*not.*found|"
    r"authentication.*required|"
    r"auth.*error.*account|"
    r"no active connection",
    re.IGNORECASE,
)

# Patterns that suggest prompt injection in MCP server responses
_INJECTION_PATTERNS = re.compile(
    r"(?:^|\n)\s*(?:"
    r"you\s+are\b|"
    r"system\s*:|"
    r"ignore\s+(?:previous|all|above)|"
    r"important\s*:|"
    r"new\s+instructions?\s*:|"
    r"disregard\s+(?:previous|all|above)"
    r")",
    re.IGNORECASE,
)


@runtime_checkable
class MCPClient(Protocol):
    """Protocol for MCP tool invocations."""

    async def invoke(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None = None,
        timeout_s: int = 30,
    ) -> dict[str, Any]: ...


class MCPClientAdapter:
    """MCP client using httpx for Streamable HTTP transport.

    Resolves logical server names to URLs via MCPConfigRegistry,
    manages sessions via MCPSessionManager, injects entity IDs
    based on server config, and sanitizes responses.
    """

    def __init__(
        self,
        config: MCPConfigRegistry,
        http_client: httpx.AsyncClient,
        session_manager: MCPSessionManager,
        *,
        composio_config: ComposioConfig | None = None,
        url_manager: MCPUrlManager | None = None,
    ) -> None:
        self._config = config
        self._http = http_client
        self._sessions = session_manager
        self._composio_config = composio_config
        self._url_manager = url_manager
        self._request_ids = itertools.count(1)

    async def invoke(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None = None,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        """Invoke an MCP tool via Streamable HTTP.

        Args:
            server: Logical MCP server name (e.g. ``"composio"``).
            tool: MCP tool name (e.g. ``"GOOGLECALENDAR_CREATE_EVENT"``).
            args: Tool arguments.
            credentials: Entity ID string or dict with entity ID.
                For Composio, this is the user's internal ID that maps
                to a Composio entity. NOT a secret.
            timeout_s: Request timeout in seconds.

        Returns:
            Tool result dict.

        Raises:
            MCPInvocationError: On config, HTTP, timeout, or parse failure.
        """
        try:
            return await self._invoke_with_retry(server, tool, args, credentials, timeout_s)
        except MCPInvocationError:
            raise
        except Exception as exc:
            raise MCPInvocationError(server, tool, str(exc)) from exc

    async def _invoke_with_retry(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None,
        timeout_s: int,
    ) -> dict[str, Any]:
        """Invoke with a single retry on session expiry (401/403)."""
        # Branch to Composio path when url_manager is active
        if self._url_manager is not None:
            return await self._invoke_with_retry_composio(
                server, tool, args, credentials, timeout_s
            )

        try:
            return await self._do_invoke_legacy(server, tool, args, credentials, timeout_s)
        except MCPInvocationError as exc:
            # Check if the error message indicates a session expiry
            if any(f"HTTP {code}" in str(exc) for code in _SESSION_EXPIRED_CODES):
                logger.info(
                    "Session expired for '%s', re-initializing",
                    server,
                )
                self._sessions.invalidate(server)
                return await self._do_invoke_legacy(server, tool, args, credentials, timeout_s)
            raise

    async def _invoke_with_retry_composio(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None,
        timeout_s: int,
    ) -> dict[str, Any]:
        """Composio-mode invoke with retry on 401/403.

        On session expiry, invalidates both the per-user URL cache and
        the session cache, then retries once.
        """
        try:
            return await self._do_invoke_composio(server, tool, args, credentials, timeout_s)
        except MCPInvocationError as exc:
            if any(f"HTTP {code}" in str(exc) for code in _SESSION_EXPIRED_CODES):
                user_id = self._extract_entity_id(credentials) or "__system__"
                logger.info(
                    "Composio session/URL expired for user '%s', re-generating",
                    user_id,
                )
                assert self._url_manager is not None
                self._url_manager.invalidate(user_id)
                # Also invalidate any cached session for this user's URL
                # (the URL itself served as cache_key)
                return await self._do_invoke_composio(server, tool, args, credentials, timeout_s)
            raise

    async def _do_invoke_composio(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None,
        timeout_s: int,
    ) -> dict[str, Any]:
        """Composio-mode invocation: per-user URL, no entity_id injection."""
        assert self._url_manager is not None
        assert self._composio_config is not None

        # 1. Extract user_id for per-user URL
        user_id = self._extract_entity_id(credentials) or "__system__"
        url = await self._url_manager.get_url(user_id)

        # 2. Build headers (Composio API key, standard content types)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-api-key": self._composio_config.api_key,
        }

        # 3. Get/create session (cache_key=url for per-user isolation)
        session = await self._sessions.get_session("composio", url, headers, cache_key=url)
        if session.session_id:
            headers["Mcp-Session-Id"] = session.session_id

        # 4. Build JSON-RPC payload — no entity_id, no credentials in body
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": dict(args),
            },
            "id": next(self._request_ids),
        }

        # 5. POST request
        try:
            response = await self._http.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout_s,
            )
        except httpx.TimeoutException:
            raise MCPInvocationError(server, tool, "timeout")
        except httpx.ConnectError:
            raise MCPInvocationError(server, tool, "connection_refused")

        # 6. Handle HTTP errors
        if response.status_code in _SESSION_EXPIRED_CODES:
            # Invalidate session for this URL so retry gets a new one
            self._sessions.invalidate(url)
            raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

        if response.status_code >= 400:
            raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

        # 7. Parse response (reuse existing parser)
        data = self._parse_response(response, server, tool)

        # 8. Check for JSON-RPC error
        if "error" in data:
            raise MCPInvocationError(server, tool, str(data["error"]))

        result = data.get("result", data)

        # 9. Sanitize (reuse existing sanitizer)
        result = self._sanitize_response(result)

        # 10. Check for "not connected" errors embedded in result content
        self._check_result_not_connected(result, server, tool)

        return result

    async def _do_invoke_legacy(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None,
        timeout_s: int,
    ) -> dict[str, Any]:
        """Core invocation logic."""
        # 1. Resolve server config
        cfg = self._config.get_or_raise(server)

        # 2. Build base headers
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if cfg.api_key:
            headers[cfg.api_key_header] = cfg.api_key
        headers.update(cfg.extra_headers)

        # 3. Get/create MCP session
        session = await self._sessions.get_session(server, cfg.url, headers)
        if session.session_id:
            headers["Mcp-Session-Id"] = session.session_id

        # 4. Inject entity ID
        entity_id = self._extract_entity_id(credentials)
        url = cfg.url
        call_args = dict(args)

        if entity_id:
            url, call_args = self._inject_entity_id(cfg, url, call_args, headers, entity_id)
            logger.debug(
                "Entity ID injected via %s for server '%s'",
                cfg.entity_id_injection,
                server,
            )

        # 5. Build JSON-RPC payload — no credentials in body
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": call_args,
            },
            "id": next(self._request_ids),
        }

        # 6. POST request
        try:
            response = await self._http.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout_s,
            )
        except httpx.TimeoutException:
            raise MCPInvocationError(server, tool, "timeout")
        except httpx.ConnectError:
            raise MCPInvocationError(server, tool, "connection_refused")

        # 7. Handle HTTP errors
        if response.status_code in _SESSION_EXPIRED_CODES:
            raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

        if response.status_code >= 400:
            raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

        # 8. Parse response
        data = self._parse_response(response, server, tool)

        # 9. Check for JSON-RPC error
        if "error" in data:
            raise MCPInvocationError(server, tool, str(data["error"]))

        result = data.get("result", data)

        # 10. Sanitize response (MCP server data is untrusted)
        result = self._sanitize_response(result)

        # 11. Check for "not connected" errors embedded in result content
        self._check_result_not_connected(result, server, tool)

        return result

    @staticmethod
    def _extract_entity_id(
        credentials: dict[str, str] | None,
    ) -> str | None:
        """Extract entity ID from the credentials parameter.

        The credentials parameter carries the user's entity ID, not a secret.
        Accepts either a string directly or a dict with common key names.
        """
        if credentials is None:
            return None
        if isinstance(credentials, str):
            return credentials
        # Try common keys
        for key in ("entity_id", "user_id", "token"):
            if key in credentials:
                return credentials[key]
        # Return first value if dict has entries
        if credentials:
            return next(iter(credentials.values()))
        return None

    @staticmethod
    def _inject_entity_id(
        cfg: MCPServerConfig,
        url: str,
        args: dict[str, Any],
        headers: dict[str, str],
        entity_id: str,
    ) -> tuple[str, dict[str, Any]]:
        """Inject entity ID based on the server's configured strategy."""
        if cfg.entity_id_injection == "argument":
            args = {**args, cfg.entity_id_field: entity_id}
        elif cfg.entity_id_injection == "header":
            headers[cfg.entity_id_field] = entity_id
        elif cfg.entity_id_injection == "query":
            parsed = urlparse(url)
            separator = "&" if parsed.query else ""
            new_query = f"{parsed.query}{separator}{urlencode({cfg.entity_id_field: entity_id})}"
            url = urlunparse(parsed._replace(query=new_query))
        return url, args

    @staticmethod
    def _parse_response(
        response: httpx.Response,
        server: str,
        tool: str,
    ) -> dict[str, Any]:
        """Parse JSON or SSE response from MCP server."""
        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            return _parse_sse_response(response.text, server, tool)

        try:
            return response.json()
        except Exception as exc:
            raise MCPInvocationError(server, tool, f"Failed to parse JSON response: {exc}") from exc

    @staticmethod
    def _check_result_not_connected(result: Any, server: str, tool: str) -> None:
        """Raise MCPInvocationError if the tool result content indicates
        the user's integration is not connected.

        Composio sometimes returns auth errors as successful HTTP 200
        JSON-RPC responses with the error text embedded in the result
        content.  Catching them here ensures the execute service's
        ``_check_integration_not_connected`` short-circuit fires.
        """
        text = ""
        if isinstance(result, dict):
            # MCP content array: {"content": [{"type": "text", "text": "..."}]}
            content = result.get("content")
            if isinstance(content, list):
                text = " ".join(
                    item.get("text", "") for item in content if isinstance(item, dict)
                )
            elif isinstance(content, str):
                text = content
            # Also check top-level error/message fields
            for key in ("error", "message", "detail"):
                val = result.get(key)
                if isinstance(val, str):
                    text += " " + val
        elif isinstance(result, str):
            text = result

        if text and _RESULT_NOT_CONNECTED_RE.search(text):
            raise MCPInvocationError(server, tool, text.strip()[:500])

    @staticmethod
    def _sanitize_response(result: Any) -> Any:
        """Wrap MCP server response in a data boundary if it contains
        strings resembling prompt injection."""
        if isinstance(result, dict):
            sanitized = {}
            for key, value in result.items():
                if isinstance(value, str) and _INJECTION_PATTERNS.search(value):
                    sanitized[key] = f"[TOOL_OUTPUT_START]{value}[TOOL_OUTPUT_END]"
                else:
                    sanitized[key] = value
            return sanitized
        return result


def _parse_sse_response(
    text: str,
    server: str,
    tool: str,
) -> dict[str, Any]:
    """Parse a Server-Sent Events response.

    Handles multi-line ``data:`` fields, ``event:`` types, and ``id:`` fields.
    Returns the parsed JSON from the last complete data message.
    """
    last_data: list[str] = []
    current_data: list[str] = []

    for line in text.split("\n"):
        if line.startswith("data:"):
            current_data.append(line[5:].strip())
        elif line.startswith("event:"):
            # Event type — we only care about data
            pass
        elif line.startswith("id:"):
            pass
        elif line.strip() == "" and current_data:
            last_data = current_data
            current_data = []

    # Handle case where there's no trailing empty line
    if current_data:
        last_data = current_data

    if not last_data:
        raise MCPInvocationError(server, tool, "Empty SSE response")

    joined = "\n".join(last_data)
    try:
        return json.loads(joined)
    except json.JSONDecodeError as exc:
        raise MCPInvocationError(server, tool, f"Failed to parse SSE data as JSON: {exc}") from exc
