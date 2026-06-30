"""
Plan Metrics Schema

Pydantic model for plan execution performance metrics.
Used by PlanWriter and ExecuteOrchestrator.

Reference: GLOBAL_SPEC §Interfaces, PlanLibrary PlanMetricsDB fields
"""

from typing import Any

from pydantic import BaseModel, Field


class PlanMetrics(BaseModel):
    """
    Plan execution performance metrics.

    Fields:
        preview_latency_ms: Time to generate plan preview (None if not previewed)
        execute_latency_ms: Total execution time in milliseconds
        step_timings: Per-step timing breakdown (None if not available)
        resource_usage: Resource usage data (None if not tracked)
    """

    preview_latency_ms: int | None = Field(
        default=None, description="Time to generate plan preview in ms"
    )

    execute_latency_ms: int = Field(..., description="Total execution time in milliseconds")

    step_timings: list[dict[str, Any]] | None = Field(
        default=None, description="Per-step timing breakdown"
    )

    resource_usage: dict[str, Any] | None = Field(default=None, description="Resource usage data")
