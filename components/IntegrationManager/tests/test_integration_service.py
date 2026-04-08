"""Tests for IntegrationManager service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.IntegrationManager.domain.models import (
    ComposioApiError,
    ProviderNotFoundError,
    UserConnection,
)
from components.IntegrationManager.service.integration_service import IntegrationManager
from shared.mcp.config import ComposioConfig


@pytest.fixture()
def mock_db():
    db = MagicMock()
    db.is_user_connected = AsyncMock(return_value=False)
    db.get_user_connections = AsyncMock(return_value=[])
    db.upsert_connection = AsyncMock(
        return_value=UserConnection(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=True,
            composio_entity_id="user-1",
        )
    )
    return db


@pytest.fixture()
def mock_composio_client():
    client = AsyncMock()
    client.get_mcp_config = AsyncMock(return_value={"allowed_tools": ["TOOL_A"]})
    client.update_allowed_tools = AsyncMock(return_value={})
    client.initiate_connection = AsyncMock(
        return_value="https://accounts.google.com/oauth?state=abc"
    )
    client.list_connections = AsyncMock(return_value=[])
    client.revoke_connection = AsyncMock()
    client.get_auth_config = AsyncMock(return_value=None)
    client.create_integration = AsyncMock(
        return_value={"id": "ac_test123", "auth_scheme": "OAUTH2"}
    )
    return client


@pytest.fixture()
def service(mock_db):
    return IntegrationManager(db_adapter=mock_db)


def _make_composio_config(**overrides) -> ComposioConfig:
    defaults = {
        "api_key": "sk-composio-test",
        "mcp_config_id": "cfg-abc",
        "auth_configs": {"google_calendar": "ac_gcal", "gmail": "ac_gmail"},
        "system_user_id": "__system__",
    }
    defaults.update(overrides)
    return ComposioConfig(**defaults)


# ------------------------------------------------------------------
# Basic connection tests (no Composio client needed)
# ------------------------------------------------------------------


class TestIsUserConnected:
    @pytest.mark.asyncio()
    async def test_connected(self, service, mock_db):
        mock_db.is_user_connected.return_value = True
        result = await service.is_user_connected("user-1", "google_calendar")
        assert result is True
        mock_db.is_user_connected.assert_called_once_with("user-1", "google_calendar")

    @pytest.mark.asyncio()
    async def test_not_connected(self, service, mock_db):
        result = await service.is_user_connected("user-1", "slack")
        assert result is False


class TestGetUserConnections:
    @pytest.mark.asyncio()
    async def test_returns_connections(self, service, mock_db):
        now = datetime.now(UTC)
        mock_db.get_user_connections.return_value = [
            UserConnection(
                user_id="user-1",
                provider_name="google_calendar",
                is_connected=True,
                connected_at=now,
                composio_entity_id="user-1",
            ),
            UserConnection(
                user_id="user-1",
                provider_name="slack",
                is_connected=False,
                composio_entity_id="user-1",
            ),
        ]

        result = await service.get_user_connections("user-1")
        assert len(result) == 2


class TestDisconnect:
    @pytest.mark.asyncio()
    async def test_disconnect_without_composio_client(self, service, mock_db):
        mock_db.upsert_connection.return_value = UserConnection(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=False,
            composio_entity_id="user-1",
        )

        result = await service.disconnect("user-1", "google_calendar")
        assert result.is_connected is False
        mock_db.upsert_connection.assert_called_once_with(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=False,
            composio_entity_id="user-1",
        )

    @pytest.mark.asyncio()
    async def test_disconnect_revokes_on_composio(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.list_connections.return_value = [
            {"id": "ca-123", "appName": "google_calendar", "status": "ACTIVE"},
        ]
        mock_db.upsert_connection.return_value = UserConnection(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=False,
            composio_entity_id="user-1",
        )

        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )
        result = await svc.disconnect("user-1", "google_calendar")

        assert result.is_connected is False
        mock_composio_client.revoke_connection.assert_called_once_with("ca-123")

    @pytest.mark.asyncio()
    async def test_disconnect_tolerates_composio_failure(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.list_connections.side_effect = ComposioApiError(500, "err")
        mock_db.upsert_connection.return_value = UserConnection(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=False,
            composio_entity_id="user-1",
        )

        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )
        # Should not raise — Composio errors are logged and tolerated
        result = await svc.disconnect("user-1", "google_calendar")
        assert result.is_connected is False


class TestMarkConnected:
    @pytest.mark.asyncio()
    async def test_mark_connected(self, service, mock_db):
        result = await service.mark_connected("user-1", "google_calendar")
        assert result.is_connected is True


class TestHandleCallback:
    @pytest.mark.asyncio()
    async def test_successful_callback(self, service, mock_db):
        await service.handle_callback(
            "user-1",
            "google_calendar",
            {"status": "connected"},
        )
        mock_db.upsert_connection.assert_called_once_with(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=True,
            composio_entity_id="user-1",
        )

    @pytest.mark.asyncio()
    async def test_failed_callback(self, service, mock_db):
        mock_db.upsert_connection.return_value = UserConnection(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=False,
            composio_entity_id="user-1",
        )

        await service.handle_callback(
            "user-1",
            "google_calendar",
            {"status": "failed"},
        )
        mock_db.upsert_connection.assert_called_once_with(
            user_id="user-1",
            provider_name="google_calendar",
            is_connected=False,
            composio_entity_id="user-1",
        )


# ------------------------------------------------------------------
# Composio provider tests
# ------------------------------------------------------------------


class TestGetAvailableProviders:
    def test_returns_providers_from_composio_config(self, mock_db):
        cfg = _make_composio_config()
        svc = IntegrationManager(db_adapter=mock_db, composio_config=cfg)
        providers = svc.get_available_providers()
        assert providers == ["gmail", "google_calendar"]  # sorted

    def test_returns_empty_when_no_composio_config(self, mock_db):
        svc = IntegrationManager(db_adapter=mock_db)
        providers = svc.get_available_providers()
        assert providers == []

    def test_returns_empty_when_no_auth_configs(self, mock_db):
        cfg = _make_composio_config(auth_configs={})
        svc = IntegrationManager(db_adapter=mock_db, composio_config=cfg)
        assert svc.get_available_providers() == []


class TestComposioInitiateConnection:
    @pytest.mark.asyncio()
    async def test_raises_not_implemented_without_config(self, mock_db):
        svc = IntegrationManager(db_adapter=mock_db)
        with pytest.raises(NotImplementedError, match="COMPOSIO_API_KEY"):
            await svc.initiate_connection("user-1", "google_calendar")

    @pytest.mark.asyncio()
    async def test_raises_provider_not_found(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        # Ensure auto-create also returns no id
        mock_composio_client.create_integration.return_value = {}
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )
        with pytest.raises(ProviderNotFoundError):
            await svc.initiate_connection("user-1", "nonexistent_provider")

    @pytest.mark.asyncio()
    async def test_reuses_existing_auth_config(self, mock_db, mock_composio_client):
        cfg = _make_composio_config(auth_configs={})  # no static config
        mock_composio_client.get_auth_config.return_value = "ac_existing789"
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        url = await svc.initiate_connection("user-1", "googlecalendar")

        assert url == "https://accounts.google.com/oauth?state=abc"
        # Should reuse existing, NOT create new
        mock_composio_client.get_auth_config.assert_called_once_with("googlecalendar")
        mock_composio_client.create_integration.assert_not_called()
        mock_composio_client.initiate_connection.assert_called_once_with(
            user_id="user-1",
            auth_config_id="ac_existing789",
            redirect_url=None,
        )

    @pytest.mark.asyncio()
    async def test_creates_auth_config_when_none_exists(self, mock_db, mock_composio_client):
        cfg = _make_composio_config(auth_configs={})  # no static config
        # get_auth_config returns None (default in fixture)
        mock_composio_client.create_integration.return_value = {"id": "ac_new456"}
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        url = await svc.initiate_connection("user-1", "googlecalendar")

        assert url == "https://accounts.google.com/oauth?state=abc"
        mock_composio_client.get_auth_config.assert_called_once_with("googlecalendar")
        mock_composio_client.create_integration.assert_called_once_with("googlecalendar")
        mock_composio_client.initiate_connection.assert_called_once_with(
            user_id="user-1",
            auth_config_id="ac_new456",
            redirect_url=None,
        )

    @pytest.mark.asyncio()
    async def test_calls_composio_client(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        url = await svc.initiate_connection("user-1", "google_calendar")

        assert url == "https://accounts.google.com/oauth?state=abc"
        mock_composio_client.initiate_connection.assert_called_once_with(
            user_id="user-1",
            auth_config_id="ac_gcal",
            redirect_url=None,
        )

    @pytest.mark.asyncio()
    async def test_passes_redirect_url(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        await svc.initiate_connection(
            "user-1", "google_calendar", redirect_url="https://myapp.com/callback"
        )

        mock_composio_client.initiate_connection.assert_called_once_with(
            user_id="user-1",
            auth_config_id="ac_gcal",
            redirect_url="https://myapp.com/callback",
        )


# ------------------------------------------------------------------
# System-level tool management tests
# ------------------------------------------------------------------


class TestAddTool:
    @pytest.mark.asyncio()
    async def test_raises_not_implemented_without_config(self, mock_db):
        svc = IntegrationManager(db_adapter=mock_db)
        with pytest.raises(NotImplementedError, match="COMPOSIO_API_KEY"):
            await svc.add_tool("GMAIL_SEND_EMAIL")

    @pytest.mark.asyncio()
    async def test_adds_tool_to_allowed_list(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.get_mcp_config.return_value = {"allowed_tools": ["TOOL_A"]}
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        await svc.add_tool("GMAIL_SEND_EMAIL")

        mock_composio_client.get_mcp_config.assert_called_once_with("cfg-abc")
        mock_composio_client.update_allowed_tools.assert_called_once_with(
            "cfg-abc", ["TOOL_A", "GMAIL_SEND_EMAIL"]
        )

    @pytest.mark.asyncio()
    async def test_no_duplicate_if_already_present(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.get_mcp_config.return_value = {
            "allowed_tools": ["TOOL_A", "GMAIL_SEND_EMAIL"]
        }
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        await svc.add_tool("GMAIL_SEND_EMAIL")

        # Should not call update since tool already present
        mock_composio_client.update_allowed_tools.assert_not_called()

    @pytest.mark.asyncio()
    async def test_adds_to_empty_list(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.get_mcp_config.return_value = {"allowed_tools": []}
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        await svc.add_tool("GMAIL_SEND_EMAIL")

        mock_composio_client.update_allowed_tools.assert_called_once_with(
            "cfg-abc", ["GMAIL_SEND_EMAIL"]
        )


class TestRemoveTool:
    @pytest.mark.asyncio()
    async def test_raises_not_implemented_without_config(self, mock_db):
        svc = IntegrationManager(db_adapter=mock_db)
        with pytest.raises(NotImplementedError, match="COMPOSIO_API_KEY"):
            await svc.remove_tool("GMAIL_SEND_EMAIL")

    @pytest.mark.asyncio()
    async def test_removes_tool_from_list(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.get_mcp_config.return_value = {
            "allowed_tools": ["TOOL_A", "GMAIL_SEND_EMAIL"]
        }
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        await svc.remove_tool("GMAIL_SEND_EMAIL")

        mock_composio_client.update_allowed_tools.assert_called_once_with("cfg-abc", ["TOOL_A"])

    @pytest.mark.asyncio()
    async def test_noop_if_tool_not_in_list(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.get_mcp_config.return_value = {"allowed_tools": ["TOOL_A"]}
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        await svc.remove_tool("NONEXISTENT")

        mock_composio_client.update_allowed_tools.assert_not_called()


# ------------------------------------------------------------------
# sync_connections tests
# ------------------------------------------------------------------


class TestSyncConnections:
    @pytest.mark.asyncio()
    async def test_without_composio_falls_back_to_db(self, mock_db):
        svc = IntegrationManager(db_adapter=mock_db)
        result = await svc.sync_connections("user-1")
        assert result == []
        mock_db.get_user_connections.assert_called()

    @pytest.mark.asyncio()
    async def test_syncs_active_connections(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.list_connections.return_value = [
            {"appName": "google_calendar", "status": "ACTIVE"},
            {"appName": "slack", "status": "ACTIVE"},
        ]
        # First call returns existing local state (one stale connection)
        mock_db.get_user_connections.side_effect = [
            [
                UserConnection(
                    user_id="user-1",
                    provider_name="gmail",
                    is_connected=True,
                    composio_entity_id="user-1",
                ),
            ],
            # Second call returns updated state
            [
                UserConnection(
                    user_id="user-1",
                    provider_name="google_calendar",
                    is_connected=True,
                    composio_entity_id="user-1",
                ),
                UserConnection(
                    user_id="user-1",
                    provider_name="slack",
                    is_connected=True,
                    composio_entity_id="user-1",
                ),
                UserConnection(
                    user_id="user-1",
                    provider_name="gmail",
                    is_connected=False,
                    composio_entity_id="user-1",
                ),
            ],
        ]

        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )
        result = await svc.sync_connections("user-1")

        assert len(result) == 3
        mock_composio_client.list_connections.assert_called_once_with("user-1")
        # gmail was marked disconnected since it wasn't in remote
        assert any(
            call.kwargs.get("provider_name") == "gmail" and call.kwargs.get("is_connected") is False
            for call in mock_db.upsert_connection.call_args_list
        )


# ------------------------------------------------------------------
# create_integration tests
# ------------------------------------------------------------------


class TestCreateIntegration:
    @pytest.mark.asyncio()
    async def test_raises_not_implemented_without_config(self, mock_db):
        svc = IntegrationManager(db_adapter=mock_db)
        with pytest.raises(NotImplementedError, match="COMPOSIO_API_KEY"):
            await svc.create_integration("googlecalendar")

    @pytest.mark.asyncio()
    async def test_creates_integration(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.create_integration.return_value = {
            "id": "ac_test789",
            "auth_scheme": "OAUTH2",
        }
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        result = await svc.create_integration("googlecalendar")

        assert result["id"] == "ac_test789"
        mock_composio_client.create_integration.assert_called_once_with("googlecalendar")


# ------------------------------------------------------------------
# list_tools / list_apps tests
# ------------------------------------------------------------------


class TestListTools:
    @pytest.mark.asyncio()
    async def test_raises_not_implemented_without_config(self, mock_db):
        svc = IntegrationManager(db_adapter=mock_db)
        with pytest.raises(NotImplementedError, match="COMPOSIO_API_KEY"):
            await svc.list_tools()

    @pytest.mark.asyncio()
    async def test_returns_allowed_tools(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.get_mcp_config.return_value = {
            "allowed_tools": ["GMAIL_SEND_EMAIL", "SLACK_POST_MESSAGE"]
        }
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        tools = await svc.list_tools()
        assert tools == ["GMAIL_SEND_EMAIL", "SLACK_POST_MESSAGE"]


class TestListApps:
    @pytest.mark.asyncio()
    async def test_groups_tools_by_app(self, mock_db, mock_composio_client):
        cfg = _make_composio_config()
        mock_composio_client.get_mcp_config.return_value = {
            "allowed_tools": [
                "GMAIL_SEND_EMAIL",
                "GMAIL_READ_INBOX",
                "SLACK_POST_MESSAGE",
            ]
        }
        svc = IntegrationManager(
            db_adapter=mock_db,
            composio_config=cfg,
            composio_client=mock_composio_client,
        )

        apps = await svc.list_apps()
        assert "gmail" in apps
        assert len(apps["gmail"]) == 2
        assert "slack" in apps
        assert len(apps["slack"]) == 1
