"""
MCP Client Adapter

Protocol and httpx-based implementation for MCP tool invocations.

Reference: LLD.md Section 6.1
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from ..domain.models import MCPInvocationError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {503, 504}


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
    """MCP client using httpx for SSE/HTTP transport.

    Resolves MCP server URL and tool name from PluginRegistry.
    """

    def __init__(self, registry_service: Any) -> None:
        self._registry = registry_service

    async def invoke(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        credentials: dict[str, str] | None = None,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        """Invoke an MCP tool via HTTP.

        Args:
            server: MCP server URL or identifier.
            tool: MCP tool name.
            args: Tool arguments.
            credentials: Optional credentials (never logged).
            timeout_s: Request timeout in seconds.

        Returns:
            Tool result dict.

        Raises:
            MCPInvocationError: On HTTP error, timeout, or parse failure.
        """
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": args,
            },
            "id": "1",
        }
        if credentials:
            payload["params"]["_credentials"] = credentials

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    server,
                    json=payload,
                    timeout=timeout_s,
                )

            if response.status_code in _RETRYABLE_STATUS_CODES:
                raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

            if response.status_code >= 400:
                raise MCPInvocationError(server, tool, f"HTTP {response.status_code}")

            data = response.json()
            if "error" in data:
                raise MCPInvocationError(server, tool, str(data["error"]))

            return data.get("result", data)

        except MCPInvocationError:
            raise
        except httpx.TimeoutException:
            raise MCPInvocationError(server, tool, "timeout")
        except httpx.ConnectError:
            raise MCPInvocationError(server, tool, "connection_reset")
        except Exception as exc:
            raise MCPInvocationError(server, tool, str(exc))
