"""
PluginRegistry API Routes

FastAPI endpoints for tool CRUD, catalog queries, version retrieval,
template resolution, and pre-execution validation.
Thin wrappers around RegistryService.

Reference: LLD.md Section 3.1
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from shared.api.auth import get_auth_context
from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import get_registry_service

from ..domain.models import (
    CreateToolRequest,
    InvalidToolIdFormatError,
    ResolveCredentialRequest,
    SchemaValidationError,
    TemplateResolutionError,
    ToolAlreadyExistsError,
    ToolNotFoundError,
    UpdateToolRequest,
    ValidatePlanToolsRequest,
)

if TYPE_CHECKING:
    from ..service.registry_service import RegistryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/registry", tags=["registry"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _ok(data: object) -> dict:
    """Wrap a successful response."""
    return {"status": "ok", "data": data}


def _handle_domain_error(exc: Exception) -> JSONResponse:
    """Map domain exceptions to HTTP error responses.

    Uses ``shared.api.error_handlers.ErrorResponse`` for consistent
    response formatting across all components.
    """
    if isinstance(exc, ToolNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="TOOL_NOT_FOUND",
                message=str(exc),
                details={"tool_id": exc.tool_id},
            ).model_dump(),
        )
    if isinstance(exc, ToolAlreadyExistsError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(
                error_code="TOOL_ALREADY_EXISTS",
                message=str(exc),
                details={"tool_id": exc.tool_id},
            ).model_dump(),
        )
    if isinstance(exc, InvalidToolIdFormatError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="INVALID_TOOL_ID_FORMAT",
                message=str(exc),
                details={"tool_id": exc.tool_id},
            ).model_dump(),
        )
    if isinstance(exc, SchemaValidationError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="SCHEMA_VALIDATION_ERROR",
                message=str(exc),
                details={"details": exc.details},
            ).model_dump(),
        )
    if isinstance(exc, TemplateResolutionError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="TEMPLATE_RESOLUTION_ERROR",
                message=str(exc),
                details={
                    "tool_id": exc.tool_id,
                    "template": exc.template,
                    "missing_variables": exc.missing_variables,
                },
            ).model_dump(),
        )
    # Fallback — delegate to shared handler for unknown errors
    return APIErrorHandler.handle_generic_error(exc)


# ------------------------------------------------------------------
# Read endpoints
# ------------------------------------------------------------------


@router.get("/tools/{tool_id}")
async def get_tool(
    tool_id: str,
    request: Request,
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Retrieve a single tool definition with all operations."""
    try:
        plan_id = request.headers.get("X-Plan-ID")
        tool = await service.get_tool(tool_id, plan_id=plan_id)
        return _ok(tool.model_dump(mode="json"))
    except (
        ToolNotFoundError,
        InvalidToolIdFormatError,
    ) as exc:
        return _handle_domain_error(exc)


@router.get("/catalog")
async def list_catalog(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Retrieve the full catalog of active tools."""
    plan_id = request.headers.get("X-Plan-ID")
    catalog = await service.list_catalog(
        page=page,
        page_size=page_size,
        plan_id=plan_id,
    )
    return _ok(catalog.model_dump(mode="json"))


@router.get("/version")
async def get_version(
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Retrieve the current registry version."""
    version = await service.get_version()
    return _ok({"registry_version": version})


# ------------------------------------------------------------------
# Validation / resolution endpoints
# ------------------------------------------------------------------


@router.post("/validate")
async def validate_plan_tools(
    body: ValidatePlanToolsRequest,
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Pre-execution validation of plan tool references."""
    result = await service.validate_plan_tools(
        plan_registry_version=body.plan_registry_version,
        referenced_tool_ids=body.referenced_tool_ids,
    )
    return _ok(result.model_dump(mode="json"))


@router.post("/resolve")
async def resolve_credential(
    body: ResolveCredentialRequest,
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Resolve a credential ID template with user variables."""
    try:
        resolved = await service.resolve_credential_template(
            tool_id=body.tool_id,
            variables=body.variables,
        )
        return _ok(resolved.model_dump(mode="json"))
    except (
        ToolNotFoundError,
        TemplateResolutionError,
        InvalidToolIdFormatError,
    ) as exc:
        return _handle_domain_error(exc)


# ------------------------------------------------------------------
# Write endpoints (admin CRUD)
# ------------------------------------------------------------------


@router.post("/tools")
async def create_tool(
    body: CreateToolRequest,
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Register a new tool in the catalog."""
    try:
        resp = await service.create_tool(body)
        return _ok(resp.model_dump(mode="json"))
    except (
        ToolAlreadyExistsError,
        InvalidToolIdFormatError,
        SchemaValidationError,
    ) as exc:
        return _handle_domain_error(exc)


@router.put("/tools/{tool_id}")
async def update_tool(
    tool_id: str,
    body: UpdateToolRequest,
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Update an existing tool."""
    try:
        resp = await service.update_tool(tool_id, body)
        return _ok(resp.model_dump(mode="json"))
    except (
        ToolNotFoundError,
        InvalidToolIdFormatError,
        SchemaValidationError,
    ) as exc:
        return _handle_domain_error(exc)


@router.delete("/tools/{tool_id}")
async def deactivate_tool(
    tool_id: str,
    auth_context: dict = Depends(get_auth_context),
    service: RegistryService = Depends(get_registry_service),
):
    """Deactivate a tool (soft-delete)."""
    try:
        resp = await service.deactivate_tool(tool_id)
        return _ok(resp.model_dump(mode="json"))
    except (
        ToolNotFoundError,
        InvalidToolIdFormatError,
    ) as exc:
        return _handle_domain_error(exc)


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@router.get("/health")
async def health_check():
    """Health check for PluginRegistry service."""
    return {"status": "ok", "service": "pluginregistry"}
