"""
Plan validator tests for trust boundary rules E, F, G, H.

Covers:
  T806 — Rules E, F, G, H validation
  AC-1  — Rule F: api -> llm_reasoning without sanitizer is rejected
  AC-2  — Rule E: Tier 1 reasoner without output_schema_ref is rejected
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.domain.models import PlanValidationError
from shared.schemas.plan import Plan

from .conftest import SAMPLE_INTENT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC).isoformat()
_TOOL_IDS = {"google.calendar", "system.echo"}


def _plan_json(
    graph: list[dict],
    plugins: list[str] | None = None,
) -> str:
    """Build a valid plan JSON string from a graph snippet."""
    return json.dumps(
        {
            "plan_id": "01JBXYZ1234567890ABCDEFGHI",
            "intent": SAMPLE_INTENT.model_dump(mode="json"),
            "trace_id": SAMPLE_INTENT.trace_id,
            "graph": graph,
            "constraints": {
                "scopes": [],
                "ttl_s": 900,
                "max_retries": 3,
            },
            "plugins": plugins or ["google.calendar", "system.echo"],
            "meta": {
                "created_at": _NOW,
                "canonical_hash": "a" * 64,
            },
        }
    )


def _api_step(
    step: int = 1,
    after: list[int] | None = None,
) -> dict:
    """Return a minimal valid API step."""
    return {
        "step": step,
        "mode": "interactive",
        "role": "Fetcher",
        "type": "api",
        "uses": "google.calendar",
        "call": "list_events",
        "args": {},
        "after": after or [],
        "timeout_s": 30,
        "dry_run": True,
    }


def _sanitizer_step(
    step: int = 2,
    context_from: list[int] | None = None,
    after: list[int] | None = None,
    **overrides,
) -> dict:
    """Return a minimal valid sanitizer step."""
    base = {
        "step": step,
        "mode": "interactive",
        "role": "Guard",
        "type": "sanitizer",
        "uses": "trust_filter.scan",
        "call": "scan",
        "args": {},
        "after": after or [],
        "context_from": context_from or [],
        "timeout_s": 30,
        "dry_run": True,
    }
    base.update(overrides)
    return base


def _tier1_reasoner_step(
    step: int = 3,
    context_from: list[int] | None = None,
    after: list[int] | None = None,
    output_schema_ref: str | None = "slot_proposal_v1",
    **overrides,
) -> dict:
    """Return a minimal valid Tier 1 reasoner step."""
    base = {
        "step": step,
        "mode": "interactive",
        "role": "Reasoner",
        "type": "llm_reasoning",
        "trust_level": "untrusted_input",
        "uses": "system.echo",
        "call": "analyze",
        "args": {},
        "after": after or [],
        "context_from": context_from or [],
        "timeout_s": 60,
        "dry_run": True,
        "policy_ref": "policy-tier1",
        "reasoning_config": {
            "system_prompt_ref": "reasoner.analyze",
            "output_schema_ref": output_schema_ref,
        },
    }
    base.update(overrides)
    return base


def _tier2_reasoner_step(
    step: int = 4,
    context_from: list[int] | None = None,
    after: list[int] | None = None,
    **overrides,
) -> dict:
    """Return a minimal valid Tier 2 (trusted) reasoner step."""
    base = {
        "step": step,
        "mode": "interactive",
        "role": "Reasoner",
        "type": "llm_reasoning",
        "trust_level": "trusted",
        "uses": "system.echo",
        "call": "plan",
        "args": {},
        "after": after or [],
        "context_from": context_from or [],
        "timeout_s": 60,
        "dry_run": True,
        "policy_ref": "policy-tier2",
        "reasoning_config": {
            "system_prompt_ref": "reasoner.plan",
        },
    }
    base.update(overrides)
    return base


# ===================================================================
# Rule E -- Tier 1 reasoner requires output_schema_ref (FR-017)
# ===================================================================


class TestRuleE:
    """Rule E: Tier 1 reasoners must have output_schema_ref
    pointing to a valid SCHEMA_REGISTRY key."""

    def setup_method(self):
        self.validator = PlanValidator()

    @pytest.mark.asyncio
    async def test_tier1_missing_output_schema_ref_rejects(self):
        """AC-2: Tier 1 without output_schema_ref is rejected."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                output_schema_ref=None,
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert exc_info.value.layer == "business_rules"
        assert "Rule E" in exc_info.value.message
        assert "output_schema_ref" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_tier1_invalid_output_schema_ref_rejects(self):
        """Tier 1 with unknown output_schema_ref key is rejected."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                output_schema_ref="nonexistent_schema_v99",
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert exc_info.value.layer == "business_rules"
        assert "Rule E" in exc_info.value.message
        assert "SCHEMA_REGISTRY" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_tier1_valid_output_schema_ref_passes(self):
        """Tier 1 with valid output_schema_ref passes Rule E."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                output_schema_ref="slot_proposal_v1",
            ),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)
        ref = plan.graph[2].reasoning_config.output_schema_ref
        assert ref == "slot_proposal_v1"

    @pytest.mark.asyncio
    async def test_tier2_without_output_schema_ref_passes(self):
        """Tier 2 reasoners don't need output_schema_ref."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier2_reasoner_step(
                3,
                context_from=[2],
                after=[2],
            ),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "schema_key",
        [
            "slot_proposal_v1",
            "free_slots_v1",
            "flight_recommendation_v1",
            "email_summary_v1",
            "freebusy_sanitized_v1",
        ],
    )
    async def test_all_registry_keys_accepted(self, schema_key: str):
        """Every SCHEMA_REGISTRY key is accepted by Rule E."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                output_schema_ref=schema_key,
            ),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)


