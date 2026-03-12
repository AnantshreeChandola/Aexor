"""
PluginRegistry Domain Models

Pydantic models for tools, operations, and request/response contracts.
Custom exceptions for domain-specific error handling.

Reference: LLD.md Section 5.4
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class OperationModel(BaseModel):
    """A single operation (capability) of a tool."""

    operation_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,126}$")
    n8n_node: str = Field(min_length=1, max_length=255)
    previewable: bool = False
    idempotent: bool = False
    scopes: list[str] = Field(default_factory=list)
    compensation: str | None = None


class ToolModel(BaseModel):
    """A registered external integration tool."""

    tool_id: str = Field(pattern=r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1, max_length=255)
    credential_template: str = Field(max_length=512)
    n8n_credential_type: str = Field(max_length=128)
    active: bool = True
    operations: dict[str, OperationModel] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RegistryVersionModel(BaseModel):
    """A version counter entry for the registry."""

    version: int = Field(ge=0)
    created_at: datetime
    change_summary: str = Field(max_length=512)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateToolRequest(BaseModel):
    """Request to create a new tool."""

    tool_id: str = Field(pattern=r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=1, max_length=255)
    credential_template: str = Field(max_length=512)
    n8n_credential_type: str = Field(max_length=128)
    operations: dict[str, OperationModel]


class UpdateToolRequest(BaseModel):
    """Request to update an existing tool -- all fields optional."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    credential_template: str | None = Field(default=None, max_length=512)
    n8n_credential_type: str | None = Field(default=None, max_length=128)
    operations: dict[str, OperationModel] | None = None


class ResolveCredentialRequest(BaseModel):
    """Request to resolve a credential template."""

    tool_id: str
    variables: dict[str, str]


class ValidatePlanToolsRequest(BaseModel):
    """Request for pre-execution validation."""

    plan_registry_version: int
    referenced_tool_ids: list[str]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CatalogResponse(BaseModel):
    """Paginated catalog response."""

    tools: list[ToolModel]
    registry_version: int
    total: int
    page: int
    page_size: int


class ResolvedCredential(BaseModel):
    """Resolved credential ID output."""

    credential_id: str
    tool_id: str
    n8n_credential_type: str


class ValidationIssue(BaseModel):
    """A single issue found during pre-execution validation."""

    tool_id: str
    reason: str


class ValidationResult(BaseModel):
    """Pre-execution validation result."""

    valid: bool
    current_version: int
    issues: list[ValidationIssue] = Field(default_factory=list)


class CreateToolResponse(BaseModel):
    """Response after creating a tool."""

    tool_id: str
    registry_version: int
    created_at: datetime


class UpdateToolResponse(BaseModel):
    """Response after updating a tool."""

    tool_id: str
    registry_version: int
    updated_at: datetime


class DeactivateToolResponse(BaseModel):
    """Response after deactivating a tool."""

    tool_id: str
    active: bool = False
    registry_version: int
    deactivated_at: datetime


class ScopeVerificationResult(BaseModel):
    """Result of scope verification for a tool operation."""

    supported: bool
    missing_scopes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ToolNotFoundError(Exception):
    """Tool ID does not exist or is inactive."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(f"Tool '{tool_id}' not found")


class ToolAlreadyExistsError(Exception):
    """Tool ID already exists in the registry."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(f"Tool '{tool_id}' already exists")


class SchemaValidationError(Exception):
    """Tool definition fails schema validation."""

    def __init__(self, details: str) -> None:
        self.details = details
        super().__init__(f"Schema validation failed: {details}")


class TemplateResolutionError(Exception):
    """Missing required variables in credential template."""

    def __init__(
        self,
        tool_id: str,
        template: str,
        missing_variables: list[str],
    ) -> None:
        self.tool_id = tool_id
        self.template = template
        self.missing_variables = missing_variables
        missing = ", ".join(missing_variables)
        super().__init__(
            f"Missing variable(s) '{missing}' in credential template "
            f"for tool '{tool_id}'"
        )


class ScopeNotSupportedError(Exception):
    """Tool operation does not support required scopes."""

    def __init__(
        self,
        tool_id: str,
        operation_id: str,
        missing_scopes: list[str],
    ) -> None:
        self.tool_id = tool_id
        self.operation_id = operation_id
        self.missing_scopes = missing_scopes
        super().__init__(
            f"Scopes {missing_scopes} not supported by "
            f"'{tool_id}.{operation_id}'"
        )


class InvalidToolIdFormatError(Exception):
    """Tool ID does not match provider.service format."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id
        super().__init__(
            f"Invalid tool_id format: '{tool_id}'. "
            f"Expected 'provider.service' (lowercase)."
        )
