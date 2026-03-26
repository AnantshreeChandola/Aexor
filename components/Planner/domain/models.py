"""
Planner Domain Models

PlannerResult Pydantic model and error classes for the Planner component.
All field names match GLOBAL_SPEC v2.2 Section 2.3 / 2.4 contracts.

Reference: LLD.md SS5.1, SS5.2
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from shared.schemas.plan import Plan
from shared.schemas.signature import Signature


class PlannerResult(BaseModel):
    """Result of plan generation."""

    plan: Plan
    signature: Signature
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
        description="PluginRegistry version used for this plan",
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
