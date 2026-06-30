"""
TrustFilter domain models -- LLD Section 5.2.

Internal models for the S1/S2/S3 pipeline. These are NOT shared
schemas; they live inside the component boundary.
"""

from typing import Literal

from pydantic import BaseModel, Field

from shared.schemas.trust import Verdict


class ScanContext(BaseModel):
    """Internal context passed through S1 -> S2 -> S3."""

    plan_id: str
    step_number: int
    trace_id: str
    load_bearing_fields: set[str] = Field(default_factory=set)
    strict_mode: bool = False


class RuleHit(BaseModel):
    """One S1 rule match on one string field."""

    field_path: str
    rule_id: str
    severity: Literal["low", "med", "high"]
    matched_substring: str = Field(
        default="",
        description="NEVER logged; used only for S2 input",
    )


class S1Result(BaseModel):
    """Aggregate result of the S1 regex scan pass."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    hits: list[RuleHit] = Field(default_factory=list)
    fields_scanned: int = 0


class S2Result(BaseModel):
    """Result of the S2 Haiku-as-judge classification."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    degraded: bool = False
