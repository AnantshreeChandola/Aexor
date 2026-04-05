"""
Audit Domain Models

Pydantic v2 models for audit events, query parameters, query results,
and custom exceptions.

Reference: LLD.md Section 5
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# AuditEventType enum (11 values)
# ---------------------------------------------------------------------------


class AuditEventType(str, Enum):
    """All auditable event types in the system."""

    EXECUTION_STARTED = "execution_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_EXPIRED = "approval_expired"
    POLICY_ATTESTATION = "policy_attestation"
    POLICY_DENIAL = "policy_denial"
    EXECUTION_STUCK = "execution_stuck"
    EXECUTION_TIMEOUT = "execution_timeout"


# ---------------------------------------------------------------------------
# AuditEvent (core entity)
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """Immutable audit event record.

    Each event captures a single system action with plan_id correlation.
    event_id is a 26-char ULID for chronological ordering.
    """

    event_id: str = Field(..., min_length=26, max_length=26)
    event_type: AuditEventType
    plan_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None
    step_number: int | None = None
    event_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Query models
# ---------------------------------------------------------------------------


class AuditQueryParams(BaseModel):
    """Parameters for querying audit events."""

    plan_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None
    event_type: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class AuditQueryResult(BaseModel):
    """Paginated query result for audit events."""

    events: list[AuditEvent]
    next_cursor: str | None = None
    total_count: int


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuditError(Exception):
    """Base exception for all Audit errors."""


class AuditDatabaseError(AuditError):
    """Raised when a database operation fails."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Audit database error: {detail}")


class AuditBufferOverflowError(AuditError):
    """Raised when the in-memory buffer exceeds max capacity."""

    def __init__(self, dropped: int) -> None:
        self.dropped = dropped
        super().__init__(f"Audit buffer overflow: {dropped} oldest events dropped")
