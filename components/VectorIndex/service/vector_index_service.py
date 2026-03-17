"""
VectorIndex Service

Orchestrates TextBuilder, EmbeddingAdapter, and PgvectorAdapter
to provide hybrid search (BM25 + semantic via RRF) over plan embeddings.
Library component -- no HTTP routes.
"""

import logging
import os
import time
from pathlib import Path

from components.VectorIndex.adapters.embedding_adapter import EmbeddingAdapter
from components.VectorIndex.adapters.pgvector_adapter import PgvectorAdapter
from components.VectorIndex.adapters.text_builder import (
    build_search_text,
    extract_intent_type,
)
from components.VectorIndex.domain.models import HybridSearchResult
from shared.database.adapter import SharedDatabaseAdapter

logger = logging.getLogger("vectorindex")


class VectorIndexService:
    """Hybrid search over plan embeddings (BM25 + semantic via RRF)."""

    def __init__(
        self,
        embedding_adapter: EmbeddingAdapter,
        pgvector_adapter: PgvectorAdapter,
    ) -> None:
        """Initialize with adapters for embedding generation and DB storage.

        Args:
            embedding_adapter: ONNX Runtime embedding generator.
            pgvector_adapter: PostgreSQL pgvector storage adapter.
        """
        self._embedding = embedding_adapter
        self._pgvector = pgvector_adapter

    async def store_embedding(
        self,
        plan_id: str,
        plan_data: dict,
    ) -> None:
        """Generate embedding + tsvector from plan_data and store.

        Extracts intent_type from plan_data. Upserts on conflict (plan_id).

        Args:
            plan_id: ULID plan identifier.
            plan_data: Plan dictionary.

        Raises:
            ValueError: If plan_data is empty or None.
        """
        if not plan_data:
            raise ValueError("plan_data must be a non-empty dict")

        total_start = time.monotonic()

        search_text = build_search_text(plan_data)
        intent_type = extract_intent_type(plan_data)

        embed_start = time.monotonic()
        embedding = self._embedding.embed(search_text)
        embed_ms = (time.monotonic() - embed_start) * 1000

        await self._pgvector.upsert_embedding(
            plan_id=plan_id,
            intent_type=intent_type,
            embedding=embedding,
            search_text=search_text,
        )

        total_ms = (time.monotonic() - total_start) * 1000

        logger.info(
            "embedding_stored",
            extra={
                "plan_id": plan_id,
                "intent_type": intent_type,
                "embedding_latency_ms": round(embed_ms, 2),
                "total_latency_ms": round(total_ms, 2),
            },
        )

    async def search(
        self,
        query_text: str,
        intent_type: str | None = None,
        top_k: int = 5,
    ) -> list[HybridSearchResult]:
        """Hybrid search: BM25 + semantic via RRF.

        Args:
            query_text: Natural-language query string.
            intent_type: Optional B-tree pre-filter (exact match).
            top_k: Maximum results (default 5, max 50).

        Returns:
            List of HybridSearchResult sorted by rrf_score descending.

        Raises:
            ValueError: If top_k < 1 or > 50, or query_text is empty.
        """
        if not query_text or not query_text.strip():
            raise ValueError("query_text must be a non-empty string")
        if top_k < 1 or top_k > 50:
            raise ValueError(f"top_k must be between 1 and 50, got {top_k}")

        total_start = time.monotonic()

        embed_start = time.monotonic()
        query_embedding = self._embedding.embed(query_text)
        embed_ms = (time.monotonic() - embed_start) * 1000

        search_start = time.monotonic()
        rows = await self._pgvector.hybrid_search(
            query_embedding=query_embedding,
            query_text=query_text,
            intent_type=intent_type,
            top_k=top_k,
        )
        search_ms = (time.monotonic() - search_start) * 1000

        results = [
            HybridSearchResult(
                plan_id=row["plan_id"],
                intent_type=row["intent_type"],
                rrf_score=row["rrf_score"],
                keyword_rank=row.get("keyword_rank"),
                semantic_rank=row.get("semantic_rank"),
            )
            for row in rows
        ]

        total_ms = (time.monotonic() - total_start) * 1000

        logger.info(
            "hybrid_search",
            extra={
                "intent_type": intent_type or "all",
                "top_k": top_k,
                "result_count": len(results),
                "keyword_hits": sum(1 for r in results if r.keyword_rank is not None),
                "semantic_hits": sum(1 for r in results if r.semantic_rank is not None),
                "embedding_latency_ms": round(embed_ms, 2),
                "search_latency_ms": round(search_ms, 2),
                "total_latency_ms": round(total_ms, 2),
            },
        )

        return results

    async def delete_embedding(self, plan_id: str) -> None:
        """Delete embedding for a plan. Idempotent.

        Args:
            plan_id: ULID plan identifier.
        """
        await self._pgvector.delete_by_plan_id(plan_id)
        logger.info("embedding_deleted", extra={"plan_id": plan_id})

    async def bulk_store(self, plans: list[dict]) -> int:
        """Batch generate embeddings + tsvectors and store.

        Args:
            plans: List of plan dicts, each with "plan_id" key.

        Returns:
            Count of rows stored.

        Raises:
            ValueError: If plans list is empty.
        """
        if not plans:
            raise ValueError("plans list must not be empty")

        total_start = time.monotonic()

        # Build search texts and extract metadata
        prepared_rows = []
        search_texts = []
        for plan in plans:
            plan_data = plan.get("plan_data", plan)
            search_text = build_search_text(plan_data)
            search_texts.append(search_text)
            prepared_rows.append(
                {
                    "plan_id": plan.get("plan_id", ""),
                    "intent_type": extract_intent_type(plan_data),
                    "search_text": search_text,
                }
            )

        # Batch embed
        embeddings = self._embedding.embed_batch(search_texts)

        # Combine embeddings with prepared rows
        rows = []
        for row_data, embedding in zip(prepared_rows, embeddings, strict=True):
            rows.append({**row_data, "embedding": embedding})

        count = await self._pgvector.bulk_upsert(rows)

        total_ms = (time.monotonic() - total_start) * 1000
        logger.info(
            "bulk_store_completed",
            extra={
                "count": count,
                "total_latency_ms": round(total_ms, 2),
            },
        )

        return count


def create_vector_index_service(
    db_adapter: SharedDatabaseAdapter,
) -> "VectorIndexService":
    """Create VectorIndexService with ONNX embedding adapter and pgvector adapter.

    Reads ONNX_MODEL_PATH env var for the model file location.
    Defaults to ~/.cache/vectorindex/model.onnx.

    Args:
        db_adapter: Shared database adapter for PostgreSQL connections.

    Returns:
        Configured VectorIndexService.

    Raises:
        EmbeddingModelError: If ONNX model cannot be loaded.
        VectorIndexUnavailableError: If pgvector extension is not installed.
    """
    model_path = os.environ.get(
        "ONNX_MODEL_PATH",
        str(Path("~/.cache/vectorindex/model.onnx").expanduser()),
    )

    embedding_adapter = EmbeddingAdapter(model_path=model_path)
    pgvector_adapter = PgvectorAdapter(db_adapter=db_adapter)

    logger.info(
        "vector_index_service_created",
        extra={"model_path": model_path},
    )

    return VectorIndexService(
        embedding_adapter=embedding_adapter,
        pgvector_adapter=pgvector_adapter,
    )
