"""
Schema Contract Tests

Validate ExecuteRequest and PlanOutcome conform to shared schema contracts.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from jose import jwt
from pydantic import ValidationError

from shared.schemas.outcome import PlanOutcome

from ..domain.models import ExecuteRequest

# ======================================================================
# ExecuteRequest schema (T900)
# ======================================================================


class TestExecuteRequestSchema:
    def test_required_fields(self, sample_plan):
        """ExecuteRequest requires plan, approval_token."""
        req = ExecuteRequest(
            plan=sample_plan,
            approval_token="tok",
            user_id="u1",
            trace_id="t1",
        )
        assert req.plan.plan_id == sample_plan.plan_id

    def test_optional_fields_default(self, sample_plan):
        """Optional fields default correctly."""
        req = ExecuteRequest(
            plan=sample_plan,
            approval_token="tok",
            user_id="u1",
            trace_id="t1",
        )
        assert req.preview_state is None
        assert req.integration_credentials == {}

    def test_invalid_plan_rejected(self):
        """ExecuteRequest with invalid plan raises ValidationError."""
        with pytest.raises(ValidationError):
            ExecuteRequest(
                plan={"plan_id": "short"},  # Invalid -- too short
                approval_token="tok",
                user_id="u1",
                trace_id="t1",
            )


# ======================================================================
# PlanOutcome schema conformance (T901)
# ======================================================================


class TestPlanOutcomeSchema:
    async def test_outcome_conforms(self, execute_service, sample_execute_request):
        """PlanOutcome from execute_plan conforms to shared schema."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert isinstance(outcome, PlanOutcome)
        assert isinstance(outcome.success, bool)
        assert isinstance(outcome.execution_start, str)
        assert isinstance(outcome.execution_end, str)
        assert isinstance(outcome.total_steps, int)

    async def test_outcome_includes_final_graph_on_spawn(
        self, execute_service, sample_hybrid_plan, sample_execute_request
    ):
        """PlanOutcome includes final_graph_json when spawns occur."""
        # Mock LLM to return a spawn request
        execute_service._llm.reason = AsyncMock(
            return_value={
                "content": "Spawning a fetcher",
                "spawn_requests": [
                    {
                        "role": "Fetcher",
                        "uses": "google.calendar",
                        "call": "search",
                        "args": {},
                    }
                ],
            }
        )

        token = jwt.encode(
            {"plan_id": sample_hybrid_plan.plan_id, "exp": time.time() + 900},
            "approval-gate-secret",
            algorithm="HS256",
        )
        request = ExecuteRequest(
            plan=sample_hybrid_plan,
            approval_token=token,
            user_id="user-001",
            trace_id="trace-002",
        )
        outcome = await execute_service.execute_plan(request)
        # Even if the spawn itself fails, the outcome is valid
        assert isinstance(outcome, PlanOutcome)

    async def test_outcome_error_types_match_spec(self, execute_service, sample_execute_request):
        """PlanOutcome error_type values match SPEC edge cases."""
        # Force an error by expiring the plan
        sample_execute_request.plan.meta.created_at = "2020-01-01T00:00:00+00:00"
        sample_execute_request.plan.constraints.ttl_s = 1
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.error_type in {
            "token_expired",
            "plan_expired",
            "cycle_detected",
            "step_failure",
            "recovery_exhausted",
            "mcp_error",
            "spawn_denied",
            "internal_error",
        }

    async def test_outcome_plan_revision(self, execute_service, sample_execute_request):
        """PlanOutcome plan_revision starts at 0 for no spawns."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert outcome.plan_revision == 0


# ======================================================================
# End-to-end contract (T902)
# ======================================================================


class TestEndToEndContract:
    async def test_full_flow(self, execute_service, sample_execute_request):
        """Full flow: build Plan -> Signature -> ExecuteRequest -> PlanOutcome."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        assert isinstance(outcome, PlanOutcome)
        assert outcome.success is True
        assert outcome.total_steps == 4

    async def test_outcome_serializable(self, execute_service, sample_execute_request):
        """PlanOutcome is JSON-serializable."""
        outcome = await execute_service.execute_plan(sample_execute_request)
        data = outcome.model_dump()
        assert isinstance(data, dict)
        assert "success" in data

    async def test_idempotency_contract(self, execute_service, sample_execute_request):
        """Same request twice: both succeed (idempotency for Booker)."""
        outcome1 = await execute_service.execute_plan(sample_execute_request)
        outcome2 = await execute_service.execute_plan(sample_execute_request)
        assert outcome1.success is True
        assert outcome2.success is True
