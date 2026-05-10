"""
Planner unit tests — adapters and domain models.

Covers: plan_hasher, circuit_breaker, prompt_builder, plan_validator, llm_adapter
"""

from __future__ import annotations

import time
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.Planner.adapters.circuit_breaker import CircuitBreaker, CircuitState
from components.Planner.adapters.llm import LLMAdapter, LLMConfig
from components.Planner.adapters.llm.providers.anthropic import AnthropicAdapter
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
        for role in [
            "Fetcher",
            "Analyzer",
            "Watcher",
            "Resolver",
            "Booker",
            "Notifier",
            "Reasoner",
        ]:
            assert role in prompt

    def test_system_prompt_contains_hybrid_step_types(self):
        prompt = self.builder.build_system_prompt()
        assert "llm_reasoning" in prompt
        assert "policy_check" in prompt
        assert "api" in prompt

    def test_system_prompt_contains_spawning_rules(self):
        prompt = self.builder.build_system_prompt()
        assert "can_spawn" in prompt
        assert "max_spawned_steps" in prompt

    def test_system_prompt_contains_dry_run_rule(self):
        prompt = self.builder.build_system_prompt()
        assert "dry_run" in prompt
        assert "true" in prompt.lower()

    def test_user_prompt_contains_intent_and_evidence(self):
        from .conftest import SAMPLE_EVIDENCE, _make_tool_definition

        tools = [_make_tool_definition("system.echo", "Echo")]
        prompt = self.builder.build_user_prompt(SAMPLE_INTENT, list(SAMPLE_EVIDENCE), tools)
        assert "schedule_meeting" in prompt
        assert "meeting_duration_min" in prompt

    def test_user_prompt_truncates_long_intent(self):
        long_intent = Intent(
            intent="x" * 20_000,
            entities={},
            constraints={},
            user_id="test-user",
        )
        prompt = self.builder.build_user_prompt(long_intent, [], [])
        assert "[truncated]" in prompt

    # -- T1100/T1101: Trust boundary prompt builder additions --

    def test_system_prompt_contains_guard_role(self):
        """Guard role should be listed in schema and role assignments."""
        prompt = self.builder.build_system_prompt()
        assert "Guard" in prompt

    def test_system_prompt_contains_sanitizer_type(self):
        """Sanitizer step type should be listed in schema and step types."""
        prompt = self.builder.build_system_prompt()
        assert "sanitizer" in prompt

    def test_system_prompt_contains_trust_filter_scan(self):
        """trust_filter.scan pseudo-tool should be mentioned."""
        prompt = self.builder.build_system_prompt()
        assert "trust_filter.scan" in prompt

    def test_system_prompt_contains_sanitizer_insertion_rule(self):
        """Rule 21 instructs mandatory sanitizer insertion."""
        prompt = self.builder.build_system_prompt()
        assert "MANDATORY SANITIZER STEPS" in prompt
        assert "trust_filter.scan" in prompt
        assert "load_bearing_fields" in prompt

    def test_system_prompt_contains_tier1_reasoner_rules(self):
        """Rule 22 describes Tier 1 reasoner requirements."""
        prompt = self.builder.build_system_prompt()
        assert "output_schema_ref" in prompt
        assert "slot_proposal_v1" in prompt

    def test_system_prompt_contains_sanitizer_example_pattern(self):
        """Rule 24 shows example pipeline patterns."""
        prompt = self.builder.build_system_prompt()
        assert "sanitizer(step 2, context_from=[1])" in prompt
        assert "reasoner(step 3, context_from=[2])" in prompt

    def test_system_prompt_pure_api_plans_exempt(self):
        """Prompt should note that pure-API plans do not need sanitizers."""
        prompt = self.builder.build_system_prompt()
        assert "Pure-API plans" in prompt

    def test_system_prompt_updated_plan_structure_pattern(self):
        """Rule 12 mentions Guard step between Fetcher and Reasoner."""
        prompt = self.builder.build_system_prompt()
        assert "Guard step" in prompt


# ===========================
# T503: Plan Validator Tests
# ===========================


