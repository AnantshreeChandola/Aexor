"""
ApprovalGate Domain Models

Pydantic models for approval request/response contracts,
approval state, and custom exceptions.

Reference: LLD.md Section 5.1, 5.2
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    """Input contract for plan approval."""

    plan_id: str = Field(..., min_length=26, max_length=26, description="ULID plan identifier")
    user_id: str = Field(..., min_length=1, description="User approving the plan")
    gate_id: str = Field(
        default="gate-A",
        pattern=r"^gate-[A-Za-z0-9]+$",
        description="HITL gate identifier",
    )
    scopes: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Approved OAuth scopes for this execution",
    )
    selected_option: dict[str, Any] | None = Field(
        default=None,
        description="Optional user selection from preview (e.g., chosen time slot)",
    )
    trace_id: str = Field(default="", description="Distributed tracing ID")
    policy_matched: bool = Field(
        default=True,
        description="Whether a stored policy matched. False triggers learn_from_approval.",
    )
    role: str | None = Field(
        default=None,
        description="Role of the spawned step (for learn_from_approval).",
    )
    tool: str | None = Field(
        default=None,
        description="Tool of the spawned step (for learn_from_approval).",
    )


class ApprovalToken(BaseModel):
    """Issued approval token (GLOBAL_SPEC S2.7)."""

    token: str = Field(..., description="JWT approval token string")
    plan_id: str = Field(..., min_length=26, max_length=26, description="ULID of the approved plan")
    user_id: str = Field(..., description="User who approved")
    gate_id: str = Field(..., description="Gate this token covers")
    scopes: list[str] = Field(..., description="Approved scopes")
    exp: str = Field(..., description="Expiration timestamp (ISO 8601)")
    iat: str = Field(..., description="Issued-at timestamp (ISO 8601)")
    token_id: str = Field(..., description="Unique token identifier (ULID)")


class ApprovalState(BaseModel):
    """Full approval state returned by get_approval_state()."""

    plan_id: str = Field(..., min_length=26, max_length=26, description="ULID plan identifier")
    gate_id: str = Field(..., description="Gate identifier")
    status: Literal["approved", "pending", "expired"] = Field(
        ..., description="Gate approval status"
    )
    token_claims: dict[str, Any] = Field(default_factory=dict, description="Decoded JWT claims")
    preview_state: dict[int, dict[str, Any]] | None = Field(
        default=None, description="Cached preview step results"
    )
    selected_option: dict[str, Any] | None = Field(
        default=None, description="User selection from preview"
    )
    approved_at: str = Field(..., description="Approval timestamp (ISO 8601)")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ApprovalError(Exception):
    """Base error for ApprovalGate."""


class ApprovalConfigError(ApprovalError):
    """Configuration error (e.g., missing JWT secret)."""


class InvalidGateError(ApprovalError):
    """Invalid gate_id submitted."""

    def __init__(self, gate_id: str) -> None:
        self.gate_id = gate_id
        super().__init__(f"Invalid gate_id: {gate_id}")


class TokenExpiredError(ApprovalError):
    """Token has expired."""


class TokenValidationError(ApprovalError):
    """Token failed validation (signature, plan_id mismatch, scope mismatch)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Token validation failed: {reason}")


class TokenConsumedError(ApprovalError):
    """Token has already been consumed (single-use enforcement)."""
