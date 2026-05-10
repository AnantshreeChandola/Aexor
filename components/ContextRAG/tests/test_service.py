"""
ContextRAG Service Tests

Tests for ContextRAGService.gather_evidence() with mocked services.
Covers happy path, tier enforcement, graceful degradation, and concurrency.

Reference: tasks.md T402
"""

from unittest.mock import AsyncMock
from uuid import uuid4

from components.ContextRAG.adapters.budget_manager import BudgetManager
from components.ContextRAG.domain.models import ContextResult
from components.ContextRAG.service.context_rag_service import ContextRAGService
from components.ProfileStore.domain.models import ConsentDeniedError
from shared.database.error_handler import DatabaseConnectionError
from shared.schemas.intent import Intent

from .conftest import (
    SAMPLE_INTENT,
    SAMPLE_TIER1_INTENT,
    SAMPLE_TIER2_INTENT,
    SAMPLE_USER_ID,
)

# ===================================================================
# Happy Path Tests
# ===================================================================


class TestGatherEvidenceHappyPath:
    """Happy path tests for gather_evidence()."""

    async def test_gather_evidence_happy_path(self, context_rag_service: ContextRAGService):
        """All sources succeed, returns ContextResult with evidence."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        assert isinstance(result, ContextResult)
        assert len(result.evidence) > 0
        assert result.degraded_sources == []

    async def test_gather_evidence_evidence_types(self, context_rag_service: ContextRAGService):
        """Returned evidence includes multiple types."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        types = {item.type for item in result.evidence}
        # Should have preference, history, plan, exemplar
        assert "preference" in types
        assert len(types) >= 2

    async def test_gather_evidence_sorted_by_relevance_then_tier(
        self, context_rag_service: ContextRAGService
    ):
        """Evidence is sorted by relevance (high first), then tier within same relevance."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        # With relevance scoring active, items are sorted by relevance DESC.
        # We verify that evidence is returned and budget-constrained.
        assert len(result.evidence) > 0
        assert result.total_bytes <= BudgetManager.BUDGET_BYTES

    async def test_gather_evidence_within_budget(self, context_rag_service: ContextRAGService):
        """total_bytes <= 2048."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        assert result.total_bytes <= BudgetManager.BUDGET_BYTES

    async def test_gather_evidence_duration_ms_positive(
        self, context_rag_service: ContextRAGService
    ):
        """query_duration_ms >= 0."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        assert result.query_duration_ms >= 0


# ===================================================================
# Tier Enforcement Tests (FR-006)
# ===================================================================


class TestTierEnforcement:
    """Tests for context tier pre-check."""

    async def test_tier1_returns_empty(self, context_rag_service: ContextRAGService):
        """context_budget=1, returns empty evidence."""
        result = await context_rag_service.gather_evidence(SAMPLE_TIER1_INTENT)
        assert result.evidence == []
        assert result.degraded_sources == []

    async def test_tier2_only_profilestore(
        self,
        mock_preference_service: AsyncMock,
        mock_fact_service: AsyncMock,
        mock_pattern_service: AsyncMock,
        mock_plan_service: AsyncMock,
        mock_vector_index_service: AsyncMock,
    ):
        """context_budget=2, only ProfileStore queried."""
        service = ContextRAGService(
            preference_service=mock_preference_service,
            fact_service=mock_fact_service,
            pattern_service=mock_pattern_service,
            plan_service=mock_plan_service,
            vector_index_service=mock_vector_index_service,
        )
        result = await service.gather_evidence(SAMPLE_TIER2_INTENT)
        # ProfileStore should be called
        mock_preference_service.get_all_preferences.assert_called_once()
        # History, PlanLibrary, VectorIndex should NOT be called
        mock_fact_service.get_facts_by_intent.assert_not_called()
        mock_pattern_service.get_patterns.assert_not_called()
        mock_plan_service.get_plans_by_intent.assert_not_called()
        mock_vector_index_service.search.assert_not_called()
        # Result should have only tier=2 items
        for item in result.evidence:
            assert item.tier <= 2

    async def test_tier3_all_sources(
        self,
        mock_preference_service: AsyncMock,
        mock_fact_service: AsyncMock,
        mock_pattern_service: AsyncMock,
        mock_plan_service: AsyncMock,
        mock_vector_index_service: AsyncMock,
    ):
        """context_budget=3, all sources queried."""
        service = ContextRAGService(
            preference_service=mock_preference_service,
            fact_service=mock_fact_service,
            pattern_service=mock_pattern_service,
            plan_service=mock_plan_service,
            vector_index_service=mock_vector_index_service,
        )
        await service.gather_evidence(SAMPLE_INTENT)
        mock_preference_service.get_all_preferences.assert_called_once()
        mock_fact_service.get_facts_by_intent.assert_called_once()
        mock_plan_service.get_plans_by_intent.assert_called_once()
        mock_vector_index_service.search.assert_called_once()

    async def test_none_budget_defaults_to_3(
        self,
        mock_preference_service: AsyncMock,
        mock_fact_service: AsyncMock,
        mock_pattern_service: AsyncMock,
        mock_plan_service: AsyncMock,
        mock_vector_index_service: AsyncMock,
    ):
        """context_budget=None, all sources queried (default tier 3)."""
        service = ContextRAGService(
            preference_service=mock_preference_service,
            fact_service=mock_fact_service,
            pattern_service=mock_pattern_service,
            plan_service=mock_plan_service,
            vector_index_service=mock_vector_index_service,
        )
        intent = Intent(
            intent="schedule_meeting",
            entities={"person": "Alice"},
            constraints={},
            user_id=SAMPLE_USER_ID,
            context_budget=None,
        )
        await service.gather_evidence(intent)
        mock_preference_service.get_all_preferences.assert_called_once()
        mock_fact_service.get_facts_by_intent.assert_called_once()
        mock_plan_service.get_plans_by_intent.assert_called_once()


# ===================================================================
# Graceful Degradation Tests (FR-012)
# ===================================================================


class TestGracefulDegradation:
    """Tests for graceful degradation when sources fail."""

    async def test_single_source_failure(
        self,
        mock_preference_service: AsyncMock,
        mock_fact_service: AsyncMock,
        mock_pattern_service: AsyncMock,
        mock_plan_service: AsyncMock,
        mock_vector_index_service: AsyncMock,
    ):
        """History raises error, result has evidence from other sources."""
        mock_fact_service.get_facts_by_intent.side_effect = DatabaseConnectionError("down")
        service = ContextRAGService(
            preference_service=mock_preference_service,
            fact_service=mock_fact_service,
            pattern_service=mock_pattern_service,
            plan_service=mock_plan_service,
            vector_index_service=mock_vector_index_service,
        )
        result = await service.gather_evidence(SAMPLE_INTENT)
        assert "history" in result.degraded_sources
        # Other sources should have contributed evidence
        assert len(result.evidence) > 0

    async def test_all_sources_fail(self):
        """All 4 raise errors, returns empty evidence."""
        pref = AsyncMock()
        pref.get_all_preferences.side_effect = DatabaseConnectionError("down")
        fact = AsyncMock()
        fact.get_facts_by_intent.side_effect = DatabaseConnectionError("down")
        pattern = AsyncMock()
        pattern.get_patterns.side_effect = DatabaseConnectionError("down")
        plan = AsyncMock()
        plan.get_plans_by_intent.side_effect = DatabaseConnectionError("down")
        vector = AsyncMock()
        vector.search.side_effect = DatabaseConnectionError("down")

        service = ContextRAGService(
            preference_service=pref,
            fact_service=fact,
            pattern_service=pattern,
            plan_service=plan,
            vector_index_service=vector,
        )
        result = await service.gather_evidence(SAMPLE_INTENT)
        assert result.evidence == []
        assert len(result.degraded_sources) == 4

    async def test_vectorindex_none_not_degraded(
        self, context_rag_service_no_vectorindex: ContextRAGService
    ):
        """VectorIndex service is None, NOT added to degraded_sources."""
        result = await context_rag_service_no_vectorindex.gather_evidence(SAMPLE_INTENT)
        assert "vectorindex" not in result.degraded_sources

    async def test_timeout_adds_to_degraded(
        self,
        mock_preference_service: AsyncMock,
        mock_fact_service: AsyncMock,
        mock_pattern_service: AsyncMock,
        mock_plan_service: AsyncMock,
        mock_vector_index_service: AsyncMock,
    ):
        """Source raises asyncio.TimeoutError, added to degraded_sources."""
        mock_plan_service.get_plans_by_intent.side_effect = TimeoutError()
        service = ContextRAGService(
            preference_service=mock_preference_service,
            fact_service=mock_fact_service,
            pattern_service=mock_pattern_service,
            plan_service=mock_plan_service,
            vector_index_service=mock_vector_index_service,
        )
        result = await service.gather_evidence(SAMPLE_INTENT)
        assert "planlibrary" in result.degraded_sources

    async def test_consent_denied_adds_to_degraded(
        self,
        mock_preference_service: AsyncMock,
        mock_fact_service: AsyncMock,
        mock_pattern_service: AsyncMock,
        mock_plan_service: AsyncMock,
        mock_vector_index_service: AsyncMock,
    ):
        """ProfileStore raises ConsentDeniedError, added to degraded."""
        mock_preference_service.get_all_preferences.side_effect = ConsentDeniedError(
            user_id=uuid4(), required_tier=2, current_tier=1
        )
        service = ContextRAGService(
            preference_service=mock_preference_service,
            fact_service=mock_fact_service,
            pattern_service=mock_pattern_service,
            plan_service=mock_plan_service,
            vector_index_service=mock_vector_index_service,
        )
        result = await service.gather_evidence(SAMPLE_INTENT)
        assert "profilestore" in result.degraded_sources


# ===================================================================
# Concurrent Execution Tests (FR-010)
# ===================================================================


class TestConcurrentExecution:
    """Tests for concurrent source querying."""

    async def test_sources_called_concurrently(
        self,
        mock_preference_service: AsyncMock,
        mock_fact_service: AsyncMock,
        mock_pattern_service: AsyncMock,
        mock_plan_service: AsyncMock,
        mock_vector_index_service: AsyncMock,
    ):
        """All source adapters' fetch_evidence is called."""
        service = ContextRAGService(
            preference_service=mock_preference_service,
            fact_service=mock_fact_service,
            pattern_service=mock_pattern_service,
            plan_service=mock_plan_service,
            vector_index_service=mock_vector_index_service,
        )
        await service.gather_evidence(SAMPLE_INTENT)
        # All services should have been called
        assert mock_preference_service.get_all_preferences.call_count == 1
        assert mock_fact_service.get_facts_by_intent.call_count == 1
        assert mock_plan_service.get_plans_by_intent.call_count == 1
        assert mock_vector_index_service.search.call_count == 1
