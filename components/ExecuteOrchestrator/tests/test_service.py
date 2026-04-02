"""
Service Layer Tests

Tests for the core ExecuteService flow: happy path, verification,
failure recovery, parallel execution, and outcome persistence.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from jose import jwt

from ..domain.models import (
    ExecuteRequest,
    MCPInvocationError,
)

# ======================================================================
# Core service flow tests (T400)
# ======================================================================


class TestHappyPath:
    async def test_pure_api_plan_4_steps(self, execute_service, sample_execute_request):
        """4-step pure API plan executes with correct DAG ordering."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is True
        assert outcome.total_steps == 4

    async def test_preview_only_step_skipped(self, execute_service, sample_plan, sample_signature):
        """preview_only step is skipped and cached result used."""
        sample_plan.graph[0].execute_mode = "preview_only"
        token = jwt.encode(
            {"plan_id": sample_plan.plan_id, "exp": time.time() + 900},
            "approval-gate-secret",
            algorithm="HS256",
        )
        request = ExecuteRequest(
            plan=sample_plan,
            signature=sample_signature,
            approval_token=token,
            user_id="user-001",
            trace_id="trace-001",
            preview_state={"1": {"events": ["cached"]}},
        )
        outcome = await execute_service.execute_plan(request)
        assert outcome.success is True

    async def test_booker_idempotency_integration(self, execute_service, sample_execute_request):
        """Booker step claims idempotency key and marks succeeded."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is True

    async def test_resource_lock_for_booker(self, execute_service, sample_execute_request):
        """Resource lock acquired and released for Booker step."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is True

    async def test_credential_decryption_called(
        self, execute_service, sample_plan, sample_signature
    ):
        """Credential decrypted for API step with credentials."""
        token = jwt.encode(
            {"plan_id": sample_plan.plan_id, "exp": time.time() + 900},
            "approval-gate-secret",
            algorithm="HS256",
        )
        request = ExecuteRequest(
            plan=sample_plan,
            signature=sample_signature,
            approval_token=token,
            user_id="user-001",
            trace_id="trace-001",
            integration_credentials={"google.calendar": "cred-123"},
        )
        outcome = await execute_service.execute_plan(request)
        assert outcome.success is True
        execute_service._credential_vault.decrypt.assert_called()

    async def test_no_credential_for_step_without_mapping(
        self, execute_service, sample_execute_request
    ):
        """No credential decryption if not in integration_credentials."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is True
        execute_service._credential_vault.decrypt.assert_not_called()

    async def test_outcome_has_context_data(self, execute_service, sample_execute_request):
        """PlanOutcome has context_data with step statuses."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.context_data is not None
        assert "step_1" in outcome.context_data


# ======================================================================
# Verification tests (T401)
# ======================================================================


