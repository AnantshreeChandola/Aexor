"""
PolicyEngine observability tests — structured logging.

Validates that policy evaluation logs include required correlation IDs
and do not leak PII.
~5 tests.
"""

from __future__ import annotations

import logging

import pytest

from components.PolicyEngine.tests.conftest import (
    SAMPLE_PLAN_ID,
    make_spawn_request,
)

# ---------------------------------------------------------------------------
# Logging tests
# ---------------------------------------------------------------------------


class TestEvaluateSpawnLogging:
    @pytest.mark.asyncio
    async def test_evaluate_spawn_logs_plan_id(self, policy_service, caplog):
        """evaluate_spawn log output includes plan_id."""
        request = make_spawn_request()
        with caplog.at_level(logging.INFO, logger="components.PolicyEngine.service.policy_service"):
            await policy_service.evaluate_spawn(request)
        assert any(SAMPLE_PLAN_ID in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_evaluate_spawn_logs_policy_id(self, policy_service, caplog):
        """evaluate_spawn log output includes policy_id."""
        request = make_spawn_request()
        with caplog.at_level(logging.INFO, logger="components.PolicyEngine.service.policy_service"):
            await policy_service.evaluate_spawn(request)
        assert any("default-reasoning" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_evaluate_spawn_logs_decision(self, policy_service, caplog):
        """evaluate_spawn log output includes ALLOWED or DENIED."""
        request = make_spawn_request()
        with caplog.at_level(logging.INFO, logger="components.PolicyEngine.service.policy_service"):
            await policy_service.evaluate_spawn(request)
        assert any("ALLOWED" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_denied_decision_logged(self, policy_service, mock_db_adapter, caplog):
        """Denied decision logged with violations."""
        mock_db_adapter.get_policy.return_value = None
        request = make_spawn_request(policy_ref="missing-policy")
        with caplog.at_level(logging.INFO, logger="components.PolicyEngine.service.policy_service"):
            await policy_service.evaluate_spawn(request)
        assert any("deny-by-default" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_no_pii_in_logs(self, policy_service, caplog):
        """Log output does not contain user PII (email, names, etc)."""
        request = make_spawn_request()
        with caplog.at_level(
            logging.DEBUG, logger="components.PolicyEngine.service.policy_service"
        ):
            await policy_service.evaluate_spawn(request)
        # Check that no common PII patterns appear
        for record in caplog.records:
            msg = record.message.lower()
            assert "@" not in msg  # no email
            assert "password" not in msg
            assert "credential" not in msg
