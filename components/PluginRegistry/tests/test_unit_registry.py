"""
Unit tests for RegistryService -- tool CRUD and versioning.

Reference: LLD.md Section 8.5 item 1
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from components.PluginRegistry.domain.models import (
    CreateToolRequest,
    InvalidToolIdFormatError,
    OperationModel,
    SchemaValidationError,
    ToolAlreadyExistsError,
    ToolModel,
    ToolNotFoundError,
    UpdateToolRequest,
)

# ------------------------------------------------------------------
# GET tool
# ------------------------------------------------------------------


class TestGetTool:
    """Tests for service.get_tool()."""

    async def test_get_tool_happy_path(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        tool = await registry_service.get_tool("google.calendar")
        assert tool.tool_id == "google.calendar"
        assert "create_event" in tool.operations
        mock_db_adapter.get_tool.assert_awaited_once_with("google.calendar")

    async def test_get_tool_not_found(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(return_value=None)
        with pytest.raises(ToolNotFoundError):
            await registry_service.get_tool("nonexistent.tool")

    async def test_get_tool_inactive_raises(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        sample_tool_model.active = False
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        with pytest.raises(ToolNotFoundError):
            await registry_service.get_tool("google.calendar")

    async def test_get_tool_invalid_format(
        self,
        registry_service,
    ):
        with pytest.raises(InvalidToolIdFormatError):
            await registry_service.get_tool("INVALID")


# ------------------------------------------------------------------
# List catalog
# ------------------------------------------------------------------


class TestListCatalog:
    """Tests for service.list_catalog()."""

    async def test_list_catalog_with_tools(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.list_active_tools = AsyncMock(
            return_value=([sample_tool_model], 1),
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=5,
        )
        catalog = await registry_service.list_catalog()
        assert catalog.total == 1
        assert catalog.registry_version == 5
        assert catalog.tools[0].tool_id == "google.calendar"
        # Verify previewable field is present
        op = catalog.tools[0].operations["list_free_busy"]
        assert op.previewable is True

    async def test_list_catalog_empty_registry(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.list_active_tools = AsyncMock(
            return_value=([], 0),
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=0,
        )
        catalog = await registry_service.list_catalog()
        assert catalog.tools == []
        assert catalog.total == 0

    async def test_list_catalog_excludes_inactive_tools(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        # The DB adapter already filters, but service should only
        # return what adapter gives it (active only).
        active_tool = sample_tool_model.model_copy()
        active_tool.active = True
        mock_db_adapter.list_active_tools = AsyncMock(
            return_value=([active_tool], 1),
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=2,
        )
        catalog = await registry_service.list_catalog()
        assert all(t.active for t in catalog.tools)

    async def test_list_catalog_pagination(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.list_active_tools = AsyncMock(
            return_value=([sample_tool_model], 50),
        )
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=1,
        )
        catalog = await registry_service.list_catalog(
            page=2,
            page_size=10,
        )
        mock_db_adapter.list_active_tools.assert_awaited_once_with(
            2,
            10,
        )
        assert catalog.page == 2
        assert catalog.page_size == 10


# ------------------------------------------------------------------
# Get version
# ------------------------------------------------------------------


class TestGetVersion:
    """Tests for service.get_version()."""

    async def test_get_version_empty_registry(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=0,
        )
        assert await registry_service.get_version() == 0

    async def test_get_version_after_writes(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_current_version = AsyncMock(
            return_value=5,
        )
        assert await registry_service.get_version() == 5


# ------------------------------------------------------------------
# Create tool
# ------------------------------------------------------------------


class TestCreateTool:
    """Tests for service.create_tool()."""

    async def test_create_tool_happy_path(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_def,
    ):
        now = datetime.now(UTC)
        created_model = ToolModel(
            tool_id=sample_tool_def.tool_id,
            display_name=sample_tool_def.display_name,
            credential_template=sample_tool_def.credential_template,
            n8n_credential_type=(sample_tool_def.n8n_credential_type),
            active=True,
            operations=sample_tool_def.operations,
            created_at=now,
            updated_at=now,
        )
        mock_db_adapter.tool_exists = AsyncMock(return_value=False)
        mock_db_adapter.create_tool = AsyncMock(
            return_value=(created_model, 1),
        )
        resp = await registry_service.create_tool(sample_tool_def)
        assert resp.tool_id == "google.calendar"
        assert resp.registry_version == 1

    async def test_create_tool_duplicate_id(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_def,
    ):
        mock_db_adapter.tool_exists = AsyncMock(return_value=True)
        with pytest.raises(ToolAlreadyExistsError):
            await registry_service.create_tool(sample_tool_def)

    async def test_create_tool_invalid_id_format(
        self,
        registry_service,
    ):
        req = CreateToolRequest(
            tool_id="google.calendar",
            display_name="G",
            credential_template="x",
            n8n_credential_type="x",
            operations={"op1": OperationModel(operation_id="op1", n8n_node="N")},
        )
        # Patch tool_id to invalid after construction
        req.tool_id = "INVALID"  # type: ignore[assignment]
        with pytest.raises(InvalidToolIdFormatError):
            await registry_service.create_tool(req)

    async def test_create_tool_compensation_referential_integrity(
        self,
        registry_service,
        mock_db_adapter,
    ):
        req = CreateToolRequest(
            tool_id="test.tool",
            display_name="Test",
            credential_template="x",
            n8n_credential_type="x",
            operations={
                "create": OperationModel(
                    operation_id="create",
                    n8n_node="N",
                    compensation="nonexistent_op",
                ),
            },
        )
        mock_db_adapter.tool_exists = AsyncMock(return_value=False)
        with pytest.raises(SchemaValidationError):
            await registry_service.create_tool(req)

    async def test_create_tool_increments_version(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_def,
    ):
        now = datetime.now(UTC)
        model = ToolModel(
            tool_id=sample_tool_def.tool_id,
            display_name=sample_tool_def.display_name,
            credential_template=sample_tool_def.credential_template,
            n8n_credential_type=(sample_tool_def.n8n_credential_type),
            active=True,
            operations=sample_tool_def.operations,
            created_at=now,
            updated_at=now,
        )
        mock_db_adapter.tool_exists = AsyncMock(return_value=False)
        mock_db_adapter.create_tool = AsyncMock(
            return_value=(model, 6),
        )
        resp = await registry_service.create_tool(sample_tool_def)
        assert resp.registry_version == 6


# ------------------------------------------------------------------
# Update tool
# ------------------------------------------------------------------


class TestUpdateTool:
    """Tests for service.update_tool()."""

    async def test_update_tool_happy_path(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        updated = sample_tool_model.model_copy(
            update={"display_name": "Updated Calendar"},
        )
        mock_db_adapter.update_tool = AsyncMock(
            return_value=(updated, 7),
        )
        updates = UpdateToolRequest(display_name="Updated Calendar")
        resp = await registry_service.update_tool(
            "google.calendar",
            updates,
        )
        assert resp.tool_id == "google.calendar"
        assert resp.registry_version == 7

    async def test_update_tool_not_found(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(return_value=None)
        with pytest.raises(ToolNotFoundError):
            await registry_service.update_tool(
                "nonexistent.tool",
                UpdateToolRequest(),
            )

    async def test_update_tool_increments_version(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        mock_db_adapter.update_tool = AsyncMock(
            return_value=(sample_tool_model, 8),
        )
        resp = await registry_service.update_tool(
            "google.calendar",
            UpdateToolRequest(),
        )
        assert resp.registry_version == 8


# ------------------------------------------------------------------
# Deactivate tool
# ------------------------------------------------------------------


class TestDeactivateTool:
    """Tests for service.deactivate_tool()."""

    async def test_deactivate_tool_happy_path(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        deactivated = sample_tool_model.model_copy(
            update={"active": False},
        )
        mock_db_adapter.deactivate_tool = AsyncMock(
            return_value=(deactivated, 9),
        )
        resp = await registry_service.deactivate_tool(
            "google.calendar",
        )
        assert resp.active is False
        assert resp.registry_version == 9

    async def test_deactivate_tool_not_found(
        self,
        registry_service,
        mock_db_adapter,
    ):
        mock_db_adapter.get_tool = AsyncMock(return_value=None)
        with pytest.raises(ToolNotFoundError):
            await registry_service.deactivate_tool(
                "nonexistent.tool",
            )

    async def test_deactivate_tool_increments_version(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        mock_db_adapter.deactivate_tool = AsyncMock(
            return_value=(sample_tool_model, 10),
        )
        resp = await registry_service.deactivate_tool(
            "google.calendar",
        )
        assert resp.registry_version == 10

    async def test_deactivate_tool_idempotent(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        """Deactivating already-inactive tool still succeeds."""
        already_inactive = sample_tool_model.model_copy(
            update={"active": False},
        )
        # get_tool returns the tool (even if inactive) for existence
        mock_db_adapter.get_tool = AsyncMock(
            return_value=already_inactive,
        )
        mock_db_adapter.deactivate_tool = AsyncMock(
            return_value=(already_inactive, 11),
        )
        resp = await registry_service.deactivate_tool(
            "google.calendar",
        )
        assert resp.active is False


# ------------------------------------------------------------------
# Compensation field
# ------------------------------------------------------------------


class TestCompensation:
    """Test compensation field presence (US-1 scenario 4)."""

    async def test_compensation_field_present(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        tool = await registry_service.get_tool("google.calendar")
        op = tool.operations["create_event"]
        assert op.compensation == "delete_event"

    async def test_compensation_field_null(
        self,
        registry_service,
        mock_db_adapter,
        sample_tool_model,
    ):
        mock_db_adapter.get_tool = AsyncMock(
            return_value=sample_tool_model,
        )
        tool = await registry_service.get_tool("google.calendar")
        op = tool.operations["list_free_busy"]
        assert op.compensation is None
