"""
PreviewOrchestrator contract tests -- model conformance, GLOBAL_SPEC S2.5 envelope.

Tests that domain models validate correctly and that full preview()
calls produce results conforming to the Preview Wrapper contract.
~15 tests.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from components.PreviewOrchestrator.domain.models import (
    PreviewError,
    PreviewRequest,
    PreviewResult,
    PreviewStepError,
    PreviewStepResult,
)
from components.PreviewOrchestrator.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
)

# ---------------------------------------------------------------------------
# Model-level contract tests (T101)
# ---------------------------------------------------------------------------


class TestPreviewResultModel:
    """PreviewResult model validation."""

    def test_valid_preview_result(self):
        """PreviewResult validates with all required fields."""
        result = PreviewResult(
            plan_id=SAMPLE_PLAN_ID,
            normalized={"steps": []},
            can_execute=True,
        )
        assert result.source == "preview"
        assert result.evidence == []

    def test_source_always_preview(self):
        """PreviewResult.source is always 'preview'."""
        result = PreviewResult(
            plan_id=SAMPLE_PLAN_ID,
            normalized={"steps": []},
            can_execute=True,
        )
        assert result.source == "preview"

    def test_plan_id_rejects_short_string(self):
        """PreviewResult.plan_id rejects strings < 26 chars."""
        with pytest.raises(ValidationError):
            PreviewResult(
                plan_id="short",
                normalized={"steps": []},
                can_execute=True,
            )

    def test_plan_id_rejects_long_string(self):
        """PreviewResult.plan_id rejects strings > 26 chars."""
        with pytest.raises(ValidationError):
            PreviewResult(
                plan_id="a" * 27,
                normalized={"steps": []},
                can_execute=True,
            )

    def test_round_trip_serialization(self):
        """model_dump() then model_validate() produces identical model."""
        original = PreviewResult(
            plan_id=SAMPLE_PLAN_ID,
            normalized={"steps": [{"step": 1, "status": "completed"}]},
            can_execute=True,
            partial=False,
            cached_state_key="preview:user:plan",
        )
        dumped = original.model_dump()
        restored = PreviewResult.model_validate(dumped)
        assert restored == original


class TestPreviewStepResultModel:
    """PreviewStepResult model validation."""

    def test_all_valid_statuses(self):
        """PreviewStepResult accepts all four status values."""
        for status in ("completed", "failed", "deferred", "skipped"):
            result = PreviewStepResult(step=1, status=status)
            assert result.status == status

    def test_invalid_status_rejected(self):
        """PreviewStepResult rejects invalid status values."""
        with pytest.raises(ValidationError):
            PreviewStepResult(step=1, status="unknown")

    def test_step_must_be_positive(self):
        """PreviewStepResult rejects step < 1."""
        with pytest.raises(ValidationError):
            PreviewStepResult(step=0, status="completed")


class TestPreviewRequestModel:
    """PreviewRequest model validation."""

    def test_rejects_empty_user_id(self, sample_plan):
        """PreviewRequest rejects empty user_id."""
        with pytest.raises(ValidationError):
            PreviewRequest(
                plan=sample_plan,
                user_id="",
                trace_id=SAMPLE_TRACE_ID,
            )

    def test_rejects_empty_trace_id(self, sample_plan):
        """PreviewRequest rejects empty trace_id."""
        with pytest.raises(ValidationError):
            PreviewRequest(
                plan=sample_plan,
                user_id=SAMPLE_USER_ID,
                trace_id="",
            )


class TestExceptionHierarchy:
    """PreviewError and PreviewStepError exception hierarchy."""

    def test_preview_error_is_base(self):
        """PreviewError is a base Exception."""
        err = PreviewError("test error")
        assert isinstance(err, Exception)

    def test_preview_step_error_extends_preview_error(self):
        """PreviewStepError is a subclass of PreviewError."""
        err = PreviewStepError(step=1, reason="test")
        assert isinstance(err, PreviewError)

    def test_preview_step_error_stores_step(self):
        """PreviewStepError stores step attribute."""
        err = PreviewStepError(step=5, reason="timeout")
        assert err.step == 5
        assert "step 5" in str(err)


# ---------------------------------------------------------------------------
# GLOBAL_SPEC S2.5 Preview Wrapper conformance (T800)
# ---------------------------------------------------------------------------


class TestGlobalSpecConformance:
    """Full preview() returns result matching GLOBAL_SPEC S2.5."""

    async def test_preview_result_has_required_keys(self, preview_service, sample_plan):
        """PreviewResult has normalized, source, can_execute, evidence."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)
        dumped = result.model_dump()

        assert "normalized" in dumped
        assert "source" in dumped
        assert "can_execute" in dumped
        assert "evidence" in dumped

    async def test_normalized_contains_steps_list(self, preview_service, sample_plan):
        """normalized contains a 'steps' list with step/status."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)

        assert "steps" in result.normalized
        steps = result.normalized["steps"]
        assert isinstance(steps, list)
        for s in steps:
            assert "step" in s
            assert "status" in s

    async def test_pure_api_plan_result(self, preview_service, parallel_plan):
        """Pure API plan: can_execute=True, partial=False, non-empty steps."""
        request = PreviewRequest(
            plan=parallel_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)

        assert result.can_execute is True
        assert result.partial is False
        assert len(result.normalized["steps"]) > 0

    async def test_all_fail_result(self, preview_service, parallel_plan, mock_mcp_client):
        """All steps fail: can_execute=False, partial=True."""
        from components.ExecuteOrchestrator.domain.models import (
            MCPInvocationError,
        )

        mock_mcp_client.invoke = AsyncMock(side_effect=MCPInvocationError("s", "t", "err"))
        request = PreviewRequest(
            plan=parallel_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)

        assert result.can_execute is False
        assert result.partial is True

    async def test_evidence_defaults_to_empty(self, preview_service, sample_plan):
        """evidence defaults to empty list."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)
        assert result.evidence == []


# ---------------------------------------------------------------------------
# Intent-to-Preview flow (T801)
# ---------------------------------------------------------------------------


class TestIntentToPreviewFlow:
    """Integration flow: Plan -> preview() -> cached state."""

    async def test_full_flow_json_serializable(
        self, preview_service, sample_plan, mock_redis_client
    ):
        """Preview result can be serialized to JSON."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)
        json_str = json.dumps(result.model_dump())
        assert json_str  # non-empty

    async def test_cached_state_matches_result_steps(
        self, preview_service, sample_plan, mock_redis_client
    ):
        """Cached state step numbers match completed steps."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result = await preview_service.preview(request)

        state = await preview_service.get_preview_state(SAMPLE_PLAN_ID, SAMPLE_USER_ID)
        assert state is not None

        result_step_nums = {s["step"] for s in result.normalized["steps"]}
        assert set(state.keys()) == result_step_nums


# ---------------------------------------------------------------------------
# Determinism validation (T802)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same plan + same MCP responses = same preview results."""

    async def test_deterministic_preview(self, preview_service, sample_plan):
        """Two preview() calls with same input produce identical results."""
        request = PreviewRequest(
            plan=sample_plan,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        result1 = await preview_service.preview(request)
        result2 = await preview_service.preview(request)

        # Compare step statuses and results (not latency_ms which varies)
        steps1 = {s["step"]: s["status"] for s in result1.normalized["steps"]}
        steps2 = {s["step"]: s["status"] for s in result2.normalized["steps"]}
        assert steps1 == steps2
        assert result1.can_execute == result2.can_execute
        assert result1.partial == result2.partial
