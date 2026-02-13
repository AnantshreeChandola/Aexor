"""
PlanLibrary Integration Tests

End-to-end flow tests with mocked database.
Tests service layer integration and graceful degradation.

Reference: tasks.md T601
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from shared.schemas.evidence import EvidenceItem

from components.PlanLibrary.domain.models import (
    EmbeddingServiceError,
    PlanDB,
    SimilarityMatch,
    StorePlanResponse,
)
from components.PlanLibrary.service.analytics_service import AnalyticsService
from components.PlanLibrary.service.evidence_service import EvidenceService
from components.PlanLibrary.service.plan_service import PlanService
from components.PlanLibrary.service.vector_service import VectorService


VALID_ULID = "01HX1234567890ABCDEFGHJKMN"
VALID_ULID_2 = "01HX9876543210ZYXWVTSRQPNM"


def _make_plan_data(plan_id=VALID_ULID, intent="schedule_meeting", steps=3):
    """Create valid plan data."""
    return {
        "plan_id": plan_id,
        "graph": [{"step": i} for i in range(steps)],
        "meta": {
            "intent_type": intent,
            "created_at": "2025-01-01T00:00:00",
        },
    }


def _make_signature():
    return {
        "algorithm": "ed25519",
        "public_key": "abc123",
        "signature_hex": "def456",
    }


def _make_outcome(success=True):
    return {
        "success": success,
        "execution_start": "2025-01-01T00:00:00",
        "execution_end": "2025-01-01T00:01:00",
        "total_steps": 3,
    }


def _make_metrics():
    return {"execute_latency_ms": 500}


@pytest.fixture
def mock_db():
    """Create mock database adapter."""
    db = MagicMock()
    db.store_plan_transaction = AsyncMock(return_value=True)
    db.get_plan_by_id = AsyncMock(return_value=None)
    db.get_plans_by_intent = AsyncMock(return_value=[])
    db.get_plan_outcomes = AsyncMock(return_value=[])
    db.get_success_rates = AsyncMock(return_value={})
    db.health_check = AsyncMock(return_value=True)
    return db


@pytest.fixture
def mock_vector_adapter():
    """Create mock vector adapter."""
    adapter = MagicMock()
    adapter.similarity_search = AsyncMock(return_value=[])
    adapter.store_embedding = AsyncMock(return_value=True)
    return adapter


@pytest.fixture
def mock_embedding_client():
    """Create mock embedding client."""
    client = MagicMock()
    client.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
    return client


@pytest.fixture
def mock_sig_verifier():
    """Create mock signature verifier."""
    verifier = MagicMock()
    verifier.verify_signature.return_value = True
    return verifier


class TestStoreThenQuery:
    """Test store plan -> query by intent flow."""

    @pytest.mark.asyncio
    async def test_store_and_query_by_intent(
        self, mock_db, mock_sig_verifier
    ):
        """Store plan then query by intent returns evidence items."""
        vector_service = MagicMock()
        vector_service.queue_embedding_generation = AsyncMock(
            return_value=True
        )

        service = PlanService(
            db_adapter=mock_db,
            vector_service=vector_service,
            signature_verifier=mock_sig_verifier,
        )

        # Step 1: Store plan
        result = await service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(success=True),
            metrics=_make_metrics(),
        )
        assert result.plan_id == VALID_ULID

        # Step 2: Query by intent
        mock_db.get_plans_by_intent.return_value = [
            {
                "plan_id": VALID_ULID,
                "intent_type": "schedule_meeting",
                "step_count": 3,
                "success_rate": 1.0,
                "avg_execution_time_ms": 500.0,
                "total_executions": 1,
            },
        ]

        evidence = await service.get_plans_by_intent(
            intent_type="schedule_meeting"
        )

        assert len(evidence) == 1
        assert isinstance(evidence[0], EvidenceItem)
        assert evidence[0].type == "plan"
        assert evidence[0].tier == 3


class TestStoreThenSimilaritySearch:
    """Test store plan -> similarity search flow."""

    @pytest.mark.asyncio
    async def test_store_and_similarity_search(
        self,
        mock_db,
        mock_vector_adapter,
        mock_embedding_client,
        mock_sig_verifier,
    ):
        """Store plan then find via similarity search."""
        vector_svc = VectorService(
            vector_adapter=mock_vector_adapter,
            embedding_client=mock_embedding_client,
        )

        plan_service = PlanService(
            db_adapter=mock_db,
            vector_service=vector_svc,
            signature_verifier=mock_sig_verifier,
        )

        # Step 1: Store plan
        result = await plan_service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(),
            metrics=_make_metrics(),
        )
        assert result.plan_id == VALID_ULID

        # Step 2: Similarity search
        mock_vector_adapter.similarity_search.return_value = [
            SimilarityMatch(
                plan_id=VALID_ULID,
                similarity_score=0.9,
                success_rate=0.85,
                intent_type="schedule_meeting",
                pattern_summary="Schedule meeting plan with 3 steps",
            ),
        ]

        evidence = await vector_svc.similarity_search(
            query_text="book a meeting",
            similarity_threshold=0.5,
        )

        assert len(evidence) == 1
        assert evidence[0].type == "plan"


class TestStoreThenAnalytics:
    """Test store multiple plans -> analytics flow."""

    @pytest.mark.asyncio
    async def test_store_and_get_success_rates(
        self, mock_db, mock_sig_verifier
    ):
        """Store multiple plans then check analytics."""
        service = PlanService(
            db_adapter=mock_db,
            signature_verifier=mock_sig_verifier,
        )

        # Store plans
        await service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(success=True),
            metrics=_make_metrics(),
        )

        # Analytics
        analytics = AnalyticsService(db_adapter=mock_db)
        mock_db.get_success_rates.return_value = {
            "schedule_meeting": 0.85,
        }

        rates = await analytics.calculate_success_rates()
        assert "schedule_meeting" in rates
        assert rates["schedule_meeting"] == 0.85


class TestStoreFailureOutcome:
    """Test storing plans with failure outcomes."""

    @pytest.mark.asyncio
    async def test_failure_outcome_filters_below_threshold(
        self, mock_db, mock_sig_verifier
    ):
        """Plans with failure outcomes filtered by success threshold."""
        service = PlanService(
            db_adapter=mock_db,
            signature_verifier=mock_sig_verifier,
        )

        # Store failed plan
        await service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(success=False),
            metrics=_make_metrics(),
        )

        # Query with high threshold -- should return empty
        mock_db.get_plans_by_intent.return_value = []

        evidence = await service.get_plans_by_intent(
            intent_type="schedule_meeting",
            success_threshold=0.7,
        )
        assert len(evidence) == 0


class TestGracefulDegradation:
    """Test graceful degradation when services are unavailable."""

    @pytest.mark.asyncio
    async def test_plan_stored_without_embedding(
        self, mock_db, mock_sig_verifier
    ):
        """Plan stored even when embedding API is down."""
        mock_vector_svc = MagicMock()
        mock_vector_svc.queue_embedding_generation = AsyncMock(
            side_effect=EmbeddingServiceError("API down")
        )

        service = PlanService(
            db_adapter=mock_db,
            vector_service=mock_vector_svc,
            signature_verifier=mock_sig_verifier,
        )

        # Should succeed even with embedding failure
        result = await service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(),
            metrics=_make_metrics(),
        )

        assert result.plan_id == VALID_ULID
        mock_db.store_plan_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_similarity_search_returns_empty_on_embedding_failure(
        self, mock_vector_adapter, mock_embedding_client
    ):
        """Similarity search returns empty when embedding fails."""
        mock_embedding_client.generate_embedding.side_effect = (
            EmbeddingServiceError("Circuit breaker open")
        )

        svc = VectorService(
            vector_adapter=mock_vector_adapter,
            embedding_client=mock_embedding_client,
        )

        result = await svc.similarity_search(query_text="test")
        assert result == []


class TestPlanServiceVectorServiceIntegration:
    """Test PlanService + VectorService integration."""

    @pytest.mark.asyncio
    async def test_embedding_queued_after_storage(
        self,
        mock_db,
        mock_vector_adapter,
        mock_embedding_client,
        mock_sig_verifier,
    ):
        """Embedding is queued after plan is stored."""
        vector_svc = VectorService(
            vector_adapter=mock_vector_adapter,
            embedding_client=mock_embedding_client,
        )

        service = PlanService(
            db_adapter=mock_db,
            vector_service=vector_svc,
            signature_verifier=mock_sig_verifier,
        )

        result = await service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(),
            metrics=_make_metrics(),
        )

        assert result.embedding_queued is True


class TestEvidenceServiceIntegration:
    """Test EvidenceService produces correct Evidence Items."""

    def test_evidence_items_formatted_correctly(self):
        """Evidence Items include all required fields."""
        service = EvidenceService()
        plans = [
            {
                "plan_id": VALID_ULID,
                "intent_type": "schedule_meeting",
                "step_count": 5,
                "success_rate": 0.9,
                "avg_execution_time_ms": 1000.0,
            },
        ]

        items = service.to_evidence_items(plans)
        assert len(items) == 1

        item = items[0]
        assert item.type == "plan"
        assert item.tier == 3
        assert item.ttl_days is None
        assert 0.0 <= item.confidence <= 1.0
        assert "planlibrary:" in item.source_ref
        assert "intent" in item.value
        assert "success_rate" in item.value


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
