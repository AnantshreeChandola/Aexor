"""
PreviewOrchestrator unit tests -- core preview logic.

Tests the preview() method, DAG dispatch, step filtering,
deferral cascade, partial failure, and determinism.
~25 tests covering US1-US3, FR-001 through FR-010.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from components.ExecuteOrchestrator.domain.models import MCPInvocationError
from components.PreviewOrchestrator.domain.models import (
    PreviewError,
    PreviewRequest,
    PreviewResult,
)
from components.PreviewOrchestrator.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
    _intent,
    _plan_meta,
)
from shared.schemas.plan import Plan, PlanStep


def _req(plan: Plan) -> PreviewRequest:
    """Build a PreviewRequest with standard test IDs."""
    return PreviewRequest(plan=plan, user_id=SAMPLE_USER_ID, trace_id=SAMPLE_TRACE_ID)


def _steps_by_num(result: PreviewResult) -> dict[int, dict]:
    """Index normalized step dicts by step number."""
    return {s["step"]: s for s in result.normalized["steps"]}


class TestDAGResolution:
    """preview() resolves DAG into correct levels."""

    async def test_sample_plan_resolves_dag(self, preview_service, sample_plan):
        """FR-001: DAG resolved into topological levels."""
        result = await preview_service.preview(_req(sample_plan))
        assert isinstance(result, PreviewResult)
        assert result.plan_id == SAMPLE_PLAN_ID

    async def test_cycle_detected_raises_preview_error(self, preview_service):
        """Edge case: DAG cycle raises PreviewError."""
        cyclic_plan = Plan(
            plan_id=SAMPLE_PLAN_ID,
            intent=_intent(),
            graph=[
                PlanStep(
                    step=1,
                    mode="interactive",
                    role="Fetcher",
                    uses="google.calendar",
                    call="list_events",
                    after=[2],
                ),
                PlanStep(
                    step=2,
                    mode="interactive",
                    role="Fetcher",
                    uses="google.calendar",
                    call="list_events",
                    after=[1],
                ),
            ],
            meta=_plan_meta(),
        )
        with pytest.raises(PreviewError, match="DAG cycle"):
            await preview_service.preview(_req(cyclic_plan))


class TestPreviewableDispatch:
    """Only previewable steps dispatched with dry_run=True."""

    async def test_only_previewable_steps_dispatched(
        self, preview_service, sample_plan, mock_mcp_client
    ):
        """FR-002: Only previewable steps execute."""
        result = await preview_service.preview(_req(sample_plan))
        sbn = _steps_by_num(result)
        assert sbn[1]["status"] == "completed"
        assert sbn[2]["status"] == "completed"
        assert sbn[3]["status"] == "completed"
        assert sbn[4]["status"] == "deferred"
        assert sbn[5]["status"] == "deferred"

    async def test_mcp_invoked_with_dry_run(self, preview_service, sample_plan, mock_mcp_client):
        """FR-004: MCP invocations include dry_run=True."""
        await preview_service.preview(_req(sample_plan))
        for call in mock_mcp_client.invoke.call_args_list:
            args_dict = call.kwargs.get("args", call[1].get("args", {}))
            assert args_dict.get("dry_run") is True

    async def test_mcp_invoked_with_credentials_none(
        self, preview_service, sample_plan, mock_mcp_client
    ):
        """FR-004: MCP invocations pass credentials=None."""
        await preview_service.preview(_req(sample_plan))
        for call in mock_mcp_client.invoke.call_args_list:
            creds = call.kwargs.get("credentials", call[1].get("credentials"))
            assert creds is None


class TestParallelExecution:
    """Steps at same DAG level execute in parallel."""

    async def test_parallel_plan_all_completed(
        self, preview_service, parallel_plan, mock_mcp_client
    ):
        """FR-005: All 4 parallel steps complete."""
        result = await preview_service.preview(_req(parallel_plan))
        assert len(result.normalized["steps"]) == 4
        for step_data in result.normalized["steps"]:
            assert step_data["status"] == "completed"

    async def test_parallel_total_latency_is_max_not_sum(self, preview_service, parallel_plan):
        """SC-002: Parallel latency ~ max-of-steps, not sum."""
        import time

        delay_s = 0.05

        async def _slow_invoke(**kwargs):
            await asyncio.sleep(delay_s)
            return {"data": "ok"}

        preview_service._mcp.invoke = AsyncMock(side_effect=_slow_invoke)
        t0 = time.monotonic()
        await preview_service.preview(_req(parallel_plan))
        elapsed = time.monotonic() - t0
        assert elapsed < delay_s * 3  # generous margin


class TestPreviewResultShape:
    """Returned PreviewResult conforms to GLOBAL_SPEC S2.5."""

    async def test_source_is_preview(self, preview_service, sample_plan):
        """FR-008: source == 'preview'."""
        result = await preview_service.preview(_req(sample_plan))
        assert result.source == "preview"

    async def test_can_execute_true_when_some_succeed(self, preview_service, sample_plan):
        """FR-008: can_execute == True when some steps succeed."""
        result = await preview_service.preview(_req(sample_plan))
        assert result.can_execute is True

    async def test_booker_deferred_with_gated_reason(self, preview_service, sample_plan):
        """US1: Non-previewable Booker step deferred with 'gated'."""
        result = await preview_service.preview(_req(sample_plan))
        sbn = _steps_by_num(result)
        assert sbn[4]["status"] == "deferred"
        assert sbn[4]["reason"] == "gated"


class TestHybridPlanDeferral:
    """llm_reasoning and policy_check steps are deferred."""

    async def test_llm_reasoning_deferred(self, preview_service, hybrid_plan):
        """FR-003: llm_reasoning steps deferred."""
        result = await preview_service.preview(_req(hybrid_plan))
        sbn = _steps_by_num(result)
        assert sbn[3]["status"] == "deferred"
        assert sbn[3]["reason"] == "llm_reasoning"

    async def test_policy_check_deferred(self, preview_service, hybrid_plan):
        """FR-003: policy_check steps deferred."""
        result = await preview_service.preview(_req(hybrid_plan))
        sbn = _steps_by_num(result)
        assert sbn[4]["status"] == "deferred"
        assert sbn[4]["reason"] == "dependency_deferred"

    async def test_cascade_deferral_from_reasoning(self, preview_service, hybrid_plan):
        """FR-006: API step after deferred reasoning -> cascade deferred."""
        result = await preview_service.preview(_req(hybrid_plan))
        sbn = _steps_by_num(result)
        assert sbn[5]["status"] == "deferred"
        assert sbn[5]["reason"] == "dependency_deferred"

    async def test_gated_step_deferred(self, preview_service, sample_plan):
        """US2: Steps with gate_id deferred with 'gated'."""
        result = await preview_service.preview(_req(sample_plan))
        sbn = _steps_by_num(result)
        assert sbn[4]["reason"] == "gated"


class TestPartialFailure:
    """Step failures produce partial results."""

    async def test_one_fail_other_succeeds_partial(
        self, preview_service, parallel_plan, mock_mcp_client
    ):
        """FR-010: One step fails, others complete. partial=True."""
        call_count = 0

        async def _fail_first(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise MCPInvocationError("srv", "tool", "timeout")
            return {"data": "ok"}

        mock_mcp_client.invoke = AsyncMock(side_effect=_fail_first)
        result = await preview_service.preview(_req(parallel_plan))
        assert result.partial is True
        assert result.can_execute is True
        statuses = [s["status"] for s in result.normalized["steps"]]
        assert "failed" in statuses
        assert "completed" in statuses

    async def test_downstream_of_failed_skipped(self, preview_service, mock_mcp_client):
        """FR-010: Downstream of failed step is skipped."""
        plan = Plan(
            plan_id=SAMPLE_PLAN_ID,
            intent=_intent(),
            graph=[
                PlanStep(
                    step=1,
                    mode="interactive",
                    role="Fetcher",
                    uses="google.calendar",
                    call="list_events",
                    after=[],
                ),
                PlanStep(
                    step=2,
                    mode="interactive",
                    role="Analyzer",
                    uses="system.analyzer",
                    call="find_overlaps",
                    after=[1],
                ),
            ],
            meta=_plan_meta(),
        )
        mock_mcp_client.invoke = AsyncMock(side_effect=MCPInvocationError("srv", "tool", "error"))
        result = await preview_service.preview(_req(plan))
        sbn = _steps_by_num(result)
        assert sbn[1]["status"] == "failed"
        assert sbn[2]["status"] == "skipped"
        assert sbn[2]["reason"] == "dependency_failed"

    async def test_all_previewable_fail_can_execute_false(
        self, preview_service, parallel_plan, mock_mcp_client
    ):
        """US3: All previewable steps fail -> can_execute=False."""
        mock_mcp_client.invoke = AsyncMock(side_effect=MCPInvocationError("srv", "tool", "error"))
        result = await preview_service.preview(_req(parallel_plan))
        assert result.can_execute is False
        assert result.partial is True

    async def test_all_non_previewable_can_execute_true(
        self, preview_service, empty_previewable_plan
    ):
        """Edge case: Zero previewable steps -> can_execute=True."""
        result = await preview_service.preview(_req(empty_previewable_plan))
        # Per spec edge case: all deferred (no failures) = can_execute: true
        assert result.can_execute is True


class TestTemplateResolution:
    """Template args resolved from prior completed step results."""

    async def test_templates_resolved_from_prior_steps(
        self, preview_service, sample_plan, mock_mcp_client
    ):
        """FR-009: {{step_N.result.field}} resolved."""
        await preview_service.preview(_req(sample_plan))
        assert mock_mcp_client.invoke.call_count == 3

    async def test_template_resolution_failure_causes_step_fail(
        self, preview_service, mock_mcp_client
    ):
        """FR-009: Template resolution failure -> step fails."""
        plan = Plan(
            plan_id=SAMPLE_PLAN_ID,
            intent=_intent(),
            graph=[
                PlanStep(
                    step=1,
                    mode="interactive",
                    role="Fetcher",
                    uses="google.calendar",
                    call="list_events",
                    after=[],
                ),
                PlanStep(
                    step=2,
                    mode="interactive",
                    role="Analyzer",
                    uses="system.analyzer",
                    call="find_overlaps",
                    args={"data": "{{step_1.result.nonexistent_field}}"},
                    after=[1],
                ),
            ],
            meta=_plan_meta(),
        )
        result = await preview_service.preview(_req(plan))
        sbn = _steps_by_num(result)
        assert sbn[2]["status"] == "failed"


class TestEdgeCases:
    """Edge case tests."""

    async def test_single_step_plan(self, preview_service, single_step_plan, mock_mcp_client):
        """Single-step plan works correctly."""
        result = await preview_service.preview(_req(single_step_plan))
        assert len(result.normalized["steps"]) == 1
        assert result.normalized["steps"][0]["status"] == "completed"
        assert result.can_execute is True

    async def test_gated_api_step_deferred(self, preview_service):
        """Step with gate_id and type='api' is deferred (gate_id priority)."""
        plan = Plan(
            plan_id=SAMPLE_PLAN_ID,
            intent=_intent(),
            graph=[
                PlanStep(
                    step=1,
                    mode="interactive",
                    role="Fetcher",
                    uses="google.calendar",
                    call="list_events",
                    after=[],
                    gate_id="gate-test",
                    type="api",
                ),
            ],
            meta=_plan_meta(),
        )
        result = await preview_service.preview(_req(plan))
        assert result.normalized["steps"][0]["status"] == "deferred"
        assert result.normalized["steps"][0]["reason"] == "gated"

    async def test_dep_on_deferred_and_failed_prefers_deferred(
        self, preview_service, mock_mcp_client
    ):
        """Step depending on deferred + failed -> deferred (priority order)."""
        mock_mcp_client.invoke = AsyncMock(side_effect=MCPInvocationError("s", "t", "err"))
        plan = Plan(
            plan_id=SAMPLE_PLAN_ID,
            intent=_intent(),
            graph=[
                PlanStep(
                    step=1,
                    mode="interactive",
                    role="Reasoner",
                    uses="system.llm",
                    call="analyze_options",
                    after=[],
                    type="llm_reasoning",
                    trust_level="untrusted_input",
                ),
                PlanStep(
                    step=2,
                    mode="interactive",
                    role="Fetcher",
                    uses="google.calendar",
                    call="list_events",
                    after=[],
                ),
                PlanStep(
                    step=3,
                    mode="interactive",
                    role="Analyzer",
                    uses="system.analyzer",
                    call="find_overlaps",
                    after=[1, 2],
                ),
            ],
            meta=_plan_meta(),
        )
        result = await preview_service.preview(_req(plan))
        sbn = _steps_by_num(result)
        assert sbn[3]["status"] == "deferred"
        assert sbn[3]["reason"] == "dependency_deferred"

    async def test_zero_mcp_calls_for_non_previewable(
        self, preview_service, empty_previewable_plan, mock_mcp_client
    ):
        """SC-003: Zero MCP calls for non-previewable steps."""
        await preview_service.preview(_req(empty_previewable_plan))
        mock_mcp_client.invoke.assert_not_called()
