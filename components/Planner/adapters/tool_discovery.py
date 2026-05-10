"""
ToolDiscoveryService — 3-Tier Hybrid Tool Retrieval

Orchestrates:
  Tier 1A: Plan-based tool discovery (proven tool combinations from plan history)
  Tier 1B: Direct tool embedding search (semantic + BM25 over tool descriptions)
  Tier 2:  Cross-encoder reranking (ONNX, scores query vs tool description)
  Tier 3:  Agentic fallback (search by unresolved tool name, self-healing)

Consumed by PlannerService as a library component (no HTTP routes).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from components.Planner.domain.tool_discovery_models import (
    ToolDiscoveryResult,
    ToolNotConnectedError,
)

if TYPE_CHECKING:
    from components.Planner.adapters.cross_encoder_reranker import CrossEncoderReranker
    from components.Planner.adapters.tool_embedding_adapter import ToolEmbeddingAdapter

logger = logging.getLogger(__name__)

_VIRTUAL_TOOL_PREFIXES = ("system.", "system_")
_HIGH_CONFIDENCE_THRESHOLD = 0.5  # Tier 1A frequency threshold for ToolNotConnectedError


class ToolDiscoveryService:
    """Orchestrates the 3-tier tool discovery pipeline."""

    def __init__(
        self,
        tool_embedding_adapter: ToolEmbeddingAdapter,
        reranker: CrossEncoderReranker | None,
        vector_index_service: Any | None,
        plan_service: Any,
        max_candidates: int = 20,
        max_reranked: int = 5,
        min_tools_threshold: int = 3,
        plan_search_k: int = 10,
        tool_search_k: int = 10,
    ) -> None:
        self._tool_embedding = tool_embedding_adapter
        self._reranker = reranker
        self._vector_index = vector_index_service
        self._plan_service = plan_service
        self._max_candidates = max_candidates
        self._max_reranked = max_reranked
        self._min_tools_threshold = min_tools_threshold
        self._plan_search_k = plan_search_k
        self._tool_search_k = tool_search_k

    # ------------------------------------------------------------------
    # Main entry point: Tier 1 (retrieval) + Tier 2 (reranking)
    # ------------------------------------------------------------------

    async def discover_tools(
        self,
        intent_text: str,
        available_tools: list[Any],
        intent_entities: dict[str, Any] | None = None,
        skip_tool_check: bool = False,
    ) -> ToolDiscoveryResult:
        """Run Tier 1 (retrieval) + Tier 2 (reranking). Returns ranked tool list.

        Raises ToolNotConnectedError if plan-based discovery identifies
        high-confidence tools the user hasn't connected (unless skip_tool_check=True).
        """
        t0 = time.monotonic()
        available_names = {getattr(t, "name", "") for t in available_tools}
        tool_by_name = {getattr(t, "name", ""): t for t in available_tools}

        # ── Tier 1A: Plan-based tool discovery ──────────────────────────
        plan_tool_freq: dict[str, float] = {}
        plan_based_count = 0
        if self._vector_index is not None:
            try:
                plan_tool_freq = await self._plan_based_discovery(intent_text)
                plan_based_count = len(plan_tool_freq)
            except Exception:
                logger.warning("tier1a_plan_search_failed", exc_info=True)

        # ── Tier 1B: Direct tool embedding search ──────────────────────
        direct_tool_scores: dict[str, float] = {}
        direct_count = 0
        try:
            results = await self._tool_embedding.search_by_intent(
                intent_text, top_k=self._tool_search_k
            )
            direct_tool_scores = {r.tool_name: r.rrf_score for r in results}
            direct_count = len(direct_tool_scores)
        except Exception:
            logger.warning("tier1b_tool_search_failed", exc_info=True)

        # ── Validate connected tools (Tier 1A high-confidence) ─────────
        if not skip_tool_check and plan_tool_freq:
            missing: list[dict[str, str]] = []
            for tool_name, freq in plan_tool_freq.items():
                if freq >= _HIGH_CONFIDENCE_THRESHOLD and tool_name not in available_names:
                    # Extract provider from tool name
                    parts = tool_name.split("_")
                    provider = parts[0].lower() if parts else tool_name.lower()
                    missing.append({
                        "tool_name": tool_name,
                        "provider_name": provider,
                    })
            if missing:
                raise ToolNotConnectedError(missing_tools=missing)

        # ── Merge Tier 1A + 1B, intersect with available_tools ─────────
        merged_scores: dict[str, float] = {}
        # Add Tier 1A tools (plan-proven, higher weight)
        for name, freq in plan_tool_freq.items():
            if name in available_names:
                merged_scores[name] = freq
        # Add Tier 1B tools (direct search)
        for name, score in direct_tool_scores.items():
            if name in available_names:
                # Combine scores if tool is in both tiers
                merged_scores[name] = merged_scores.get(name, 0.0) + score

        # Sort by combined score, cap at max_candidates
        sorted_names = sorted(merged_scores, key=lambda n: -merged_scores[n])
        sorted_names = sorted_names[: self._max_candidates]
        candidate_count = len(sorted_names)

        # ── Fail-open check ────────────────────────────────────────────
        if candidate_count < self._min_tools_threshold:
            discovery_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "tool_discovery_fail_open",
                extra={
                    "candidate_count": candidate_count,
                    "threshold": self._min_tools_threshold,
                    "discovery_ms": discovery_ms,
                },
            )
            return ToolDiscoveryResult(
                tools=available_tools,
                discovery_tier=0,
                candidate_count=len(available_tools),
                reranked_count=0,
                plan_based_tools=plan_based_count,
                direct_tools=direct_count,
                discovery_ms=discovery_ms,
            )

        candidates = [tool_by_name[n] for n in sorted_names if n in tool_by_name]

        # ── Tier 2: Cross-encoder reranking ────────────────────────────
        reranked_count = 0
        tier = 1
        if self._reranker is not None and candidates:
            try:
                reranked = self._reranker.rerank(
                    intent_text, candidates, top_k=self._max_reranked
                )
                candidates = [tool for tool, _score in reranked]
                reranked_count = len(candidates)
                tier = 2
            except Exception:
                logger.warning("tier2_reranking_failed", exc_info=True)
                # Tier 2 failed — pass through Tier 1 results
                candidates = candidates[: self._max_reranked]
        else:
            # No reranker — trim to max_reranked
            candidates = candidates[: self._max_reranked]

        discovery_ms = int((time.monotonic() - t0) * 1000)

        logger.info(
            "tool_discovery_complete",
            extra={
                "tier": tier,
                "intent": intent_text[:80],
                "candidate_count": candidate_count,
                "reranked_count": reranked_count,
                "plan_based_tools": plan_based_count,
                "direct_tools": direct_count,
                "final_tools": len(candidates),
                "discovery_ms": discovery_ms,
            },
        )

        return ToolDiscoveryResult(
            tools=candidates,
            discovery_tier=tier,
            candidate_count=candidate_count,
            reranked_count=reranked_count,
            plan_based_tools=plan_based_count,
            direct_tools=direct_count,
            discovery_ms=discovery_ms,
        )

    # ------------------------------------------------------------------
    # Tier 3: Agentic fallback — resolve unresolved tool names
    # ------------------------------------------------------------------

    async def agentic_expand(
        self,
        missing_tool_name: str,
        available_tools: list[Any],
        current_selected: list[Any],
    ) -> list[Any]:
        """Search tool_embeddings by name and return newly discovered tools.

        Used when _finalize_plan encounters a tool name the LLM generated
        that doesn't exist in the catalog. Searches tool_embeddings to find
        the canonical tool name, then checks if it's in available_tools.

        Returns:
            Updated current_selected with any newly resolved tools appended.
            Returns current_selected unchanged if no match found.
        """
        available_names = {getattr(t, "name", "") for t in available_tools}
        current_names = {getattr(t, "name", "") for t in current_selected}
        tool_by_name = {getattr(t, "name", ""): t for t in available_tools}

        try:
            results = await self._tool_embedding.search_by_tool_name(
                missing_tool_name, top_k=5
            )
        except Exception:
            logger.warning(
                "agentic_expand_search_failed",
                extra={"missing_tool": missing_tool_name},
                exc_info=True,
            )
            return current_selected

        for result in results:
            if result.tool_name in available_names and result.tool_name not in current_names:
                tool = tool_by_name.get(result.tool_name)
                if tool is not None:
                    logger.info(
                        "agentic_expand_resolved",
                        extra={
                            "missing_tool": missing_tool_name,
                            "resolved_to": result.tool_name,
                            "rrf_score": result.rrf_score,
                        },
                    )
                    return [*current_selected, tool]

        logger.info(
            "agentic_expand_no_match",
            extra={"missing_tool": missing_tool_name},
        )
        return current_selected

    # ------------------------------------------------------------------
    # Internal: Tier 1A — Plan-based tool extraction
    # ------------------------------------------------------------------

    async def _plan_based_discovery(self, intent_text: str) -> dict[str, float]:
        """Search plan_embeddings by intent, load matching plans, extract tools.

        Returns:
            Dict mapping tool_name → frequency (0.0–1.0), where frequency is
            the fraction of matching plans that used that tool.
        """
        if self._vector_index is None:
            return {}

        # Search for similar plans
        search_results = await self._vector_index.search(
            query_text=intent_text,
            top_k=self._plan_search_k,
        )
        if not search_results:
            return {}

        # Load each plan's canonical_json and extract tools
        tool_counts: dict[str, int] = {}
        plan_count = 0

        for result in search_results:
            plan_id = result.plan_id
            try:
                plan_db = await self._plan_service.get_plan_by_id(plan_id)
                if plan_db is None:
                    continue
            except Exception:
                continue

            canonical = plan_db.canonical_json
            if not isinstance(canonical, dict):
                continue

            graph = canonical.get("graph", [])
            plan_count += 1

            for step in graph:
                uses = step.get("uses", "")
                if not uses:
                    continue
                # Skip virtual tools (system.echo, system.noop, etc.)
                if any(uses.startswith(prefix) for prefix in _VIRTUAL_TOOL_PREFIXES):
                    continue
                tool_counts[uses] = tool_counts.get(uses, 0) + 1

        if plan_count == 0:
            return {}

        # Convert counts to frequencies
        return {name: count / plan_count for name, count in tool_counts.items()}
