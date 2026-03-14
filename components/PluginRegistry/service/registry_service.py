"""
PluginRegistry Service Layer

Business logic for tool management, versioning, template resolution,
and pre-execution validation.

Reference: LLD.md Sections 3.2, 4.x, 7.x
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from components.PluginRegistry.adapters.db import (
        RegistryDatabaseAdapter,
    )

from components.PluginRegistry.domain.models import (
    CatalogResponse,
    CreateToolRequest,
    CreateToolResponse,
    DeactivateToolResponse,
    InvalidToolIdFormatError,
    ResolvedCredential,
    SchemaValidationError,
    ScopeVerificationResult,
    TemplateResolutionError,
    ToolAlreadyExistsError,
    ToolModel,
    ToolNotFoundError,
    UpdateToolRequest,
    UpdateToolResponse,
    ValidationIssue,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# Regex for tool_id format validation
_TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")

# Regex for extracting template variables  {{var_name}}
_TEMPLATE_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")

# Regex for safe variable values (alphanumeric + hyphen + underscore)
_SAFE_VALUE_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_tool_id_format(tool_id: str) -> None:
    """Raise InvalidToolIdFormatError if tool_id is malformed."""
    if not tool_id:
        raise InvalidToolIdFormatError(tool_id or "")
    if not _TOOL_ID_PATTERN.match(tool_id):
        raise InvalidToolIdFormatError(tool_id)


def _validate_compensation_refs(
    operations: dict[str, object],
) -> None:
    """Ensure compensation references point to existing operations."""
    op_ids = set(operations.keys())
    for op_id, op in operations.items():
        comp = getattr(op, "compensation", None)
        if comp and comp not in op_ids:
            raise SchemaValidationError(
                f"Operation '{op_id}' references compensation "
                f"'{comp}' which does not exist on this tool"
            )


def _sanitize_variable(name: str, value: str) -> str:
    """Sanitize a template variable value."""
    if not value:
        raise TemplateResolutionError(
            tool_id="",
            template="",
            missing_variables=[name],
        )
    if not _SAFE_VALUE_PATTERN.match(value):
        raise TemplateResolutionError(
            tool_id="",
            template="",
            missing_variables=[name],
        )
    return value


class RegistryService:
    """Business logic for the PluginRegistry component."""

    def __init__(self, db_adapter: RegistryDatabaseAdapter) -> None:
        self._db = db_adapter

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_tool(
        self,
        tool_id: str,
        plan_id: str | None = None,
    ) -> ToolModel:
        """Retrieve a single active tool with all operations."""
        start = time.monotonic()
        _validate_tool_id_format(tool_id)

        tool = await self._db.get_tool(tool_id)
        if tool is None or not tool.active:
            raise ToolNotFoundError(tool_id)

        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "get_tool",
            extra={
                "service": "pluginregistry",
                "operation": "get_tool",
                "tool_id": tool_id,
                "plan_id": plan_id,
                "latency_ms": latency,
                "status": "success",
            },
        )
        return tool

    async def list_catalog(
        self,
        page: int = 1,
        page_size: int = 50,
        plan_id: str | None = None,
    ) -> CatalogResponse:
        """Retrieve paginated catalog of active tools."""
        start = time.monotonic()
        tools, total = await self._db.list_active_tools(page, page_size)
        version = await self._db.get_current_version()

        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "list_catalog",
            extra={
                "service": "pluginregistry",
                "operation": "list_catalog",
                "plan_id": plan_id,
                "total": total,
                "latency_ms": latency,
                "status": "success",
            },
        )
        return CatalogResponse(
            tools=tools,
            registry_version=version,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_version(self) -> int:
        """Retrieve current registry version."""
        return await self._db.get_current_version()

    # ------------------------------------------------------------------
    # Write operations (admin CRUD)
    # ------------------------------------------------------------------

    async def create_tool(
        self,
        tool_def: CreateToolRequest,
    ) -> CreateToolResponse:
        """Register a new tool in the catalog."""
        start = time.monotonic()
        _validate_tool_id_format(tool_def.tool_id)

        if await self._db.tool_exists(tool_def.tool_id):
            raise ToolAlreadyExistsError(tool_def.tool_id)

        # Validate compensation referential integrity
        _validate_compensation_refs(tool_def.operations)

        tool, version = await self._db.create_tool(tool_def)

        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "create_tool",
            extra={
                "service": "pluginregistry",
                "operation": "create_tool",
                "tool_id": tool_def.tool_id,
                "registry_version": version,
                "latency_ms": latency,
                "status": "success",
            },
        )
        return CreateToolResponse(
            tool_id=tool_def.tool_id,
            registry_version=version,
            created_at=tool.created_at,
        )

    async def update_tool(
        self,
        tool_id: str,
        updates: UpdateToolRequest,
    ) -> UpdateToolResponse:
        """Update an existing tool."""
        start = time.monotonic()
        _validate_tool_id_format(tool_id)

        existing = await self._db.get_tool(tool_id)
        if existing is None:
            raise ToolNotFoundError(tool_id)

        # If operations are being replaced, validate compensation
        if updates.operations is not None:
            _validate_compensation_refs(updates.operations)

        tool, version = await self._db.update_tool(tool_id, updates)

        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "update_tool",
            extra={
                "service": "pluginregistry",
                "operation": "update_tool",
                "tool_id": tool_id,
                "registry_version": version,
                "latency_ms": latency,
                "status": "success",
            },
        )
        return UpdateToolResponse(
            tool_id=tool_id,
            registry_version=version,
            updated_at=tool.updated_at,
        )

    async def deactivate_tool(
        self,
        tool_id: str,
    ) -> DeactivateToolResponse:
        """Soft-delete a tool (sets active=false)."""
        start = time.monotonic()
        _validate_tool_id_format(tool_id)

        existing = await self._db.get_tool(tool_id)
        if existing is None:
            raise ToolNotFoundError(tool_id)

        # Idempotent: deactivating already-inactive tool is a no-op
        tool, version = await self._db.deactivate_tool(tool_id)

        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "deactivate_tool",
            extra={
                "service": "pluginregistry",
                "operation": "deactivate_tool",
                "tool_id": tool_id,
                "registry_version": version,
                "latency_ms": latency,
                "status": "success",
            },
        )
        return DeactivateToolResponse(
            tool_id=tool_id,
            active=False,
            registry_version=version,
            deactivated_at=tool.updated_at,
        )

    # ------------------------------------------------------------------
    # Template resolution
    # ------------------------------------------------------------------

    async def resolve_credential_template(
        self,
        tool_id: str,
        variables: dict[str, str],
    ) -> ResolvedCredential:
        """Resolve credential ID template with user-specific variables."""
        start = time.monotonic()
        _validate_tool_id_format(tool_id)

        tool = await self._db.get_tool(tool_id)
        if tool is None or not tool.active:
            raise ToolNotFoundError(tool_id)

        template = tool.credential_template
        required_vars = set(_TEMPLATE_VAR_PATTERN.findall(template))
        provided_vars = set(variables.keys())
        missing = required_vars - provided_vars
        if missing:
            raise TemplateResolutionError(
                tool_id=tool_id,
                template=template,
                missing_variables=sorted(missing),
            )

        # Sanitize all provided values
        resolved = template
        for var_name in required_vars:
            raw_value = variables[var_name]
            try:
                safe_value = _sanitize_variable(var_name, raw_value)
            except TemplateResolutionError:
                raise TemplateResolutionError(
                    tool_id=tool_id,
                    template=template,
                    missing_variables=[var_name],
                )
            resolved = resolved.replace("{{" + var_name + "}}", safe_value)

        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "resolve_credential_template",
            extra={
                "service": "pluginregistry",
                "operation": "resolve_template",
                "tool_id": tool_id,
                "latency_ms": latency,
                "status": "success",
            },
        )
        return ResolvedCredential(
            credential_id=resolved,
            tool_id=tool_id,
            n8n_credential_type=tool.n8n_credential_type,
        )

    # ------------------------------------------------------------------
    # Pre-execution validation
    # ------------------------------------------------------------------

    async def validate_plan_tools(
        self,
        plan_registry_version: int,  # noqa: ARG002
        referenced_tool_ids: list[str],
    ) -> ValidationResult:
        """Verify all referenced tools are still active."""
        start = time.monotonic()
        if not referenced_tool_ids:
            version = await self._db.get_current_version()
            return ValidationResult(valid=True, current_version=version)

        tools_map = await self._db.get_tools_by_ids(referenced_tool_ids)
        current_version = await self._db.get_current_version()

        issues: list[ValidationIssue] = []
        for tid in referenced_tool_ids:
            if tid not in tools_map:
                issues.append(ValidationIssue(tool_id=tid, reason="TOOL_NOT_FOUND"))
            elif not tools_map[tid].active:
                issues.append(ValidationIssue(tool_id=tid, reason="TOOL_DEACTIVATED"))

        latency = int((time.monotonic() - start) * 1000)
        logger.info(
            "validate_plan_tools",
            extra={
                "service": "pluginregistry",
                "operation": "validate_plan_tools",
                "referenced_count": len(referenced_tool_ids),
                "issues_count": len(issues),
                "latency_ms": latency,
                "status": "success",
            },
        )
        return ValidationResult(
            valid=len(issues) == 0,
            current_version=current_version,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # Scope verification
    # ------------------------------------------------------------------

    async def verify_scopes(
        self,
        tool_id: str,
        operation_id: str,
        required_scopes: list[str],
    ) -> ScopeVerificationResult:
        """Verify a tool operation supports the required scopes."""
        _validate_tool_id_format(tool_id)
        tool = await self._db.get_tool(tool_id)
        if tool is None or not tool.active:
            raise ToolNotFoundError(tool_id)

        op = tool.operations.get(operation_id)
        if op is None:
            raise ToolNotFoundError(f"{tool_id}.{operation_id}")

        available = set(op.scopes)
        missing = [s for s in required_scopes if s not in available]
        return ScopeVerificationResult(
            supported=len(missing) == 0,
            missing_scopes=missing,
        )
