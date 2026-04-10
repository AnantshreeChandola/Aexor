"""
FreeSlotsV1 -- Tier 1 reasoner output schema.

Used when a Tier 1 reasoner lists available free slots
from calendar analysis.
"""

from pydantic import BaseModel, Field


class FreeSlot(BaseModel):
    """A single free time slot."""

    start: str = Field(
        ..., description="ISO-8601 datetime of slot start"
    )
    end: str = Field(
        ..., description="ISO-8601 datetime of slot end"
    )
    label: str = Field(
        ...,
        description=(
            "Human-readable label, "
            "e.g. '11:00 AM - 11:30 AM, Tue Apr 8'"
        ),
    )


class FreeSlotsV1(BaseModel):
    """Tier 1 reasoner output: free slot listing."""

    recommended_time: str = Field(
        ...,
        description="ISO-8601 datetime of best recommendation",
    )
    has_conflict: bool = Field(
        ...,
        description="True if original request time has conflict",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Descriptions of conflicting events",
    )
    free_slots: list[FreeSlot] = Field(
        default_factory=list,
        description="Available free slots during work hours",
    )
    reason: str = Field(
        ...,
        description="Explanation for the recommendation",
    )
