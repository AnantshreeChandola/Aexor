"""
PlanWriter Contract Tests

End-to-end contract tests mapping 1:1 to SPEC User Stories and
Edge Cases. Uses mocked downstream services but exercises the
full persist_outcome() flow including fact derivation.

Reference: tasks.md T600
"""

import json
import logging

import pytest

from components.PlanLibrary.domain.models import DuplicatePlanError
from components.PlanWriter.domain.models import PlanLibraryWriteError
from components.PlanWriter.service.plan_writer_service import PlanWriterService
from components.PlanWriter.tests.conftest import SAMPLE_PLAN_ID

# ── US1: Persist Successful Execution ────────────────────────────


class TestUS1_PersistSuccessfulExecution:
    """SPEC User Story 1: Persist successful plan execution."""

    @pytest.mark.asyncio
    async def test_scenario1_all_three_services_called(
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
        """PlanLibrary, History, VectorIndex all called with correct args."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        mock_plan_service.store_plan.assert_awaited_once()
        mock_fact_service.store_fact.assert_awaited_once()
        mock_vector_index_service.store_embedding.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scenario2_returns_complete_persist_result(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """Returns PersistResult with all fields correctly set."""
        result = await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        assert result.plan_id == SAMPLE_PLAN_ID
        assert result.fact_id is not None
        assert result.embedding_stored is True
        assert result.status == "ok"
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_scenario3_planlibrary_receives_unmodified_args(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        mock_plan_service,
    ):
        """PlanLibrary receives plan, signature, outcome, metrics unmodified."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        mock_plan_service.store_plan.assert_awaited_once_with(
            sample_plan,
            sample_signature,
            sample_outcome_success,
            sample_metrics,
        )

    @pytest.mark.asyncio
    async def test_persist_result_schema_matches_spec(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """PersistResult JSON serialization matches SPEC output format."""
        result = await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        data = json.loads(result.model_dump_json())
        assert set(data.keys()) == {
            "plan_id",
            "fact_id",
            "embedding_stored",
            "status",
            "errors",
        }
        assert isinstance(data["plan_id"], str)
        assert isinstance(data["embedding_stored"], bool)
        assert data["status"] in ("ok", "partial", "error")
        assert isinstance(data["errors"], list)


# ── US2: Persist Failed Execution ────────────────────────────────


class TestUS2_PersistFailedExecution:
    """SPEC User Story 2: Persist failed plan execution."""

    @pytest.mark.asyncio
    async def test_scenario1_failure_passed_to_all_services(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_failure,
        sample_metrics,
        sample_user_id,
        mock_plan_service,
        mock_fact_service,
    ):
        """Failed outcome passed to PlanLibrary; History fact has outcome=False."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_failure,
            metrics=sample_metrics,
        )
        # Verify PlanLibrary called with failure outcome
        call_args = mock_plan_service.store_plan.call_args
        assert call_args[0][2]["success"] is False

        # Verify History fact has outcome=False
        fact_call_args = mock_fact_service.store_fact.call_args
        fact_request = fact_call_args[0][1]
        assert fact_request.outcome is False

    @pytest.mark.asyncio
    async def test_scenario2_derived_fact_describes_failure(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_failure,
        sample_metrics,
        sample_user_id,
        mock_fact_service,
    ):
        """Derived fact_text describes the failure."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_failure,
            metrics=sample_metrics,
        )
        fact_call_args = mock_fact_service.store_fact.call_args
        fact_request = fact_call_args[0][1]
        assert "Failed" in fact_request.fact_text
        assert "timeout" in fact_request.fact_text


# ── US3: Graceful Degradation ────────────────────────────────────


