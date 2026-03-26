"""
Planner unit tests — adapters and domain models.

Covers: plan_hasher, circuit_breaker, prompt_builder, plan_validator, llm_adapter
"""

from __future__ import annotations

import time
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.Planner.adapters.circuit_breaker import CircuitBreaker, CircuitState
from components.Planner.adapters.llm_adapter import AnthropicAdapter, LLMAdapter
from components.Planner.adapters.plan_hasher import canonicalize_plan, compute_plan_hash
from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.adapters.prompt_builder import PromptBuilder
from components.Planner.domain.models import (
    CircuitOpenError,
    LLMCallError,
    PlanValidationError,
)
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan

from .conftest import (
    SAMPLE_INTENT,
    SAMPLE_INVALID_JSON,
    SAMPLE_PLAN_FORWARD_DEP,
    SAMPLE_PLAN_MISSING_TOOL,
    SAMPLE_PLAN_TOO_MANY_STEPS,
    SAMPLE_VALID_PLAN_JSON,
)

# ===========================
# T500: Plan Hasher Tests
# ===========================


class TestPlanHasher:
    def test_canonicalize_produces_sorted_keys(self):
        data = {"z": 1, "a": 2, "m": 3}
        result = canonicalize_plan(data)
        assert result == '{"a":2,"m":3,"z":1}'

    def test_canonicalize_deterministic(self):
        d1 = {"b": 1, "a": 2}
        d2 = {"a": 2, "b": 1}
        assert canonicalize_plan(d1) == canonicalize_plan(d2)

    def test_compute_hash_returns_64_char_hex(self):
        h = compute_plan_hash({"test": "data"})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_hash_deterministic(self):
        d = {"key": "value", "num": 42}
        assert compute_plan_hash(d) == compute_plan_hash(d)

    def test_compute_hash_matches_signer_canonicalizer(self):
        from components.Signer.adapters.canonicalizer import (
            compute_plan_hash as signer_hash,
        )

        d = {"plan_id": "test", "graph": [{"step": 1}]}
        assert compute_plan_hash(d) == signer_hash(d)


# ===========================
# T501: Circuit Breaker Tests
# ===========================


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_successful_call_stays_closed(self):
        cb = CircuitBreaker()
        result = await cb.call(AsyncMock(return_value="ok"))
        assert result == "ok"
        assert cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_increments_count(self):
        cb = CircuitBreaker(failure_threshold=5)
        with pytest.raises(ValueError):
            await cb.call(AsyncMock(side_effect=ValueError("fail")))
        assert cb._failure_count == 1
        assert cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_reaches_threshold_opens_circuit(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(5):
            with pytest.raises(ValueError):
                await cb.call(AsyncMock(side_effect=ValueError("fail")))
        assert cb.get_state() == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_raises_circuit_open_error(self):
        cb = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(AsyncMock(side_effect=ValueError("fail")))
        with pytest.raises(CircuitOpenError):
            await cb.call(AsyncMock(return_value="ok"))

    @pytest.mark.asyncio
    async def test_open_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, timeout_s=0)  # instant timeout
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(AsyncMock(side_effect=ValueError("fail")))
        assert cb._state == CircuitState.OPEN
        # After timeout, get_state returns HALF_OPEN
        cb._last_failure_time = time.monotonic() - 1
        assert cb.get_state() == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_success_increments_success_count(self):
        cb = CircuitBreaker(failure_threshold=2, timeout_s=0, success_threshold=2)
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(AsyncMock(side_effect=ValueError("fail")))
        cb._last_failure_time = time.monotonic() - 1
        await cb.call(AsyncMock(return_value="ok"))
        assert cb._success_count == 1

    @pytest.mark.asyncio
    async def test_half_open_two_successes_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, timeout_s=0, success_threshold=2)
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(AsyncMock(side_effect=ValueError("fail")))
        cb._last_failure_time = time.monotonic() - 1
        await cb.call(AsyncMock(return_value="ok"))
        await cb.call(AsyncMock(return_value="ok"))
        assert cb.get_state() == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, timeout_s=0, success_threshold=2)
        for _ in range(2):
            with pytest.raises(ValueError):
                await cb.call(AsyncMock(side_effect=ValueError("fail")))
        cb._last_failure_time = time.monotonic() - 1
        with pytest.raises(ValueError):
            await cb.call(AsyncMock(side_effect=ValueError("fail again")))
        assert cb._state == CircuitState.OPEN

    def test_get_state_returns_current_state(self):
        cb = CircuitBreaker()
        assert isinstance(cb.get_state(), CircuitState)


