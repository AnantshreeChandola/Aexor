"""
ExecuteOrchestrator sanitizer dispatch and Tier 1 schema validation tests.

Covers:
  T905 -- Sanitizer step dispatch via FilterService
  T906 -- Tier 1 output_schema_ref validation (FR-025)
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep
from shared.schemas.policy import PolicyDecision, ReasoningConfig
from shared.schemas.sanitized_payload import SanitizedPayload

from ..adapters.dag_resolver import DAGResolver
from ..adapters.idempotency import IdempotencyAdapter
from ..adapters.resource_lock import ResourceLockAdapter
from ..adapters.retry import RetryPolicy
from ..adapters.template_resolver import TemplateResolver
from ..domain.models import (
    ExecuteRequest,
    ExecutionContext,
    StepExecutionError,
    StepResult,
)
from ..service.execute_service import ExecuteService

_TOKEN_SECRET = "approval-gate-secret"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _intent() -> Intent:
    return Intent(
        intent="schedule_meeting",
        entities={"attendee": "alice"},
        constraints={},
        user_id="user-001",
    )


def _meta() -> PlanMeta:
    from datetime import UTC, datetime

    return PlanMeta(
        created_at=datetime.now(UTC).isoformat(),
        canonical_hash="a" * 64,
    )


def _token(plan_id: str) -> str:
    return jwt.encode(
        {"plan_id": plan_id, "exp": time.time() + 900},
        _TOKEN_SECRET,
        algorithm="HS256",
    )


def _make_sanitized_payload(
    verdict: str = "clean",
    degraded: bool = False,
) -> SanitizedPayload:
    return SanitizedPayload(
        original_shape={"events": [{"summary": "standup"}]},
        stripped_fields=[],
        trust_verdict=verdict,
        confidence=0.99,
        scanner_degraded=degraded,
        scanner_version="s1-only:test",
        scanned_at="2026-04-01T00:00:00Z",
    )


def _build_service(
    filter_service=None,
    mcp_client=None,
    llm_client=None,
    policy_service=None,
    tool_catalog=None,
) -> ExecuteService:
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock(return_value=True)
    redis.expire = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.set = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)

    return ExecuteService(
        policy_service=policy_service or AsyncMock(),
        tool_catalog=tool_catalog or MagicMock(),
        plan_writer_service=AsyncMock(),
        mcp_client=mcp_client or AsyncMock(),
        llm_client=llm_client or AsyncMock(),
        credential_vault=AsyncMock(),
        idempotency=IdempotencyAdapter(redis),
        resource_lock=ResourceLockAdapter(redis),
        dag_resolver=DAGResolver(),
        template_resolver=TemplateResolver(),
        retry_policy=RetryPolicy(max_retries=0, backoff_base_s=0),
        filter_service=filter_service,
    )


# ===================================================================
# T905: Sanitizer step dispatch
# ===================================================================


class TestSanitizerDispatch:
    """Verify that type='sanitizer' steps invoke FilterService."""

    @pytest.mark.asyncio
    async def test_sanitizer_step_invokes_filter_service(self):
        """Sanitizer step calls filter_service.scan with upstream result."""
        mock_filter = AsyncMock()
        mock_filter.scan = AsyncMock(
            return_value=_make_sanitized_payload("clean")
        )
        svc = _build_service(filter_service=mock_filter)

        step = PlanStep(
            step=2,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
            after=[1],
            context_from=[1],
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        # Simulate step 1 completed
        ctx.step_results[1] = StepResult(
            step=1,
            status="completed",
            result={"events": [{"summary": "standup"}]},
        )

        result = await svc._execute_sanitizer_step(step, ctx)

        mock_filter.scan.assert_awaited_once()
        call_kwargs = mock_filter.scan.call_args.kwargs
        assert call_kwargs["plan_id"] == "A" * 26
        assert call_kwargs["step_number"] == 2
        assert result["trust_verdict"] == "clean"

    @pytest.mark.asyncio
    async def test_sanitizer_propagates_verdict_to_context(self):
        """Sanitizer stores trust_verdict in ExecutionContext."""
        mock_filter = AsyncMock()
        mock_filter.scan = AsyncMock(
            return_value=_make_sanitized_payload("injection", degraded=True)
        )
        svc = _build_service(filter_service=mock_filter)

        step = PlanStep(
            step=2,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
            after=[1],
            context_from=[1],
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        ctx.step_results[1] = StepResult(
            step=1,
            status="completed",
            result={"data": "test"},
        )

        await svc._execute_sanitizer_step(step, ctx)

        assert ctx.sanitizer_verdicts[2] == "injection"
        assert ctx.sanitizer_degraded is True

    @pytest.mark.asyncio
    async def test_sanitizer_without_filter_service_raises(self):
        """If filter_service is None, sanitizer step raises."""
        svc = _build_service(filter_service=None)

        step = PlanStep(
            step=2,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
            after=[1],
            context_from=[1],
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )

        with pytest.raises(StepExecutionError) as exc_info:
            await svc._execute_sanitizer_step(step, ctx)
        assert "FilterService" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sanitizer_handles_load_bearing_error(self):
        """LoadBearingFlaggedError returns error dict, not exception."""
        from components.TrustFilter.domain.errors import (
            LoadBearingFlaggedError,
        )

        mock_filter = AsyncMock()
        mock_filter.scan = AsyncMock(
            side_effect=LoadBearingFlaggedError(
                "event.description", "role_switching_01"
            )
        )
        svc = _build_service(filter_service=mock_filter)

        step = PlanStep(
            step=2,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
            after=[1],
            context_from=[1],
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        ctx.step_results[1] = StepResult(
            step=1, status="completed", result={"data": "test"}
        )

        result = await svc._execute_sanitizer_step(step, ctx)
        assert result["_error"] is True
        assert result["error_type"] == "load_bearing_field_flagged"

    @pytest.mark.asyncio
    async def test_resolve_sanitizer_input_from_context_from(self):
        """_resolve_sanitizer_input returns the upstream step result."""
        svc = _build_service()

        step = PlanStep(
            step=3,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
            after=[1],
            context_from=[1, 2],
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        ctx.step_results[1] = StepResult(
            step=1, status="completed", result={"events": []}
        )

        payload = ExecuteService._resolve_sanitizer_input(step, ctx)
        assert payload == {"events": []}

    @pytest.mark.asyncio
    async def test_resolve_sanitizer_input_none_when_empty(self):
        """_resolve_sanitizer_input returns None if no upstream results."""
        svc = _build_service()

        step = PlanStep(
            step=2,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
            after=[1],
            context_from=[1],
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )

        payload = ExecuteService._resolve_sanitizer_input(step, ctx)
        assert payload is None


# ===================================================================
# T906: Tier 1 schema validation (FR-025)
# ===================================================================


class TestTier1SchemaValidation:
    """Verify that Tier 1 reasoner output is validated against
    output_schema_ref from SCHEMA_REGISTRY."""

    @pytest.mark.asyncio
    async def test_tier1_valid_schema_passes(self):
        """Tier 1 output matching schema proceeds normally."""
        import json

        valid_output = json.dumps({
            "proposed_start": "2026-04-01T10:00:00",
            "proposed_end": "2026-04-01T10:30:00",
            "has_conflict": False,
            "conflicts": [],
            "reason": "Best available slot",
        })

        mock_llm = AsyncMock()
        mock_llm.reason = AsyncMock(
            return_value={"content": valid_output}
        )
        svc = _build_service(llm_client=mock_llm)

        step = PlanStep(
            step=3,
            mode="interactive",
            role="Reasoner",
            type="llm_reasoning",
            trust_level="untrusted_input",
            uses="system.echo",
            call="analyze",
            args={},
            after=[2],
            context_from=[2],
            policy_ref="policy-1",
            reasoning_config=ReasoningConfig(
                system_prompt_ref="test.prompt",
                output_schema_ref="slot_proposal_v1",
            ),
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        ctx.step_results[2] = StepResult(
            step=2, status="completed", result={"data": "sanitized"}
        )

        request = ExecuteRequest(
            plan=ctx.plan,
            approval_token=_token(ctx.plan.plan_id),
            user_id="user-001",
            trace_id="trace-001",
        )

        result = await svc._execute_reasoning_step(step, ctx, request)
        # Should succeed: output matches SlotProposalV1
        assert "proposed_start" in result

    @pytest.mark.asyncio
    async def test_tier1_invalid_schema_raises(self):
        """Tier 1 output NOT matching schema causes hard failure."""
        import json

        # Missing required fields for SlotProposalV1
        bad_output = json.dumps({
            "some_random_field": "value",
        })

        mock_llm = AsyncMock()
        mock_llm.reason = AsyncMock(
            return_value={"content": bad_output}
        )
        svc = _build_service(llm_client=mock_llm)

        step = PlanStep(
            step=3,
            mode="interactive",
            role="Reasoner",
            type="llm_reasoning",
            trust_level="untrusted_input",
            uses="system.echo",
            call="analyze",
            args={},
            after=[2],
            context_from=[2],
            policy_ref="policy-1",
            reasoning_config=ReasoningConfig(
                system_prompt_ref="test.prompt",
                output_schema_ref="slot_proposal_v1",
            ),
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        ctx.step_results[2] = StepResult(
            step=2, status="completed", result={}
        )

        request = ExecuteRequest(
            plan=ctx.plan,
            approval_token=_token(ctx.plan.plan_id),
            user_id="user-001",
            trace_id="trace-001",
        )

        with pytest.raises(StepExecutionError) as exc_info:
            await svc._execute_reasoning_step(step, ctx, request)
        assert "Tier 1 schema validation failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_tier1_no_json_in_output_raises(self):
        """Tier 1 with non-JSON output causes hard failure."""
        mock_llm = AsyncMock()
        mock_llm.reason = AsyncMock(
            return_value={"content": "Just some plain text analysis."}
        )
        svc = _build_service(llm_client=mock_llm)

        step = PlanStep(
            step=3,
            mode="interactive",
            role="Reasoner",
            type="llm_reasoning",
            trust_level="untrusted_input",
            uses="system.echo",
            call="analyze",
            args={},
            after=[2],
            context_from=[2],
            policy_ref="policy-1",
            reasoning_config=ReasoningConfig(
                system_prompt_ref="test.prompt",
                output_schema_ref="slot_proposal_v1",
            ),
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        ctx.step_results[2] = StepResult(
            step=2, status="completed", result={}
        )

        request = ExecuteRequest(
            plan=ctx.plan,
            approval_token=_token(ctx.plan.plan_id),
            user_id="user-001",
            trace_id="trace-001",
        )

        with pytest.raises(StepExecutionError) as exc_info:
            await svc._execute_reasoning_step(step, ctx, request)
        assert "could not extract JSON" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_tier2_invalid_output_uses_fallback(self):
        """Tier 2 with non-JSON output does NOT hard fail (uses fallback)."""
        mock_llm = AsyncMock()
        mock_llm.reason = AsyncMock(
            return_value={"content": "No JSON here."}
        )
        svc = _build_service(llm_client=mock_llm)

        step = PlanStep(
            step=3,
            mode="interactive",
            role="Reasoner",
            type="llm_reasoning",
            trust_level="trusted",
            uses="system.echo",
            call="analyze",
            args={},
            after=[2],
            context_from=[2],
            policy_ref="policy-1",
            reasoning_config=ReasoningConfig(
                system_prompt_ref="test.prompt",
            ),
        )

        ctx = ExecutionContext(
            plan=Plan(
                plan_id="A" * 26,
                intent=_intent(),
                graph=[step],
                meta=_meta(),
            ),
            user_id="user-001",
            trace_id="trace-001",
        )
        ctx.step_results[2] = StepResult(
            step=2, status="completed", result={}
        )

        request = ExecuteRequest(
            plan=ctx.plan,
            approval_token=_token(ctx.plan.plan_id),
            user_id="user-001",
            trace_id="trace-001",
        )

        # Should NOT raise -- Tier 2 uses intent-based fallback
        result = await svc._execute_reasoning_step(step, ctx, request)
        assert "content" in result
