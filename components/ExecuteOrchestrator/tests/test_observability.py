"""
Observability Tests

Validate structured logging, no PII/secrets in logs, and metric names.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock


class TestObservability:
    async def test_credential_not_in_logs(self, execute_service, sample_execute_request, caplog):
        """Credential values never appear in log output."""
        execute_service._credential_vault.decrypt = AsyncMock(return_value="super-secret-token-xyz")
        sample_execute_request.integration_credentials = {"google.calendar": "cred-123"}
        with caplog.at_level(logging.DEBUG):
            await execute_service.execute_plan(sample_execute_request)

        for record in caplog.records:
            msg = record.getMessage()
            assert "super-secret-token-xyz" not in msg
            extra = getattr(record, "__dict__", {})
            for v in extra.values():
                if isinstance(v, str):
                    assert "super-secret-token-xyz" not in v

    async def test_execution_started_logged(self, execute_service, sample_execute_request, caplog):
        """execution_started event logged with plan_id."""
        with caplog.at_level(logging.INFO):
            await execute_service.execute_plan(sample_execute_request)

        started = [r for r in caplog.records if r.getMessage() == "execution_started"]
        assert len(started) >= 1
        extra = started[0].__dict__
        assert "plan_id" in extra

    async def test_execution_completed_logged(
        self, execute_service, sample_execute_request, caplog
    ):
        """execution_completed event logged with duration_ms."""
        with caplog.at_level(logging.INFO):
            await execute_service.execute_plan(sample_execute_request)

        completed = [r for r in caplog.records if r.getMessage() == "execution_completed"]
        assert len(completed) >= 1
        extra = completed[0].__dict__
        assert "duration_ms" in extra

    async def test_step_completed_has_latency(
        self, execute_service, sample_execute_request, caplog
    ):
        """step_completed events include latency_ms."""
        with caplog.at_level(logging.INFO):
            await execute_service.execute_plan(sample_execute_request)

        step_logs = [r for r in caplog.records if r.getMessage() == "step_completed"]
        assert len(step_logs) >= 1
        assert "latency_ms" in step_logs[0].__dict__

    async def test_credential_decrypted_no_value(
        self, execute_service, sample_execute_request, caplog
    ):
        """credential_decrypted log has tool_id but NOT credential."""
        execute_service._credential_vault.decrypt = AsyncMock(return_value="secret-val")
        sample_execute_request.integration_credentials = {"google.calendar": "cred-1"}

        with caplog.at_level(logging.DEBUG):
            await execute_service.execute_plan(sample_execute_request)

        cred_logs = [r for r in caplog.records if r.getMessage() == "credential_decrypted"]
        for log in cred_logs:
            extra = log.__dict__
            assert "tool_id" in extra
            for v in extra.values():
                if isinstance(v, str):
                    assert "secret-val" not in v
