"""
Plan Outcome Schema

Pydantic model for plan execution outcome data.
Used by PlanWriter and ExecuteOrchestrator.

Reference: GLOBAL_SPEC §Interfaces, PlanLibrary PlanOutcomeDB fields
"""

from typing import Any

from pydantic import BaseModel, Field


class PlanOutcome(BaseModel):
    """
    Plan execution outcome.

    Fields:
        success: Whether the plan executed successfully
        error_type: Error category if failed (None if success)
        error_details: Detailed error information (None if success)
        execution_start: ISO 8601 timestamp of execution start
        execution_end: ISO 8601 timestamp of execution end
        total_steps: Total number of steps in the plan
        failed_step: Step number that failed (None if success)
        context_data: Additional context data (None if not available)
    """

    success: bool = Field(..., description="Whether the plan executed successfully")

    error_type: str | None = Field(default=None, description="Error category if failed")

    error_details: dict[str, Any] | None = Field(
        default=None, description="Detailed error information"
    )

    execution_start: str = Field(..., description="ISO 8601 timestamp of execution start")

    execution_end: str = Field(..., description="ISO 8601 timestamp of execution end")

    total_steps: int = Field(..., ge=0, description="Total number of steps in the plan")

    failed_step: int | None = Field(
        default=None, description="Step number that failed (None if success)"
    )

    context_data: dict[str, Any] | None = Field(default=None, description="Additional context data")