# ===========================
# T502: Prompt Builder Tests
# ===========================


class TestPromptBuilder:
    def setup_method(self):
        self.builder = PromptBuilder()

    def test_system_prompt_contains_plan_schema(self):
        prompt = self.builder.build_system_prompt()
        assert "graph" in prompt
        assert "step" in prompt
        assert "dry_run" in prompt

    def test_system_prompt_contains_all_roles(self):
        prompt = self.builder.build_system_prompt()
        for role in ["Fetcher", "Analyzer", "Watcher", "Resolver", "Booker", "Notifier"]:
            assert role in prompt

    def test_system_prompt_contains_dry_run_rule(self):
        prompt = self.builder.build_system_prompt()
        assert "dry_run" in prompt
        assert "true" in prompt.lower()

    def test_user_prompt_contains_intent_and_evidence(self):
        from .conftest import SAMPLE_EVIDENCE, _make_catalog_response, _make_tool_model

        tools = [_make_tool_model("system.echo", "Echo")]
        catalog = _make_catalog_response(tools)
        prompt = self.builder.build_user_prompt(SAMPLE_INTENT, list(SAMPLE_EVIDENCE), catalog)
        assert "schedule_meeting" in prompt
        assert "meeting_duration_min" in prompt

    def test_user_prompt_truncates_long_intent(self):
        from .conftest import _make_catalog_response

        long_intent = Intent(
            intent="x" * 20_000,
            entities={},
            constraints={},
            user_id="test-user",
        )
        catalog = _make_catalog_response([])
        prompt = self.builder.build_user_prompt(long_intent, [], catalog)
        assert "[truncated]" in prompt


# ===========================
# T503: Plan Validator Tests
# ===========================


