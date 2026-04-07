"""
Previewability Checker Adapter

Queries ToolCatalog to determine if a tool is previewable.
In the MCP model, any tool present in the catalog is previewable
(MCP dry-run mode handles read-only semantics).

Reference: LLD.md Section 6.3
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PreviewabilityChecker:
    """Check ToolCatalog for tool previewability."""

    _WRITE_VERBS = frozenset({
        "CREATE", "UPDATE", "DELETE", "REMOVE", "SEND", "POST",
        "PATCH", "PUT", "INSERT", "ADD", "SET", "MODIFY", "MOVE",
        "CANCEL", "REVOKE", "APPROVE", "REJECT", "ARCHIVE",
    })

    def __init__(self, tool_catalog: Any) -> None:
        self._catalog = tool_catalog

    @staticmethod
    def is_write_action(tool_id: str, call: str) -> bool:
        """Check if a tool/operation performs a write/mutation.

        Checks both tool_id and call for write verbs (Composio & legacy conventions).
        """
        tokens = tool_id.upper().replace(".", "_").split("_") + call.upper().replace(".", "_").split("_")
        return any(t in PreviewabilityChecker._WRITE_VERBS for t in tokens)

    async def is_previewable(
        self,
        tool_id: str,
        operation_id: str,  # noqa: ARG002 — kept for interface compat
    ) -> bool:
        """Check if a tool is previewable.

        In the MCP model, any tool present in the catalog is previewable.
        Returns False if tool not found (fail-safe).
        """
        try:
            tool = self._catalog.get_tool(tool_id)
            return tool is not None
        except Exception as exc:
            logger.warning(
                "previewability_check_failed",
                extra={
                    "tool_id": tool_id,
                    "reason": str(exc),
                },
            )
            return False