class TestPlanValidator:
    def setup_method(self):
        self.registry = AsyncMock()
        self.registry.validate_plan_tools = AsyncMock(
            return_value=MagicMock(valid=True, current_version=1, issues=[])
        )
        self.validator = PlanValidator()
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
    @pytest.mark.xfail(
        reason="Tool existence check moved from validator to _finalize_plan() for fuzzy name matching",
        strict=True,
    )
    async def test_layer3_nonexistent_tool_raises_business_error(self):
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(SAMPLE_PLAN_MISSING_TOOL, SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"

    @pytest.mark.asyncio
    async def test_layer3_exceeds_100_steps_raises_error(self):
        """Plans with >100 steps are rejected (by schema or business rules layer)."""
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                SAMPLE_PLAN_TOO_MANY_STEPS, SAMPLE_INTENT, 1, {"system.echo"}
            )
        assert exc_info.value.layer in ("schema", "business_rules")

    @pytest.mark.asyncio
    async def test_layer3_valid_plan_passes_all_layers(self):
        plan = await self.validator.validate(
            SAMPLE_VALID_PLAN_JSON, SAMPLE_INTENT, 1, self.tool_ids
        )
        assert isinstance(plan, Plan)
        assert len(plan.graph) == 4

    # --- Hybrid execution validation tests ---

    @pytest.mark.asyncio
    async def test_layer3_reasoner_without_policy_ref_raises(self):
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
                    "role": "Reasoner",
                    "uses": "system.echo",
                    "call": "analyze",
                    "type": "llm_reasoning",
                    "args": {},
                    "after": [],
                    "timeout_s": 30,
                    "dry_run": True,
                    "reasoning_config": {
                        "system_prompt_ref": "test.prompt",
                    },
                }
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "policy_ref" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_layer3_llm_reasoning_without_config_raises(self):
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
                    "role": "Reasoner",
                    "uses": "system.echo",
                    "call": "analyze",
                    "type": "llm_reasoning",
                    "args": {},
                    "after": [],
                    "timeout_s": 30,
                    "dry_run": True,
                    "policy_ref": "policy-1",
                }
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "reasoning_config" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_layer3_policy_check_without_policy_ref_raises(self):
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
                    "role": "Analyzer",
                    "uses": "system.echo",
                    "call": "check",
                    "type": "policy_check",
                    "args": {},
                    "after": [],
                    "timeout_s": 30,
                    "dry_run": True,
                }
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "policy_ref" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_layer3_context_from_forward_ref_raises(self):
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
                    "context_from": [2],
                }
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "context_from" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_layer3_valid_reasoner_step_passes(self):
        """Reasoner step with proper sanitizer in between passes all rules."""
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
                    "role": "Guard",
                    "type": "sanitizer",
                    "uses": "trust_filter.scan",
                    "call": "scan",
                    "args": {},
                    "after": [1],
                    "context_from": [1],
                    "timeout_s": 30,
                    "dry_run": True,
                },
                {
                    "step": 3,
                    "mode": "interactive",
                    "role": "Reasoner",
                    "uses": "system.echo",
                    "call": "analyze",
                    "type": "llm_reasoning",
                    "trust_level": "untrusted_input",
                    "args": {},
                    "after": [2],
                    "context_from": [2],
                    "timeout_s": 60,
                    "dry_run": True,
                    "policy_ref": "policy-flight-analysis",
                    "reasoning_config": {
                        "system_prompt_ref": "reasoner.flight_analysis",
                        "output_schema_ref": "slot_proposal_v1",
                    },
                },
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["google.calendar", "system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        plan = await self.validator.validate(
            json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids
        )
        assert isinstance(plan, Plan)
        assert plan.graph[2].role == "Reasoner"
        assert plan.graph[2].type == "llm_reasoning"

    @pytest.mark.asyncio
    async def test_layer3_existing_api_steps_default_type(self):
        """Existing plans without type field default to 'api'."""
        plan = await self.validator.validate(
            SAMPLE_VALID_PLAN_JSON, SAMPLE_INTENT, 1, self.tool_ids
        )
        for step in plan.graph:
            assert step.type == "api"

    # --- v6.1 trust boundary & spawning rules ---

    @pytest.mark.asyncio
    async def test_layer3_trust_boundary_no_sanitizer_raises(self):
        """Rule F: llm_reasoning referencing API step without intervening sanitizer is rejected."""
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
                    "type": "api",
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
                    "role": "Reasoner",
                    "type": "llm_reasoning",
                    "trust_level": "trusted",
                    "context_from": [1],
                    "uses": "system.echo",
                    "call": "analyze",
                    "args": {},
                    "after": [1],
                    "timeout_s": 60,
                    "dry_run": True,
                    "policy_ref": "policy-test",
                    "reasoning_config": {
                        "system_prompt_ref": "test.prompt",
                    },
                },
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["google.calendar", "system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "Rule F" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_layer3_trust_boundary_with_sanitizer_passes(self):
        """Rule F: llm_reasoning referencing API via sanitizer passes."""
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
                    "type": "api",
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
                    "role": "Guard",
                    "type": "sanitizer",
                    "context_from": [1],
                    "uses": "trust_filter.scan",
                    "call": "scan",
                    "args": {},
                    "after": [1],
                    "timeout_s": 30,
                    "dry_run": True,
                },
                {
                    "step": 3,
                    "mode": "interactive",
                    "role": "Reasoner",
                    "type": "llm_reasoning",
                    "trust_level": "untrusted_input",
                    "context_from": [2],
                    "uses": "system.echo",
                    "call": "analyze",
                    "args": {},
                    "after": [2],
                    "timeout_s": 60,
                    "dry_run": True,
                    "policy_ref": "policy-sanitize",
                    "reasoning_config": {
                        "system_prompt_ref": "sanitize.prompt",
                        "output_schema_ref": "slot_proposal_v1",
                    },
                },
                {
                    "step": 4,
                    "mode": "interactive",
                    "role": "Reasoner",
                    "type": "llm_reasoning",
                    "trust_level": "trusted",
                    "context_from": [3],
                    "uses": "system.echo",
                    "call": "plan",
                    "args": {},
                    "after": [3],
                    "timeout_s": 60,
                    "dry_run": True,
                    "policy_ref": "policy-analyze",
                    "reasoning_config": {
                        "system_prompt_ref": "analyze.prompt",
                    },
                },
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["google.calendar", "system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        plan = await self.validator.validate(
            json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids
        )
        assert plan.graph[3].trust_level == "trusted"

    @pytest.mark.asyncio
    async def test_layer3_no_recursive_spawning_raises(self):
        """Rule B: Spawned step with can_spawn=true is rejected."""
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
                    "uses": "google.calendar",
                    "call": "list_events",
                    "args": {},
                    "after": [],
                    "timeout_s": 30,
                    "dry_run": True,
                    "spawned_by": 0,
                    "can_spawn": True,
                },
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["google.calendar"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "spawned" in exc_info.value.message.lower()
        assert "can_spawn" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_layer3_spawned_booker_without_gate_raises(self):
        """Rule D: Spawned Booker step without gate_id is rejected."""
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
                    "role": "Booker",
                    "uses": "google.calendar",
                    "call": "create_event",
                    "args": {},
                    "after": [],
                    "timeout_s": 60,
                    "dry_run": True,
                    "spawned_by": 0,
                },
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["google.calendar"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "booker" in exc_info.value.message.lower()
        assert "gate_id" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_layer3_spawning_step_tool_not_in_plugins_raises(self):
        """Rule C: API step with can_spawn using tool not in plugins is rejected."""
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
                    "type": "api",
                    "uses": "google.calendar",
                    "call": "list_events",
                    "args": {},
                    "after": [],
                    "timeout_s": 60,
                    "dry_run": True,
                    "can_spawn": True,
                    "max_spawned_steps": 3,
                },
            ],
            "constraints": {"scopes": [], "ttl_s": 900, "max_retries": 3},
            "plugins": ["system.echo"],
            "meta": {
                "created_at": datetime.now(UTC).isoformat(),
                "canonical_hash": "a" * 64,
            },
        }
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(json.dumps(data), SAMPLE_INTENT, 1, self.tool_ids)
        assert exc_info.value.layer == "business_rules"
        assert "plugins" in exc_info.value.message.lower()


