"""
Shared test fixtures for PluginRegistry component tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.PluginRegistry.domain.models import (
    CreateToolRequest,
    OperationModel,
    ResolveCredentialRequest,
    ToolModel,
    ValidatePlanToolsRequest,
)
from components.PluginRegistry.service.registry_service import (
    RegistryService,
)


# ------------------------------------------------------------------
# Sample data builders
# ------------------------------------------------------------------

def _gcal_operations() -> dict[str, OperationModel]:
    return {
        "list_free_busy": OperationModel(
            operation_id="list_free_busy",
            n8n_node="Google Calendar",
            previewable=True,
            idempotent=True,
            scopes=["calendar.read"],
            compensation=None,
        ),
        "create_event": OperationModel(
            operation_id="create_event",
            n8n_node="Google Calendar",
            previewable=False,
            idempotent=True,
            scopes=["calendar.write"],
            compensation="delete_event",
        ),
        "delete_event": OperationModel(
            operation_id="delete_event",
            n8n_node="Google Calendar",
            previewable=False,
            idempotent=True,
            scopes=["calendar.write"],
            compensation=None,
        ),
    }


@pytest.fixture()
def sample_tool_def() -> CreateToolRequest:
    """Complete CreateToolRequest for google.calendar."""
    return CreateToolRequest(
        tool_id="google.calendar",
        display_name="Google Calendar",
        credential_template=(
            "gcal_user_{{user_id}}_{{account_name}}"
        ),
        n8n_credential_type="googleCalendarOAuth2Api",
        operations=_gcal_operations(),
    )


@pytest.fixture()
def sample_slack_tool_def() -> CreateToolRequest:
    """CreateToolRequest for slack.messaging."""
    return CreateToolRequest(
        tool_id="slack.messaging",
        display_name="Slack Messaging",
        credential_template=(
            "slack_user_{{user_id}}_{{account_name}}"
        ),
        n8n_credential_type="slackOAuth2Api",
        operations={
            "send_message": OperationModel(
                operation_id="send_message",
                n8n_node="Slack",
                previewable=True,
                idempotent=False,
                scopes=["chat:write"],
                compensation=None,
            ),
        },
    )


@pytest.fixture()
def sample_tool_model() -> ToolModel:
    """A fully populated ToolModel for assertion comparisons."""
    now = datetime.now(timezone.utc)
    return ToolModel(
        tool_id="google.calendar",
        display_name="Google Calendar",
        credential_template=(
            "gcal_user_{{user_id}}_{{account_name}}"
        ),
        n8n_credential_type="googleCalendarOAuth2Api",
        active=True,
        operations=_gcal_operations(),
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
def sample_validation_request() -> ValidatePlanToolsRequest:
    """Typical pre-execution validation request."""
    return ValidatePlanToolsRequest(
        plan_registry_version=5,
        referenced_tool_ids=[
            "google.calendar",
            "slack.messaging",
        ],
    )


@pytest.fixture()
def sample_resolve_request() -> ResolveCredentialRequest:
    """Typical credential resolution request."""
    return ResolveCredentialRequest(
        tool_id="google.calendar",
        variables={
            "user_id": "u-123",
            "account_name": "work",
        },
    )


# ------------------------------------------------------------------
# Mock adapter and service
# ------------------------------------------------------------------

@pytest.fixture()
def mock_db_adapter() -> MagicMock:
    """Mocked RegistryDatabaseAdapter with configurable returns."""
    adapter = MagicMock()
    adapter.get_tool = AsyncMock(return_value=None)
    adapter.list_active_tools = AsyncMock(return_value=([], 0))
    adapter.get_tools_by_ids = AsyncMock(return_value={})
    adapter.create_tool = AsyncMock(return_value=(None, 1))
    adapter.update_tool = AsyncMock(return_value=(None, 2))
    adapter.deactivate_tool = AsyncMock(return_value=(None, 3))
    adapter.tool_exists = AsyncMock(return_value=False)
    adapter.get_current_version = AsyncMock(return_value=0)
    adapter.increment_version = AsyncMock(return_value=1)
    adapter.health_check = AsyncMock(return_value=True)
    return adapter


@pytest.fixture()
def registry_service(mock_db_adapter: MagicMock) -> RegistryService:
    """RegistryService with a mocked DB adapter."""
    return RegistryService(db_adapter=mock_db_adapter)
