"""
API handler tests for PluginRegistry routes.

Tests the thin API layer in isolation by mocking RegistryService.
Reference: LLD.md Section 8.5
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from components.PluginRegistry.api.routes import router
from components.PluginRegistry.domain.models import (
    CatalogResponse,
    CreateToolResponse,
    DeactivateToolResponse,
    InvalidToolIdFormatError,
    OperationModel,
    ResolvedCredential,
    SchemaValidationError,
    TemplateResolutionError,
    ToolAlreadyExistsError,
    ToolModel,
    ToolNotFoundError,
    UpdateToolResponse,
    ValidationResult,
)

# ------------------------------------------------------------------
# Test app setup
# ------------------------------------------------------------------


def _create_test_app(mock_service) -> FastAPI:
    """Create a minimal FastAPI app for testing."""
    app = FastAPI()
    app.state.registry_service = mock_service
    app.include_router(router)
    return app


@pytest.fixture()
def mock_service():
    svc = AsyncMock()
    svc.get_tool = AsyncMock()
    svc.list_catalog = AsyncMock()
    svc.get_version = AsyncMock(return_value=0)
    svc.validate_plan_tools = AsyncMock()
    svc.resolve_credential_template = AsyncMock()
    svc.create_tool = AsyncMock()
    svc.update_tool = AsyncMock()
    svc.deactivate_tool = AsyncMock()
    return svc


@pytest.fixture()
def client(mock_service):
    """TestClient with auth bypassed."""
    app = _create_test_app(mock_service)

    # Bypass auth by overriding the dependency
    from shared.api.auth import get_auth_context
    from shared.dependencies import get_registry_service

    app.dependency_overrides[get_auth_context] = lambda: {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "context_tier": 1,
        "email": "test@example.com",
    }
    app.dependency_overrides[get_registry_service] = lambda: mock_service
    return TestClient(app)


def _now() -> datetime:
    return datetime.now(UTC)


def _sample_tool() -> ToolModel:
    return ToolModel(
        tool_id="google.calendar",
        display_name="Google Calendar",
        credential_template="gcal_{{user_id}}",
        n8n_credential_type="googleCalendarOAuth2Api",
        active=True,
        operations={
            "create_event": OperationModel(
                operation_id="create_event",
                n8n_node="Google Calendar",
                scopes=["calendar.write"],
            ),
        },
        created_at=_now(),
        updated_at=_now(),
    )


# ------------------------------------------------------------------
# GET /registry/tools/{tool_id}
# ------------------------------------------------------------------


class TestGetTool:
    def test_returns_200_with_data(self, client, mock_service):
        mock_service.get_tool.return_value = _sample_tool()
        resp = client.get("/registry/tools/google.calendar")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["tool_id"] == "google.calendar"

    def test_returns_404_when_not_found(self, client, mock_service):
        mock_service.get_tool.side_effect = ToolNotFoundError("google.calendar")
        resp = client.get("/registry/tools/google.calendar")
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == "error"
        assert body["error_code"] == "TOOL_NOT_FOUND"

    def test_invalid_id_format_returns_400(
        self,
        client,
        mock_service,
    ):
        mock_service.get_tool.side_effect = InvalidToolIdFormatError("INVALID")
        resp = client.get("/registry/tools/INVALID")
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "INVALID_TOOL_ID_FORMAT"


# ------------------------------------------------------------------
# GET /registry/catalog
# ------------------------------------------------------------------


class TestListCatalog:
    def test_returns_200_with_tools(self, client, mock_service):
        mock_service.list_catalog.return_value = CatalogResponse(
            tools=[_sample_tool()],
            registry_version=5,
            total=1,
            page=1,
            page_size=50,
        )
        resp = client.get("/registry/catalog")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]["tools"]) == 1

    def test_returns_empty_list(self, client, mock_service):
        mock_service.list_catalog.return_value = CatalogResponse(
            tools=[],
            registry_version=0,
            total=0,
            page=1,
            page_size=50,
        )
        resp = client.get("/registry/catalog")
        assert resp.status_code == 200
        assert resp.json()["data"]["tools"] == []

    def test_pagination_params(self, client, mock_service):
        mock_service.list_catalog.return_value = CatalogResponse(
            tools=[],
            registry_version=0,
            total=0,
            page=2,
            page_size=10,
        )
        resp = client.get("/registry/catalog?page=2&page_size=10")
        assert resp.status_code == 200
        mock_service.list_catalog.assert_awaited_once()


# ------------------------------------------------------------------
# GET /registry/version
# ------------------------------------------------------------------


class TestGetVersion:
    def test_returns_200(self, client, mock_service):
        mock_service.get_version.return_value = 5
        resp = client.get("/registry/version")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["registry_version"] == 5


# ------------------------------------------------------------------
# POST /registry/validate
# ------------------------------------------------------------------


class TestValidate:
    def test_returns_200_valid(self, client, mock_service):
        mock_service.validate_plan_tools.return_value = ValidationResult(
            valid=True, current_version=7
        )
        resp = client.post(
            "/registry/validate",
            json={
                "plan_registry_version": 5,
                "referenced_tool_ids": ["google.calendar"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["valid"] is True

    def test_returns_200_invalid_with_issues(
        self,
        client,
        mock_service,
    ):
        mock_service.validate_plan_tools.return_value = ValidationResult(
            valid=False,
            current_version=7,
            issues=[
                {
                    "tool_id": "slack.messaging",
                    "reason": "TOOL_DEACTIVATED",
                }
            ],
        )
        resp = client.post(
            "/registry/validate",
            json={
                "plan_registry_version": 5,
                "referenced_tool_ids": ["slack.messaging"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["valid"] is False


# ------------------------------------------------------------------
# POST /registry/resolve
# ------------------------------------------------------------------


class TestResolve:
    def test_returns_200_with_credential_id(
        self,
        client,
        mock_service,
    ):
        mock_service.resolve_credential_template.return_value = ResolvedCredential(
            credential_id="gcal_user_u-123_work",
            tool_id="google.calendar",
            n8n_credential_type="googleCalendarOAuth2Api",
        )
        resp = client.post(
            "/registry/resolve",
            json={
                "tool_id": "google.calendar",
                "variables": {
                    "user_id": "u-123",
                    "account_name": "work",
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["credential_id"] == ("gcal_user_u-123_work")

    def test_returns_error_for_missing_var(
        self,
        client,
        mock_service,
    ):
        mock_service.resolve_credential_template.side_effect = TemplateResolutionError(
            tool_id="google.calendar",
            template="gcal_{{user_id}}_{{account_name}}",
            missing_variables=["account_name"],
        )
        resp = client.post(
            "/registry/resolve",
            json={
                "tool_id": "google.calendar",
                "variables": {"user_id": "u-123"},
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "TEMPLATE_RESOLUTION_ERROR"


# ------------------------------------------------------------------
# POST /registry/tools (create)
# ------------------------------------------------------------------


class TestCreateTool:
    def test_returns_200(self, client, mock_service):
        mock_service.create_tool.return_value = CreateToolResponse(
            tool_id="slack.messaging",
            registry_version=6,
            created_at=_now(),
        )
        resp = client.post(
            "/registry/tools",
            json={
                "tool_id": "slack.messaging",
                "display_name": "Slack",
                "credential_template": "slack_{{user_id}}",
                "n8n_credential_type": "slackOAuth2Api",
                "operations": {
                    "send": {
                        "operation_id": "send",
                        "n8n_node": "Slack",
                    }
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["tool_id"] == "slack.messaging"

    def test_returns_409_duplicate(self, client, mock_service):
        mock_service.create_tool.side_effect = ToolAlreadyExistsError("slack.messaging")
        resp = client.post(
            "/registry/tools",
            json={
                "tool_id": "slack.messaging",
                "display_name": "Slack",
                "credential_template": "x",
                "n8n_credential_type": "x",
                "operations": {
                    "op": {
                        "operation_id": "op",
                        "n8n_node": "N",
                    }
                },
            },
        )
        assert resp.status_code == 409

    def test_returns_400_schema_error(self, client, mock_service):
        mock_service.create_tool.side_effect = SchemaValidationError("bad field")
        resp = client.post(
            "/registry/tools",
            json={
                "tool_id": "test.tool",
                "display_name": "T",
                "credential_template": "x",
                "n8n_credential_type": "x",
                "operations": {
                    "op": {
                        "operation_id": "op",
                        "n8n_node": "N",
                    }
                },
            },
        )
        assert resp.status_code == 400


# ------------------------------------------------------------------
# PUT /registry/tools/{tool_id}
# ------------------------------------------------------------------


class TestUpdateTool:
    def test_returns_200(self, client, mock_service):
        mock_service.update_tool.return_value = UpdateToolResponse(
            tool_id="google.calendar",
            registry_version=7,
            updated_at=_now(),
        )
        resp = client.put(
            "/registry/tools/google.calendar",
            json={"display_name": "Updated"},
        )
        assert resp.status_code == 200


# ------------------------------------------------------------------
# DELETE /registry/tools/{tool_id}
# ------------------------------------------------------------------


class TestDeactivateTool:
    def test_returns_200(self, client, mock_service):
        mock_service.deactivate_tool.return_value = DeactivateToolResponse(
            tool_id="google.calendar",
            active=False,
            registry_version=8,
            deactivated_at=_now(),
        )
        resp = client.delete("/registry/tools/google.calendar")
        assert resp.status_code == 200
        assert resp.json()["data"]["active"] is False


# ------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------


class TestHealth:
    def test_health_endpoint_returns_ok(self, client):
        resp = client.get("/registry/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ------------------------------------------------------------------
# Response envelope tests
# ------------------------------------------------------------------


class TestResponseEnvelopes:
    def test_response_format_matches_spec(
        self,
        client,
        mock_service,
    ):
        mock_service.get_version.return_value = 0
        resp = client.get("/registry/version")
        body = resp.json()
        assert "status" in body
        assert body["status"] == "ok"
        assert "data" in body

    def test_error_response_format_matches_spec(
        self,
        client,
        mock_service,
    ):
        mock_service.get_tool.side_effect = ToolNotFoundError("x.y")
        resp = client.get("/registry/tools/x.y")
        body = resp.json()
        assert body["status"] == "error"
        assert "error_code" in body
        assert "message" in body
