"""
VectorIndex Service

VectorIndexService and factory function for DI wiring.
"""

from components.VectorIndex.service.vector_index_service import (
    VectorIndexService,
    create_vector_index_service,
)

__all__ = [
    "VectorIndexService",
    "create_vector_index_service",
]
