"""
SlotProposalV1 -- Tier 1 reasoner output schema.

Used when a Tier 1 reasoner proposes a single time slot
for booking after analyzing calendar data.
"""

from pydantic import BaseModel, Field


class SlotProposalV1(BaseModel):
    """Tier 1 reasoner output: proposed meeting time slot."""

    proposed_start: str = Field(
        ...,
        description="ISO-8601 datetime of proposed start",
    )
    proposed_end: str = Field(
        ...,
        description="ISO-8601 datetime of proposed end",
    )
    has_conflict: bool = Field(
        ...,
        description="True if the proposed time has a conflict",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Descriptions of conflicting events",
    )
    reason: str = Field(
        ...,
        description="Explanation for the proposal",
    )