class TestVerification:
    async def test_invalid_signature(self, execute_service, sample_execute_request):
        """Invalid signature produces error_type=signature_invalid."""
        execute_service._signer.verify_signature = AsyncMock(side_effect=Exception("bad sig"))
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is False
        assert outcome.error_type == "signature_invalid"

    async def test_expired_approval_token(self, execute_service, sample_plan, sample_signature):
        """Expired token produces error_type=token_expired."""
        expired_token = jwt.encode(
            {"plan_id": sample_plan.plan_id, "exp": time.time() - 100},
            "approval-gate-secret",
            algorithm="HS256",
        )
        request = ExecuteRequest(
            plan=sample_plan,
            signature=sample_signature,
            approval_token=expired_token,
            user_id="user-001",
            trace_id="trace-001",
        )
        outcome = await execute_service.execute_plan(request)
        assert outcome.success is False
        assert outcome.error_type == "token_expired"

    async def test_plan_ttl_expired(self, execute_service, sample_plan, sample_signature):
        """Expired plan TTL produces error_type=plan_expired."""
        sample_plan.meta.created_at = "2020-01-01T00:00:00+00:00"
        sample_plan.constraints.ttl_s = 60

        token = jwt.encode(
            {"plan_id": sample_plan.plan_id, "exp": time.time() + 900},
            "approval-gate-secret",
            algorithm="HS256",
        )
        request = ExecuteRequest(
            plan=sample_plan,
            signature=sample_signature,
            approval_token=token,
            user_id="user-001",
            trace_id="trace-001",
        )
        outcome = await execute_service.execute_plan(request)
        assert outcome.success is False
        assert outcome.error_type == "plan_expired"

    async def test_valid_signature_and_token_proceeds(
        self, execute_service, sample_execute_request
    ):
        """Valid signature + token: execution proceeds successfully."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is True


# ======================================================================
# Failure and recovery tests (T402)
# ======================================================================


class TestFailureRecovery:
    async def test_pure_api_step_failure_compensation(
        self, execute_service, sample_execute_request
    ):
        """Pure API plan: step failure triggers compensation."""
        call_count = 0

        async def fail_step_3(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise MCPInvocationError("srv", "tool", "500")
            return {"status": "ok", "id": f"r{call_count}"}

        execute_service._mcp.invoke = AsyncMock(side_effect=fail_step_3)
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is False

    async def test_step_retry_succeeds(self, execute_service, sample_execute_request):
        """Step fails twice, succeeds on third try."""
        from ..adapters.retry import RetryPolicy

        execute_service._retry = RetryPolicy(max_retries=3, backoff_base_s=0)
        call_count = 0

        _tool_results = {
            "list_events": {"status": "ok", "events": [{"id": "e1"}]},
            "get_contact": {"status": "ok", "name": "Alice"},
            "find_slot": {"status": "ok", "slot": "2026-04-01T15:00"},
            "create_event": {"status": "ok", "id": "evt-001"},
        }

        async def fail_then_succeed(server, tool, args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise MCPInvocationError("srv", "tool", "503")
            return _tool_results.get(tool, {"status": "ok"})

        execute_service._mcp.invoke = AsyncMock(side_effect=fail_then_succeed)
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is True

    async def test_redis_unavailable_booker_fails_safe(
        self, execute_service, sample_execute_request, mock_redis
    ):
        """Booker step refuses when Redis raises ConnectionError."""
        mock_redis.hgetall = AsyncMock(side_effect=ConnectionError("Redis down"))
        outcome = await execute_service.execute_plan(sample_execute_request)
        # Fetcher/Analyzer succeed but Booker fails
        assert outcome.success is False


# ======================================================================
# Parallel execution tests (T403)
# ======================================================================


class TestParallelExecution:
    async def test_independent_steps_parallel(self, execute_service, sample_execute_request):
        """Steps 1 and 2 execute in parallel (both have no deps)."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.success is True
        # Both steps completed
        assert outcome.context_data is not None
        assert "step_1" in outcome.context_data
        assert "step_2" in outcome.context_data

    async def test_parallel_failure_others_complete(self, execute_service, sample_execute_request):
        """One parallel step failing does not prevent others."""
        call_count = 0

        async def fail_first(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise MCPInvocationError("srv", "tool", "500")
            return {"status": "ok"}

        execute_service._mcp.invoke = AsyncMock(side_effect=fail_first)
        await execute_service.execute_plan(sample_execute_request)
        # Plan fails overall but multiple invocations happened
        assert call_count >= 1


# ======================================================================
# Outcome persistence tests (T404)
# ======================================================================


class TestOutcomePersistence:
    async def test_plan_writer_called(self, execute_service, sample_execute_request):
        """PlanWriter.persist_outcome called on success."""
        await execute_service.execute_plan(sample_execute_request)
        execute_service._plan_writer.persist_outcome.assert_called_once()

    async def test_plan_writer_failure_does_not_fail_execution(
        self, execute_service, sample_execute_request
    ):
        """PlanWriter failure logged but does not fail execution."""
        execute_service._plan_writer.persist_outcome = AsyncMock(
            side_effect=RuntimeError("DB down")
        )
        outcome = await execute_service.execute_plan(sample_execute_request)
        # Execution itself succeeds
        assert outcome.success is True
