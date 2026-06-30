"""
Trust verdict schema -- SPEC 037 FR-013.

Shared model for the three-valued trust classification produced
by the TrustFilter S1+S2 pipeline.
"""

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["clean", "suspicious", "injection"]


class TrustVerdict(BaseModel):
    """Verdict metadata from the S1+S2 pipeline."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=512)
    stage: Literal["s1", "s2", "s1_only_degraded"]
