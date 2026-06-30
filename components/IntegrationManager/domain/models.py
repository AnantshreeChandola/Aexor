"""
IntegrationManager Domain Models

Tracks which providers each user has connected via hosted MCP services.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UserConnection(BaseModel):
    """Connection status for a single user-provider pair."""

    user_id: str
    provider_name: str
    is_connected: bool = False
    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
    composio_entity_id: str = ""


class InitiateConnectionResponse(BaseModel):
    """Response from initiating an OAuth connection flow."""

    redirect_url: str
    provider_name: str


class ConnectionStatusResponse(BaseModel):
    """Response listing a user's provider connections."""

    connections: list[UserConnection]
    total: int


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProviderNotFoundError(Exception):
    """Provider name not found in configured MCP servers."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"Provider '{provider_name}' not found")


class ConnectionNotFoundError(Exception):
    """No connection record for user + provider."""

    def __init__(self, user_id: str, provider_name: str) -> None:
        self.user_id = user_id
        self.provider_name = provider_name
        super().__init__(f"No connection found for user '{user_id}' and provider '{provider_name}'")


class ComposioApiError(Exception):
    """Non-2xx response from Composio REST API."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Composio API error {status_code}: {detail}")


class ComposioUnreachableError(Exception):
    """Network error connecting to Composio."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Composio unreachable: {detail}")
