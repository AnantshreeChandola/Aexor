"""
PlanWriter Service Unit Tests -- persist_outcome and bulk_persist

Tests for PlanWriterService methods with mocked downstream services.

Reference: tasks.md T303, T304
"""

import pytest

from components.PlanLibrary.domain.models import DuplicatePlanError
from components.PlanWriter.domain.models import PlanLibraryWriteError
from components.PlanWriter.service.plan_writer_service import PlanWriterService
from components.PlanWriter.tests.conftest import SAMPLE_PLAN_ID

# ── persist_outcome Tests ────────────────────────────────────────


class TestPersistOutcomeHappyPath:
    """All three writes succeed."""

    @pytest.mark.asyncio
    async def test_all_writes_succeed(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
    ):
        """Returns PersistResult with status=ok and all fields set."""
        result = await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        assert result.status == "ok"
        assert result.plan_id == sample_plan["plan_id"]
        assert result.fact_id is not None
        assert result.embedding_stored is True
        assert result.errors == []

        # Verify downstream services called with correct args
        mock_plan_service.store_plan.assert_awaited_once_with(
            sample_plan,
            sample_signature,
            sample_outcome_success,
            sample_metrics,
        )
        mock_fact_service.store_fact.assert_awaited_once()
        mock_vector_index_service.store_embedding.assert_awaited_once()


class TestPersistOutcomeFailedExecution:
    """outcome.success=False, all writes succeed."""

    @pytest.mark.asyncio
    async def test_failed_execution_still_persists(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_failure,
        sample_metrics,
        sample_user_id,
    ):
        """Failed outcome is persisted with status=ok."""
        result = await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_failure,
            metrics=sample_metrics,
        )
        assert result.status == "ok"
        assert result.fact_id is not None


