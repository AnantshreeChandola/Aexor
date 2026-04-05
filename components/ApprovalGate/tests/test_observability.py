"""
ApprovalGate observability tests -- structured logging, no PII/secrets.

Validates that all approval operations emit structured log records with
plan_id correlation, and that no JWT values or user selections appear in logs.
~10 tests.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from components.ApprovalGate.domain.models import (
    ApprovalRequest,
    TokenConsumedError,
)
from components.ApprovalGate.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_SCOPES,
    SAMPLE_USER_ID,
)

# ---------------------------------------------------------------------------
# Log capture helpers
# ---------------------------------------------------------------------------


def _collect_records(
    caplog: pytest.LogCaptureFixture,
    name_prefix: str = "components.ApprovalGate",
) -> list[logging.LogRecord]:
    """Collect log records from ApprovalGate loggers."""
    return [r for r in caplog.records if r.name.startswith(name_prefix)]


def _find_records(records: list[logging.LogRecord], message: str) -> list[logging.LogRecord]:
    """Find records matching a message substring."""
    return [r for r in records if message in r.getMessage()]


# ---------------------------------------------------------------------------
# Structured logging tests
# ---------------------------------------------------------------------------


class TestStructuredLogging:
    """Verify all required log events are emitted."""

    async def test_approval_started_logged(self, approval_service, sample_approval_request, caplog):
        """approval_started log emitted with correct plan_id, gate_id, trace_id."""
        with caplog.at_level(logging.DEBUG):
            await approval_service.approve(sample_approval_request)

        records = _find_records(_collect_records(caplog), "approval_started")
        assert len(records) == 1
        extra = records[0].__dict__
        assert extra.get("plan_id") == SAMPLE_PLAN_ID
        assert extra.get("gate_id") == "gate-A"

    async def test_approval_issued_logged_with_token_id(
        self, approval_service, sample_approval_request, caplog
    ):
        """approval_issued log emitted with token_id (not token value)."""
        with caplog.at_level(logging.DEBUG):
            token = await approval_service.approve(sample_approval_request)

        records = _find_records(_collect_records(caplog), "approval_issued")
        assert len(records) == 1
        extra = records[0].__dict__
        assert extra.get("token_id") == token.token_id
        assert extra.get("plan_id") == SAMPLE_PLAN_ID

    async def test_approval_idempotent_logged(
        self, approval_service, sample_approval_request, caplog
    ):
        """approval_idempotent log emitted on re-approval."""
        await approval_service.approve(sample_approval_request)
        with caplog.at_level(logging.DEBUG):
            await approval_service.approve(sample_approval_request)

        records = _find_records(_collect_records(caplog), "approval_idempotent")
        assert len(records) >= 1
        extra = records[0].__dict__
        assert extra.get("plan_id") == SAMPLE_PLAN_ID

    async def test_token_validated_logged(self, approval_service, sample_approval_request, caplog):
        """token_validated log emitted with plan_id and token_id."""
        token = await approval_service.approve(sample_approval_request)
        with caplog.at_level(logging.DEBUG):
            await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)

        records = _find_records(_collect_records(caplog), "token_validated")
        assert len(records) == 1
        extra = records[0].__dict__
        assert extra.get("plan_id") == SAMPLE_PLAN_ID
        assert extra.get("token_id") == token.token_id

    async def test_token_consumed_logged_at_warning(
        self, approval_service, sample_approval_request, caplog
    ):
        """token_consumed log emitted at WARNING level on reuse attempt."""
        token = await approval_service.approve(sample_approval_request)
        await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)

        with caplog.at_level(logging.DEBUG), pytest.raises(TokenConsumedError):
            await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)

        records = _find_records(_collect_records(caplog), "token_consumed")
        assert len(records) >= 1
        assert records[0].levelno == logging.WARNING

    async def test_preview_state_retrieval_failed_logged(
        self, approval_service, sample_approval_request, mock_preview_service, caplog
    ):
        """preview_state_retrieval_failed log emitted when preview service fails."""
        mock_preview_service.get_preview_state = AsyncMock(side_effect=RuntimeError("preview down"))
        with caplog.at_level(logging.DEBUG):
            await approval_service.approve(sample_approval_request)

        records = _find_records(_collect_records(caplog), "preview_state_retrieval_failed")
        assert len(records) >= 1

    async def test_learn_from_approval_failed_logged(
        self, approval_service, mock_policy_service, caplog
    ):
        """learn_from_approval_failed log emitted when policy service fails."""
        mock_policy_service.learn_from_approval = AsyncMock(side_effect=RuntimeError("policy down"))
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            gate_id="gate-spawn8",
            scopes=SAMPLE_SCOPES,
            policy_matched=False,
            role="Fetcher",
            tool="google.calendar",
        )
        with caplog.at_level(logging.DEBUG):
            await approval_service.approve(req)

        records = _find_records(_collect_records(caplog), "learn_from_approval_failed")
        assert len(records) >= 1


# ---------------------------------------------------------------------------
# No PII tests
# ---------------------------------------------------------------------------


class TestNoPII:
    """Verify no JWT values or user selections appear in logs."""

    async def test_no_jwt_token_in_logs(self, approval_service, sample_approval_request, caplog):
        """JWT token value is NOT present in any log record."""
        with caplog.at_level(logging.DEBUG):
            token = await approval_service.approve(sample_approval_request)

        all_text = caplog.text
        # JWT tokens start with "eyJ" and are long
        assert token.token not in all_text

    async def test_no_selected_option_in_logs(self, approval_service, caplog):
        """selected_option values are NOT present in any log record."""
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            scopes=SAMPLE_SCOPES,
            selected_option={"slot": "Tuesday 10:00-10:30 SECRET_VALUE"},
        )
        with caplog.at_level(logging.DEBUG):
            await approval_service.approve(req)

        all_text = caplog.text
        assert "SECRET_VALUE" not in all_text
        assert "Tuesday 10:00-10:30" not in all_text

    async def test_plan_id_correlation_in_all_records(
        self, approval_service, sample_approval_request, caplog
    ):
        """plan_id present in all log records from a single approve() call."""
        with caplog.at_level(logging.DEBUG):
            await approval_service.approve(sample_approval_request)

        records = _collect_records(caplog)
        service_records = [r for r in records if "approval_service" in r.name]
        for r in service_records:
            assert r.__dict__.get("plan_id") == SAMPLE_PLAN_ID, (
                f"Missing plan_id in log: {r.getMessage()}"
            )
