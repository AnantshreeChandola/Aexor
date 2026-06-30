"""
PreviewOrchestrator observability tests -- structured logging, no PII.

Validates that all preview operations emit structured log records with
plan_id correlation, and that no step args or results appear in logs.
~10 tests.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from components.ExecuteOrchestrator.domain.models import MCPInvocationError
from components.PreviewOrchestrator.domain.models import PreviewRequest
from components.PreviewOrchestrator.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
)

# ---------------------------------------------------------------------------
# Log capture helper
# ---------------------------------------------------------------------------


def _collect_records(
    caplog: pytest.LogCaptureFixture,
    name_prefix: str = "components.PreviewOrchestrator",
) -> list[logging.LogRecord]:
    """Collect log records from PreviewOrchestrator loggers."""
    return [r for r in caplog.records if r.name.startswith(name_prefix)]


def _find_records(records: list[logging.LogRecord], message: str) -> list[logging.LogRecord]:
    """Find records matching a message substring."""
    return [r for r in records if message in r.getMessage()]


# ---------------------------------------------------------------------------
# Structured logging tests
# ---------------------------------------------------------------------------


class TestStructuredLogging:
    """Verify all required log events are emitted."""

    async def test_preview_started_logged(self, preview_service, sample_plan, caplog):
        """preview_started log emitted with plan_id, user_id, trace_id."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        records = _find_records(_collect_records(caplog), "preview_started")
        assert len(records) == 1
        extra = records[0].__dict__
        assert extra.get("plan_id") == SAMPLE_PLAN_ID
        assert extra.get("user_id") == SAMPLE_USER_ID
        assert extra.get("trace_id") == SAMPLE_TRACE_ID

    async def test_step_completed_logged_with_latency(self, preview_service, sample_plan, caplog):
        """step_completed log emitted for each completed step."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        records = _find_records(_collect_records(caplog), "step_completed")
        assert len(records) >= 1
        for r in records:
            assert "latency_ms" in r.__dict__
            assert r.__dict__["latency_ms"] >= 0

    async def test_step_deferred_logged_with_reason(self, preview_service, sample_plan, caplog):
        """step_deferred log emitted for deferred steps."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        records = _find_records(_collect_records(caplog), "step_deferred")
        assert len(records) >= 1
        for r in records:
            assert "reason" in r.__dict__

    async def test_step_failed_logged_at_warning(
        self, preview_service, parallel_plan, mock_mcp_client, caplog
    ):
        """step_failed log emitted at WARNING level."""
        mock_mcp_client.invoke = AsyncMock(side_effect=MCPInvocationError("srv", "tool", "timeout"))
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=parallel_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        records = _find_records(_collect_records(caplog), "step_failed")
        assert len(records) >= 1
        for r in records:
            assert r.levelno == logging.WARNING

    async def test_preview_completed_logged_with_summary(
        self, preview_service, sample_plan, caplog
    ):
        """preview_completed log emitted with summary counts."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        records = _find_records(_collect_records(caplog), "preview_completed")
        assert len(records) == 1
        extra = records[0].__dict__
        assert "total_steps" in extra
        assert "completed" in extra
        assert "deferred" in extra
        assert "duration_ms" in extra

    async def test_cache_stored_logged(self, preview_service, sample_plan, caplog):
        """cache_stored log emitted on successful cache write."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        records = _find_records(_collect_records(caplog), "cache_stored")
        assert len(records) >= 1

    async def test_cache_store_failed_logged_when_no_redis(
        self, preview_service_no_redis, sample_plan, caplog
    ):
        """cache_store_failed log emitted when Redis unavailable."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service_no_redis.preview(request)

        records = _find_records(_collect_records(caplog), "cache_store_failed")
        assert len(records) >= 1


# ---------------------------------------------------------------------------
# No PII tests
# ---------------------------------------------------------------------------


class TestNoPII:
    """Verify no step args or results appear in logs."""

    async def test_no_step_args_in_logs(self, preview_service, sample_plan, caplog):
        """Step args are NOT logged (PII/secrets risk)."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        all_text = caplog.text
        # sample_plan step 1 has args {"calendar_id": "primary"}
        assert "calendar_id" not in all_text
        assert '"primary"' not in all_text

    async def test_no_step_results_in_logs(self, preview_service, sample_plan, caplog):
        """Step results are NOT logged (may contain external data)."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        all_text = caplog.text
        # MCP mock returns {"events": [{"id": "evt-1", ...}]}
        assert "evt-1" not in all_text

    async def test_plan_id_correlation_in_all_records(self, preview_service, sample_plan, caplog):
        """plan_id present in all log records from a single preview."""
        with caplog.at_level(logging.DEBUG):
            request = PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )
            await preview_service.preview(request)

        records = _collect_records(caplog)
        # Filter to service-level logs (not adapter-level cache logs)
        service_records = [r for r in records if "preview_service" in r.name]
        for r in service_records:
            assert r.__dict__.get("plan_id") == SAMPLE_PLAN_ID, (
                f"Missing plan_id in log: {r.getMessage()}"
            )
