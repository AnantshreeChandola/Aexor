"""
PlanLibrary Domain Models

Pydantic models for plans, outcomes, metrics, requests, and responses.
Error classes for PlanLibrary-specific exceptions.

Reference: LLD.md, SPEC.md
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

# --- Constants ---

ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
MAX_PLAN_SIZE_BYTES = 1_048_576  # 1MB
MAX_STEP_COUNT = 100


# --- Error Classes ---


class PlanLibraryError(Exception):
    """Base exception for PlanLibrary operations."""

    pass


class DuplicatePlanError(PlanLibraryError):
    """Raised when attempting to store duplicate plan ID."""

    def __init__(self, plan_id: str):
        self.plan_id = plan_id
        super().__init__(f"Plan with ID {plan_id} already exists")


class PlanTooLargeError(PlanLibraryError):
    """Raised when plan exceeds size limits (1MB, 100 steps)."""

    def __init__(self, plan_id: str, reason: str):
        self.plan_id = plan_id
        self.reason = reason
        super().__init__(f"Plan {plan_id} too large: {reason}")


class InvalidQueryError(PlanLibraryError):
    """Raised when query parameters are invalid."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Invalid query: {reason}")


class PlanNotFoundError(PlanLibraryError):
    """Raised when a plan is not found."""

    def __init__(self, plan_id: str):
        self.plan_id = plan_id
        super().__init__(f"Plan {plan_id} not found")


# --- Database Domain Models ---


class PlanDB(BaseModel):
    """
    Database model for plan records.

    Maps to PlanTable in shared/database/models.py.
    """

    plan_id: str = Field(..., min_length=26, max_length=26)
    canonical_json: dict[str, Any]
    signature_data: dict[str, Any]
    intent_type: str = Field(..., max_length=64)
    step_count: int = Field(..., ge=0)
    plan_hash: str = Field(..., max_length=64)
    size_bytes: int = Field(..., ge=0)
    created_at: datetime
    stored_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("plan_id")
    @classmethod
    def validate_plan_id(cls, v: str) -> str:
        """Validate plan_id is valid ULID format."""
        if not ULID_PATTERN.match(v):
            raise ValueError(f"plan_id must be valid ULID format, got: {v}")
        return v

    @field_validator("step_count")
    @classmethod
    def validate_step_count(cls, v: int) -> int:
        """Validate step count within limits."""
        if v > MAX_STEP_COUNT:
            raise ValueError(f"step_count exceeds maximum of {MAX_STEP_COUNT}")
        return v

    @field_validator("size_bytes")
    @classmethod
    def validate_size_bytes(cls, v: int) -> int:
        """Validate plan size within limits."""
        if v > MAX_PLAN_SIZE_BYTES:
            raise ValueError(f"size_bytes exceeds maximum of {MAX_PLAN_SIZE_BYTES}")
        return v


class PlanOutcomeDB(BaseModel):
    """
    Database model for plan outcome records.

    Maps to PlanOutcomeTable in shared/database/models.py.
    """

    outcome_id: UUID = Field(default_factory=uuid4)
    plan_id: str = Field(..., min_length=26, max_length=26)
    success: bool
    error_type: str | None = None
    error_details: dict[str, Any] | None = None
    execution_start: datetime
    execution_end: datetime
    total_steps: int = Field(..., ge=0)
    failed_step: int | None = None
    context_data: dict[str, Any] | None = None
    final_graph_json: dict[str, Any] | None = None
    plan_revision: int = Field(default=0, ge=0)


class PlanMetricsDB(BaseModel):
    """
    Database model for plan metrics records.

    Maps to PlanMetricsTable in shared/database/models.py.
    """

    metrics_id: UUID = Field(default_factory=uuid4)
    plan_id: str = Field(..., min_length=26, max_length=26)
    preview_latency_ms: int | None = None
    execute_latency_ms: int = Field(..., ge=0)
    step_timings: dict[str, Any] | None = None
    resource_usage: dict[str, Any] | None = None


# --- Request/Response Models ---


class StorePlanRequest(BaseModel):
    """Request model for storing an executed plan."""

    plan: dict[str, Any] = Field(..., description="Plan JSON with plan_id, graph, meta")
    signature: dict[str, Any] = Field(
        default_factory=dict, description="Legacy signature data (unused)"
    )
    outcome: dict[str, Any] = Field(..., description="Execution outcome data")
    metrics: dict[str, Any] = Field(..., description="Performance metrics data")

    @field_validator("plan")
    @classmethod
    def validate_plan_fields(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Validate required plan fields are present."""
        required = {"plan_id", "graph", "meta"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Plan missing required fields: {missing}")
        return v


class StorePlanResponse(BaseModel):
    """Response model for plan storage operations."""

    status: Literal["ok"] = "ok"
    plan_id: str
    stored_at: datetime


class QueryPlansRequest(BaseModel):
    """Request model for querying plans by intent."""

    intent_type: str = Field(..., min_length=1, max_length=64)
    success_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=1000)
    recency_days: int | None = Field(default=None, ge=1)

    @field_validator("intent_type")
    @classmethod
    def validate_intent_type(cls, v: str) -> str:
        """Validate intent type format."""
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("intent_type must be alphanumeric with underscores/hyphens")
        return v


class PlanPattern(BaseModel):
    """Plan pattern summary for analytics and evidence items."""

    plan_id: str
    intent_type: str
    success_rate: float = Field(..., ge=0.0, le=1.0)
    avg_execution_time_ms: float = Field(..., ge=0.0)
    steps_count: int = Field(..., ge=0)
    pattern_summary: str


class PerformanceTrends(BaseModel):
    """Performance trend analytics."""

    intent_type: str | None = None
    avg_preview_latency_ms: float = 0.0
    avg_execute_latency_ms: float = 0.0
    total_plans: int = 0
    success_rate: float = 0.0
    trend_period_days: int = 30


# --- Standard Response Wrappers ---


class ErrorResponse(BaseModel):
    """Standard error response format."""

    status: Literal["error"] = "error"
    error_code: str
    message: str
    details: dict[str, Any] | None = None


class SuccessResponse(BaseModel):
    """Standard success response wrapper."""

    status: Literal["ok"] = "ok"
    data: Any
    tier: int = Field(default=3)  # PlanLibrary is Tier 3


# --- Utility Functions ---


def canonicalize_plan(plan_data: dict[str, Any]) -> str:
    """
    Canonicalize plan JSON for deterministic hashing.

    Sorted keys, no whitespace, consistent float formatting.

    Args:
        plan_data: Plan dictionary to canonicalize

    Returns:
        Canonical JSON string
    """
    return json.dumps(plan_data, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(canonical_json: str) -> str:
    """
    Compute SHA-256 hash of canonical plan JSON.

    Args:
        canonical_json: Canonicalized JSON string

    Returns:
        SHA-256 hex digest
    """
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
