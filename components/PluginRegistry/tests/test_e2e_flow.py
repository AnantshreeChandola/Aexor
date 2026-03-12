"""
End-to-end flow tests for PluginRegistry.

These tests exercise complete workflows through the service layer
with mocked DB adapter, verifying cross-operation consistency.

Reference: LLD.md Section 8.5
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.PluginRegistry.domain.models import (
    CreateToolRequest,
    OperationModel,
    ToolModel,
    UpdateToolRequest,
    ValidationIssue,
    ValidationResult,
)
from components.PluginRegistry.service.registry_service import (
    RegistryService,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.get_tool = AsyncMock(return_value=None)
    adapter.list_active_tools = AsyncMock(return_value=([], 0))
    adapter.get_tools_by_ids = AsyncMock(return_value={})
    adapter.create_tool = AsyncMock()
    adapter.update_tool = AsyncMock()
    adapter.deactivate_tool = AsyncMock()
    adapter.tool_exists = AsyncMock(return_value=False)
    adapter.get_current_version = AsyncMock(return_value=0)
    return adapter


def _tool(
    tool_id: str = "google.calendar",
    active: bool = True,
    version: int = 1,
) -> ToolModel:
    now = _now()
    return ToolModel(
        tool_id=tool_id,
        display_name="Google Calendar",
        credential_template="gcal_user_{{user_id}}_{{account_name}}",
        n8n_credential_type="googleCalendarOAuth2Api",
        active=active,
        operations={
            "create_event": OperationModel(
                operation_id="create_event",
                n8n_node="Google Calendar",
                scopes=["calendar.write"],
                compensation="delete_event",
            ),
            "delete_event": OperationModel(
                operation_id="delete_event",
                n8n_node="Google Calendar",
                scopes=["calendar.write"],
            ),
        },
        created_at=now,
        updated_at=now,
    )


class TestPlannerFlow:
    """Planner flow: catalog -> resolve -> validate -> deactivate."""

    async def test_catalog_to_plan_validation(self):
        adapter = _make_adapter()
        service = RegistryService(db_adapter=adapter)

        # Step 1: Create tool
        tool = _tool()
        adapter.tool_exists.return_value = False
        adapter.create_tool.return_value = (tool, 1)
        adapter.get_current_version.return_value = 1

        req = CreateToolRequest(
            tool_id="google.calendar",
            display_name="Google Calendar",
            credential_template=(
                "gcal_{{user_id}}_{{account_name}}"
            ),
            n8n_credential_type="googleCalendarOAuth2Api",
            operations={
                "create_event": OperationModel(
                    operation_id="create_event",
                    n8n_node="Google Calendar",
                    scopes=["calendar.write"],
                    compensation="delete_event",
                ),
                "delete_event": OperationModel(
                    operation_id="delete_event",
                    n8n_node="Google Calendar",
                    scopes=["calendar.write"],
                ),
            },
        )
        resp = await service.create_tool(req)
        assert resp.registry_version == 1

        # Step 2: Retrieve catalog
        adapter.list_active_tools.return_value = ([tool], 1)
        catalog = await service.list_catalog()
        assert catalog.total == 1
        assert catalog.registry_version == 1

        # Step 3: Resolve credential template
        adapter.get_tool.return_value = tool
        resolved = await service.resolve_credential_template(
            "google.calendar",
            {"user_id": "u-123", "account_name": "work"},
        )
        assert resolved.credential_id == "gcal_user_u-123_work"

        # Step 4: Validate plan tools (all active)
        adapter.get_tools_by_ids.return_value = {
            "google.calendar": tool,
        }
        result = await service.validate_plan_tools(
            plan_registry_version=1,
            referenced_tool_ids=["google.calendar"],
        )
        assert result.valid is True

        # Step 5: Deactivate tool
        deactivated = tool.model_copy(update={"active": False})
        adapter.get_tool.return_value = tool
        adapter.deactivate_tool.return_value = (deactivated, 2)
        adapter.get_current_version.return_value = 2
        deact = await service.deactivate_tool("google.calendar")
        assert deact.active is False

        # Step 6: Validate same plan (now deactivated)
        adapter.get_tools_by_ids.return_value = {
            "google.calendar": deactivated,
        }
        result2 = await service.validate_plan_tools(
            plan_registry_version=1,
            referenced_tool_ids=["google.calendar"],
        )
        assert result2.valid is False
        assert result2.issues[0].reason == "TOOL_DEACTIVATED"


class TestAdminFlow:
    """Admin flow: create -> update -> deactivate."""

    async def test_create_update_deactivate(self):
        adapter = _make_adapter()
        service = RegistryService(db_adapter=adapter)

        now = _now()
        tool_v1 = _tool()

        # Create
        adapter.tool_exists.return_value = False
        adapter.create_tool.return_value = (tool_v1, 1)
        adapter.get_current_version.return_value = 1
        req = CreateToolRequest(
            tool_id="google.calendar",
            display_name="Google Calendar",
            credential_template=(
                "gcal_{{user_id}}_{{account_name}}"
            ),
            n8n_credential_type="googleCalendarOAuth2Api",
            operations={
                "create_event": OperationModel(
                    operation_id="create_event",
                    n8n_node="Google Calendar",
                    scopes=["calendar.write"],
                    compensation="delete_event",
                ),
                "delete_event": OperationModel(
                    operation_id="delete_event",
                    n8n_node="Google Calendar",
                    scopes=["calendar.write"],
                ),
            },
        )
        r1 = await service.create_tool(req)
        assert r1.registry_version == 1

        # Update (add operation) -- version 2
        adapter.get_tool.return_value = tool_v1
        updated_tool = tool_v1.model_copy(
            update={"display_name": "Updated"},
        )
        adapter.update_tool.return_value = (updated_tool, 2)
        r2 = await service.update_tool(
            "google.calendar",
            UpdateToolRequest(display_name="Updated"),
        )
        assert r2.registry_version == 2

        # Deactivate -- version 3
        deactivated = updated_tool.model_copy(
            update={"active": False},
        )
        adapter.deactivate_tool.return_value = (deactivated, 3)
        r3 = await service.deactivate_tool("google.calendar")
        assert r3.registry_version == 3
        assert r3.active is False


class TestScopeVerificationFlow:
    """Scope verification: create tool, verify scopes."""

    async def test_scope_verification(self):
        adapter = _make_adapter()
        service = RegistryService(db_adapter=adapter)

        tool = _tool()
        adapter.get_tool.return_value = tool

        # Scope present -> supported
        result = await service.verify_scopes(
            "google.calendar",
            "create_event",
            ["calendar.write"],
        )
        assert result.supported is True

        # Scope not present -> not supported
        result2 = await service.verify_scopes(
            "google.calendar",
            "create_event",
            ["calendar.admin"],
        )
        assert result2.supported is False
        assert "calendar.admin" in result2.missing_scopes