# ===========================
# T504: LLM Adapter Tests
# ===========================


class TestLLMAdapter:
    def test_anthropic_adapter_implements_protocol(self):
        adapter = AnthropicAdapter(
            LLMConfig(provider="anthropic", api_key="test-key", timeout_s=10)
        )
        assert isinstance(adapter, LLMAdapter)

    @pytest.mark.asyncio
    async def test_anthropic_adapter_wraps_api_errors_in_llm_call_error(self):
        import anthropic

        adapter = AnthropicAdapter(
            LLMConfig(provider="anthropic", api_key="test-key", timeout_s=10)
        )

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

        adapter = AnthropicAdapter(
            LLMConfig(provider="anthropic", api_key="test-key", timeout_s=10)
        )

        adapter._client = AsyncMock()
        adapter._client.messages.create = AsyncMock(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )
        with pytest.raises(LLMCallError) as exc_info:
            await adapter.generate("test-model", "sys", "usr")
        assert "timeout" in exc_info.value.reason.lower()

    @pytest.mark.asyncio
    async def test_anthropic_adapter_returns_text_content(self):
        adapter = AnthropicAdapter(
            LLMConfig(provider="anthropic", api_key="test-key", timeout_s=10)
        )

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = '{"plan": "test"}'
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        adapter._client = AsyncMock()
        adapter._client.messages.create = AsyncMock(return_value=mock_response)

        result = await adapter.generate("test-model", "sys", "usr")
        assert result == '{"plan": "test"}'


