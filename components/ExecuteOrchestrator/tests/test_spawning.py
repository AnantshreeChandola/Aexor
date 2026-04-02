"""
Step Spawning + PolicyEngine Attestation Tests

Tests for spawn request evaluation, PolicyEngine integration,
attestation creation, limits, and gate injection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from shared.schemas.plan import PlanStep
from shared.schemas.policy import PolicyDecision, ReasoningConfig

from ..domain.models import (
    ExecutionContext,
    SpawnDeniedError,
    StepResult,
)


def _make_parent_step(can_spawn=True, max_spawned=3) -> PlanStep:
    return PlanStep(
        step=4,
        mode="interactive",
        role="Reasoner",
        uses="system.reasoner",
        call="reason",
        type="llm_reasoning",
        trust_level="trusted",
        can_spawn=can_spawn,
        max_spawned_steps=max_spawned,
        reasoning_config=ReasoningConfig(
            system_prompt_ref="test.prompt",
        ),
        context_from=[],
        policy_ref="policy-001",
    )


def _spawn_request(role="Fetcher", uses="google.flights", call="search"):
    return {
        "role": role,
        "uses": uses,
        "call": call,
        "args": {"from": "LAX"},
        "step_type": "api",
    }


class TestSpawnHandling:
    @pytest.fixture()
    def ctx(self, sample_plan):
        ctx = ExecutionContext(plan=sample_plan, user_id="u1", trace_id="t1")
        # Pre-populate some results so the service has context
        for s in sample_plan.graph:
            ctx.step_results[s.step] = StepResult(
                step=s.step,
                status="completed",
                result={"status": "ok"},
            )
        return ctx

    async def test_spawn_approved(self, execute_service, ctx, sample_execute_request):
        """Spawn approved: step appended, revision increments, attestation."""
        parent = _make_parent_step()
        await execute_service._handle_spawn(_spawn_request(), parent, ctx, sample_execute_request)

        assert len(ctx.spawned_steps) == 1
        assert ctx.plan_revision == 1
        assert len(ctx.attestations) == 1

    async def test_spawn_denied_tool_not_in_plugins(
        self, execute_service, ctx, sample_execute_request
    ):
        """Spawn denied when tool not in plan plugins."""
        execute_service._policy.evaluate_spawn = AsyncMock(
            return_value=PolicyDecision(
                allowed=False,
                reason="tool not allowed",
                violations=["tool 'bad.tool' not in plan plugins"],
            )
        )
        parent = _make_parent_step()
        with pytest.raises(SpawnDeniedError):
            await execute_service._handle_spawn(
                _spawn_request(uses="bad.tool"),
                parent,
                ctx,
                sample_execute_request,
            )

    async def test_spawn_denied_plan_limit(self, execute_service, ctx, sample_execute_request):
        """Spawn denied when plan step limit (100) exceeded."""
        # Artificially add 96 spawned steps
        for i in range(96):
            ctx.spawned_steps.append(
                PlanStep(
                    step=100 + i,
                    mode="interactive",
                    role="Fetcher",
                    uses="t",
                    call="c",
                )
            )

        parent = _make_parent_step()
        with pytest.raises(SpawnDeniedError, match="plan step limit"):
            await execute_service._handle_spawn(
                _spawn_request(),
                parent,
                ctx,
                sample_execute_request,
            )

    async def test_spawn_denied_per_step_limit(self, execute_service, ctx, sample_execute_request):
        """Spawn denied when per-step spawn limit exceeded."""
        parent = _make_parent_step(max_spawned=1)
        # Add one existing spawn from this parent
        ctx.spawned_steps.append(
            PlanStep(
                step=50,
                mode="interactive",
                role="Fetcher",
                uses="t",
                call="c",
                spawned_by=parent.step,
            )
        )

        with pytest.raises(SpawnDeniedError, match="spawn limit"):
            await execute_service._handle_spawn(
                _spawn_request(),
                parent,
                ctx,
                sample_execute_request,
            )

    async def test_spawned_booker_gets_gate_id(self, execute_service, ctx, sample_execute_request):
        """Spawned Booker step gets gate_id injected."""
        execute_service._policy.evaluate_spawn = AsyncMock(
            return_value=PolicyDecision(
                allowed=True,
                requires_approval=True,
                reason="Approved by policy 'test' v1",
            )
        )
        parent = _make_parent_step()
        await execute_service._handle_spawn(
            _spawn_request(role="Booker"),
            parent,
            ctx,
            sample_execute_request,
        )

        spawned = ctx.spawned_steps[0]
        assert spawned.gate_id is not None
        assert spawned.gate_id.startswith("gate-spawn-")

    async def test_spawned_step_no_recursive_spawn(
        self, execute_service, ctx, sample_execute_request
    ):
        """Spawned steps always have can_spawn=False."""
        parent = _make_parent_step()
        await execute_service._handle_spawn(
            _spawn_request(),
            parent,
            ctx,
            sample_execute_request,
        )
        assert ctx.spawned_steps[0].can_spawn is False

    async def test_spawned_step_has_spawned_by(self, execute_service, ctx, sample_execute_request):
        """Spawned step has spawned_by set to parent step number."""
        parent = _make_parent_step()
        await execute_service._handle_spawn(
            _spawn_request(),
            parent,
            ctx,
            sample_execute_request,
        )
        assert ctx.spawned_steps[0].spawned_by == parent.step

    async def test_spawn_requires_approval_not_auto_executed(
        self, execute_service, ctx, sample_execute_request
    ):
        """If requires_approval, spawned step is NOT auto-executed."""
        execute_service._policy.evaluate_spawn = AsyncMock(
            return_value=PolicyDecision(
                allowed=True,
                requires_approval=True,
                reason="Approved by policy 'test' v1",
            )
        )
        parent = _make_parent_step()
        await execute_service._handle_spawn(
            _spawn_request(),
            parent,
            ctx,
            sample_execute_request,
        )
        # Step should be in spawned_steps but NOT in step_results
        assert len(ctx.spawned_steps) == 1
        spawned_num = ctx.spawned_steps[0].step
        assert spawned_num not in ctx.step_results

    async def test_multiple_spawns_tracked(self, execute_service, ctx, sample_execute_request):
        """Multiple spawns from same Reasoner tracked correctly."""
        parent = _make_parent_step(max_spawned=3)
        for i in range(2):
            await execute_service._handle_spawn(
                _spawn_request(call=f"search_{i}"),
                parent,
                ctx,
                sample_execute_request,
            )
        assert len(ctx.spawned_steps) == 2
        assert ctx.plan_revision == 2

    async def test_attestation_fields(self, execute_service, ctx, sample_execute_request):
        """Attestation has correct fields."""
        parent = _make_parent_step()
        await execute_service._handle_spawn(
            _spawn_request(),
            parent,
            ctx,
            sample_execute_request,
        )
        att = ctx.attestations[0]
        assert len(att.attestation_id) == 26  # ULID
        assert att.plan_id == ctx.plan.plan_id
        assert att.plan_revision == 1
        assert att.spawned_by_step == parent.step
        assert len(att.new_steps) == 1
