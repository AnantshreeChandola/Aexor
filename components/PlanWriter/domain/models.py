"""
PlanWriter Domain Models

Pydantic result models and error classes for plan outcome persistence.
PersistResult captures the composite result of writing to PlanLibrary,
History, and VectorIndex. Error classes follow the Signer pattern.

Reference: LLD.md SS5.1, SS5.2
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PersistResult(BaseModel):
    """Result of persisting a single plan outcome."""

    plan_id: str = Field(
        description="ULID plan identifier",
        min_length=26,
        max_length=26,
    )
    fact_id: UUID | None = Field(
        default=None,
        description="History fact UUID, None if History write failed",
    )
    embedding_stored: bool = Field(
        default=False,
        description="True if VectorIndex embedding was stored successfully",
    )
    status: Literal["ok", "partial", "error"] = Field(
        description=(
            "'ok' = all writes succeeded, "
            "'partial' = PlanLibrary succeeded but History/VectorIndex had errors, "
            "'error' = used in bulk_persist for items where PlanLibrary failed"
        ),
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Human-readable error descriptions for partial failures",
    )


class BulkPersistResult(BaseModel):
    """Result of bulk persisting multiple plan outcomes."""

    results: list[PersistResult] = Field(
        default_factory=list,
        description="Individual results for each outcome",
    )
    total: int = Field(
        description="Total outcomes submitted",
    )
    succeeded: int = Field(
        default=0,
        description="Count with status='ok'",
    )
    partial: int = Field(
        default=0,
        description="Count with status='partial'",
    )
    failed: int = Field(
        default=0,
        description="Count with status='error'",
    )


# --- Error Classes ---


class PlanWriterError(Exception):
    """Base error for PlanWriter component."""


class PlanLibraryWriteError(PlanWriterError):
    """Raised when the primary PlanLibrary write fails."""

    def __init__(self, plan_id: str, reason: str) -> None:
        self.plan_id = plan_id
        self.reason = reason
        super().__init__(f"PlanLibrary write failed for plan {plan_id}: {reason}")


class FactDerivationError(PlanWriterError):
    """Raised when fact derivation fails (non-fatal in persist_outcome)."""

    def __init__(self, plan_id: str, reason: str) -> None:
        self.plan_id = plan_id
        self.reason = reason
        super().__init__(f"Fact derivation failed for plan {plan_id}: {reason}")
