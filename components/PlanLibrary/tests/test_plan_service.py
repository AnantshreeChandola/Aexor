"""
PlanService Unit Tests

Tests for plan storage, retrieval, and Evidence Item integration.
Uses mocked adapters following ProfileStore test patterns.

Reference: tasks.md T204
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from shared.database.error_handler import DatabaseIntegrityError
from shared.schemas.evidence import EvidenceItem

from components.PlanLibrary.domain.models import (
    DuplicatePlanError,
    InvalidSignatureError,
    PlanDB,
    PlanTooLargeError,
    StorePlanResponse,
)
from components.PlanLibrary.service.plan_service import PlanService


# Test constants
VALID_ULID = "01HX1234567890ABCDEFGHJKMN"
VALID_ULID_2 = "01HX9876543210ZYXWVTSRQPNM"


def _make_plan_data(plan_id=VALID_ULID, step_count=3):
    """Create valid plan data for testing."""
    return {
        "plan_id": plan_id,
        "graph": [{"step": i} for i in range(step_count)],
        "meta": {
            "intent_type": "schedule_meeting",
            "created_at": "2025-01-01T00:00:00",
        },
    }


def _make_signature():
    """Create valid signature data for testing."""
    return {
        "algorithm": "ed25519",
        "public_key": "abc123",
        "signature_hex": "def456",
    }


def _make_outcome(success=True):
    """Create valid outcome data for testing."""
    return {
        "success": success,
        "execution_start": "2025-01-01T00:00:00",
        "execution_end": "2025-01-01T00:01:00",
        "total_steps": 3,
        "error_type": None if success else "PROVIDER_ERROR",
        "error_details": None if success else {"msg": "timeout"},
        "failed_step": None if success else 2,
    }


def _make_metrics():
    """Create valid metrics data for testing."""
    return {
        "preview_latency_ms": 100,
        "execute_latency_ms": 500,
        "step_timings": {"step_0": 100, "step_1": 200},
    }


@pytest.fixture
def mock_db_adapter():
    """Create mock database adapter."""
    adapter = MagicMock()
    adapter.store_plan_transaction = AsyncMock(return_value=True)
    adapter.get_plan_by_id = AsyncMock(return_value=None)
    adapter.get_plans_by_intent = AsyncMock(return_value=[])
    adapter.get_plan_outcomes = AsyncMock(return_value=[])
    adapter.get_success_rates = AsyncMock(return_value={})
    adapter.health_check = AsyncMock(return_value=True)
    return adapter


@pytest.fixture
def mock_signature_verifier():
    """Create mock signature verifier that always passes."""
    verifier = MagicMock()
    verifier.verify_signature.return_value = True
    return verifier


@pytest.fixture
def plan_service(mock_db_adapter, mock_signature_verifier):
    """Create PlanService with mocked dependencies."""
    return PlanService(
        db_adapter=mock_db_adapter,
        signature_verifier=mock_signature_verifier,
    )


class TestStorePlan:
    """Tests for PlanService.store_plan()."""

    @pytest.mark.asyncio
    async def test_store_plan_success(self, plan_service, mock_db_adapter):
        """Store plan with valid signature -- success (US-1 scenario 1)."""
        result = await plan_service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(success=True),
            metrics=_make_metrics(),
        )

        assert isinstance(result, StorePlanResponse)
        assert result.status == "ok"
        assert result.plan_id == VALID_ULID
        assert result.stored_at is not None
        mock_db_adapter.store_plan_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_plan_failure_outcome(self, plan_service, mock_db_adapter):
        """Store plan with failure outcome -- records failure (US-1 scenario 2)."""
        result = await plan_service.store_plan(
            plan=_make_plan_data(),
            signature=_make_signature(),
            outcome=_make_outcome(success=False),
            metrics=_make_metrics(),
        )

        assert result.plan_id == VALID_ULID
        # Verify the outcome was passed with success=False
        call_args = mock_db_adapter.store_plan_transaction.call_args
        outcome_arg = call_args.kwargs.get("outcome") or call_args[1].get("outcome")
        if outcome_arg is None:
            outcome_arg = call_args[0][1] if len(call_args[0]) > 1 else None
        assert outcome_arg is not None
        assert outcome_arg.success is False

    @pytest.mark.asyncio
    async def test_store_plan_duplicate_raises_error(
        self, plan_service, mock_db_adapter
    ):
        """Store plan with duplicate plan_id -- DuplicatePlanError (DR 4)."""
        mock_db_adapter.store_plan_transaction.side_effect = (
            DatabaseIntegrityError("duplicate key violates unique constraint")
        )

        with pytest.raises(DuplicatePlanError) as exc_info:
            await plan_service.store_plan(
                plan=_make_plan_data(),
                signature=_make_signature(),
                outcome=_make_outcome(),
                metrics=_make_metrics(),
            )

        assert exc_info.value.plan_id == VALID_ULID

    @pytest.mark.asyncio
    async def test_store_plan_invalid_signature(
        self, plan_service, mock_signature_verifier
    ):
        """Store plan with invalid signature -- InvalidSignatureError (US-1 scenario 4)."""
        mock_signature_verifier.verify_signature.side_effect = (
            InvalidSignatureError(plan_id=VALID_ULID, reason="bad sig")
        )

        with pytest.raises(InvalidSignatureError):
            await plan_service.store_plan(
                plan=_make_plan_data(),
                signature=_make_signature(),
                outcome=_make_outcome(),
                metrics=_make_metrics(),
            )

    @pytest.mark.asyncio
    async def test_store_plan_too_many_steps(self, plan_service):
        """Store plan exceeding step limit -- PlanTooLargeError (DR 5)."""
        with pytest.raises(PlanTooLargeError) as exc_info:
            await plan_service.store_plan(
                plan=_make_plan_data(step_count=101),
                signature=_make_signature(),
                outcome=_make_outcome(),
                metrics=_make_metrics(),
            )

        assert "101 steps" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_store_plan_invalid_plan_id(self, plan_service):
        """Store plan with invalid plan_id -- ValueError (DR 1)."""
        with pytest.raises(ValueError, match="Invalid plan_id"):
            await plan_service.store_plan(
                plan={"plan_id": "not-valid", "graph": [], "meta": {"intent_type": "t"}},
                signature=_make_signature(),
                outcome=_make_outcome(),
                metrics=_make_metrics(),
            )

    @pytest.mark.asyncio
    async def test_store_plan_empty_plan_id(self, plan_service):
        """Store plan with empty plan_id -- ValueError (DR 1)."""
        with pytest.raises(ValueError, match="Invalid plan_id"):
            await plan_service.store_plan(
                plan={"plan_id": "", "graph": [], "meta": {"intent_type": "t"}},
                signature=_make_signature(),
                outcome=_make_outcome(),
                metrics=_make_metrics(),
            )

    @pytest.mark.asyncio
    async def test_store_plan_missing_required_fields(self, plan_service):
        """Store plan with missing fields -- ValueError (DR 2)."""
        with pytest.raises(ValueError, match="missing required fields"):
            await plan_service.store_plan(
                plan={"plan_id": VALID_ULID},  # missing graph and meta
                signature=_make_signature(),
                outcome=_make_outcome(),
                metrics=_make_metrics(),
            )


class TestGetPlansByIntent:
    """Tests for PlanService.get_plans_by_intent()."""

    @pytest.mark.asyncio
    async def test_query_returns_filtered_results(
        self, plan_service, mock_db_adapter
    ):
        """Query by intent with success threshold returns filtered results (US-2 s1)."""
        mock_db_adapter.get_plans_by_intent.return_value = [
            {
                "plan_id": VALID_ULID,
                "intent_type": "schedule_meeting",
                "step_count": 5,
                "success_rate": 0.85,
                "avg_execution_time_ms": 1200.0,
                "total_executions": 10,
            },
        ]

        result = await plan_service.get_plans_by_intent(
            intent_type="schedule_meeting",
            success_threshold=0.7,
        )

        assert len(result) == 1
        assert isinstance(result[0], EvidenceItem)
        assert result[0].type == "plan"
        assert result[0].tier == 3

    @pytest.mark.asyncio
    async def test_query_filters_by_intent(
        self, plan_service, mock_db_adapter
    ):
        """Query filters to matching intent types only (US-2 s2)."""
        mock_db_adapter.get_plans_by_intent.return_value = []

        result = await plan_service.get_plans_by_intent(
            intent_type="nonexistent_intent",
        )

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_query_with_recency(
        self, plan_service, mock_db_adapter
    ):
        """Query with recency preference (US-2 scenario 3)."""
        await plan_service.get_plans_by_intent(
            intent_type="test",
            recency_days=7,
        )

        mock_db_adapter.get_plans_by_intent.assert_called_once_with(
            intent_type="test",
            success_threshold=0.7,
            limit=50,
            recency_days=7,
        )


class TestGetPlanById:
    """Tests for PlanService.get_plan_by_id()."""

    @pytest.mark.asyncio
    async def test_get_plan_found(self, plan_service, mock_db_adapter):
        """Get plan by ID returns plan when found."""
        mock_db_adapter.get_plan_by_id.return_value = PlanDB(
            plan_id=VALID_ULID,
            canonical_json={},
            signature_data={},
            intent_type="test",
            step_count=3,
            plan_hash="a" * 64,
            size_bytes=100,
            created_at=datetime.utcnow(),
        )

        result = await plan_service.get_plan_by_id(VALID_ULID)
        assert result is not None
        assert result.plan_id == VALID_ULID

    @pytest.mark.asyncio
    async def test_get_plan_not_found(self, plan_service, mock_db_adapter):
        """Get plan by ID returns None when not found."""
        mock_db_adapter.get_plan_by_id.return_value = None

        result = await plan_service.get_plan_by_id(VALID_ULID)
        assert result is None


class TestEvidenceItemFormat:
    """Tests for Evidence Item output format compliance."""

    @pytest.mark.asyncio
    async def test_evidence_item_type_plan(
        self, plan_service, mock_db_adapter
    ):
        """Evidence Items have type='plan'."""
        mock_db_adapter.get_plans_by_intent.return_value = [
            {
                "plan_id": VALID_ULID,
                "intent_type": "test",
                "step_count": 3,
                "success_rate": 0.9,
                "avg_execution_time_ms": 100.0,
                "total_executions": 5,
            },
        ]

        result = await plan_service.get_plans_by_intent(intent_type="test")

        assert len(result) == 1
        evidence = result[0]
        assert evidence.type == "plan"
        assert evidence.tier == 3
        assert evidence.ttl_days is None
        assert "planlibrary:plans/" in evidence.source_ref
        assert 0.0 <= evidence.confidence <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
