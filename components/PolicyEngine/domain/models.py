"""
PolicyEngine Domain Models

Pydantic models for database mapping, spawn request/response,
and custom exceptions for policy evaluation.

Reference: GLOBAL_SPEC §2.9, §2.4.1
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Database mapping models
# ---------------------------------------------------------------------------


class PolicyDB(BaseModel):
    """Pydantic model mapping to the policies table."""

    policy_id: str = Field(..., max_length=128)
    name: str = Field(..., max_length=256)
    version: int = Field(default=1, ge=1)
    scope: str = Field(...)  # step, role, system
    allowed_tools: list[str] = Field(default_factory=lambda: ["*"])
    allowed_roles: list[str] = Field(default_factory=list)
    max_spawned_steps: int = Field(default=3, ge=0, le=10)
    require_approval: bool = Field(default=False)
    data_access: list[str] = Field(default_factory=lambda: ["tier1"])
    forbidden_actions: list[str] = Field(default_factory=list)
    token_budget: int = Field(default=8192, ge=256)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PolicyAttestationDB(BaseModel):
    """Pydantic model mapping to the policy_attestations table."""

    attestation_id: str = Field(..., min_length=26, max_length=26)
    plan_id: str = Field(..., min_length=26, max_length=26)
    plan_revision: int = Field(..., ge=1)
    spawned_by_step: int = Field(..., ge=1)
    new_steps: list[dict[str, Any]] = Field(...)
    policy_id: str = Field(..., max_length=128)
    policy_version: int = Field(..., ge=1)
    decision: dict[str, Any] = Field(...)
    attested_at: datetime | None = None


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class SpawnRequest(BaseModel):
    """Input to PolicyService.evaluate_spawn().

    Contains all information needed to evaluate whether a step may
    spawn child steps under the governing policy.
    """

    plan_id: str = Field(..., min_length=26, max_length=26)
    plan_revision: int = Field(..., ge=1)
    spawning_step: int = Field(..., ge=1, description="Step number requesting to spawn")
    proposed_steps: list[dict[str, Any]] = Field(
        ..., min_length=1, description="Serialized PlanStep dicts for proposed child steps"
    )
    current_step_count: int = Field(
        ..., ge=0, description="Current total number of steps in the plan"
    )
    plan_plugins: list[str] = Field(
        default_factory=list, description="Allowed plugin IDs from the parent plan"
    )
    policy_ref: str | None = Field(
        default=None, description="Explicit policy ID to evaluate against"
    )


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class PolicyEngineError(Exception):
    """Base exception for all PolicyEngine errors."""


class PolicyNotFoundError(PolicyEngineError):
    """Raised when the requested policy does not exist."""

    def __init__(self, policy_id: str, version: int | None = None) -> None:
        self.policy_id = policy_id
        self.version = version
        msg = f"Policy '{policy_id}' not found"
        if version is not None:
            msg += f" at version {version}"
        super().__init__(msg)


class PolicyEvaluationError(PolicyEngineError):
    """Raised when policy evaluation encounters an internal error."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Policy evaluation failed: {detail}")


class AttestationError(PolicyEngineError):
    """Raised when attestation creation or retrieval fails."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Attestation error: {detail}")
