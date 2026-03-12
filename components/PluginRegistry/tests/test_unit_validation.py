"""
Unit tests for pre-execution validation and scope verification.

Reference: LLD.md Section 8.5 item 3
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from components.PluginRegistry.domain.models import (
    OperationModel,
    ToolModel,
    ToolNotFoundError,
)
from components.PluginRegistry.service.registry_service import (
    RegistryService,
)


def _tool(
    tool_id: str,
    active: bool = True,
    scopes: list[str] | None = None,
) -> ToolModel:
    now = datetime.now(timezone.utc)
    ops = {}
    if scopes is not None:
        ops["main_op"] = OperationModel(
            operation_id="main_op",
            n8n_node="Node",
            scopes=scopes,
        )
    return ToolModel(
        tool_id=tool_id,
        display_name=tool_id,
        credential_template="x",
        n8n_credential_type="x",
        active=active,
        operations=ops,
        created_at=now,
        updated_at=now,
    )


# ------------------------------------------------------------------
# Pre-execution validation
# ------------------------------------------------------------------

class TestValidatePlanTools:
    """Tests for service.validate_plan_tools()."""

    async def test_validate_all_tools_active(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_tools_by_ids = AsyncMock(
            return_value={
                "google.calendar": _tool(
                    "google.calendar", active=True
                ),
                "slack.messaging": _tool(
                    "slack.messaging", active=True
                ),
            },
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=5,
        )
        result = await registry_service.validate_plan_tools(
            plan_registry_version=3,
            referenced_tool_ids=[
                "google.calendar",
                "slack.messaging",
            ],
        )
        assert result.valid is True
        assert result.current_version == 5
        assert result.issues == []

    async def test_validate_deactivated_tool(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_tools_by_ids = AsyncMock(
            return_value={
                "google.calendar": _tool(
                    "google.calendar", active=True
                ),
                "slack.messaging": _tool(
                    "slack.messaging", active=False
                ),
            },
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=7,
        )
        result = await registry_service.validate_plan_tools(
            plan_registry_version=5,
            referenced_tool_ids=[
                "google.calendar",
                "slack.messaging",
            ],
        )
        assert result.valid is False
        assert result.current_version == 7
        assert len(result.issues) == 1
        assert result.issues[0].tool_id == "slack.messaging"
        assert result.issues[0].reason == "TOOL_DEACTIVATED"

    async def test_validate_missing_tool(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_tools_by_ids = AsyncMock(
            return_value={
                "google.calendar": _tool(
                    "google.calendar", active=True
                ),
            },
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=7,
        )
        result = await registry_service.validate_plan_tools(
            plan_registry_version=5,
            referenced_tool_ids=[
                "google.calendar",
                "jira.issues",
            ],
        )
        assert result.valid is False
        assert any(
            i.reason == "TOOL_NOT_FOUND" for i in result.issues
        )

    async def test_validate_mixed_results(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_tools_by_ids = AsyncMock(
            return_value={
                "google.calendar": _tool(
                    "google.calendar", active=True
                ),
                "slack.messaging": _tool(
                    "slack.messaging", active=False
                ),
            },
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=10,
        )
        result = await registry_service.validate_plan_tools(
            plan_registry_version=5,
            referenced_tool_ids=[
                "google.calendar",
                "slack.messaging",
                "jira.issues",
            ],
        )
        assert result.valid is False
        assert len(result.issues) == 2
        reasons = {i.reason for i in result.issues}
        assert "TOOL_DEACTIVATED" in reasons
        assert "TOOL_NOT_FOUND" in reasons

    async def test_validate_empty_tool_list(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=5,
        )
        result = await registry_service.validate_plan_tools(
            plan_registry_version=5,
            referenced_tool_ids=[],
        )
        assert result.valid is True
        assert result.issues == []

    async def test_validate_returns_current_version(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_tools_by_ids = AsyncMock(
            return_value={},
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=42,
        )
        result = await registry_service.validate_plan_tools(
            plan_registry_version=1,
            referenced_tool_ids=["x.y"],
        )
        assert result.current_version == 42


# ------------------------------------------------------------------
# Scope verification
# ------------------------------------------------------------------

class TestVerifyScopes:
    """Tests for service.verify_scopes()."""

    async def test_verify_scopes_all_present(
        self, registry_service, mock_db_adapter,
    ):
        tool = _tool(
            "google.calendar",
            scopes=["calendar.read", "calendar.write"],
        )
        mock_db_adapter.get_tool = AsyncMock(return_value=tool)
        result = await registry_service.verify_scopes(
            "google.calendar",
            "main_op",
            ["calendar.read"],
        )
        assert result.supported is True
        assert result.missing_scopes == []

    async def test_verify_scopes_missing_scope(
        self, registry_service, mock_db_adapter,
    ):
        tool = _tool(
            "google.calendar",
            scopes=["calendar.read"],
        )
        mock_db_adapter.get_tool = AsyncMock(return_value=tool)
        result = await registry_service.verify_scopes(
            "google.calendar",
            "main_op",
            ["calendar.write"],
        )
        assert result.supported is False
        assert "calendar.write" in result.missing_scopes

    async def test_verify_scopes_tool_not_found(
        self, registry_service, mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(return_value=None)
        with pytest.raises(ToolNotFoundError):
            await registry_service.verify_scopes(
                "nonexistent.tool", "op", ["scope"],
            )
