"""
Scheduler Domain Models

Pydantic models for scheduled plan management — request/response schemas,
recurrence configuration, and domain exceptions.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Recurrence Configuration
# ---------------------------------------------------------------------------


class RecurrenceConfig(BaseModel):
    """UI-friendly recurrence descriptor."""

    frequency: str = Field(
        ..., pattern="^(hourly|daily|weekly|monthly)$",
        description="Recurrence frequency",
    )
    interval: int = Field(default=1, ge=1, description="Every N units")
    days_of_week: list[int] | None = Field(
        default=None, description="0=Mon..6=Sun, used when frequency=weekly",
    )
    day_of_month: int | None = Field(
        default=None, ge=1, le=31, description="Day of month (1-31)",
    )
    time_of_day: str | None = Field(
        default=None, pattern=r"^\d{2}:\d{2}$", description="HH:MM",
    )
    end_date: datetime | None = Field(default=None, description="Stop recurring after this date")
    max_runs: int | None = Field(default=None, ge=1, description="Max executions, None=unlimited")


# ---------------------------------------------------------------------------
# Domain Model
# ---------------------------------------------------------------------------


class ScheduledPlan(BaseModel):
    """Represents a scheduled plan (maps to DB table)."""

    id: UUID
    user_id: UUID
    name: str
    intent_type: str
    skeleton_json: dict
    entities_json: dict = Field(default_factory=dict)
    constraints_json: dict = Field(default_factory=dict)
    schedule_type: str  # "once" or "recurring"
    scheduled_at: datetime | None = None
    cron_expression: str | None = None
    recurrence_config: dict | None = None
    timezone: str = "UTC"
    status: str = "active"
    approval_mode: str = "auto_approve"
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    run_count: int = 0
    max_runs: int | None = None
    last_error: dict | None = None
    source_plan_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------


class CreateScheduledPlanRequest(BaseModel):
    """POST body for creating a scheduled plan."""

    name: str = Field(..., max_length=255)
    intent_type: str = Field(..., max_length=64)
    skeleton_json: dict
    entities_json: dict = Field(default_factory=dict)
    constraints_json: dict = Field(default_factory=dict)
    schedule_type: str = Field(..., pattern="^(once|recurring)$")
    scheduled_at: datetime | None = None  # Required for schedule_type="once"
    recurrence_config: RecurrenceConfig | None = None  # Required for "recurring"
    timezone: str = Field(default="UTC", max_length=64)
    approval_mode: str | None = Field(
        default=None,
        pattern="^(auto_approve|notify_and_wait)$",
        description="If None, inferred from intent_type: read-only → auto_approve, write → notify_and_wait",
    )
    max_runs: int | None = Field(default=None, ge=1)
    source_plan_id: str | None = None


class UpdateScheduledPlanRequest(BaseModel):
    """PATCH body for updating a scheduled plan."""

    name: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, pattern="^(active|paused|cancelled)$")
    entities_json: dict | None = None
    scheduled_at: datetime | None = None
    recurrence_config: RecurrenceConfig | None = None
    timezone: str | None = Field(default=None, max_length=64)
    approval_mode: str | None = Field(
        default=None, pattern="^(auto_approve|notify_and_wait)$",
    )
    max_runs: int | None = None


class ScheduledPlanResponse(BaseModel):
    """Single scheduled plan response."""

    schedule: ScheduledPlan


class ScheduledPlanListResponse(BaseModel):
    """List of scheduled plans response."""

    schedules: list[ScheduledPlan]
    total: int


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScheduledPlanNotFoundError(Exception):
    """Raised when a scheduled plan is not found or not accessible."""


class ScheduleValidationError(Exception):
    """Raised for invalid schedule configuration."""
