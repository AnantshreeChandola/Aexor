"""
3-layer plan validation pipeline.

Layer 1: JSON parse
Layer 2: Pydantic schema validation (Plan.model_validate)
Layer 3: Business rules (tool existence, step limits, gate_id, etc.)

Reference: LLD SS6.2
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from pydantic import ValidationError

from components.Planner.domain.models import PlanValidationError
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan

logger = logging.getLogger(__name__)

MAX_STEPS = 100
MAX_PARALLEL_STEPS = 10
MAX_STEP_ARGS_SIZE = 10_240  # 10KB per step args
MAX_PLAN_SIZE = 102_400  # 100KB total plan


class PlanValidator:
    """3-layer validation pipeline for LLM-generated plans."""

    def __init__(self) -> None:
        pass

    async def validate(
        self,
        raw_output: str,
        intent: Intent,  # noqa: ARG002 — reserved for future per-intent validation rules
        registry_version: int,  # noqa: ARG002 — reserved for version-aware tool checks
        tool_ids: set[str],
    ) -> Plan:
        """Run all 3 validation layers, return validated Plan."""
        # Layer 1: JSON parse
        parsed = self._validate_json(raw_output)

        # Layer 2: Schema validation
        plan = self._validate_schema(parsed)

        # Layer 3: Business rules
        await self._validate_business_rules(plan, tool_ids)

        return plan

    def _validate_json(self, raw_output: str) -> dict[str, Any]:
        """Layer 1: Parse raw string as JSON."""
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError) as e:
            raise PlanValidationError(
                layer="json_parse",
                message=f"Invalid JSON: {e}",
            ) from e
        if not isinstance(data, dict):
            raise PlanValidationError(
                layer="json_parse",
                message=f"Expected JSON object, got {type(data).__name__}",
            )
        return data

    def _validate_schema(self, data: dict[str, Any]) -> Plan:
        """Layer 2: Pydantic Plan schema validation with structural checks.

        The LLM generates only graph/constraints/plugins. Server-side fields
        (plan_id, intent, meta) are injected as placeholders here and
        overwritten by ``_finalize_plan`` after validation.
        """
        # Inject placeholder values for fields the LLM does not produce
        if "plan_id" not in data:
            data["plan_id"] = "0" * 26  # placeholder ULID
        if "intent" not in data:
            data["intent"] = {
                "intent": "placeholder",
                "entities": {},
                "constraints": {},
                "tz": "UTC",
                "user_id": "placeholder",
                "session_id": "placeholder",
            }
        if "meta" not in data:
            data["meta"] = {
                "created_at": "1970-01-01T00:00:00Z",
                "canonical_hash": "0" * 64,
            }
        try:
            plan = Plan.model_validate(data)
        except ValidationError as e:
            raise PlanValidationError(
                layer="schema",
                message=f"Schema validation failed: {e.error_count()} errors",
                details={"errors": e.errors()},
            ) from e

        # Structural checks within schema layer
        step_numbers = [s.step for s in plan.graph]

        # Check for duplicate step numbers
        if len(step_numbers) != len(set(step_numbers)):
            raise PlanValidationError(
                layer="schema",
                message="Duplicate step numbers found",
            )

        step_set = set(step_numbers)

        for step in plan.graph:
            # Check for self-dependency
            if step.step in step.after:
                raise PlanValidationError(
                    layer="schema",
                    message=f"Step {step.step} has self-dependency",
                )
            # Check for forward dependencies
            for dep in step.after:
                if dep >= step.step:
                    raise PlanValidationError(
                        layer="schema",
                        message=f"Step {step.step} has forward dependency on step {dep}",
                    )
                if dep not in step_set:
                    raise PlanValidationError(
                        layer="schema",
                        message=f"Step {step.step} depends on non-existent step {dep}",
                    )

        return plan

    async def _validate_business_rules(
        self,
        plan: Plan,
        tool_ids: set[str],
    ) -> None:
        """Layer 3: Business rule validation."""
        step_set = {s.step for s in plan.graph}

        # Max steps
        if len(plan.graph) > MAX_STEPS:
            raise PlanValidationError(
                layer="business_rules",
                message=f"Plan has {len(plan.graph)} steps, max is {MAX_STEPS}",
            )

        # Max parallel steps (steps with same set of after deps)
        from collections import Counter

        after_groups = Counter(tuple(sorted(s.after)) for s in plan.graph)
        max_parallel = max(after_groups.values()) if after_groups else 0
        if max_parallel > MAX_PARALLEL_STEPS:
            raise PlanValidationError(
                layer="business_rules",
                message=f"Plan has {max_parallel} parallel steps, max is {MAX_PARALLEL_STEPS}",
            )

        # Tool existence — API tools must be in the catalog.
        # Exceptions:
        # - llm_reasoning and policy_check steps don't call tools via MCP
        # - Resolver steps use pass-through tool names (e.g. "system.confirm",
        #   "confirm_action") that aren't real MCP tools — the execution engine
        #   handles them as gate-only checkpoints without MCP invocation.
        plan_tool_ids = {
            s.uses for s in plan.graph
            if s.type == "api" and s.role != "Resolver"
        }
        missing_tools = plan_tool_ids - tool_ids
        if missing_tools:
            raise PlanValidationError(
                layer="business_rules",
                message=f"Unknown tools: {missing_tools}",
                details={"missing_tools": list(missing_tools)},
            )

        # dry_run enforcement
        non_dry_run = [s.step for s in plan.graph if not s.dry_run]
        if non_dry_run:
            raise PlanValidationError(
                layer="business_rules",
                message=f"Steps {non_dry_run} have dry_run=false",
            )

        # gate_id on Booker steps
        booker_without_gate = [
            s.step for s in plan.graph if s.role == "Booker" and s.gate_id is None
        ]
        if booker_without_gate:
            raise PlanValidationError(
                layer="business_rules",
                message=f"Booker steps {booker_without_gate} missing gate_id",
            )

        # --- Hybrid execution rules (§2.3.1, §2.3.2) ---

        # Reasoner role with llm_reasoning type requires policy_ref
        reasoner_no_policy = [
            s.step
            for s in plan.graph
            if s.role == "Reasoner" and s.type == "llm_reasoning" and s.policy_ref is None
        ]
        if reasoner_no_policy:
            raise PlanValidationError(
                layer="business_rules",
                message=f"Reasoner steps {reasoner_no_policy} missing policy_ref",
            )

        # llm_reasoning steps require reasoning_config
        reasoning_no_config = [
            s.step for s in plan.graph if s.type == "llm_reasoning" and s.reasoning_config is None
        ]
        if reasoning_no_config:
            raise PlanValidationError(
                layer="business_rules",
                message=f"llm_reasoning steps {reasoning_no_config} missing reasoning_config",
            )

        # policy_check steps require policy_ref
        policy_check_no_ref = [
            s.step for s in plan.graph if s.type == "policy_check" and s.policy_ref is None
        ]
        if policy_check_no_ref:
            raise PlanValidationError(
                layer="business_rules",
                message=f"policy_check steps {policy_check_no_ref} missing policy_ref",
            )

        # Spawning constraints: max_spawned_steps ≤ 10, absolute max
        for step in plan.graph:
            if (
                step.can_spawn
                and step.max_spawned_steps is not None
                and step.max_spawned_steps > 10
            ):
                raise PlanValidationError(
                    layer="business_rules",
                    message=f"Step {step.step} max_spawned_steps={step.max_spawned_steps} exceeds limit of 10",
                )

        # context_from must reference valid earlier steps
        for step in plan.graph:
            for ref in step.context_from:
                if ref >= step.step:
                    raise PlanValidationError(
                        layer="business_rules",
                        message=f"Step {step.step} context_from references non-earlier step {ref}",
                    )
                if ref not in step_set:
                    raise PlanValidationError(
                        layer="business_rules",
                        message=f"Step {step.step} context_from references non-existent step {ref}",
                    )

        # --- v6.1 trust boundary & spawning rules ---

        step_by_num = {s.step: s for s in plan.graph}

        # Rule A — Default-untrusted (§8.2): Tier 2 Reasoner referencing
        # API steps via context_from is allowed because the runtime enforces
        # trust boundaries via _summarize_context() (data sanitization) and
        # _build_messages() (hard truncation).  Log for audit but do not reject.
        for step in plan.graph:
            if step.type == "llm_reasoning" and step.trust_level == "trusted":
                for ref in step.context_from:
                    ref_step = step_by_num.get(ref)
                    if ref_step is None:
                        continue
                    if ref_step.type == "api":
                        logger.info(
                            "trust_boundary_note",
                            extra={
                                "component": "planner",
                                "reasoner_step": step.step,
                                "api_step": ref,
                                "note": "Tier 2 Reasoner references API step — runtime sanitization applies",
                            },
                        )

        # Rule B — No recursive spawning (§2.3.2 rule 3): spawned steps
        # cannot spawn further steps.
        for step in plan.graph:
            if step.spawned_by is not None and step.can_spawn:
                raise PlanValidationError(
                    layer="business_rules",
                    message=(
                        f"Step {step.step} is spawned (spawned_by={step.spawned_by}) "
                        f"but has can_spawn=true. Spawned steps cannot spawn further steps."
                    ),
                )

        # Rule C — Inherited plugins (§2.3.2 rule 4): all tools referenced
        # by API steps with can_spawn must be in the plan's plugins array.
        # (llm_reasoning/policy_check steps use descriptive names, not real tools)
        plan_plugins = set(plan.plugins)
        for step in plan.graph:
            if step.can_spawn and step.type == "api" and step.uses not in plan_plugins:
                raise PlanValidationError(
                    layer="business_rules",
                    message=(
                        f"Step {step.step} has can_spawn=true but uses tool '{step.uses}' "
                        f"which is not in the plan's plugins array."
                    ),
                )

        # Rule D — Booker HITL on spawned steps (§2.3.2 rule 5): any
        # spawned Booker step must also have a gate_id for HITL approval.
        spawned_booker_no_gate = [
            s.step
            for s in plan.graph
            if s.spawned_by is not None and s.role == "Booker" and s.gate_id is None
        ]
        if spawned_booker_no_gate:
            raise PlanValidationError(
                layer="business_rules",
                message=(
                    f"Spawned Booker steps {spawned_booker_no_gate} missing gate_id. "
                    f"All Booker steps (including spawned) require HITL approval."
                ),
            )

        # Step args size check
        for step in plan.graph:
            args_size = sys.getsizeof(json.dumps(step.args))
            if args_size > MAX_STEP_ARGS_SIZE:
                raise PlanValidationError(
                    layer="business_rules",
                    message=f"Step {step.step} args exceed {MAX_STEP_ARGS_SIZE} bytes",
                )

        # Total plan size check
        plan_json = plan.model_dump_json()
        if len(plan_json.encode("utf-8")) > MAX_PLAN_SIZE:
            raise PlanValidationError(
                layer="business_rules",
                message=f"Plan exceeds {MAX_PLAN_SIZE} bytes",
            )
