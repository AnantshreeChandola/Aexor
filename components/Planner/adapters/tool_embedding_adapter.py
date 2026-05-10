"""
ToolEmbeddingAdapter — Embed, Store, and Search Tool Definitions

Manages the tool_embeddings table: generates 384-dim embeddings for tool
descriptions using the existing all-MiniLM-L6-v2 ONNX model, upserts them
into PostgreSQL, and performs hybrid BM25 + cosine search via RRF.

Reuses the same patterns as VectorIndex's PgvectorAdapter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text as sa_text

from components.Planner.domain.tool_discovery_models import ToolEmbeddingResult
from shared.database.error_handler import with_db_error_handling

if TYPE_CHECKING:
    from components.VectorIndex.adapters.embedding_adapter import EmbeddingAdapter
    from shared.database.adapter import SharedDatabaseAdapter

logger = logging.getLogger(__name__)


class ToolEmbeddingAdapter:
    """Embed, store, and search tool definitions via hybrid BM25 + cosine."""

    def __init__(
        self,
        embedding_adapter: EmbeddingAdapter,
        db_adapter: SharedDatabaseAdapter,
    ) -> None:
        self._embedding = embedding_adapter
        self._db = db_adapter

    # ------------------------------------------------------------------
    # Build search text
    # ------------------------------------------------------------------

    @staticmethod
    def build_tool_search_text(tool: Any) -> str:
        """Build structured text for embedding and full-text search.

        Format: '{provider} {action_words} {description} {param_names}'

        Example for GOOGLECALENDAR_CREATE_EVENT:
            "googlecalendar create event Create a new calendar event
             start_time end_time summary attendees"
        """
        name: str = getattr(tool, "name", "") or ""
        description: str = getattr(tool, "description", "") or ""
        input_schema: dict = getattr(tool, "input_schema", None) or {}

        # Provider: first segment of tool name, lowercased
        parts = name.split("_")
        provider = parts[0].lower() if parts else name.lower()

        # Action words: remaining segments, lowercased
        action_words = " ".join(p.lower() for p in parts[1:]) if len(parts) > 1 else ""

        # Parameter names from input_schema.properties
        properties = input_schema.get("properties", {})
        param_names = " ".join(properties.keys()) if properties else ""

        segments = [provider, action_words, description, param_names]
        return " ".join(s for s in segments if s)

    # ------------------------------------------------------------------
    # Sync (upsert all tool embeddings)
    # ------------------------------------------------------------------

    @with_db_error_handling
    async def sync_tool_embeddings(self, tools: list[Any]) -> int:
        """Upsert embeddings for all tools. Returns count stored.

        Generates embeddings in batch using the ONNX model, then batch-upserts
        into tool_embeddings using ON CONFLICT (tool_name) DO UPDATE.
        """
        if not tools:
            return 0

        # Build search texts and batch-embed
        search_texts: list[str] = []
        tool_data: list[dict] = []
        for tool in tools:
            text = self.build_tool_search_text(tool)
            name = getattr(tool, "name", "")
            provider = getattr(tool, "provider_name", "")
            search_texts.append(text)
            tool_data.append({
                "tool_name": name,
                "provider_name": provider,
                "search_text": text,
            })

        embeddings = self._embedding.embed_batch(search_texts)

        sql = sa_text("""
            INSERT INTO tool_embeddings (
                tool_name, provider_name, embedding, search_text, model_version
            ) VALUES (
                :tool_name, :provider_name, :embedding, :search_text,
                'all-MiniLM-L6-v2'
            )
            ON CONFLICT (tool_name) DO UPDATE SET
                provider_name = EXCLUDED.provider_name,
                embedding = EXCLUDED.embedding,
                search_text = EXCLUDED.search_text,
                model_version = EXCLUDED.model_version
        """)

        async with self._db.get_session() as session:
            for data, emb in zip(tool_data, embeddings):
                await session.execute(
                    sql,
                    {
                        "tool_name": data["tool_name"],
                        "provider_name": data["provider_name"],
                        "embedding": str(emb),
                        "search_text": data["search_text"],
                    },
                )
            await session.commit()

        logger.info(
            "tool_embeddings_synced",
            extra={"count": len(tools)},
        )
        return len(tools)

    # ------------------------------------------------------------------
    # Hybrid search by intent text (Tier 1B)
    # ------------------------------------------------------------------

    @with_db_error_handling
    async def search_by_intent(
        self,
        intent_text: str,
        top_k: int = 10,
        rrf_k: int = 60,
    ) -> list[ToolEmbeddingResult]:
        """Hybrid BM25 + cosine search over tool_embeddings.

        Same RRF pattern as plan_embeddings (VectorIndex pgvector_adapter).
        """
        query_embedding = self._embedding.embed(intent_text)

        sql = sa_text("""
            WITH keyword AS (
                SELECT tool_name, provider_name,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank_cd(tsv, query) DESC
                       ) AS rank_kw
                FROM tool_embeddings,
                     plainto_tsquery('english', :query_text) AS query
                WHERE tsv @@ plainto_tsquery('english', :query_text)
                LIMIT 20
            ),
            semantic AS (
                SELECT tool_name, provider_name,
                       ROW_NUMBER() OVER (
                           ORDER BY embedding <=> :query_embedding
                       ) AS rank_vec
                FROM tool_embeddings
                ORDER BY embedding <=> :query_embedding
                LIMIT 20
            )
            SELECT COALESCE(k.tool_name, s.tool_name) AS tool_name,
                   COALESCE(k.provider_name, s.provider_name) AS provider_name,
                   COALESCE(1.0 / (:rrf_k + k.rank_kw), 0.0)
                     + COALESCE(1.0 / (:rrf_k + s.rank_vec), 0.0)
                     AS rrf_score,
                   k.rank_kw AS keyword_rank,
                   s.rank_vec AS semantic_rank
            FROM keyword k
            FULL OUTER JOIN semantic s USING (tool_name)
            ORDER BY rrf_score DESC
            LIMIT :top_k
        """)

        async with self._db.get_session() as session:
            result = await session.execute(
                sql,
                {
                    "query_text": intent_text,
                    "query_embedding": str(query_embedding),
                    "rrf_k": rrf_k,
                    "top_k": top_k,
                },
            )
            rows = result.fetchall()

        return [
            ToolEmbeddingResult(
                tool_name=row.tool_name,
                provider_name=row.provider_name,
                rrf_score=float(row.rrf_score),
                keyword_rank=int(row.keyword_rank) if row.keyword_rank is not None else None,
                semantic_rank=int(row.semantic_rank) if row.semantic_rank is not None else None,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Search by tool name fragment (Tier 3 agentic fallback)
    # ------------------------------------------------------------------

    @with_db_error_handling
    async def search_by_tool_name(
        self,
        tool_name: str,
        top_k: int = 10,
        rrf_k: int = 60,
    ) -> list[ToolEmbeddingResult]:
        """Search tool_embeddings by tool name fragment.

        Converts the tool name to lowercase, replaces underscores/dots with
        spaces, and uses the same hybrid search as search_by_intent.
        """
        # Normalize tool name into search-friendly text
        search_text = tool_name.replace("_", " ").replace(".", " ").lower()
        return await self.search_by_intent(search_text, top_k=top_k, rrf_k=rrf_k)
