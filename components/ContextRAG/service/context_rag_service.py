"""
ContextRAG Service

Orchestrates concurrent reads from Memory Layer sources (ProfileStore,
History, PlanLibrary, VectorIndex) and returns a budget-constrained
ContextResult. Never raises -- always returns ContextResult.

Reference: LLD.md SS4.1, SS7.1, SS9.2
"""

import asyncio
import logging
import time
from typing import Any

from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

from ..adapters.budget_manager import BudgetManager
from ..adapters.history_adapter import HistoryAdapter
from ..adapters.planlibrary_adapter import PlanLibraryAdapter
from ..adapters.profilestore_adapter import ProfileStoreAdapter
from ..adapters.vectorindex_adapter import VectorIndexAdapter
from ..domain.models import ContextResult, SourceQueryError

logger = logging.getLogger("contextrag")


def _elapsed_ms(start: float) -> int:
    """Calculate elapsed milliseconds from a monotonic start time."""
    return int((time.monotonic() - start) * 1000)


class ContextRAGService:
    """Context assembler -- gathers typed evidence from Memory Layer."""

    def __init__(
        self,
        preference_service: Any,
        fact_service: Any,
        pattern_service: Any,
        plan_service: Any,
        vector_index_service: Any | None,
    ) -> None:
        """Initialize with downstream Memory Layer services.

        Args:
            preference_service: ProfileStore PreferenceService.
            fact_service: History FactService.
            pattern_service: History PatternService.
            plan_service: PlanLibrary PlanService.
            vector_index_service: VectorIndex service, may be None.
        """
        self._profilestore_adapter = ProfileStoreAdapter(preference_service)
        self._history_adapter = HistoryAdapter(fact_service, pattern_service)
        self._planlibrary_adapter = PlanLibraryAdapter(plan_service)
        self._vectorindex_adapter = VectorIndexAdapter(vector_index_service)
        self._budget_manager = BudgetManager()

    async def gather_evidence(self, intent: Intent) -> ContextResult:
        """Assemble typed evidence from Memory Layer for plan generation.

        Args:
            intent: Validated Intent model (GLOBAL_SPEC SS2.1).

        Returns:
            ContextResult with evidence list, budget info, and degradation
            metadata. Never raises -- returns empty ContextResult on total
            failure.
        """
        start = time.monotonic()
        effective_budget = intent.context_budget or 3

        logger.info(
            "gather_evidence_start",
            extra={
                "intent_type": intent.intent,
                "user_id": intent.user_id,
                "effective_budget": effective_budget,
                "component": "ContextRAG",
                "op": "gather_evidence",
            },
        )

        # 1. Determine eligible sources based on tier
        sources: list = []
        if effective_budget >= 2:
            sources.append(self._profilestore_adapter)
        if effective_budget >= 3:
            sources.append(self._history_adapter)
            sources.append(self._planlibrary_adapter)
            if self._vectorindex_adapter._service is not None:
                sources.append(self._vectorindex_adapter)

        # 2. Tier 1 early return (no Memory Layer sources)
        if not sources:
            return ContextResult(query_duration_ms=_elapsed_ms(start))

        # 3. Concurrent fetch with per-source timeouts
        results = await asyncio.gather(
            *[
                asyncio.wait_for(
                    adapter.fetch_evidence(intent, adapter.default_timeout),
                    timeout=adapter.default_timeout,
                )
                for adapter in sources
            ],
            return_exceptions=True,
        )

        # 4. Collect evidence and degraded sources
        all_evidence: list[EvidenceItem] = []
        degraded: list[str] = []

        for adapter, result in zip(sources, results, strict=True):
            if isinstance(result, SourceQueryError):
                degraded.append(adapter.source_name)
                logger.warning(
                    "source_degraded",
                    extra={
                        "source": adapter.source_name,
                        "reason": result.reason,
                        "intent_type": intent.intent,
                        "trace_id": intent.trace_id,
                        "component": "ContextRAG",
                        "op": "gather_evidence",
                    },
                )
            elif isinstance(result, BaseException):
                degraded.append(adapter.source_name)
                logger.warning(
                    "source_degraded",
                    extra={
                        "source": adapter.source_name,
                        "reason": type(result).__name__,
                        "intent_type": intent.intent,
                        "trace_id": intent.trace_id,
                        "component": "ContextRAG",
                        "op": "gather_evidence",
                    },
                )
            else:
                all_evidence.extend(result)

        # 5. Deduplicate by key
        all_evidence = self._budget_manager.deduplicate(all_evidence)

        # 6. Budget enforcement (sort + trim)
        trimmed, total_bytes = self._budget_manager.enforce_budget(all_evidence)

        duration_ms = _elapsed_ms(start)

        logger.info(
            "gather_evidence_complete",
            extra={
                "intent_type": intent.intent,
                "user_id": intent.user_id,
                "trace_id": intent.trace_id,
                "evidence_count": len(trimmed),
                "total_bytes": total_bytes,
                "degraded_sources": degraded,
                "duration_ms": duration_ms,
                "component": "ContextRAG",
                "op": "gather_evidence",
            },
        )

        # 7. Return result
        return ContextResult(
            evidence=trimmed,
            total_bytes=total_bytes,
            degraded_sources=degraded,
            query_duration_ms=duration_ms,
        )


def create_context_rag_service(
    preference_service: Any,
    fact_service: Any,
    pattern_service: Any,
    plan_service: Any,
    vector_index_service: Any | None,
) -> ContextRAGService:
    """Create ContextRAGService with Memory Layer services.

    Called once during application lifespan startup in shared/app.py.

    Args:
        preference_service: Initialized PreferenceService from ProfileStore.
        fact_service: Initialized FactService from History.
        pattern_service: Initialized PatternService from History.
        plan_service: Initialized PlanService from PlanLibrary.
        vector_index_service: Initialized VectorIndexService, or None.

    Returns:
        Configured ContextRAGService.
    """
    logger.info(
        "context_rag_service_created",
        extra={
            "vectorindex_available": vector_index_service is not None,
            "component": "ContextRAG",
        },
    )
    return ContextRAGService(
        preference_service=preference_service,
        fact_service=fact_service,
        pattern_service=pattern_service,
        plan_service=plan_service,
        vector_index_service=vector_index_service,
    )
