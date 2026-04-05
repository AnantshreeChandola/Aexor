"""
PreviewOrchestrator Domain Models

Pydantic models for preview request/response contracts,
step results, and custom exceptions.

Reference: LLD.md Section 5.1, 5.2
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from shared.schemas.plan import Plan

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class PreviewRequest(BaseModel):
    """Input contract for plan preview."""

    plan: Plan
    user_id: str = Field(..., min_length=1, description="User requesting preview")
    trace_id: str = Field(..., min_length=1, description="Distributed tracing ID")


class PreviewStepResult(BaseModel):
    """Result of previewing a single step."""

    step: int = Field(..., ge=1, description="Step number")
    status: Literal["completed", "failed", "deferred", "skipped"] = Field(
        ..., description="Preview outcome for this step"
    )
    result: dict[str, Any] | None = Field(
        default=None, description="Step result data (if completed)"
    )
    error: dict[str, Any] | None = Field(default=None, description="Error details (if failed)")
    latency_ms: int = Field(default=0, ge=0, description="Step execution time in ms")
    reason: str | None = Field(
        default=None,
        description=(
            "Why step was deferred/skipped "
            "(e.g., 'llm_reasoning', 'non_previewable', "
            "'dependency_failed', 'gated')"
        ),
    )


class PreviewResult(BaseModel):
    """Preview response wrapper (GLOBAL_SPEC S2.5)."""

    plan_id: str = Field(
        ...,
        min_length=26,
        max_length=26,
        description="ULID plan identifier",
    )
    normalized: dict[str, Any] = Field(
        ...,
        description="Normalized preview payload with step results",
    )
    source: Literal["preview"] = Field(default="preview", description="Always 'preview'")
    can_execute: bool = Field(
        ...,
        description="Whether execution is possible after this preview",
    )
    partial: bool = Field(
        default=False,
        description="True if some previewable steps failed",
    )
    cached_state_key: str | None = Field(
        default=None,
        description="Redis cache key for preview state",
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional supporting evidence",
    )


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class PreviewError(Exception):
    """Base error for PreviewOrchestrator."""


class PreviewStepError(PreviewError):
    """A preview step failed (non-fatal -- used for logging)."""

    def __init__(self, step: int, reason: str) -> None:
        self.step = step
        super().__init__(f"Preview step {step} failed: {reason}")