# ===========================
# T505: DeterministicPlanner Tests
# ===========================


class TestDeterministicPlanner:
    """Verify DeterministicPlanner builds multi-step DAGs from registry."""

    def setup_method(self):
        from components.Planner.adapters.deterministic_planner import DeterministicPlanner

        self.planner = DeterministicPlanner()

    def _make_tools(self):
        """Build Composio-style tools for catalog validation."""
        from .conftest import _make_composio_tool

        return [
            # Gmail
            _make_composio_tool("GMAIL_SEND_EMAIL", "gmail"),
            _make_composio_tool("GMAIL_FETCH_EMAILS", "gmail"),
            _make_composio_tool("GMAIL_CREATE_DRAFT", "gmail"),
            # Google Calendar
            _make_composio_tool("GOOGLECALENDAR_CREATE_EVENT", "googlecalendar"),
            _make_composio_tool("GOOGLECALENDAR_FIND_EVENT", "googlecalendar"),
            _make_composio_tool("GOOGLECALENDAR_LIST_EVENTS", "googlecalendar"),
            # Google Docs
            _make_composio_tool("GOOGLEDOCS_CREATE_DOCUMENT_FROM_TEXT", "googledocs"),
            _make_composio_tool("GOOGLEDOCS_GET_DOCUMENT", "googledocs"),
            _make_composio_tool("GOOGLEDOCS_APPEND_TEXT", "googledocs"),
            # Google Drive
            _make_composio_tool("GOOGLEDRIVE_UPLOAD_FILE", "googledrive"),
            _make_composio_tool("GOOGLEDRIVE_FIND_FILE", "googledrive"),
            _make_composio_tool("GOOGLEDRIVE_SEARCH_FILE", "googledrive"),
            _make_composio_tool("GOOGLEDRIVE_LIST_FILES", "googledrive"),
            # Notion
            _make_composio_tool("NOTION_CREATE_A_NEW_PAGE", "notion"),
            _make_composio_tool("NOTION_SEARCH_NOTION", "notion"),
            _make_composio_tool("NOTION_FETCH_DATABASE", "notion"),
            # GitHub
            _make_composio_tool("GITHUB_ISSUES_CREATE", "github"),
            _make_composio_tool("GITHUB_ISSUES_LIST", "github"),
            _make_composio_tool("GITHUB_PULLS_CREATE", "github"),
            _make_composio_tool("GITHUB_PULLS_LIST", "github"),
            # Slack
            _make_composio_tool("SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL", "slack"),
            _make_composio_tool("SLACK_SEARCH_FOR_MESSAGES_IN_SLACK", "slack"),
            _make_composio_tool("SLACK_LIST_ALL_SLACK_TEAM_CHANNELS", "slack"),
        ]

    def _make_intent(self, intent_type: str, entities: dict | None = None, sub_intents: list[str] | None = None):
        return Intent(
            intent=intent_type,
            entities=entities or {},
            constraints={},
            user_id="test-user",
            sub_intents=sub_intents or [],
        )

    # --- can_handle ---

    def test_can_handle_all_32_intents(self):
        from components.Planner.adapters.workflow_registry import get_all_intents

        all_intents = get_all_intents()
        assert len(all_intents) == 32
        for intent_name in all_intents:
            assert self.planner.can_handle(intent_name), f"can_handle({intent_name}) should be True"

    NEW_INTENTS = (
        "create_document_google_docs", "edit_document_google_docs",
        "upload_file_google_drive", "download_file_google_drive",
        "search_files_google_drive", "list_files_google_drive",
        "create_page_notion", "create_task_notion", "search_notion", "list_tasks_notion",
        "create_issue_github", "list_issues_github", "create_pr_github", "list_prs_github",
        "send_message_slack", "search_messages_slack", "list_channels_slack",
    )

    @pytest.mark.parametrize("intent", NEW_INTENTS)
    def test_can_handle_new_intents(self, intent: str):
        assert self.planner.can_handle(intent) is True

    def test_can_handle_compound_sub_intents(self):
        intent = self._make_intent(
            "schedule_meeting_and_email",
            sub_intents=["schedule_meeting", "send_email"],
        )
        assert self.planner.can_handle(intent) is True

    def test_can_handle_unknown_returns_false(self):
        assert self.planner.can_handle("analyze_stocks") is False

    def test_can_handle_partial_unknown_sub_intents_false(self):
        intent = self._make_intent(
            "schedule_and_stock",
            sub_intents=["schedule_meeting", "analyze_stocks"],
        )
        assert self.planner.can_handle(intent) is False

    # --- build_plan: write intents ---

    def test_schedule_meeting_produces_4_step_dag(self):
        intent = self._make_intent("schedule_meeting_google_calendar", {"attendee": "Alice", "date_time": "tomorrow"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 4
        roles = [s.role for s in plan.graph]
        assert roles == ["Fetcher", "Reasoner", "Resolver", "Booker"]

    def test_schedule_meeting_reasoner_has_config(self):
        intent = self._make_intent("schedule_meeting_google_calendar")
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        reasoner = next(s for s in plan.graph if s.role == "Reasoner")
        assert reasoner.can_spawn is True
        assert reasoner.reasoning_config is not None
        assert reasoner.policy_ref is not None

    def test_schedule_meeting_booker_has_gate(self):
        intent = self._make_intent("schedule_meeting_google_calendar")
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        booker = next(s for s in plan.graph if s.role == "Booker")
        assert booker.gate_id is not None

    def test_send_email_produces_3_step_dag(self):
        intent = self._make_intent("send_email_gmail", {"recipient": "bob@x.com", "subject": "Hi", "body": "Test"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 3
        roles = [s.role for s in plan.graph]
        assert roles == ["Reasoner", "Resolver", "Booker"]

    # --- build_plan: read intents ---

    def test_read_email_produces_2_step_dag(self):
        intent = self._make_intent("read_email_gmail")
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 2
        roles = [s.role for s in plan.graph]
        assert roles == ["Fetcher", "Reasoner"]

    def test_list_meetings_produces_2_step_dag(self):
        intent = self._make_intent("list_meetings_google_calendar")
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 2
        assert plan.graph[0].role == "Fetcher"
        assert plan.graph[1].role == "Reasoner"
        # No Booker
        assert all(s.role != "Booker" for s in plan.graph)

    def test_check_calendar_produces_2_step_dag(self):
        intent = self._make_intent("check_calendar_google_calendar")
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 2

    def test_draft_email_produces_1_step_dag(self):
        intent = self._make_intent("draft_email_gmail", {"subject": "Hi", "body": "Draft"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 1
        assert plan.graph[0].role == "Booker"

    # --- build_plan: compound intents ---

    def test_compound_intent_via_sub_intents(self):
        intent = self._make_intent(
            "schedule_meeting_and_email",
            entities={"attendee": "Alice", "date_time": "tomorrow", "subject": "Meeting", "body": "Details"},
            sub_intents=["schedule_meeting_google_calendar", "send_email_gmail"],
        )
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        # schedule_meeting_google_calendar (4) + send_email_gmail (3) = 7 steps
        assert len(plan.graph) == 7

    def test_compound_intent_inter_workflow_deps(self):
        intent = self._make_intent(
            "schedule_meeting_and_email",
            sub_intents=["schedule_meeting_google_calendar", "send_email_gmail"],
        )
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        # Step 5 (first of email workflow) should depend on step 4 (last of schedule)
        step_5 = plan.graph[4]
        assert 4 in step_5.after

    def test_compound_intent_via_decomposition(self):
        """Compound intent string decomposes into known workflows."""
        intent = self._make_intent("create_issue_github_and_send_message_slack")
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        # create_issue_github (3) + send_message_slack (3) = 6 steps
        assert len(plan.graph) == 6

    # --- build_plan: tool validation ---

    def test_returns_none_when_tools_missing(self):
        from .conftest import _make_tool_definition

        intent = self._make_intent("schedule_meeting_google_calendar")
        wrong_tools = [_make_tool_definition("SLACK_SEND_MESSAGE", "Send Slack message")]
        plan = self.planner.build_plan(intent, wrong_tools)
        assert plan is None

    def test_all_steps_have_dry_run(self):
        intent = self._make_intent("schedule_meeting_google_calendar")
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        for step in plan.graph:
            assert step.dry_run is True

    # --- build_plan: new provider intents ---

    def test_build_plan_notion_create_page_steps(self):
        """create_page_notion (write with fetcher) → Fetcher + Reasoner + Resolver + Booker = 4 steps."""
        intent = self._make_intent("create_page_notion", {"title": "My Page"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 4
        roles = [s.role for s in plan.graph]
        assert roles == ["Fetcher", "Reasoner", "Resolver", "Booker"]

    def test_build_plan_googledrive_search_files(self):
        """search_files_google_drive (read) → Fetcher + Reasoner = 2 steps."""
        intent = self._make_intent("search_files_google_drive", {"query": "report"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 2
        roles = [s.role for s in plan.graph]
        assert roles == ["Fetcher", "Reasoner"]

    def test_build_plan_slack_send_message(self):
        """send_message_slack (write without fetcher) → Reasoner + Resolver + Booker = 3 steps."""
        intent = self._make_intent("send_message_slack", {"channel": "#general", "message": "Hello"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 3
        roles = [s.role for s in plan.graph]
        assert roles == ["Reasoner", "Resolver", "Booker"]

    def test_build_plan_edit_document_full_write(self):
        """edit_document_google_docs (write with fetcher) → 4 steps."""
        intent = self._make_intent("edit_document_google_docs", {"document_id": "doc-123", "content": "Updated"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 4
        roles = [s.role for s in plan.graph]
        assert roles == ["Fetcher", "Reasoner", "Resolver", "Booker"]

    def test_build_plan_create_document_light_write(self):
        """create_document_google_docs (light-write) → 1 step Booker only."""
        intent = self._make_intent("create_document_google_docs", {"title": "Doc", "content": "Content"})
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        assert len(plan.graph) == 1
        assert plan.graph[0].role == "Booker"

    def test_compound_cross_provider(self):
        """Compound: create_issue_github + send_message_slack → composed DAG from different providers."""
        intent = self._make_intent(
            "create_issue_and_send_message",
            entities={"title": "Bug", "repo": "org/repo", "channel": "#dev", "message": "Issue created"},
            sub_intents=["create_issue_github", "send_message_slack"],
        )
        plan = self.planner.build_plan(intent, self._make_tools())
        assert plan is not None
        # create_issue_github (3) + send_message_slack (3) = 6 steps
        assert len(plan.graph) == 6
        # Last step of first workflow chains to first step of second
        step_4 = plan.graph[3]
        assert 3 in step_4.after
