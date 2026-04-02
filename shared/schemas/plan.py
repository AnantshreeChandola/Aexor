"""
Plan Schema - GLOBAL_SPEC §2.3 Implementation

Pydantic models for the pure agentic execution plan contract.
Used by Planner, PlanWriter, Signer, and ExecuteOrchestrator.

Reference: GLOBAL_SPEC.md §2.3, plan.schema.json
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .intent import Intent
from .policy import ReasoningConfig


class PlanStep(BaseModel):
    """Single execution step in the plan graph.

    Supports three step types:
    - ``api``: deterministic MCP tool invocation (default, backward-compatible)
    - ``llm_reasoning``: LLM-based adaptive reasoning (requires reasoning_config)
    - ``policy_check``: policy evaluation gate (requires policy_ref)
    """

    step: int = Field(..., ge=1, description="Step number (1-indexed)")

    mode: Literal["interactive", "durable"] = Field(..., description="Execution mode")

    role: Literal[
        "Fetcher", "Analyzer", "Watcher", "Resolver", "Booker", "Notifier", "Reasoner"
    ] = Field(..., description="Runtime agent role")

    uses: str = Field(..., min_length=1, description="Tool/connector ID (e.g., 'google.calendar')")

    call: str = Field(..., min_length=1, description="Operation to call (e.g., 'create_event')")

    args: dict[str, Any] = Field(default_factory=dict, description="Arguments for the operation")

    after: list[int] = Field(default_factory=list, description="Dependency step numbers")

    timeout_s: int = Field(default=30, ge=5, le=3600, description="Step timeout in seconds")

    deadline: str | None = Field(
        default=None, description="Hard deadline for durable mode (ISO 8601)"
    )

    gate_id: str | None = Field(default=None, description="HITL gate identifier")

    dry_run: bool = Field(default=True, description="Preview mode flag")

    # --- Hybrid execution fields (all have defaults for backward compat) ---

    type: Literal["api", "llm_reasoning", "policy_check"] = Field(
        default="api", description="Step type for hybrid execution"
    )

    trust_level: Literal["untrusted_input", "trusted"] | None = Field(
        default=None,
        description="Trust tier for llm_reasoning steps. 'untrusted_input' = Tier 1 sandboxed (no tools, strict schema). 'trusted' = Tier 2 agent reasoning. None for non-LLM steps.",
    )

    context_from: list[int] = Field(
        default_factory=list,
        description="Step numbers whose results feed into this step's context",
    )

    can_spawn: bool = Field(
        default=False,
        description="Whether this step may spawn child steps at runtime",
    )

    max_spawned_steps: int | None = Field(
        default=None, ge=1, le=10, description="Max child steps this step may spawn"
    )

    spawned_by: int | None = Field(
        default=None, description="Parent step number if this step was spawned at runtime"
    )

    policy_ref: str | None = Field(default=None, description="PolicyRule ID governing this step")

    reasoning_config: ReasoningConfig | None = Field(
        default=None, description="LLM config for llm_reasoning steps"
    )

    status: Literal["pending", "running", "completed", "failed", "skipped"] = Field(
        default="pending", description="Runtime execution status"
    )

    result: dict[str, Any] | None = Field(
        default=None, description="Step execution result (populated at runtime)"
    )

    error: dict[str, Any] | None = Field(
        default=None, description="Step error details (populated on failure)"
    )

    execute_mode: Literal["preview_only", "execute_only", "both"] | None = Field(
        default=None, description="Execution mode override for this step"
    )


class PlanConstraints(BaseModel):
    """Plan-level constraints."""

    scopes: list[str] = Field(default_factory=list, description="Required OAuth scopes")

    ttl_s: int = Field(default=900, ge=60, le=86400, description="Plan time-to-live in seconds")

    max_retries: int = Field(default=3, ge=0, le=5, description="Maximum retry attempts per step")

    policy_version: int = Field(
        default=0, ge=0, description="Policy ruleset version used during planning"
    )

    model_config = {"extra": "allow"}


class PlanMeta(BaseModel):
    """Plan metadata."""

    created_at: str = Field(..., description="ISO 8601 timestamp")

    author: str = Field(default="planner@system", description="Plan author")

    version: str = Field(default="v2.0.0", description="Plan schema semantic version")

    canonical_hash: str = Field(..., description="SHA-256 hash of canonical plan")

    hash_algo: Literal["sha256"] = Field(
        default="sha256", description="Hash algorithm used for canonical_hash"
    )

    model_config = {"extra": "allow"}


class Plan(BaseModel):
    """
    Pure agentic execution plan contract (GLOBAL_SPEC §2.3).

    Fields:
        plan_id: ULID unique plan identifier (26 chars)
        intent: Original user intent
        trace_id: Distributed tracing correlation ID
        graph: Execution steps graph
        constraints: Plan-level constraints
        plugins: List of plugin IDs used in this plan
        meta: Plan metadata (created_at, author, canonical_hash)
        plan_revision: Monotonic revision counter for runtime graph mutations
    """

    plan_id: str = Field(
        ..., min_length=26, max_length=26, description="ULID unique plan identifier"
    )

    intent: Intent = Field(..., description="Original user intent")

    trace_id: str | None = Field(default=None, description="Distributed tracing correlation ID")

    graph: list[PlanStep] = Field(
        ..., min_length=1, max_length=100, description="Execution steps graph"
    )

    constraints: PlanConstraints = Field(
        default_factory=PlanConstraints, description="Plan-level constraints"
    )

    plugins: list[str] = Field(
        default_factory=list, description="List of plugin IDs used in this plan"
    )

    meta: PlanMeta = Field(..., description="Plan metadata")

    plan_revision: int = Field(
        default=0, ge=0, description="Monotonic revision counter (0 = original plan)"
    )
