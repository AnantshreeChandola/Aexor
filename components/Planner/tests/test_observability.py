"""
Planner observability tests — logging safety, structured fields.

Covers: no PII in logs, no credential values, structured log fields.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from components.Planner.adapters.circuit_breaker import CircuitBreaker
from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.adapters.prompt_builder import PromptBuilder
from components.Planner.domain.models import LLMCallError
from components.Planner.service.planner_service import PlannerService

from .conftest import SAMPLE_INTENT


def _make_service_with_adapter(
    adapter,
    context_rag,
    tool_catalog,
    plan_service,
):
    return PlannerService(
        context_rag_service=context_rag,
        tool_catalog=tool_catalog,
        plan_service=plan_service,
        llm_adapter=adapter,
        prompt_builder=PromptBuilder(),
        validator=PlanValidator(),
        primary_breaker=CircuitBreaker(model_name="p", failure_threshold=1),
        fallback_breaker=CircuitBreaker(model_name="f", failure_threshold=1),
        primary_model="test-primary",
        fallback_model="test-fallback",
        max_output_tokens=4096,
    )


class TestObservability:
    @pytest.mark.asyncio
    async def test_no_pii_in_log_output(
        self,
        planner_service,
        sample_intent,
        caplog,
    ):
        with caplog.at_level(logging.DEBUG):
            await planner_service.generate_plan(sample_intent)
        log_text = caplog.text
        # Entity values should NOT appear in logs
        assert "Alice" not in log_text
        assert "tomorrow 2pm" not in log_text

    @pytest.mark.asyncio
    async def test_no_credential_values_in_logs(
        self,
        planner_service,
        sample_intent,
        caplog,
    ):
        with caplog.at_level(logging.DEBUG):
            await planner_service.generate_plan(sample_intent)
        log_text = caplog.text
        assert "ANTHROPIC_API_KEY" not in log_text
        assert "sk-ant-" not in log_text

    @pytest.mark.asyncio
    async def test_structured_log_fields_present(
        self,
        planner_service,
        sample_intent,
        caplog,
    ):
        with caplog.at_level(logging.INFO):
            await planner_service.generate_plan(sample_intent)
        # Check that structured fields are present in log records
        found_start = False
        found_complete = False
        for record in caplog.records:
            if record.msg == "plan_generation_start":
                found_start = True
                assert record.component == "planner"
                assert record.op == "generate_plan"
                assert record.intent_type == "schedule_meeting"
            if record.msg == "plan_generation_complete":
                found_complete = True
                assert record.component == "planner"
                assert hasattr(record, "plan_id")
                assert hasattr(record, "duration_ms")
        assert found_start, "plan_generation_start log not found"
        assert found_complete, "plan_generation_complete log not found"

    @pytest.mark.asyncio
    async def test_fallback_triggered_logged(
        self,
        mock_context_rag_service,
        mock_tool_catalog,
        mock_plan_service,
        caplog,
    ):
        """Verify fallback_triggered event is logged when falling back."""
        adapter = AsyncMock()
        adapter.generate = AsyncMock(side_effect=LLMCallError("test", "fail"))
        svc = _make_service_with_adapter(
            adapter,
            mock_context_rag_service,
            mock_tool_catalog,
            mock_plan_service,
        )
        with caplog.at_level(logging.WARNING):
            await svc.generate_plan(SAMPLE_INTENT)
        fallback_logs = [r for r in caplog.records if r.msg == "fallback_triggered"]
        assert len(fallback_logs) >= 1

    @pytest.mark.asyncio
    async def test_context_rag_failure_logged(
        self,
        mock_tool_catalog,
        mock_plan_service,
        mock_llm_adapter,
        caplog,
    ):
        """Verify warning when ContextRAG fails."""
        failing_crag = AsyncMock()
        failing_crag.gather_evidence = AsyncMock(side_effect=RuntimeError("crag down"))

        svc = _make_service_with_adapter(
            mock_llm_adapter,
            failing_crag,
            mock_tool_catalog,
            mock_plan_service,
        )
        with caplog.at_level(logging.WARNING):
            result = await svc.generate_plan(SAMPLE_INTENT)
        assert result.context_degraded is True
        assert any("context_rag_failed" in r.msg for r in caplog.records)
