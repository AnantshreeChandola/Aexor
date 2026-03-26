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

MAX_STEPS = 50
MAX_PARALLEL_STEPS = 10
MAX_STEP_ARGS_SIZE = 10_240  # 10KB per step args
MAX_PLAN_SIZE = 102_400  # 100KB total plan


class PlanValidator:
    """3-layer validation pipeline for LLM-generated plans."""

    def __init__(self, registry_service: Any = None) -> None:
        self._registry_service = registry_service

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
        try:
            data = json.loads(raw_output)
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
        """Layer 2: Pydantic Plan schema validation with structural checks."""
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

        # Tool existence — tools must be in the catalog
        plan_tool_ids = {s.uses for s in plan.graph}
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
