"""
Intent Schema - GLOBAL_SPEC §2.1 Implementation

Pydantic model for user intent input contract.
Used by Planner, PlanWriter, and ContextRAG.

Reference: GLOBAL_SPEC.md §2.1
"""

from typing import Any

from pydantic import BaseModel, Field


class Intent(BaseModel):
    """
    User intent input contract (GLOBAL_SPEC §2.1).

    Fields:
        intent: Action type (e.g., "schedule_meeting", "book_flight")
        entities: Extracted entities from user input
        constraints: User-specified constraints or preferences
        tz: User timezone (IANA format)
        user_id: Unique user identifier (UUID string)
        context_budget: Context tier budget (1-5), None = system decides
        session_id: Optional session identifier for multi-turn conversations
        trace_id: Distributed tracing correlation ID (32-char hex)
    """

    intent: str = Field(
        ..., min_length=1, description="Action type (e.g., 'schedule_meeting', 'book_flight')"
    )

    entities: dict[str, Any] = Field(..., description="Extracted entities from user input")

    constraints: dict[str, Any] = Field(
        ..., description="User-specified constraints or preferences"
    )

    tz: str = Field(default="America/Chicago", description="User timezone (IANA format)")

    user_id: str = Field(..., description="Unique user identifier (UUID string)")

    context_budget: int | None = Field(
        default=None, ge=1, le=5, description="Context tier budget (1-5), None = system decides"
    )

    session_id: str | None = Field(
        default=None, description="Optional session identifier for multi-turn conversations"
    )

    trace_id: str | None = Field(
        default=None, description="Distributed tracing correlation ID (32-char hex)"
    )

    sub_intents: list[str] = Field(
        default_factory=list,
        description="Decomposed sub-intent keys for compound requests (e.g. ['schedule_meeting', 'send_email'])",
    )

    tool_overrides: dict[int, str] = Field(
        default_factory=dict,
        description="Step-number → tool-name overrides from the plan builder tool picker",
    )
