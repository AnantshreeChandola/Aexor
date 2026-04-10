"""
SanitizedPayload schema -- SPEC 037 FR-012.

Shape-preserving sanitized wrapper for any MCP tool response.
Emitted by the TrustFilter component and consumed by
ExecuteOrchestrator and PolicyEngine.
"""

from typing import Any

from pydantic import BaseModel, Field

from shared.schemas.trust import Verdict


class SanitizedPayload(BaseModel):
    """Shape-preserving sanitized wrapper for any MCP tool response."""

    original_shape: Any = Field(
        description=(
            "Original JSON with flagged non-load-bearing fields "
            "replaced by '[redacted: injection]'. Structured "
            "fields (numbers, dates, IDs, enums, emails) pass "
            "through."
        )
    )

    stripped_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Dotted paths (e.g. 'events[0].description') of "
            "fields that were flagged and stripped."
        ),
    )

    trust_verdict: Verdict

    confidence: float = Field(ge=0.0, le=1.0)

    scanner_degraded: bool = Field(
        default=False,
        description=(
            "True if S2 (Haiku) was unreachable and only S1 ran."
        ),
    )

    scanner_version: str = Field(
        description=(
            "Frozen version string: "
            "'trust_filter@<semver>'"
        )
    )

    scanned_at: str = Field(
        description="ISO-8601 UTC timestamp"
    )
