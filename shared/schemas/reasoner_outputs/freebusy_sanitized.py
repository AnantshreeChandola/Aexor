"""
FreeBusySanitizedV1 -- Tier 1 reasoner output schema.

Used when a Tier 1 reasoner processes free/busy calendar
data after sanitization.
"""

from pydantic import BaseModel, Field


class BusyBlock(BaseModel):
    """A single busy time block."""

    start: str = Field(
        ..., description="ISO-8601 start of busy period"
    )
    end: str = Field(
        ..., description="ISO-8601 end of busy period"
    )


class FreeBusySanitizedV1(BaseModel):
    """Tier 1 reasoner output: sanitized free/busy data."""

    calendar_id: str = Field(
        ..., description="Calendar identifier"
    )
    busy_blocks: list[BusyBlock] = Field(
        default_factory=list,
        description="List of busy time blocks",
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone of the calendar",
    )
    query_start: str = Field(
        ..., description="ISO-8601 start of query range"
    )
    query_end: str = Field(
        ..., description="ISO-8601 end of query range"
    )
