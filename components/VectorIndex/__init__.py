"""
VectorIndex Component

Hybrid search over plan embeddings (BM25 + semantic via RRF)
using pgvector, PostgreSQL tsvector, and ONNX Runtime.
Library component -- no HTTP routes.
"""

from components.VectorIndex.domain.models import (
    EmbeddingModelError,
    HybridSearchResult,
    VectorIndexError,
    VectorIndexUnavailableError,
)
from components.VectorIndex.service.vector_index_service import (
    VectorIndexService,
    create_vector_index_service,
)

__all__ = [
    "EmbeddingModelError",
    "HybridSearchResult",
    "VectorIndexError",
    "VectorIndexService",
    "VectorIndexUnavailableError",
    "create_vector_index_service",
]
