"""
Policy Schema — GLOBAL_SPEC §2.3.1, §2.4.1, §2.9

Pydantic models for the PolicyEngine contract: reasoning configuration,
policy rules, evaluation decisions, and runtime attestations.

Used by PolicyEngine, Planner, Signer, PlanWriter, and ExecuteOrchestrator.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReasoningConfig(BaseModel):
    """LLM configuration for reasoning steps (GLOBAL_SPEC §2.3.1).

    Attached to PlanSteps of type ``llm_reasoning`` to specify
    which model, temperature, and prompt template to use.
    """

    model: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="LLM model identifier for reasoning",
    )

    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Sampling temperature",
    )

    max_tokens: int = Field(
        default=2048,
        ge=256,
        le=8192,
        description="Maximum output tokens",
    )

    system_prompt_ref: str = Field(
        ...,
        description="Prompt template reference (e.g. 'reasoner.flight_analysis')",
    )

    output_schema_ref: str | None = Field(
        default=None,
        description="Optional JSON schema reference for structured output",
    )


class PolicyRule(BaseModel):
    """Policy definition (GLOBAL_SPEC §2.9).

    Defines constraints that the PolicyEngine evaluates against
    plan steps and agent roles at runtime.
    """

    policy_id: str = Field(..., description="Unique policy identifier")

    name: str = Field(..., description="Human-readable policy name")

    version: int = Field(default=1, ge=1, description="Policy version number")

    scope: Literal["step", "role", "system"] = Field(..., description="Evaluation scope")

    allowed_tools: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Permitted tool IDs (wildcard '*' = all)",
    )

    allowed_roles: list[str] = Field(
        default_factory=list,
        description="Roles this policy applies to",
    )

    max_spawned_steps: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Max steps a single step may spawn",
    )

    require_approval: bool = Field(
        default=False,
        description="Whether HITL approval is required",
    )

    data_access: list[str] = Field(
        default_factory=list,
        description="Allowed data access scopes",
    )

    forbidden_actions: list[str] = Field(
        default_factory=list,
        description="Explicitly forbidden operation IDs",
    )

    token_budget: int = Field(
        default=8192,
        ge=256,
        description="Token budget for LLM reasoning under this policy",
    )


class PolicyDecision(BaseModel):
    """Policy evaluation result (GLOBAL_SPEC §2.9).

    Returned by PolicyEngine after evaluating a PolicyRule
    against a plan step or spawning request.
    """

    allowed: bool = Field(..., description="Whether the action is permitted")

    requires_approval: bool = Field(
        default=False,
        description="Whether HITL approval is needed before execution",
    )

    reason: str = Field(..., description="Human-readable explanation")

    violations: list[str] = Field(
        default_factory=list,
        description="List of violated policy clauses",
    )


class PolicyAttestation(BaseModel):
    """Runtime audit record for spawned steps (GLOBAL_SPEC §2.4.1).

    Created by PolicyEngine when a step spawns child steps at
    runtime. Stored alongside the plan signature for audit.
    """

    attestation_id: str = Field(
        ...,
        min_length=26,
        max_length=26,
        description="ULID unique attestation identifier",
    )

    plan_id: str = Field(
        ...,
        min_length=26,
        max_length=26,
        description="ULID of the plan being attested",
    )

    plan_revision: int = Field(..., ge=1, description="Plan revision number")

    spawned_by_step: int = Field(..., ge=1, description="Step number that triggered spawning")

    new_steps: list[dict[str, Any]] = Field(
        ..., description="Serialized PlanStep dicts for the spawned steps"
    )

    policy_id: str = Field(..., description="Policy ID that authorized spawning")

    policy_version: int = Field(..., ge=1, description="Version of the policy used")

    decision: PolicyDecision = Field(..., description="The policy evaluation result")

    attested_at: str = Field(..., description="ISO 8601 timestamp of attestation")
