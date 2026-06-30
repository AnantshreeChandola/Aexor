"""
DeterministicPlanner — rule-based plan builder for known intent types.

Returns a valid Plan without calling the LLM. Falls through to None
(caller uses LLM fallback chain) if the intent is unknown or the
required tool is not in the catalog.

Uses WorkflowRegistry for multi-step DAG templates that match LLM
output patterns exactly — same Reasoner steps, same HITL gates, same
template references.

Reference: LLD §6.6, §6.7
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import ulid

from components.Planner.adapters.workflow_registry import (
    StepTemplate,
    WorkflowDefinition,
    compose_workflows,
    decompose_intent,
    get_workflow,
    has_workflow,
)
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep

logger = logging.getLogger(__name__)


class DeterministicPlanner:
    """Rule-based plan builder for known intent types."""

    def can_handle(self, intent: str | Intent) -> bool:
        """Check if this intent type has a deterministic template.

        Accepts either a bare intent string or a full Intent object.
        Supports:
        - Single intents registered in the WorkflowRegistry
        - Compound intents where all sub_intents are registered
        - Compound intent strings that decompose into known workflows
        """
        if isinstance(intent, Intent):
            # User-selected tools need LLM for param mapping
            if intent.tool_overrides:
                return False
            # Check sub_intents first (compound from Intake parser)
            if intent.sub_intents and all(
                has_workflow(si) for si in intent.sub_intents
            ):
                return True
            intent_type = intent.intent
        else:
            intent_type = intent

        if has_workflow(intent_type):
            return True

        # Try decomposition for compound intent strings
        result = decompose_intent(intent_type)
        return result is not None and len(result) >= 1

    def build_plan(self, intent: Intent, tools: list[Any]) -> Plan | None:
        """Build a Plan from workflow registry templates without LLM.

        Produces multi-step DAGs matching LLM output patterns:
        - Write intents: Fetcher → Reasoner → Resolver → Booker
        - Read intents: Fetcher → Reasoner
        - Compound intents: composed DAGs from sub-workflows

        Args:
            intent: The classified intent with entities.
            tools: Available tools from the catalog.

        Returns:
            A valid Plan, or None if required tools are not available.
        """
        # Determine which workflows to use
        workflows: list[WorkflowDefinition] = []

        # Priority 1: sub_intents from Intake parser (semantic decomposition)
        if intent.sub_intents:
            for si in intent.sub_intents:
                wf = get_workflow(si)
                if wf is None:
                    return None  # Unknown sub-intent, fall to LLM
                workflows.append(wf)
        else:
            # Priority 2: exact match
            wf = get_workflow(intent.intent)
            if wf is not None:
                workflows = [wf]
            else:
                # Priority 3: decompose compound intent string
                decomposed = decompose_intent(intent.intent)
                if decomposed is None:
                    return None
                workflows = decomposed

        if not workflows:
            return None

        # Build step sequence
        if len(workflows) == 1:
            step_templates = list(workflows[0].steps)
            [
                st.tool for st in step_templates
                if st.tool and not st.tool.startswith("system.")
            ]
        else:
            step_templates, _tool_list = compose_workflows(workflows, intent.entities)

        # Validate tools against catalog
        if not self._validate_tools(step_templates, tools):
            logger.info(
                "deterministic_planner_tool_not_found",
                extra={
                    "intent": intent.intent,
                    "required_tools": [st.tool for st in step_templates if st.type == "api"],
                    "available_tools": [getattr(t, "name", "?") for t in tools[:10]],
                },
            )
            return None

        # Build entity-derived args for the appropriate steps
        entity_args = self._build_entity_args(workflows, intent.entities)

        # Convert StepTemplates to PlanSteps
        plan_steps: list[PlanStep] = []
        all_plugins: set[str] = set()

        for st in step_templates:
            # Merge entity-derived args into step args
            step_args: dict[str, Any] = {}
            if st.args_template:
                step_args.update(st.args_template)
            # Override with actual entity values for Fetcher/Booker api steps
            if st.type == "api" and st.role in ("Fetcher", "Booker", "Notifier"):
                step_args.update(entity_args.get(st.tool, {}))

            # Resolve tool name against catalog (fuzzy match)
            resolved_tool = self._resolve_tool_name(st.tool, tools) if st.tool else st.tool

            plan_step = PlanStep(
                step=st.step,
                mode="interactive",
                role=st.role,
                type=st.type,
                uses=resolved_tool or st.tool,
                call=resolved_tool or st.call,
                args=step_args,
                after=list(st.after),
                context_from=list(st.context_from),
                timeout_s=st.timeout_s,
                gate_id=st.gate_id,
                dry_run=True,
                can_spawn=st.can_spawn,
                max_spawned_steps=st.max_spawned_steps,
                trust_level=st.trust_level,
                policy_ref=st.policy_ref,
                reasoning_config=st.reasoning_config,
            )
            plan_steps.append(plan_step)
            all_plugins.add(plan_step.uses)

        plan_id = str(ulid.new())
        now = datetime.now(UTC).isoformat()

        plan = Plan(
            plan_id=plan_id,
            intent=intent,
            trace_id=intent.trace_id,
            graph=plan_steps,
            constraints=PlanConstraints(),
            plugins=list(all_plugins),
            meta=PlanMeta(
                created_at=now,
                canonical_hash="0" * 64,
            ),
        )

        logger.info(
            "deterministic_plan_built",
            extra={
                "intent": intent.intent,
                "step_count": len(plan_steps),
                "workflow_count": len(workflows),
                "plugins": list(all_plugins),
            },
        )

        return plan

    @staticmethod
    def _validate_tools(
        step_templates: list[StepTemplate],
        tools: list[Any],
    ) -> bool:
        """Check that required API tools are available in the catalog.

        Returns True if catalog is empty (fail-open) or all required
        tools have a match (exact or fuzzy).
        """
        if not tools:
            return True  # Fail-open when catalog is empty

        tool_names = {getattr(t, "name", "").lower() for t in tools}

        for st in step_templates:
            if st.type != "api" or st.tool.startswith("system."):
                continue
            # Generic workflows (tool="") need user tool selection — can't validate
            if not st.tool:
                return False
            # Exact match
            if st.tool.lower() in tool_names:
                continue
            # Fuzzy match
            st_lower = st.tool.lower()
            if any(st_lower in name or name in st_lower for name in tool_names):
                continue
            return False
        return True

    @staticmethod
    def _resolve_tool_name(tool: str, tools: list[Any]) -> str | None:
        """Resolve a template tool name to an actual catalog name."""
        if not tools:
            return None
        # Exact match
        for t in tools:
            if getattr(t, "name", None) == tool:
                return tool
        # Fuzzy match
        tool_lower = tool.lower()
        for t in tools:
            name = getattr(t, "name", "").lower()
            if tool_lower in name or name in tool_lower:
                return t.name
        return None

    @staticmethod
    def _build_entity_args(
        workflows: list[WorkflowDefinition],
        entities: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Map intent entities to tool parameter args, keyed by tool name.

        Uses the entity definitions' ``tool_param`` and ``aliases`` to map
        user-provided entity values to MCP tool parameter names.
        """
        tool_args: dict[str, dict[str, Any]] = {}

        for wf in workflows:
            # Build alias→(entity_name, tool_param) lookup
            for entity_def in wf.entities:
                if not entity_def.tool_param:
                    continue

                # Find entity value by name or alias
                value = entities.get(entity_def.name)
                if value is None:
                    for alias in entity_def.aliases:
                        value = entities.get(alias)
                        if value is not None:
                            break

                if value is None:
                    continue

                # Handle list-type parameters
                if entity_def.tool_param == "attendees" and isinstance(value, str):
                    value = [value]

                # Map to each API step's tool that uses this entity
                for st in wf.steps:
                    if st.type == "api" and not st.tool.startswith("system."):
                        if st.tool not in tool_args:
                            tool_args[st.tool] = {}
                        tool_args[st.tool][entity_def.tool_param] = value

        return tool_args
