"""
VectorIndex Domain Models

HybridSearchResult and error classes for the VectorIndex component.
"""

from components.VectorIndex.domain.models import (
    EmbeddingModelError,
    HybridSearchResult,
    VectorIndexError,
    VectorIndexUnavailableError,
)

__all__ = [
    "EmbeddingModelError",
    "HybridSearchResult",
    "VectorIndexError",
    "VectorIndexUnavailableError",
]
