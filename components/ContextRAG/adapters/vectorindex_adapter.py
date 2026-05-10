"""
VectorIndex Source Adapter

Thin wrapper around VectorIndexService.search().
Converts HybridSearchResult to EvidenceItem with type="exemplar".

Reference: LLD.md SS6.5
"""

import logging
from typing import Any

from shared.database.error_handler import DatabaseConnectionError
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

from ..domain.models import SourceQueryError

logger = logging.getLogger("contextrag")

# Lazy import: VectorIndex may not be loadable if numpy/onnxruntime are missing.
# The domain models module itself is light but its package __init__.py pulls
# in heavy deps. We import the specific submodule directly to avoid that.
try:
    from components.VectorIndex.domain.models import (
        EmbeddingModelError,
        VectorIndexUnavailableError,
    )
except ImportError:  # pragma: no cover
    EmbeddingModelError = None  # type: ignore[assignment,misc]
    VectorIndexUnavailableError = None  # type: ignore[assignment,misc]


class VectorIndexAdapter:
    """Source adapter for VectorIndex similarity search."""

    source_name = "vectorindex"
    required_tier = 3
    default_timeout = 0.1

    def __init__(self, vector_index_service: Any | None) -> None:
        self._service = vector_index_service

    async def fetch_evidence(
        self,
        intent: Intent,
        _timeout_s: float = 0.1,
    ) -> list[EvidenceItem]:
        """Call VectorIndexService.search() and convert results.

        Returns list[EvidenceItem] with type="exemplar", tier=3.
        Returns empty list if service is None (not wired).
        """
        if self._service is None:
            return []

        try:
            query_text = f"{intent.intent} {' '.join(str(v) for v in intent.entities.values())}"
            results = await self._service.search(
                query_text=query_text,
                intent_type=intent.intent,
                top_k=3,
            )

            evidence: list[EvidenceItem] = []
            for result in results:
                evidence.append(
                    EvidenceItem(
                        type="exemplar",
                        key=f"similar_plan_{result.plan_id[:8]}",
                        value={
                            "plan_id": result.plan_id,
                            "rrf_score": result.rrf_score,
                        },
                        confidence=min(result.rrf_score, 1.0),
                        source_ref=f"vectorindex:search/{result.plan_id}",
                        ttl_days=None,
                        tier=3,
                    )
                )
            return evidence

        except Exception as e:
            # Check against dynamically-loaded error types
            if VectorIndexUnavailableError and isinstance(e, VectorIndexUnavailableError):
                raise SourceQueryError("vectorindex", "unavailable")
            if EmbeddingModelError and isinstance(e, EmbeddingModelError):
                raise SourceQueryError("vectorindex", "model_error")
            if isinstance(e, ValueError):
                raise SourceQueryError("vectorindex", "invalid_query")
            if isinstance(e, DatabaseConnectionError):
                raise SourceQueryError("vectorindex", "connection_error")
            if isinstance(e, SourceQueryError):
                raise
            logger.warning(
                "vectorindex_unexpected_error",
                extra={"error_type": type(e).__name__},
            )
            raise SourceQueryError("vectorindex", f"unexpected: {type(e).__name__}")