class TestUS3_GracefulDegradation:
    """SPEC User Story 3: VectorIndex unavailable."""

    @pytest.mark.asyncio
    async def test_scenario1_vectorindex_none(
        self,
        plan_writer_service_no_vectorindex,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        mock_plan_service,
        mock_fact_service,
        caplog,
    ):
        """VectorIndex=None: PlanLibrary+History succeed, warning logged."""
        with caplog.at_level(logging.WARNING, logger="planwriter"):
            result = await plan_writer_service_no_vectorindex.persist_outcome(
                user_id=sample_user_id,
                plan=sample_plan,
                signature=sample_signature,
                outcome=sample_outcome_success,
                metrics=sample_metrics,
            )
        assert result.embedding_stored is False
        assert result.status == "ok"
        mock_plan_service.store_plan.assert_awaited_once()
        mock_fact_service.store_fact.assert_awaited_once()
        assert any("vectorindex_unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_scenario2_vectorindex_raises(
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
        """VectorIndex raises: PlanLibrary+History succeed, embedding_stored=False."""
        mock_vector_index_service.store_embedding.side_effect = RuntimeError("fail")
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
        mock_plan_service.store_plan.assert_awaited_once()
        mock_fact_service.store_fact.assert_awaited_once()


# ── US4: Derive Facts from Execution ─────────────────────────────


class TestUS4_DeriveFactsFromExecution:
    """SPEC User Story 4: Fact derivation quality."""

    @pytest.mark.asyncio
    async def test_scenario1_book_flight_fact_text(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        mock_fact_service,
    ):
        """book_flight with entities -> human-readable fact_text."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        fact_call_args = mock_fact_service.store_fact.call_args
        fact_request = fact_call_args[0][1]
        assert fact_request.intent_type == "book_flight"
        assert fact_request.source_plan_id == SAMPLE_PLAN_ID
        assert "NYC" in fact_request.fact_text
        assert "Delta" in fact_request.fact_text

    @pytest.mark.asyncio
    async def test_scenario2_no_raw_api_responses(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        mock_fact_service,
    ):
        """fact_text does not contain raw API responses or plan JSON."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        fact_call_args = mock_fact_service.store_fact.call_args
        fact_request = fact_call_args[0][1]
        plan_json = json.dumps(sample_plan)
        assert plan_json not in fact_request.fact_text
        assert "search_flights" not in fact_request.fact_text

    @pytest.mark.asyncio
    async def test_scenario3_no_entities(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """No entities -> entities={}, fact_text uses intent_type only."""
        plan = {
            "plan_id": SAMPLE_PLAN_ID,
            "meta": {"intent_type": "check_status"},
        }
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
        fact_call_args = mock_fact_service.store_fact.call_args
        fact_request = fact_call_args[0][1]
        assert fact_request.entities == {}
        assert "check_status" in fact_request.fact_text


# ── US5: Bulk Persist ────────────────────────────────────────────


class TestUS5_BulkPersist:
    """SPEC User Story 5: Bulk persist."""

    @pytest.mark.asyncio
    async def test_scenario1_ten_outcomes(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
    ):
        """10 outcomes -> all 10 persisted."""
        outcomes = [
            {
                "plan": sample_plan,
                "signature": sample_signature,
                "outcome": sample_outcome_success,
                "metrics": sample_metrics,
            }
        ] * 10
        result = await plan_writer_service.bulk_persist(
            user_id=sample_user_id,
            outcomes=outcomes,
        )
        assert result.total == 10
        assert result.succeeded == 10
        assert len(result.results) == 10

    @pytest.mark.asyncio
    async def test_scenario2_empty_list(
        self,
        plan_writer_service,
        sample_user_id,
    ):
        """Empty list -> ValueError."""
        with pytest.raises(ValueError):
            await plan_writer_service.bulk_persist(
                user_id=sample_user_id,
                outcomes=[],
            )


# ── Edge Cases ───────────────────────────────────────────────────


class TestEdgeCases:
    """SPEC Edge Cases."""

    @pytest.mark.asyncio
    async def test_planlibrary_fails_entire_persist_fails(
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
        """PlanLibrary fails -> entire persist_outcome fails."""
        mock_plan_service.store_plan.side_effect = RuntimeError("db down")
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

    @pytest.mark.asyncio
    async def test_history_fails_vectorindex_still_attempted(
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
        """History fails after PlanLibrary -> partial, VectorIndex still attempted."""
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

    @pytest.mark.asyncio
    async def test_same_plan_id_twice_idempotent(
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
        """Same plan_id twice -> idempotent success."""
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
        assert result.plan_id == SAMPLE_PLAN_ID
