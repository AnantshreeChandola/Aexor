"""
Planner Domain Models

PlannerResult Pydantic model and error classes for the Planner component.
All field names match GLOBAL_SPEC v2.2 Section 2.3 / 2.4 contracts.

Reference: LLD.md SS5.1, SS5.2
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from shared.schemas.plan import Plan


class EntityRequirement(BaseModel):
    """A single entity required for an intent type."""

    name: str = Field(..., description="Entity name, e.g. 'attendee'")
    description: str = Field(
        ..., description="Human-readable description, e.g. 'Who should attend?'"
    )
    required: bool = Field(default=True, description="Essential vs optional entity")
    default_preference_key: str | None = Field(
        default=None,
        description="ProfileStore preference key for this entity, e.g. 'default_meeting_duration'",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative field names the LLM may use, e.g. ['duration_minutes'] for 'duration'",
    )


class RequiredEntitiesResult(BaseModel):
    """Result from Planner's get_required_entities() lightweight query."""

    intent_type: str
    resolved_tools: list[str] = Field(
        default_factory=list,
        description="Tool IDs from the registry that can fulfill this intent",
    )
    required_entities: list[EntityRequirement] = Field(default_factory=list)
    missing_entities: list[EntityRequirement] = Field(
        default_factory=list,
        description="Subset of required_entities not yet provided",
    )


class PlannerResult(BaseModel):
    """Result of plan generation."""

    plan: Plan
    fallback_level: int = Field(
        ...,
        ge=1,
        le=4,
        description=(
            "Which fallback level produced this plan "
            "(1=primary, 2=secondary, 3=template, 4=minimal)"
        ),
    )
    context_degraded: bool = Field(
        default=False,
        description="True if ContextRAG returned with degraded sources",
    )
    generation_duration_ms: int = Field(
        ...,
        ge=0,
        description="Total wall-clock time for generate_plan() in ms",
    )
    registry_version: int = Field(
        ...,
        description="ToolCatalog version used for this plan",
    )


# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class PlannerError(Exception):
    """Base error for Planner component."""


class PlanValidationError(PlannerError):
    """LLM output failed validation."""

    def __init__(
        self,
        layer: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        self.layer = layer  # "json_parse", "schema", "business_rules"
        self.message = message
        self.details = details or {}
        super().__init__(f"Validation failed at {layer}: {message}")


class CircuitOpenError(PlannerError):
    """Circuit breaker is open for the requested model."""

    def __init__(self, model: str) -> None:
        self.model = model
        super().__init__(f"Circuit breaker open for model: {model}")


class PlanGenerationError(PlannerError):
    """All fallback levels exhausted (should never happen)."""


class LLMCallError(PlannerError):
    """LLM API call failed."""

    def __init__(self, model: str, reason: str) -> None:
        self.model = model
        self.reason = reason
        super().__init__(f"LLM call failed ({model}): {reason}")


class ToolNotAvailableError(PlannerError):
    """Intent requires tools that are not registered in the ToolCatalog."""

    def __init__(self, intent_type: str, required_tools: list[str]) -> None:
        self.intent_type = intent_type
        self.required_tools = required_tools
        tools_str = ", ".join(required_tools) if required_tools else "unknown"
        super().__init__(
            f"No registered tools can fulfill intent '{intent_type}'. Required tools: {tools_str}"
        )