class TestPersistOutcomeVectorIndexNone:
    """VectorIndex is None -- graceful degradation."""

    @pytest.mark.asyncio
    async def test_vectorindex_none(
        self,
        plan_writer_service_no_vectorindex,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Returns ok with embedding_stored=False."""
        result = await plan_writer_service_no_vectorindex.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        assert result.status == "ok"
        assert result.embedding_stored is False


class TestPersistOutcomeVectorIndexError:
    """VectorIndex raises an error."""

    @pytest.mark.asyncio
    async def test_vectorindex_error(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Returns partial with embedding_stored=False."""
        mock_vector_index_service.store_embedding.side_effect = RuntimeError(
            "embed fail",
        )
        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        result = await service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        assert result.embedding_stored is False
        assert any("VectorIndex" in e for e in result.errors)


class TestPersistOutcomeHistoryFails:
    """History raises -- VectorIndex still attempted."""

    @pytest.mark.asyncio
    async def test_history_fails(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Returns partial with fact_id=None, VectorIndex still called."""
        mock_fact_service.store_fact.side_effect = RuntimeError("history down")
        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        result = await service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        assert result.status == "partial"
        assert result.fact_id is None
        mock_vector_index_service.store_embedding.assert_awaited_once()


class TestPersistOutcomePlanLibraryFails:
    """PlanLibrary raises -- History and VectorIndex NOT called."""

    @pytest.mark.asyncio
    async def test_planlibrary_fails(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Raises PlanLibraryWriteError, no downstream calls."""
        mock_plan_service.store_plan.side_effect = RuntimeError("db error")
        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        with pytest.raises(PlanLibraryWriteError):
            await service.persist_outcome(
                user_id=sample_user_id,
                plan=sample_plan,
                signature=sample_signature,
                outcome=sample_outcome_success,
                metrics=sample_metrics,
            )
        mock_fact_service.store_fact.assert_not_awaited()
        mock_vector_index_service.store_embedding.assert_not_awaited()


class TestPersistOutcomeDuplicatePlan:
    """PlanLibrary raises DuplicatePlanError -- treated as success."""

    @pytest.mark.asyncio
    async def test_duplicate_plan_idempotent(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Duplicate plan is treated as success, downstream still called."""
        mock_plan_service.store_plan.side_effect = DuplicatePlanError(
            plan_id=SAMPLE_PLAN_ID,
        )
        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        result = await service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        assert result.status == "ok"
        mock_fact_service.store_fact.assert_awaited_once()
        mock_vector_index_service.store_embedding.assert_awaited_once()


class TestPersistOutcomeFactDerivationFails:
    """Fact derivation fails -- partial result, VectorIndex still attempted."""

    @pytest.mark.asyncio
    async def test_fact_derivation_fails(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Plan missing intent -> FactDerivation uses fallback, still ok."""
        plan = {"plan_id": SAMPLE_PLAN_ID}  # minimal valid plan
        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        await service.persist_outcome(
            user_id=sample_user_id,
            plan=plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        # Should still attempt VectorIndex
        mock_vector_index_service.store_embedding.assert_awaited_once()


class TestPersistOutcomeValidation:
    """Input validation tests."""

    @pytest.mark.asyncio
    async def test_empty_plan_raises(self, plan_writer_service, sample_user_id):
        """Empty plan dict raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            await plan_writer_service.persist_outcome(
                user_id=sample_user_id,
                plan={},
                signature={},
                outcome={},
                metrics={},
            )

    @pytest.mark.asyncio
    async def test_none_plan_raises(self, plan_writer_service, sample_user_id):
        """None plan raises ValueError."""
        with pytest.raises(ValueError):
            await plan_writer_service.persist_outcome(
                user_id=sample_user_id,
                plan=None,
                signature={},
                outcome={},
                metrics={},
            )

    @pytest.mark.asyncio
    async def test_plan_missing_plan_id_raises(
        self,
        plan_writer_service,
        sample_user_id,
    ):
        """Plan without plan_id raises ValueError."""
        with pytest.raises(ValueError, match="plan_id"):
            await plan_writer_service.persist_outcome(
                user_id=sample_user_id,
                plan={"meta": {}},
                signature={},
                outcome={},
                metrics={},
            )


# ── bulk_persist Tests ───────────────────────────────────────────


class TestBulkPersist:
    """Test bulk_persist with various scenarios."""

    @pytest.mark.asyncio
    async def test_three_successful_outcomes(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """All 3 outcomes succeed."""
        outcomes = [
            {
                "plan": sample_plan,
                "signature": sample_signature,
                "outcome": sample_outcome_success,
                "metrics": sample_metrics,
            }
        ] * 3
        result = await plan_writer_service.bulk_persist(
            user_id=sample_user_id,
            outcomes=outcomes,
        )
        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_mix_success_and_failure(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """2 succeed, 1 PlanLibrary fails."""
        call_count = 0

        async def store_plan_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("db error")
            from datetime import UTC, datetime

            from components.PlanLibrary.domain.models import StorePlanResponse

            return StorePlanResponse(
                plan_id=SAMPLE_PLAN_ID,
                stored_at=datetime.now(UTC),
            )

        mock_plan_service.store_plan.side_effect = store_plan_side_effect

        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        outcomes = [
            {
                "plan": sample_plan,
                "signature": sample_signature,
                "outcome": sample_outcome_success,
                "metrics": sample_metrics,
            }
        ] * 3
        result = await service.bulk_persist(
            user_id=sample_user_id,
            outcomes=outcomes,
        )
        assert result.total == 3
        assert result.succeeded == 2
        assert result.failed == 1

    @pytest.mark.asyncio
    async def test_empty_list_raises(self, plan_writer_service, sample_user_id):
        """Empty outcomes list raises ValueError."""
        with pytest.raises(ValueError, match="not be empty"):
            await plan_writer_service.bulk_persist(
                user_id=sample_user_id,
                outcomes=[],
            )

    @pytest.mark.asyncio
    async def test_single_item(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Single item works correctly."""
        outcomes = [
            {
                "plan": sample_plan,
                "signature": sample_signature,
                "outcome": sample_outcome_success,
                "metrics": sample_metrics,
            },
        ]
        result = await plan_writer_service.bulk_persist(
            user_id=sample_user_id,
            outcomes=outcomes,
        )
        assert result.total == 1
        assert result.succeeded == 1
