"""
IntegrationManager API Routes

Endpoints for managing user-provider connections and system-level tool management.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..domain.models import ComposioApiError, ComposioUnreachableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class AvailableProvidersResponse(BaseModel):
    providers: list[str]
    total: int


class ConnectRequest(BaseModel):
    provider_name: str = Field(min_length=1, max_length=64)


class ConnectResponse(BaseModel):
    redirect_url: str
    provider_name: str


class ConnectionStatus(BaseModel):
    provider_name: str
    is_connected: bool
    connected_at: str | None = None


class ConnectionListResponse(BaseModel):
    connections: list[ConnectionStatus]
    total: int


class ToolInfo(BaseModel):
    name: str
    provider_name: str
    description: str = ""


class UserToolsResponse(BaseModel):
    tools: list[ToolInfo]
    total: int


class ComposioToolsResponse(BaseModel):
    tools: list[str]
    total: int


class ComposioAppEntry(BaseModel):
    app: str
    tools: list[str]
    tool_count: int


class ComposioAppsResponse(BaseModel):
    apps: list[ComposioAppEntry]
    total: int


class SetupIntegrationRequest(BaseModel):
    """Request to create an OAuth integration for an app on Composio."""

    app_name: str = Field(min_length=1, max_length=64)


class SetupIntegrationResponse(BaseModel):
    app_name: str
    integration_id: str
    status: str = "created"


class AddToolRequest(BaseModel):
    """Request to add a tool to the system MCP config."""

    tool_name: str = Field(min_length=1, max_length=128)


class AddToolResponse(BaseModel):
    tool_name: str
    status: str = "added"


def _handle_composio_error(exc: Exception) -> HTTPException:
    """Map Composio errors to HTTP responses."""
    if isinstance(exc, ComposioApiError):
        return HTTPException(502, f"Composio API error: {exc.detail}")
    if isinstance(exc, ComposioUnreachableError):
        return HTTPException(503, f"Composio unreachable: {exc.detail}")
    return HTTPException(500, f"Unexpected error: {exc}")


@router.get("/available", response_model=AvailableProvidersResponse)
async def available_providers(request: Request):
    """List providers that support OAuth connection via Composio."""
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    providers = service.get_available_providers()
    return AvailableProvidersResponse(providers=providers, total=len(providers))


@router.get("", response_model=ConnectionListResponse)
async def list_connections(request: Request):
    """List all provider connection statuses for the current user."""
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authentication required")

    connections = await service.get_user_connections(str(user_id))
    return ConnectionListResponse(
        connections=[
            ConnectionStatus(
                provider_name=c.provider_name,
                is_connected=c.is_connected,
                connected_at=c.connected_at.isoformat() if c.connected_at else None,
            )
            for c in connections
        ],
        total=len(connections),
    )


@router.post("/connect", response_model=ConnectResponse)
async def initiate_connection(body: ConnectRequest, request: Request):
    """Initiate OAuth connection flow for a provider."""
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authentication required")

    try:
        redirect_url = await service.initiate_connection(str(user_id), body.provider_name)
        return ConnectResponse(
            redirect_url=redirect_url,
            provider_name=body.provider_name,
        )
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)


# ---------------------------------------------------------------------------
# Tool management — system-level, declared BEFORE /{provider_name}
# ---------------------------------------------------------------------------


@router.get("/tools", response_model=UserToolsResponse)
async def list_user_tools(request: Request):
    """List all MCP tools available to the current user.

    Returns cached per-user tools if available, otherwise
    falls back to the global tool catalog.
    """
    tool_catalog = getattr(request.app.state, "tool_catalog", None)
    if tool_catalog is None:
        raise HTTPException(503, "Tool catalog not available")

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authentication required")

    # Try per-user cached tools first
    user_tools = await tool_catalog.get_user_tools(str(user_id))

    if user_tools is not None:
        tools = [
            ToolInfo(
                name=t.name,
                provider_name=t.provider_name,
                description=t.description,
            )
            for t in user_tools
        ]
    else:
        # Fall back to global catalog
        all_tools = tool_catalog.get_all_tools()
        tools = [
            ToolInfo(
                name=t.name,
                provider_name=t.provider_name,
                description=t.description,
            )
            for t in all_tools
        ]

    return UserToolsResponse(tools=tools, total=len(tools))


@router.get("/tools/composio", response_model=ComposioToolsResponse)
async def list_composio_tools(request: Request):
    """List allowed_tools on the Composio MCP config.

    System-level admin operation — no user authentication required.
    """
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    try:
        tools = await service.list_tools()
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)

    return ComposioToolsResponse(tools=tools, total=len(tools))


@router.get("/tools/composio/apps", response_model=ComposioAppsResponse)
async def list_composio_apps(request: Request):
    """List unique apps (providers) derived from allowed_tools on the Composio MCP config.

    Groups tools by app prefix (e.g. GOOGLECALENDAR_CREATE_EVENT -> googlecalendar).
    System-level admin operation — no user authentication required.
    """
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    try:
        apps_map = await service.list_apps()
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)

    entries = [
        ComposioAppEntry(app=app, tools=tools, tool_count=len(tools))
        for app, tools in sorted(apps_map.items())
    ]
    return ComposioAppsResponse(apps=entries, total=len(entries))


@router.post("/tools", response_model=AddToolResponse, status_code=201)
async def add_tool(body: AddToolRequest, request: Request):
    """Add a tool to the system MCP config's allowed_tools list.

    System-level admin operation — no user authentication required.
    """
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    try:
        await service.add_tool(tool_name=body.tool_name)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)

    return AddToolResponse(tool_name=body.tool_name)


@router.post("/setup", response_model=SetupIntegrationResponse, status_code=201)
async def setup_integration(body: SetupIntegrationRequest, request: Request):
    """Create an OAuth integration for an app on Composio.

    One-time setup per app. After creation, users can connect
    via ``POST /api/integrations/connect``.

    System-level admin operation — no user authentication required.
    """
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    try:
        result = await service.create_integration(body.app_name)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)

    integration_id = result.get("id", "")
    return SetupIntegrationResponse(
        app_name=body.app_name,
        integration_id=integration_id,
    )


@router.delete("/tools/{tool_name}")
async def remove_tool(tool_name: str, request: Request):
    """Remove a tool from the system MCP config's allowed_tools list.

    System-level admin operation — no user authentication required.
    """
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    try:
        await service.remove_tool(tool_name=tool_name)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)

    return {"status": "removed", "tool_name": tool_name}


# ---------------------------------------------------------------------------
# Provider disconnect — path parameter route MUST come after /tools routes
# ---------------------------------------------------------------------------


@router.delete("/{provider_name}")
async def disconnect_provider(provider_name: str, request: Request):
    """Disconnect a provider for the current user."""
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authentication required")

    try:
        await service.disconnect(str(user_id), provider_name)
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)

    return {"status": "disconnected", "provider_name": provider_name}
