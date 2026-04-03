"""
Planner contract tests — GLOBAL_SPEC envelope conformance.

Covers: Plan §2.3, HITL gate_id, canonical_hash.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from shared.schemas.plan import Plan

from .conftest import SAMPLE_INTENT

# ===========================
# T700: GLOBAL_SPEC Conformance
# ===========================


class TestGlobalSpecConformance:
    @pytest.mark.asyncio
    async def test_plan_conforms_to_global_spec_section_2_3(
        self,
        planner_service,
        sample_intent,
    ):
        result = await planner_service.generate_plan(sample_intent)
        # Re-validate through Pydantic to confirm conformance
        plan_dict = result.plan.model_dump(mode="json")
        validated = Plan.model_validate(plan_dict)
        assert validated.plan_id == result.plan.plan_id

    @pytest.mark.asyncio
    async def test_plan_intent_embedded(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert result.plan.intent.intent == sample_intent.intent
        assert result.plan.intent.user_id == sample_intent.user_id

    @pytest.mark.asyncio
    async def test_plan_meta_has_canonical_hash(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        h = result.plan.meta.canonical_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    @pytest.mark.asyncio
    async def test_plan_meta_author_is_planner_at_system(
        self,
        planner_service,
        sample_intent,
    ):
        result = await planner_service.generate_plan(sample_intent)
        assert result.plan.meta.author == "planner@system"

    @pytest.mark.asyncio
    async def test_plan_constraints_present(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert result.plan.constraints is not None
        assert result.plan.constraints.ttl_s > 0


# ===========================
# T701: HITL Gate Insertion
# ===========================


class TestHITLGateInsertion:
    @pytest.mark.asyncio
    async def test_booker_steps_have_gate_id(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        booker_steps = [s for s in result.plan.graph if s.role == "Booker"]
        for step in booker_steps:
            assert step.gate_id is not None, f"Booker step {step.step} missing gate_id"

    @pytest.mark.asyncio
    async def test_readonly_plan_no_gate_id_required(
        self,
        mock_context_rag_service,
        mock_registry_service,
        mock_plan_service,
    ):
        """Plan with only Fetcher/Analyzer steps doesn't require gate_id."""
        import json
        from datetime import datetime
        from unittest.mock import AsyncMock

        from components.Planner.adapters.circuit_breaker import CircuitBreaker
        from components.Planner.adapters.plan_validator import PlanValidator
        from components.Planner.adapters.prompt_builder import PromptBuilder
        from components.Planner.service.planner_service import PlannerService

        readonly_plan_json = json.dumps(
            {
                "plan_id": "01JBXYZ1234567890ABCDEFGHI",
                "intent": SAMPLE_INTENT.model_dump(mode="json"),
                "trace_id": SAMPLE_INTENT.trace_id,
                "graph": [
                    {
                        "step": 1,
                        "mode": "interactive",
                        "role": "Fetcher",
                        "uses": "google.calendar",
                        "call": "list_events",
                        "args": {},
                        "after": [],
                        "timeout_s": 30,
                        "dry_run": True,
                    },
                    {
                        "step": 2,
                        "mode": "interactive",
                        "role": "Analyzer",
                        "uses": "system.echo",
                        "call": "analyze",
                        "args": {},
                        "after": [1],
                        "timeout_s": 30,
                        "dry_run": True,
                    },
                ],
                "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
                "plugins": ["google.calendar", "system.echo"],
                "meta": {
                    "created_at": datetime.now(UTC).isoformat(),
                    "author": "planner@system",
                    "version": "v2.0.0",
                    "canonical_hash": "a" * 64,
                    "hash_algo": "sha256",
                },
            }
        )

        adapter = AsyncMock()
        adapter.generate = AsyncMock(return_value=readonly_plan_json)

        svc = PlannerService(
            context_rag_service=mock_context_rag_service,
            registry_service=mock_registry_service,
            plan_service=mock_plan_service,
            llm_adapter=adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(registry_service=mock_registry_service),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        # All steps are Fetcher/Analyzer — no gate_id needed
        for step in result.plan.graph:
            assert step.role in ("Fetcher", "Analyzer")

    @pytest.mark.asyncio
    async def test_gate_id_format(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        booker_steps = [s for s in result.plan.graph if s.role == "Booker"]
        for step in booker_steps:
            assert step.gate_id is not None
            assert step.gate_id.startswith("gate-")
