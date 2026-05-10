"""
Skeleton Schema — Visual Plan Builder models.

Pydantic models for the plan skeleton flow:
  1. SkeletonRequest  — client sends user message
  2. PlanSkeleton     — lightweight plan structure with entity fields + DAG
  3. SkeletonResponse — skeleton + session_id + partial entities

Reference: Plan-First Entity Collection design
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkeletonEntityField(BaseModel):
    """A single entity field shown in the builder form."""

    name: str = Field(..., description="Canonical entity key (e.g. 'attendee')")
    description: str = Field(..., description="Human-readable label from EntityDefinition")
    required: bool = Field(default=True)
    default_value: Any | None = Field(
        default=None, description="Pre-filled value from ProfileStore"
    )
    default_source: str | None = Field(
        default=None, description="'profile' if default came from ProfileStore, else None"
    )
    used_by_steps: list[int] = Field(
        default_factory=list,
        description="Step numbers that reference this entity",
    )
    unit: str = Field(default="", description="Expected unit, e.g. 'minutes', 'email address'")
    example: str = Field(default="", description="Example value shown as placeholder")


class AvailableTool(BaseModel):
    """A tool option shown in the builder dropdown."""

    name: str = Field(..., description="Composio tool name, e.g. NOTION_SEARCH_NOTION")
    description: str = Field(default="", description="Human-readable description of the tool")


class SkeletonStep(BaseModel):
    """A single step in the skeleton DAG visualization."""

    step: int = Field(..., ge=1)
    role: str = Field(..., description="Fetcher, Reasoner, Resolver, Booker, etc.")
    type: str = Field(default="api", description="api, llm_reasoning, policy_check")
    tool: str = Field(default="")
    call: str = Field(default="")
    after: list[int] = Field(default_factory=list, description="DAG dependency edges")
    gate_id: str | None = Field(default=None)
    entity_refs: list[str] = Field(
        default_factory=list,
        description="Entity names referenced in this step's args_template",
    )
    description: str = Field(default="", description="Human-readable step description")
    available_tools: list[str | dict] = Field(
        default_factory=list,
        description="Available tools for user selection when tool is empty. "
        "Each entry is a dict with 'name' and 'description', or a plain string (legacy).",
    )


class PlanSkeleton(BaseModel):
    """Lightweight plan structure returned before entity collection."""

    intent: str = Field(..., description="Detected intent type")
    intent_source: str = Field(
        ..., description="'registry' for known intents, 'llm' for unknown"
    )
    steps: list[SkeletonStep] = Field(default_factory=list)
    entities: list[SkeletonEntityField] = Field(default_factory=list)
    dag_levels: list[list[int]] = Field(
        default_factory=list,
        description="Parallel groups, e.g. [[1], [2,3], [4]]",
    )
    sub_intents: list[str] = Field(default_factory=list)


class SkeletonRequest(BaseModel):
    """Request body for POST /orchestrate/skeleton."""

    message: str = Field(default="", max_length=10_000)
    session_id: str | None = Field(default=None)
    intent_type: str | None = Field(
        default=None,
        description="If provided, skip LLM parse and build skeleton directly for this intent",
    )
    entities: dict[str, Any] | None = Field(
        default=None,
        description="Pre-filled entities (used by rerun flow)",
    )


class SkeletonResponse(BaseModel):
    """Response from POST /orchestrate/skeleton."""

    skeleton: PlanSkeleton
    session_id: str
    partial_entities: dict[str, Any] = Field(
        default_factory=dict,
        description="Entities extracted from the user message",
    )
