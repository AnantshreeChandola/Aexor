"""
VectorIndex Domain Models

HybridSearchResult Pydantic model and error classes for the VectorIndex component.
Field names conform to SPEC Section "Key Entities" and LLD Sections 5.1 / 5.2.
"""

from pydantic import BaseModel, Field


class HybridSearchResult(BaseModel):
    """Result from hybrid BM25 + semantic search via RRF."""

    plan_id: str = Field(description="ULID plan identifier")
    intent_type: str = Field(description="Plan intent type (denormalized)")
    rrf_score: float = Field(
        description="Combined RRF score (higher = more relevant)",
    )
    keyword_rank: int | None = Field(
        default=None,
        description="BM25 rank position (None if not in keyword results)",
    )
    semantic_rank: int | None = Field(
        default=None,
        description="Cosine similarity rank position (None if not in semantic results)",
    )


# --- Error Classes ---


class VectorIndexError(Exception):
    """Base error for VectorIndex component."""


class VectorIndexUnavailableError(VectorIndexError):
    """Raised when pgvector extension is not available."""

    def __init__(self, reason: str = "pgvector extension not installed") -> None:
        self.reason = reason
        super().__init__(f"VectorIndex unavailable: {reason}")


class EmbeddingModelError(VectorIndexError):
    """Raised when ONNX embedding model cannot be loaded."""

    def __init__(self, model_name: str, reason: str = "") -> None:
        self.model_name = model_name
        self.reason = reason
        super().__init__(f"Embedding model error ({model_name}): {reason}")
