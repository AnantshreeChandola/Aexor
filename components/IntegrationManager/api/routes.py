"""
IntegrationManager API Routes

Endpoints for managing user-provider connections and system-level tool management.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
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


class ToolParameter(BaseModel):
    name: str
    type: str  # "string", "number", "boolean", "array", "object"
    description: str
    required: bool
    enum: list[str] | None = None


class ToolSchemaResponse(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter]


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


class AppCard(BaseModel):
    slug: str
    name: str
    logo: str = ""
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    tool_count: int = 0


class AppListResponse(BaseModel):
    apps: list[AppCard]
    total: int


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
    """List all provider connection statuses for the current user.

    Syncs fresh state from Composio before reading the DB so connections
    made via the OAuth popup or the Composio dashboard are reflected
    immediately.  Best-effort: on Composio outage, falls back to whatever
    is already in the local DB.
    """
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authentication required")

    try:
        await service.warm_connection_cache(str(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "list_connections_warm_failed",
            extra={"user_id": str(user_id), "error": str(exc)},
        )

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


def _build_callback_url(request: Request) -> str:
    """Construct the absolute OAuth callback URL from the incoming request.

    Uses the request's base URL so the callback matches whichever origin
    the user is accessing the app from (localhost, a reverse proxy host,
    etc.).  Relies on ``--proxy-headers`` when running behind a TLS-
    terminating proxy so the scheme is correct.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/integrations/callback"


@router.post("/connect", response_model=ConnectResponse)
async def initiate_connection(body: ConnectRequest, request: Request):
    """Initiate OAuth connection flow for a provider."""
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "Authentication required")

    callback_url = _build_callback_url(request)

    try:
        redirect_url = await service.initiate_connection(
            str(user_id),
            body.provider_name,
            redirect_url=callback_url,
        )
        return ConnectResponse(
            redirect_url=redirect_url,
            provider_name=body.provider_name,
        )
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)


_CALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Connection complete</title>
  <style>
    html, body { height: 100%; margin: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background:
        radial-gradient(ellipse 90% 60% at 15% -10%, rgba(124, 58, 237, 0.09), transparent 60%),
        radial-gradient(ellipse 70% 50% at 85% 5%, rgba(236, 72, 153, 0.06), transparent 60%),
        linear-gradient(180deg, #fbfbfd 0%, #f5f5f7 100%);
      color: #0a0a0a;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .box {
      background: #ffffff;
      border: 1px solid #e5e7eb;
      border-radius: 16px;
      padding: 40px 48px;
      text-align: center;
      box-shadow: 0 10px 40px rgba(0, 0, 0, 0.05);
      max-width: 400px;
    }
    .check {
      width: 56px; height: 56px; border-radius: 50%;
      background: linear-gradient(135deg, #7c3aed, #ec4899);
      display: inline-flex; align-items: center; justify-content: center;
      margin-bottom: 16px; color: white; font-size: 28px; font-weight: 600;
    }
    h1 { font-size: 20px; margin: 0 0 8px; font-weight: 600; }
    p { color: #6b7280; margin: 0; font-size: 14px; }
  </style>
</head>
<body>
  <div class="box">
    <div class="check">&#10003;</div>
    <h1>Connection complete</h1>
    <p>You can close this window. It will close automatically.</p>
  </div>
  <script>
    (function () {
      try {
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage({ type: "aexor-integration-connected" }, "*");
        }
      } catch (e) {}
      setTimeout(function () { try { window.close(); } catch (e) {} }, 800);
    })();
  </script>
</body>
</html>"""


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(request: Request):  # noqa: ARG001
    """OAuth callback landing page.

    Composio redirects the user's browser here after completing the OAuth
    flow.  This route requires no authentication — the actual connection
    state is synced by ``warm_connection_cache()`` on the next
    ``GET /api/integrations`` call in the parent window.  This page just
    closes the popup and notifies the parent to refresh.
    """
    return HTMLResponse(content=_CALLBACK_HTML)


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

    if user_tools is None:
        # Not cached — live refresh from Composio
        try:
            user_tools = await tool_catalog.refresh_user(str(user_id))
        except Exception:
            user_tools = []

    tools = [
        ToolInfo(
            name=t.name,
            provider_name=t.provider_name,
            description=t.description,
        )
        for t in user_tools
    ]

    return UserToolsResponse(tools=tools, total=len(tools))


@router.get("/tools/{tool_name}/schema", response_model=ToolSchemaResponse)
async def get_tool_schema(tool_name: str, request: Request):
    """Return the input schema for a specific tool (for the plan builder tool picker)."""
    tool_catalog = getattr(request.app.state, "tool_catalog", None)
    if tool_catalog is None:
        raise HTTPException(503, "Tool catalog not available")

    # Search user tools first, then global catalog for schema lookup
    tool = None
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        user_tools = await tool_catalog.get_user_tools(str(user_id))
        if user_tools is None:
            try:
                user_tools = await tool_catalog.refresh_user(str(user_id))
            except Exception:
                user_tools = []
        tool = next((t for t in user_tools if t.name == tool_name), None)
    if tool is None:
        # Fall back to global catalog for schema (tool name was already
        # validated when the user selected it from their connected tools)
        all_tools = tool_catalog.get_all_tools()
        tool = next((t for t in all_tools if t.name == tool_name), None)
    if tool is None:
        raise HTTPException(404, f"Tool '{tool_name}' not found")

    schema = tool.input_schema or {}
    props = schema.get("properties", {})
    required_list = set(schema.get("required", []))
    parameters = [
        ToolParameter(
            name=k,
            type=v.get("type", "string"),
            description=v.get("description", k),
            required=k in required_list,
            enum=v.get("enum"),
        )
        for k, v in props.items()
    ]
    return ToolSchemaResponse(
        name=tool.name, description=tool.description, parameters=parameters
    )


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


@router.get("/apps", response_model=AppListResponse)
async def list_apps(request: Request):
    """List all added apps (from allowed_tools) with metadata."""
    service = request.app.state.integration_manager
    if service is None:
        raise HTTPException(503, "IntegrationManager not available")

    try:
        apps = await service.list_apps_with_metadata()
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except (ComposioApiError, ComposioUnreachableError) as exc:
        raise _handle_composio_error(exc)

    return AppListResponse(apps=[AppCard(**a) for a in apps], total=len(apps))


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
