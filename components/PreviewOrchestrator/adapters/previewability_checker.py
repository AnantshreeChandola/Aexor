"""
Previewability Checker Adapter

Queries PluginRegistry to determine if a tool operation is previewable.
Returns False on any lookup failure (fail-safe).

Reference: LLD.md Section 6.3
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PreviewabilityChecker:
    """Check PluginRegistry for operation previewability."""

    def __init__(self, registry_service: Any) -> None:
        self._registry = registry_service

    async def is_previewable(
        self,
        tool_id: str,
        operation_id: str,
    ) -> bool:
        """Check if a tool operation is marked previewable.

        Returns False if tool/operation not found (fail-safe).
        """
        try:
            tool = await self._registry.get_tool(tool_id)
            op = tool.operations.get(operation_id)
            if op is None:
                logger.warning(
                    "previewability_check_failed",
                    extra={
                        "tool_id": tool_id,
                        "operation_id": operation_id,
                        "reason": "operation_not_found",
                    },
                )
                return False
            return bool(op.previewable)
        except Exception as exc:
            logger.warning(
                "previewability_check_failed",
                extra={
                    "tool_id": tool_id,
                    "operation_id": operation_id,
                    "reason": str(exc),
                },
            )
            return False