# ===================================================================
# Rule F -- API -> llm_reasoning requires intervening sanitizer
# ===================================================================


class TestRuleF:
    """Rule F: llm_reasoning step referencing an API step via
    context_from must have an intervening sanitizer."""

    def setup_method(self):
        self.validator = PlanValidator()

    @pytest.mark.asyncio
    async def test_direct_api_to_reasoning_rejects(self):
        """AC-1: api -> llm_reasoning without sanitizer is rejected."""
        graph = [
            _api_step(1),
            _tier2_reasoner_step(
                2,
                context_from=[1],
                after=[1],
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert exc_info.value.layer == "business_rules"
        assert "Rule F" in exc_info.value.message
        assert "sanitizer" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_api_to_sanitizer_to_reasoning_passes(self):
        """api -> sanitizer -> llm_reasoning passes Rule F."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                output_schema_ref="slot_proposal_v1",
            ),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)

    @pytest.mark.asyncio
    async def test_transitive_api_reference_without_sanitizer_rejects(self):
        """api(1) -> analyzer(2, context_from=[1]) -> reasoning(3, context_from=[2])
        is rejected because the api data flows through transitively."""
        graph = [
            _api_step(1),
            {
                "step": 2,
                "mode": "interactive",
                "role": "Analyzer",
                "type": "api",
                "uses": "system.echo",
                "call": "transform",
                "args": {},
                "after": [1],
                "context_from": [1],
                "timeout_s": 30,
                "dry_run": True,
            },
            _tier2_reasoner_step(
                3,
                context_from=[2],
                after=[2],
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert "Rule F" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_transitive_with_sanitizer_in_chain_passes(self):
        """api(1) -> sanitizer(2) -> reasoning(3, context_from=[2])
        even when step 3 transitively reaches api(1), sanitizer
        is in the chain."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
            ),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)

    @pytest.mark.asyncio
    async def test_reasoning_without_context_from_passes(self):
        """llm_reasoning step without context_from (no api input)
        passes Rule F."""
        graph = [
            _tier2_reasoner_step(
                1,
                context_from=[],
                after=[],
            ),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)

    @pytest.mark.asyncio
    async def test_two_api_steps_both_need_sanitizers(self):
        """Two API steps feeding into one reasoner both need
        sanitizers."""
        graph = [
            _api_step(1),
            {
                "step": 2,
                "mode": "interactive",
                "role": "Fetcher",
                "type": "api",
                "uses": "system.echo",
                "call": "fetch",
                "args": {},
                "after": [],
                "timeout_s": 30,
                "dry_run": True,
            },
            _sanitizer_step(3, context_from=[1], after=[1]),
            # Only sanitizer for step 1, not step 2
            _tier2_reasoner_step(
                4,
                context_from=[3, 2],
                after=[3, 2],
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert "Rule F" in exc_info.value.message
        # Should mention step 2 as the unsanitized api step
        assert "step 2" in exc_info.value.message.lower() or "2" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_pure_api_plan_without_reasoning_passes(self):
        """AC-10: Pure API plans (no llm_reasoning) pass Rule F."""
        graph = [
            _api_step(1),
            {
                "step": 2,
                "mode": "interactive",
                "role": "Analyzer",
                "type": "api",
                "uses": "system.echo",
                "call": "analyze",
                "args": {},
                "after": [1],
                "context_from": [1],
                "timeout_s": 30,
                "dry_run": True,
            },
            {
                "step": 3,
                "mode": "interactive",
                "role": "Booker",
                "type": "api",
                "uses": "google.calendar",
                "call": "create_event",
                "args": {},
                "after": [2],
                "timeout_s": 60,
                "gate_id": "gate-A",
                "dry_run": True,
            },
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)
        assert len(plan.graph) == 3


# ===================================================================
# Rule G -- Sanitizer step constraints (FR-019)
# ===================================================================


class TestRuleG:
    """Rule G: Sanitizer steps must have can_spawn=false
    and trust_level must not be set."""

    def setup_method(self):
        self.validator = PlanValidator()

    @pytest.mark.asyncio
    async def test_sanitizer_with_can_spawn_rejects(self):
        """Sanitizer step with can_spawn=true is rejected."""
        graph = [
            _api_step(1),
            _sanitizer_step(
                2,
                context_from=[1],
                after=[1],
                can_spawn=True,
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert exc_info.value.layer == "business_rules"
        assert "Rule G" in exc_info.value.message
        assert "can_spawn" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_sanitizer_with_trust_level_rejects(self):
        """Sanitizer step with trust_level set is rejected."""
        graph = [
            _api_step(1),
            _sanitizer_step(
                2,
                context_from=[1],
                after=[1],
                trust_level="untrusted_input",
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert exc_info.value.layer == "business_rules"
        assert "Rule G" in exc_info.value.message
        assert "trust_level" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_valid_sanitizer_passes(self):
        """Properly configured sanitizer passes Rule G."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)
        assert plan.graph[1].type == "sanitizer"
        assert plan.graph[1].role == "Guard"

    @pytest.mark.asyncio
    async def test_sanitizer_with_trusted_level_also_rejects(self):
        """Sanitizer step with trust_level='trusted' is also rejected."""
        graph = [
            _api_step(1),
            _sanitizer_step(
                2,
                context_from=[1],
                after=[1],
                trust_level="trusted",
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert "Rule G" in exc_info.value.message


# ===================================================================
# Rule H -- Tier 1 reasoner constraints (FR-020)
# ===================================================================


class TestRuleH:
    """Rule H: Tier 1 reasoners (trust_level=untrusted_input)
    must have can_spawn=false."""

    def setup_method(self):
        self.validator = PlanValidator()

    @pytest.mark.asyncio
    async def test_tier1_with_can_spawn_rejects(self):
        """Tier 1 reasoner with can_spawn=true is rejected."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                can_spawn=True,
                max_spawned_steps=3,
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        assert exc_info.value.layer == "business_rules"
        assert "Rule H" in exc_info.value.message
        assert "can_spawn" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_tier1_without_can_spawn_passes(self):
        """Tier 1 reasoner with can_spawn=false passes Rule H."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
            ),
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)
        assert plan.graph[2].can_spawn is False

    @pytest.mark.asyncio
    async def test_tier2_with_can_spawn_not_caught_by_rule_h(self):
        """Tier 2 reasoner with can_spawn=true is NOT caught by
        Rule H (Rule H only applies to Tier 1)."""
        graph = [
            _api_step(1),
            _sanitizer_step(2, context_from=[1], after=[1]),
            _tier2_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                can_spawn=True,
                max_spawned_steps=3,
            ),
        ]
        # Should not raise Rule H -- may raise other rules
        # depending on whether the spawning tool is in plugins.
        # We just verify Rule H is not the failure.
        try:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        except PlanValidationError as e:
            assert "Rule H" not in e.message


# ===================================================================
# Composite: full pipeline plan validation
# ===================================================================


class TestFullPipelinePlan:
    """End-to-end validation of a complete trust-boundary pipeline plan."""

    def setup_method(self):
        self.validator = PlanValidator()

    @pytest.mark.asyncio
    async def test_full_meeting_booking_pipeline_passes(self):
        """A realistic meeting-booking plan with sanitizers passes
        all rules (AC-6 partial -- plan shape)."""
        graph = [
            # Step 1: Fetch my calendar
            _api_step(1),
            # Step 2: Fetch Alice's calendar
            {
                "step": 2,
                "mode": "interactive",
                "role": "Fetcher",
                "type": "api",
                "uses": "google.calendar",
                "call": "list_events",
                "args": {"calendar_id": "alice@example.com"},
                "after": [],
                "timeout_s": 30,
                "dry_run": True,
            },
            # Step 3: Sanitize my calendar data
            _sanitizer_step(3, context_from=[1], after=[1]),
            # Step 4: Sanitize Alice's calendar data
            _sanitizer_step(4, context_from=[2], after=[2]),
            # Step 5: Tier 1 reasoner proposes slot
            _tier1_reasoner_step(
                5,
                context_from=[3, 4],
                after=[3, 4],
                output_schema_ref="slot_proposal_v1",
            ),
            # Step 6: Policy check
            {
                "step": 6,
                "mode": "interactive",
                "role": "Analyzer",
                "type": "policy_check",
                "uses": "system.echo",
                "call": "check_policy",
                "args": {},
                "after": [5],
                "context_from": [5],
                "timeout_s": 30,
                "dry_run": True,
                "policy_ref": "policy-meeting-booking",
            },
            # Step 7: Booker creates event
            {
                "step": 7,
                "mode": "interactive",
                "role": "Booker",
                "type": "api",
                "uses": "google.calendar",
                "call": "create_event",
                "args": {},
                "after": [6],
                "timeout_s": 60,
                "gate_id": "gate-booking",
                "dry_run": True,
            },
        ]
        plan = await self.validator.validate(
            _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
        )
        assert isinstance(plan, Plan)
        assert len(plan.graph) == 7
        # Verify step types
        assert plan.graph[0].type == "api"
        assert plan.graph[2].type == "sanitizer"
        assert plan.graph[4].type == "llm_reasoning"
        assert plan.graph[5].type == "policy_check"
        assert plan.graph[6].role == "Booker"

    @pytest.mark.asyncio
    async def test_plan_with_mixed_violations_reports_first(self):
        """Plan with multiple rule violations raises the first
        encountered violation."""
        graph = [
            _api_step(1),
            # Sanitizer with can_spawn (Rule G violation)
            _sanitizer_step(
                2,
                context_from=[1],
                after=[1],
                can_spawn=True,
            ),
            # Tier 1 without schema ref (Rule E violation)
            _tier1_reasoner_step(
                3,
                context_from=[2],
                after=[2],
                output_schema_ref=None,
            ),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            await self.validator.validate(
                _plan_json(graph), SAMPLE_INTENT, 1, _TOOL_IDS
            )
        # Rule G comes before Rule E in evaluation order
        assert "Rule" in exc_info.value.message
