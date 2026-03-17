"""
VectorIndex Adapters

EmbeddingAdapter (ONNX Runtime), PgvectorAdapter (PostgreSQL),
and TextBuilder (pure text conversion functions).
"""

from components.VectorIndex.adapters.embedding_adapter import EmbeddingAdapter
from components.VectorIndex.adapters.pgvector_adapter import PgvectorAdapter
from components.VectorIndex.adapters.text_builder import (
    build_search_text,
    extract_intent_type,
)

__all__ = [
    "EmbeddingAdapter",
    "PgvectorAdapter",
    "build_search_text",
    "extract_intent_type",
]
