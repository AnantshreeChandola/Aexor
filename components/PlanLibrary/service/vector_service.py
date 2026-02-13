"""
Vector Service for PlanLibrary

Manages embedding generation and vector similarity search.
Implements graceful degradation for embedding API failures.

Reference: LLD.md, tasks.md T201
"""

import logging
import time
from typing import Any

from shared.schemas.evidence import EvidenceItem

from ..adapters.embedding_client import EmbeddingClient
from ..adapters.vector_db import VectorAdapter
from ..domain.models import EmbeddingServiceError
from .evidence_service import EvidenceService

logger = logging.getLogger(__name__)


class VectorService:
    """
    Vector embedding and similarity search service.

    Coordinates embedding generation and pgvector similarity search.
    Graceful degradation: store plans without embeddings if API fails.
    """

    def __init__(
        self,
        vector_adapter: VectorAdapter,
        embedding_client: EmbeddingClient,
    ) -> None:
        """
        Initialize vector service.

        Args:
            vector_adapter: pgvector database operations
            embedding_client: OpenAI embedding API client
        """
        self.vector_adapter = vector_adapter
        self.embedding_client = embedding_client
        self.evidence_service = EvidenceService()
        logger.info(
            "Vector service initialized",
            extra={"component": "PlanLibrary"},
        )

    async def similarity_search(
        self,
        query_text: str,
        similarity_threshold: float = 0.5,
        limit: int = 10,
        success_threshold: float = 0.5,
    ) -> list[EvidenceItem]:
        """
        Find similar plans using vector search.

        Generates embedding for query text, then searches pgvector.
        Returns empty results if no matches above threshold.

        Args:
            query_text: Text to search for similar plans
            similarity_threshold: Minimum similarity score
            limit: Maximum results
            success_threshold: Minimum success rate

        Returns:
            List of Evidence Items sorted by relevance
        """
        start_time = time.time()

        try:
            # Generate embedding for query text
            query_vector = await self.embedding_client.generate_embedding(
                query_text
            )

            # Execute similarity search
            matches = await self.vector_adapter.similarity_search(
                query_vector=query_vector,
                threshold=similarity_threshold,
                limit=limit,
            )

            # Filter by success threshold and convert to Evidence Items
            evidence_items = []
            for match in matches:
                if match.success_rate >= success_threshold:
                    evidence = (
                        self.evidence_service.similarity_to_evidence_item(
                            plan_id=match.plan_id,
                            intent_type=match.intent_type,
                            similarity_score=match.similarity_score,
                            success_rate=match.success_rate,
                            pattern_summary=match.pattern_summary,
                        )
                    )
                    evidence_items.append(evidence)

            latency_ms = (time.time() - start_time) * 1000
            logger.info(
                "Similarity search completed",
                extra={
                    "result_count": len(evidence_items),
                    "latency_ms": round(latency_ms, 2),
                    "component": "PlanLibrary",
                    "operation": "similarity_search",
                },
            )

            return evidence_items

        except EmbeddingServiceError as e:
            logger.warning(
                "Similarity search unavailable due to embedding service",
                extra={
                    "reason": e.reason,
                    "component": "PlanLibrary",
                    "operation": "similarity_search",
                },
            )
            return []

    async def queue_embedding_generation(
        self,
        plan_id: str,
        plan_text: str,
    ) -> bool:
        """
        Generate and store embedding for a plan (fire-and-forget).

        Graceful degradation: logs warning on failure, plan still stored.

        Args:
            plan_id: ULID plan identifier
            plan_text: Text representation of plan for embedding

        Returns:
            True if embedding was generated and stored
        """
        try:
            # Generate embedding
            vector = await self.embedding_client.generate_embedding(plan_text)

            # Store in vector database
            await self.vector_adapter.store_embedding(
                plan_id=plan_id,
                vector=vector,
            )

            logger.info(
                "Embedding generated and stored",
                extra={
                    "plan_id": plan_id,
                    "component": "PlanLibrary",
                    "operation": "queue_embedding_generation",
                },
            )
            return True

        except EmbeddingServiceError as e:
            logger.warning(
                "Embedding generation failed, plan stored without embedding",
                extra={
                    "plan_id": plan_id,
                    "reason": e.reason,
                    "component": "PlanLibrary",
                    "operation": "queue_embedding_generation",
                },
            )
            return False

        except Exception as e:
            logger.warning(
                "Embedding storage failed",
                extra={
                    "plan_id": plan_id,
                    "error_type": type(e).__name__,
                    "component": "PlanLibrary",
                    "operation": "queue_embedding_generation",
                },
            )
            return False
