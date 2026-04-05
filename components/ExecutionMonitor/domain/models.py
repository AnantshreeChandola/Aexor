"""
ExecutionMonitor Domain Models

Pydantic models for tracker records, request/response types,
notification payloads, and custom exceptions.

Reference: Project_HLD.md §2.14
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Tracker record (maps to execution_tracker table)
# ---------------------------------------------------------------------------


class TrackerRecord(BaseModel):
    """Pydantic model mapping to the execution_tracker table."""

    tracker_id: str = Field(..., description="UUID primary key")
    plan_id: str = Field(..., min_length=26, max_length=26)
    user_id: str = Field(..., min_length=1, max_length=255)
    trace_id: str = Field(..., min_length=1, max_length=255)
    status: str = Field(default="running", max_length=32)
    total_steps: int = Field(default=0, ge=0)
    completed_steps: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=64)
    error_details: dict[str, Any] | None = Field(default=None)
    notification_sent: bool = Field(default=False)
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RegisterExecutionRequest(BaseModel):
    """Input for TrackerService.register()."""

    plan_id: str = Field(..., min_length=26, max_length=26)
    user_id: str = Field(..., min_length=1, max_length=255)
    trace_id: str = Field(..., min_length=1, max_length=255)
    total_steps: int = Field(..., ge=0)


class ProgressUpdate(BaseModel):
    """Input for TrackerService.report_progress()."""

    plan_id: str = Field(..., min_length=26, max_length=26)
    completed_steps: int = Field(..., ge=0)


class CompleteExecutionRequest(BaseModel):
    """Input for TrackerService.complete()."""

    plan_id: str = Field(..., min_length=26, max_length=26)
    success: bool = Field(...)
    error_type: str | None = Field(default=None, max_length=64)
    error_details: dict[str, Any] | None = Field(default=None)


# ---------------------------------------------------------------------------
# Notification payload
# ---------------------------------------------------------------------------


class UserNotification(BaseModel):
    """Notification payload sent when an execution is stuck or timed out."""

    plan_id: str = Field(..., min_length=26, max_length=26)
    user_id: str = Field(..., min_length=1, max_length=255)
    trace_id: str = Field(...)
    failure_type: Literal["stuck", "timeout"] = Field(...)
    total_steps: int = Field(default=0, ge=0)
    completed_steps: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    message: str = Field(default="")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MonitorError(Exception):
    """Base exception for all ExecutionMonitor errors."""


class TrackerNotFoundError(MonitorError):
    """Raised when a tracker record is not found for the given plan_id."""

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"Tracker not found for plan_id '{plan_id}'")


class TrackerDatabaseError(MonitorError):
    """Raised when a database operation fails."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Tracker database error: {detail}")
