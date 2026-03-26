"""
Planner service integration tests — PlannerService.generate_plan() with mocked deps.

Covers: happy path, determinism, fallback hierarchy, edge cases.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from components.Planner.adapters.circuit_breaker import CircuitBreaker
from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.adapters.prompt_builder import PromptBuilder
from components.Planner.domain.models import (
    LLMCallError,
    PlannerResult,
)
from components.Planner.service.planner_service import PlannerService
from shared.schemas.plan import Plan

from .conftest import SAMPLE_INTENT, SAMPLE_VALID_PLAN_JSON

# ===========================
# T600: Happy Path Tests
# ===========================


class TestGeneratePlanHappyPath:
    @pytest.mark.asyncio
    async def test_generate_plan_happy_path(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert isinstance(result, PlannerResult)
        assert isinstance(result.plan, Plan)
        assert result.fallback_level == 1
        assert result.generation_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_generate_plan_deterministic_hash(self, planner_service, sample_intent):
        r1 = await planner_service.generate_plan(sample_intent)
        r2 = await planner_service.generate_plan(sample_intent)
        assert r1.plan.meta.canonical_hash == r2.plan.meta.canonical_hash

    @pytest.mark.asyncio
    async def test_generate_plan_signature_present(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert result.signature.algo == "Ed25519"
        assert result.signature.signer == "planner@system"
        assert len(result.signature.plan_hash) == 64

    @pytest.mark.asyncio
    async def test_generate_plan_plan_id_is_ulid(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert len(result.plan.plan_id) == 26

    @pytest.mark.asyncio
    async def test_generate_plan_plugins_populated(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        assert len(result.plan.plugins) > 0
        # All tool_ids from graph should be in plugins
        graph_tools = {s.uses for s in result.plan.graph}
        assert graph_tools == set(result.plan.plugins)

    @pytest.mark.asyncio
    async def test_generate_plan_dry_run_enforced(self, planner_service, sample_intent):
        result = await planner_service.generate_plan(sample_intent)
        for step in result.plan.graph:
            assert step.dry_run is True

    @pytest.mark.asyncio
    async def test_generate_plan_context_degraded_flag(
        self,
        mock_degraded_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_plan_service,
        mock_llm_adapter,
    ):
        svc = PlannerService(
            context_rag_service=mock_degraded_context_rag_service,
            registry_service=mock_registry_service,
            signer_service=mock_signer_service,
            plan_service=mock_plan_service,
            llm_adapter=mock_llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(registry_service=mock_registry_service),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.context_degraded is True

    @pytest.mark.asyncio
    async def test_generate_plan_registry_version_in_result(
        self,
        planner_service,
        sample_intent,
    ):
        result = await planner_service.generate_plan(sample_intent)
        assert result.registry_version == 1


# ===========================
# T601: Fallback Hierarchy Tests
# ===========================


class TestFallbackHierarchy:
    def _make_service(
        self,
        llm_adapter,
        context_rag,
        registry,
        signer,
        plan_service,
    ):
        return PlannerService(
            context_rag_service=context_rag,
            registry_service=registry,
            signer_service=signer,
            plan_service=plan_service,
            llm_adapter=llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(registry_service=registry),
            primary_breaker=CircuitBreaker(model_name="p", failure_threshold=1),
            fallback_breaker=CircuitBreaker(model_name="f", failure_threshold=1),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )

    @pytest.mark.asyncio
    async def test_fallback_level_2_on_primary_failure(
        self,
        mock_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_plan_service,
    ):
        """Primary fails, fallback succeeds -> level 2."""
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMCallError("primary", "simulated failure")
            return SAMPLE_VALID_PLAN_JSON

        adapter = AsyncMock()
        adapter.generate = AsyncMock(side_effect=side_effect)

        svc = self._make_service(
            adapter,
            mock_context_rag_service,
            mock_registry_service,
            mock_signer_service,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 2

    @pytest.mark.asyncio
    async def test_fallback_level_3_on_both_llms_fail(
        self,
        mock_failing_llm_adapter,
        mock_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_plan_service,
    ):
        """Both LLMs fail -> PlanLibrary template -> level 3."""
        svc = self._make_service(
            mock_failing_llm_adapter,
            mock_context_rag_service,
            mock_registry_service,
            mock_signer_service,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 3

    @pytest.mark.asyncio
    async def test_fallback_level_4_minimal_plan(
        self,
        mock_failing_llm_adapter,
        mock_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_empty_plan_service,
    ):
        """Both LLMs fail + no templates -> level 4 minimal plan."""
        svc = self._make_service(
            mock_failing_llm_adapter,
            mock_context_rag_service,
            mock_registry_service,
            mock_signer_service,
            mock_empty_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 4
        assert result.plan.graph[0].uses == "system.echo"

    @pytest.mark.asyncio
    async def test_fallback_level_indicator(
        self,
        mock_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_plan_service,
        mock_llm_adapter,
    ):
        """Level 1 when primary succeeds."""
        svc = self._make_service(
            mock_llm_adapter,
            mock_context_rag_service,
            mock_registry_service,
            mock_signer_service,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 1

    @pytest.mark.asyncio
    async def test_minimal_plan_structure(
        self,
        mock_failing_llm_adapter,
        mock_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_empty_plan_service,
    ):
        """Minimal plan has 1 Fetcher step with system.echo and dry_run=True."""
        svc = self._make_service(
            mock_failing_llm_adapter,
            mock_context_rag_service,
            mock_registry_service,
            mock_signer_service,
            mock_empty_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert len(result.plan.graph) == 1
        step = result.plan.graph[0]
        assert step.role == "Fetcher"
        assert step.uses == "system.echo"
        assert step.call == "echo"
        assert step.dry_run is True

    @pytest.mark.asyncio
    async def test_validation_failure_triggers_fallback(
        self,
        mock_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_plan_service,
    ):
        """LLM returns invalid plan -> falls to next level."""
        adapter = AsyncMock()
        # Returns invalid JSON that parses but fails schema
        adapter.generate = AsyncMock(return_value='{"invalid": "plan"}')

        svc = self._make_service(
            adapter,
            mock_context_rag_service,
            mock_registry_service,
            mock_signer_service,
            mock_plan_service,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        # Should fall through LLM levels to template (level 3) or minimal (level 4)
        assert result.fallback_level >= 3


# ===========================
# T602: Edge Cases and Concurrent Safety
# ===========================


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_entities_still_generates_plan(
        self,
        planner_service,
    ):
        from shared.schemas.intent import Intent

        intent = Intent(
            intent="schedule_meeting",
            entities={},
            constraints={},
            user_id="test-user",
        )
        result = await planner_service.generate_plan(intent)
        assert isinstance(result, PlannerResult)

    @pytest.mark.asyncio
    async def test_empty_evidence_context_degraded(
        self,
        mock_degraded_context_rag_service,
        mock_registry_service,
        mock_signer_service,
        mock_plan_service,
        mock_llm_adapter,
    ):
        svc = PlannerService(
            context_rag_service=mock_degraded_context_rag_service,
            registry_service=mock_registry_service,
            signer_service=mock_signer_service,
            plan_service=mock_plan_service,
            llm_adapter=mock_llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(registry_service=mock_registry_service),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.context_degraded is True

    @pytest.mark.asyncio
    async def test_empty_catalog_fallback_to_minimal(
        self,
        mock_context_rag_service,
        mock_empty_registry_service,
        mock_signer_service,
        mock_empty_plan_service,
        mock_failing_llm_adapter,
    ):
        """Empty tool catalog + LLM fails -> minimal plan."""
        svc = PlannerService(
            context_rag_service=mock_context_rag_service,
            registry_service=mock_empty_registry_service,
            signer_service=mock_signer_service,
            plan_service=mock_empty_plan_service,
            llm_adapter=mock_failing_llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(registry_service=mock_empty_registry_service),
            primary_breaker=CircuitBreaker(model_name="p", failure_threshold=1),
            fallback_breaker=CircuitBreaker(model_name="f", failure_threshold=1),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.fallback_level == 4

    @pytest.mark.asyncio
    async def test_concurrent_calls_safe(self, planner_service, sample_intent):
        """5 concurrent generate_plan calls should all succeed."""
        results = await asyncio.gather(
            *[planner_service.generate_plan(sample_intent) for _ in range(5)]
        )
        assert len(results) == 5
        for r in results:
            assert isinstance(r, PlannerResult)

    @pytest.mark.asyncio
    async def test_signer_failure_propagates(
        self,
        mock_context_rag_service,
        mock_registry_service,
        mock_plan_service,
        mock_llm_adapter,
    ):
        """Signer failure is fatal and propagates."""
        signer = AsyncMock()
        signer.sign_plan = AsyncMock(side_effect=RuntimeError("key not configured"))

        svc = PlannerService(
            context_rag_service=mock_context_rag_service,
            registry_service=mock_registry_service,
            signer_service=signer,
            plan_service=mock_plan_service,
            llm_adapter=mock_llm_adapter,
            prompt_builder=PromptBuilder(),
            validator=PlanValidator(registry_service=mock_registry_service),
            primary_breaker=CircuitBreaker(model_name="p"),
            fallback_breaker=CircuitBreaker(model_name="f"),
            primary_model="test-primary",
            fallback_model="test-fallback",
            max_output_tokens=4096,
        )
        with pytest.raises(RuntimeError, match="key not configured"):
            await svc.generate_plan(SAMPLE_INTENT)
