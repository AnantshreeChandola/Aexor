"""
ContextRAG Domain Models

Pydantic result model and error classes for context assembly.
ContextResult captures the composite result of querying Memory Layer sources.
Error classes follow the PlanWriter pattern.

Reference: LLD.md SS5.1, SS5.2
"""

from pydantic import BaseModel, Field

from shared.schemas.evidence import EvidenceItem


class ContextResult(BaseModel):
    """Result of context assembly."""

    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Budget-trimmed, tier-sorted evidence items",
    )

    total_bytes: int = Field(
        default=0,
        ge=0,
        description="Total serialized size of evidence in bytes",
    )

    degraded_sources: list[str] = Field(
        default_factory=list,
        description="Sources that failed (e.g., ['history', 'vectorindex'])",
    )

    query_duration_ms: int = Field(
        default=0,
        ge=0,
        description="Total wall-clock time for gather_evidence() in ms",
    )


class ContextRAGError(Exception):
    """Base error for ContextRAG component."""


class SourceQueryError(ContextRAGError):
    """A single source query failed (non-fatal, logged and degraded)."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"Source '{source}' failed: {reason}")
