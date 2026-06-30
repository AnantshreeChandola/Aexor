"""
EmailSummaryV1 -- Tier 1 reasoner output schema.

Used when a Tier 1 reasoner summarizes email content.
"""

from pydantic import BaseModel, Field


class EmailSummaryV1(BaseModel):
    """Tier 1 reasoner output: email summary."""

    subject: str = Field(
        ..., description="Email subject line"
    )
    sender: str = Field(
        ..., description="Sender email address"
    )
    summary: str = Field(
        ...,
        description="Concise summary of email content",
    )
    action_items: list[str] = Field(
        default_factory=list,
        description="Extracted action items",
    )
    priority: str = Field(
        default="normal",
        description="Inferred priority: low, normal, high",
    )
