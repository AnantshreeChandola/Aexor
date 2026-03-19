"""
PlanWriter Observability Tests

Verifies log safety: no raw plan content, embeddings, signatures,
or credentials in log output. Verifies structured log events.

Reference: tasks.md T500
"""

import json
import logging

import pytest

from components.PlanWriter.service.plan_writer_service import PlanWriterService
from components.PlanWriter.tests.conftest import SAMPLE_PLAN_ID


@pytest.fixture()
def caplog_planwriter(caplog):
    """Capture planwriter logger output at DEBUG level."""
    with caplog.at_level(logging.DEBUG, logger="planwriter"):
        yield caplog


class TestLogSafety:
    """Verify logs do not leak sensitive data."""

    @pytest.mark.asyncio
    async def test_persist_does_not_log_plan_json(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """Logs must not contain plan graph steps or action names."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        log_text = caplog_planwriter.text
        assert "search_flights" not in log_text
        assert "select_flight" not in log_text
        assert json.dumps(sample_plan["graph"]) not in log_text

    @pytest.mark.asyncio
    async def test_persist_does_not_log_signature_bytes(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """Logs must not contain signature base64 value."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        log_text = caplog_planwriter.text
        assert sample_signature["signature"] not in log_text

    @pytest.mark.asyncio
    async def test_persist_does_not_log_metrics_payload(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """Logs must not contain raw step_timings array."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        log_text = caplog_planwriter.text
        assert json.dumps(sample_metrics["step_timings"]) not in log_text

    @pytest.mark.asyncio
    async def test_persist_logs_plan_id(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """Logs must contain the plan_id in extra fields."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        plan_id_logged = any(
            getattr(r, "plan_id", None) == SAMPLE_PLAN_ID for r in caplog_planwriter.records
        )
        assert plan_id_logged

    @pytest.mark.asyncio
    async def test_persist_logs_status(
        self,
        plan_writer_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """Logs must contain the status value."""
        await plan_writer_service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        log_text = caplog_planwriter.text
        assert "outcome_persisted" in log_text

    @pytest.mark.asyncio
    async def test_partial_failure_logs_warning(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """History failure emits WARNING with persist_partial_failure."""
        mock_fact_service.store_fact.side_effect = RuntimeError("history down")
        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        await service.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        warning_records = [
            r
            for r in caplog_planwriter.records
            if r.levelno == logging.WARNING and "persist_partial_failure" in r.message
        ]
        assert len(warning_records) >= 1
        plan_id_in_warnings = any(
            getattr(r, "plan_id", None) == SAMPLE_PLAN_ID for r in warning_records
        )
        assert plan_id_in_warnings

    @pytest.mark.asyncio
    async def test_vectorindex_unavailable_logs_warning(
        self,
        plan_writer_service_no_vectorindex,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """VectorIndex=None emits WARNING with vectorindex_unavailable."""
        await plan_writer_service_no_vectorindex.persist_outcome(
            user_id=sample_user_id,
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome_success,
            metrics=sample_metrics,
        )
        warning_records = [
            r
            for r in caplog_planwriter.records
            if r.levelno == logging.WARNING and "vectorindex_unavailable" in r.message
        ]
        assert len(warning_records) >= 1

    @pytest.mark.asyncio
    async def test_planlibrary_failure_logs_error(
        self,
        mock_plan_service,
        mock_fact_service,
        mock_vector_index_service,
        sample_plan,
        sample_signature,
        sample_outcome_success,
        sample_metrics,
        sample_user_id,
        caplog_planwriter,
    ):
        """PlanLibrary failure emits ERROR with persist_failed."""
        mock_plan_service.store_plan.side_effect = RuntimeError("db crash")
        service = PlanWriterService(
            mock_plan_service,
            mock_fact_service,
            mock_vector_index_service,
        )
        with pytest.raises(Exception):
            await service.persist_outcome(
                user_id=sample_user_id,
                plan=sample_plan,
                signature=sample_signature,
                outcome=sample_outcome_success,
                metrics=sample_metrics,
            )
        error_records = [
            r
            for r in caplog_planwriter.records
            if r.levelno == logging.ERROR and "persist_failed" in r.message
        ]
        assert len(error_records) >= 1