class TestPlanValidator:
    def setup_method(self):
        self.registry = AsyncMock()
        self.registry.validate_plan_tools = AsyncMock(
            return_value=MagicMock(valid=True, current_version=1, issues=[])
        )
        self.validator = PlanValidator(registry_service=self.registry)
        self.tool_ids = {"google.calendar", "system.echo"}

    # Layer 1: JSON parse
    @pytest.mark.asyncio
    async def test_layer1_invalid_json_raises_json_parse_error(self):
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(SAMPLE_INVALID_JSON, SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "json_parse"

    @pytest.mark.asyncio
    async def test_layer1_empty_string_raises_json_parse_error(self):
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate("", SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "json_parse"

    # Layer 2: Schema validation
    @pytest.mark.asyncio
    async def test_layer2_valid_plan_passes(self):
        plan = await self.validator.validate(
            SAMPLE_VALID_PLAN_JSON, SAMPLE_INTENT, 1, self.tool_ids
        )
        assert isinstance(plan, Plan)

    @pytest.mark.asyncio
    async def test_layer2_missing_required_field_raises_schema_error(self):
        bad = '{"plan_id": "01JBXYZ1234567890ABCDEFGHI"}'
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(bad, SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "schema"

    @pytest.mark.asyncio
    async def test_layer2_forward_dependency_raises_schema_error(self):
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(SAMPLE_PLAN_FORWARD_DEP, SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "schema"
        assert "forward" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_layer2_self_dependency_raises_schema_error(self):
        import json
        from datetime import datetime

        data = {
            "plan_id": "01JBXYZ1234567890ABCDEFGHI",
            "intent": SAMPLE_INTENT.model_dump(mode="json"),
            "trace_id": SAMPLE_INTENT.trace_id,
            "graph": [
                {
                    "step": 1,
                    "mode": "interactive",
                    "role": "Fetcher",
                    "uses": "system.echo",
                    "call": "echo",
                    "args": {},
                    "after": [1],
                    "timeout_s": 30,
                    "dry_run": True,
                }
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "author": "planner@system",
                "version": "v2.0.0",
                "canonical_hash": "a" * 64,
                "hash_algo": "sha256",
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "schema"
        assert "self-dependency" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_layer2_duplicate_step_numbers_raises_schema_error(self):
        import json
        from datetime import datetime

        data = {
            "plan_id": "01JBXYZ1234567890ABCDEFGHI",
            "intent": SAMPLE_INTENT.model_dump(mode="json"),
            "trace_id": SAMPLE_INTENT.trace_id,
            "graph": [
                {
                    "step": 1,
                    "mode": "interactive",
                    "role": "Fetcher",
                    "uses": "system.echo",
                    "call": "echo",
                    "args": {},
                    "after": [],
                    "timeout_s": 30,
                    "dry_run": True,
                },
                {
                    "step": 1,
                    "mode": "interactive",
                    "role": "Fetcher",
                    "uses": "system.echo",
                    "call": "echo",
                    "args": {},
                    "after": [],
                    "timeout_s": 30,
                    "dry_run": True,
                },
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "author": "planner@system",
                "version": "v2.0.0",
                "canonical_hash": "a" * 64,
                "hash_algo": "sha256",
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert "duplicate" in exc_info.value.message.lower()

    # Layer 3: Business rules
    @pytest.mark.asyncio
    async def test_layer3_nonexistent_tool_raises_business_error(self):
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(SAMPLE_PLAN_MISSING_TOOL, SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"

    @pytest.mark.asyncio
    async def test_layer3_exceeds_50_steps_raises_business_error(self):
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                SAMPLE_PLAN_TOO_MANY_STEPS, SAMPLE_INTENT, 1, {"system.echo"}
            )
        assert exc_info.value.layer == "business_rules"
        assert "50" in exc_info.value.message or "51" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_layer3_valid_plan_passes_all_layers(self):
        plan = await self.validator.validate(
            SAMPLE_VALID_PLAN_JSON, SAMPLE_INTENT, 1, self.tool_ids
        )
        assert isinstance(plan, Plan)
        assert len(plan.graph) == 4


# ===========================
# T504: LLM Adapter Tests
# ===========================


class TestLLMAdapter:
    def test_anthropic_adapter_implements_protocol(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            adapter = AnthropicAdapter(api_key="test-key")
        assert isinstance(adapter, LLMAdapter)

    @pytest.mark.asyncio
    async def test_anthropic_adapter_wraps_api_errors_in_llm_call_error(self):
        import anthropic

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            adapter = AnthropicAdapter(api_key="test-key")

        adapter._client = AsyncMock()
        adapter._client.messages.create = AsyncMock(
            side_effect=anthropic.APIStatusError(
                message="server error",
                response=MagicMock(status_code=500),
                body=None,
            )
        )
        with pytest.raises(LLMCallError) as exc_info:
            await adapter.generate("test-model", "sys", "usr")
        assert "500" in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_anthropic_adapter_wraps_timeout_in_llm_call_error(self):
        import anthropic

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            adapter = AnthropicAdapter(api_key="test-key")

        adapter._client = AsyncMock()
        adapter._client.messages.create = AsyncMock(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )
        with pytest.raises(LLMCallError) as exc_info:
            await adapter.generate("test-model", "sys", "usr")
        assert "timeout" in exc_info.value.reason.lower()

    @pytest.mark.asyncio
    async def test_anthropic_adapter_returns_text_content(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            adapter = AnthropicAdapter(api_key="test-key")

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = '{"plan": "test"}'
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        adapter._client = AsyncMock()
        adapter._client.messages.create = AsyncMock(return_value=mock_response)

        result = await adapter.generate("test-model", "sys", "usr")
        assert result == '{"plan": "test"}'
