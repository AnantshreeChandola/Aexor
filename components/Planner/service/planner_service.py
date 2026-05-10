"""
PlannerService — deterministic plan generation orchestrator.

Coordinates: ContextRAG → ToolCatalog → LLM (with fallbacks) → Validator → Hasher

Reference: LLD SS7
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import ulid

from components.Planner.adapters.circuit_breaker import CircuitBreaker
from components.Planner.adapters.llm_adapter import (
    LLMAdapter,
    LLMAdapterFactory,
)
from components.Planner.adapters.plan_hasher import compute_plan_hash
from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.adapters.prompt_builder import PromptBuilder
from components.Planner.adapters.tool_filter import (
    compact_tool_schemas,
    filter_tools_by_action,
    filter_tools_by_intent,
)
from components.Planner.domain.models import (
    CircuitOpenError,
    EntityRequirement,
    LLMCallError,
    PlannerResult,
    PlanValidationError,
    RequiredEntitiesResult,
    SkeletonStepHint,
    ToolNotAvailableError,
)
from components.Planner.adapters.workflow_registry import (
    get_entity_map,
    get_workflow,
    merge_entity_requirements,
)
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static entity map — sourced from WorkflowRegistry
# ---------------------------------------------------------------------------

_STATIC_ENTITY_MAP: dict[str, dict] = get_entity_map()


class PlannerService:
    """Orchestrates deterministic plan generation with 4-level fallback."""

    def __init__(
        self,
        context_rag_service: Any,
        tool_catalog: Any,
        plan_service: Any,
        llm_adapter: LLMAdapter,
        prompt_builder: PromptBuilder,
        validator: PlanValidator,
        primary_breaker: CircuitBreaker,
        fallback_breaker: CircuitBreaker,
        primary_model: str,
        fallback_model: str,
        max_output_tokens: int,
        fallback_llm_adapter: LLMAdapter | None = None,
        deterministic_planner: Any | None = None,
        tool_discovery: Any | None = None,
    ) -> None:
        self._context_rag = context_rag_service
        self._tool_catalog = tool_catalog
        self._plan_service = plan_service
        self._llm = llm_adapter
        self._fallback_llm = fallback_llm_adapter
        self._prompt = prompt_builder
        self._validator = validator
        self._primary_breaker = primary_breaker
        self._fallback_breaker = fallback_breaker
        self._primary_model = primary_model
        self._fallback_model = fallback_model
        self._max_output_tokens = max_output_tokens
        self._deterministic_planner = deterministic_planner
        self._tool_discovery = tool_discovery
        # Skeleton structure cache: reused during plan generation to ensure
        # the skeleton preview and actual plan stay in sync.
        self._skeleton_cache: dict[str, dict] = {}
        self._skeleton_cache_ttl_s = 900  # 15 minutes

    async def get_required_entities(
        self,
        intent_type: str,
        collected_entities: dict[str, Any] | None = None,
        user_id: str | None = None,
        sub_intents: list[str] | None = None,
    ) -> RequiredEntitiesResult:
        """Lightweight query: determine required entities for an intent type.

        Step 1: Ask LLM what tools and entities are needed (no catalog context).
        Step 2: Validate the LLM's tool suggestions against the ToolCatalog,
                checking per-user tools first (if user_id given), then global.

        Raises:
            ToolNotAvailableError: If none of the LLM-suggested tools exist in
                the catalog.

        This is NOT a full plan generation — no ContextRAG.
        """
        collected = collected_entities or {}

        logger.info(
            "get_required_entities_start",
            extra={
                "component": "planner",
                "op": "get_required_entities",
                "intent_type": intent_type,
                "collected_count": len(collected),
                "user_id": user_id,
            },
        )

        # ── Fast path: static entity map for known intents ──────────────
        static = _STATIC_ENTITY_MAP.get(intent_type)
        if static is not None:
            all_entities: list[EntityRequirement] = []
            missing: list[EntityRequirement] = []
            for e in static["entities"]:
                entity = EntityRequirement(
                    name=e["name"],
                    description=e["description"],
                    required=e.get("required", True),
                    default_preference_key=e.get("default_preference_key"),
                    aliases=e.get("aliases", []),
                )
                all_entities.append(entity)
                # Check collected entities by name AND aliases
                found = e["name"] in collected or any(
                    alias in collected for alias in e.get("aliases", [])
                )
                if not found:
                    missing.append(entity)

            logger.info(
                "get_required_entities_static_hit",
                extra={
                    "component": "planner",
                    "intent_type": intent_type,
                    "required_count": len(all_entities),
                    "missing_count": len(missing),
                },
            )
            return RequiredEntitiesResult(
                intent_type=intent_type,
                resolved_tools=static["tools"],
                required_entities=all_entities,
                missing_entities=missing,
            )

        # ── Fast path: compound intents with sub_intents ──────────────
        if sub_intents:
            workflows = [get_workflow(si) for si in sub_intents]
            if all(wf is not None for wf in workflows):
                merged_entities = merge_entity_requirements(workflows)  # type: ignore[arg-type]
                # Collect tools across all sub-workflows
                resolved_tools: list[str] = []
                seen_tools: set[str] = set()
                for wf in workflows:
                    for st in wf.steps:  # type: ignore[union-attr]
                        if st.type == "api" and st.tool and not st.tool.startswith("system.") and st.tool not in seen_tools:
                            resolved_tools.append(st.tool)
                            seen_tools.add(st.tool)

                all_entities_compound: list[EntityRequirement] = []
                missing_compound: list[EntityRequirement] = []
                for edef in merged_entities:
                    entity = EntityRequirement(
                        name=edef.name,
                        description=edef.description,
                        required=edef.required,
                        default_preference_key=edef.default_preference_key,
                        aliases=list(edef.aliases),
                    )
                    all_entities_compound.append(entity)
                    found = edef.name in collected or any(
                        alias in collected for alias in edef.aliases
                    )
                    if not found:
                        missing_compound.append(entity)

                logger.info(
                    "get_required_entities_compound_hit",
                    extra={
                        "component": "planner",
                        "intent_type": intent_type,
                        "sub_intents": sub_intents,
                        "required_count": len(all_entities_compound),
                        "missing_count": len(missing_compound),
                    },
                )
                return RequiredEntitiesResult(
                    intent_type=intent_type,
                    resolved_tools=resolved_tools,
                    required_entities=all_entities_compound,
                    missing_entities=missing_compound,
                )

        # ── Step 1: Ask LLM (no catalog knowledge) ──────────────────────

        # Build preference-key reference so the LLM maps entities to the
        # correct profile-store keys (instead of guessing or returning null).
        pref_key_lines = self._build_preference_key_reference()

        system_prompt = (
            "You are an intent analysis engine for a personal assistant. "
            "Given an intent type and already-collected entities (with values), "
            "determine:\n"
            "1. What tool(s) are needed (provider.service format)\n"
            "2. What entities are needed AND whether each is already satisfied "
            "by the collected data\n"
            "3. What execution steps the plan should have\n\n"
            "Return ONLY valid JSON with this structure:\n"
            "{\n"
            '  "tools_needed": ["provider.service", ...],\n'
            '  "entities": [\n'
            "    {\n"
            '      "name": "entity_name",\n'
            '      "description": "brief human-readable description",\n'
            '      "required": true,\n'
            '      "missing": true,\n'
            '      "default_preference_key": "profile_store_key_or_null"\n'
            "    }\n"
            "  ],\n"
            '  "steps": [\n'
            '    {"role": "Fetcher", "type": "api", "tool": "TOOL_NAME", '
            '"description": "Searching for data"},\n'
            '    {"role": "Reasoner", "type": "llm_reasoning", "tool": "summarizer", '
            '"description": "Summarizing results"}\n'
            "  ]\n"
            "}\n\n"
            "Entity rules:\n"
            "- tools_needed: tool IDs needed (provider.service format)\n"
            "- required: true ONLY for fields the user MUST provide\n"
            "- missing: true ONLY if the entity is NOT satisfied by collected data. "
            "Use SEMANTIC matching — e.g. if 'time' and 'date' are collected, "
            "then 'start_time'/'start_datetime' is satisfied (missing=false). "
            "If 'duration_minutes' is collected, 'duration' is satisfied. "
            "If 'email' is collected, 'attendee_email' is satisfied.\n"
            "- Do NOT mark as missing fields that have sensible defaults "
            "(calendar_id='primary', send_invitations=true, timezone=user's tz)\n"
            "- Do NOT mark as missing fields derivable from collected data "
            "(end_time from start_time + duration)\n"
            "- default_preference_key: MUST be one of the known preference keys "
            "listed below, or null if no preference applies. Map entities to "
            "the most relevant preference key (e.g. duration → meeting_duration_min, "
            "timezone → timezone).\n"
            "- Be MINIMAL — only mark missing=true for what the user truly "
            "still needs to tell us. Think like a smart assistant.\n\n"
            "Step rules:\n"
            "- For READ-ONLY intents (list, search, check, show, summarize, get, view, find):\n"
            "  Use ONLY Fetcher + Reasoner. No Resolver, no Booker.\n"
            "- For WRITE intents (create, send, update, delete, schedule, book):\n"
            "  Use Fetcher → Reasoner → Resolver → Booker.\n"
            "- Fetcher: retrieves data (type=\"api\")\n"
            "- Reasoner: analyzes/summarizes (type=\"llm_reasoning\")\n"
            "- Resolver: user confirmation gate (type=\"api\", tool=\"system.confirm\")\n"
            "- Booker: executes the action (type=\"api\")\n"
            "- Match tool names to the available tool schemas provided.\n\n"
            "Known preference keys (use these EXACT strings for default_preference_key):\n"
            + pref_key_lines
        )

        user_prompt = (
            f"Intent type: {intent_type}\n"
            f"Already collected entities: {json.dumps(collected)}\n\n"
            "Analyze what's needed and what's already satisfied."
        )

        # Inject relevant tool schemas so the LLM uses actual API parameter names
        t_schema = time.monotonic()
        tool_schemas = await self._pre_resolve_tool_schemas(intent_type, user_id)
        t_schema_ms = int((time.monotonic() - t_schema) * 1000)
        if tool_schemas:
            user_prompt += (
                f"\n\nAvailable tool schemas:\n{json.dumps(tool_schemas, indent=2)}\n"
                "Use these schemas to determine exact parameter names and types.\n"
            )
        logger.info(
            "planner_entity_schema_resolve",
            extra={
                "component": "planner",
                "intent_type": intent_type,
                "schema_count": len(tool_schemas),
                "schema_tools": [s["tool"] for s in tool_schemas],
                "schema_resolve_ms": t_schema_ms,
            },
        )

        suggested_tools: list[str] = []
        entities_data: list[dict[str, Any]] = []
        steps_data: list[dict[str, Any]] = []

        t_llm = time.monotonic()
        try:
            raw = await self._primary_breaker.call(
                self._llm.generate,
                model=self._fallback_model,  # Sonnet for entity inference
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.0,
            )
            t_llm_ms = int((time.monotonic() - t_llm) * 1000)
            logger.info(
                "planner_entity_llm_complete",
                extra={
                    "component": "planner",
                    "intent_type": intent_type,
                    "model": self._fallback_model,
                    "llm_ms": t_llm_ms,
                    "response_length": len(raw),
                },
            )
            # Strip markdown fences / preamble text before JSON
            cleaned = raw.strip()
            if "```" in cleaned:
                fence_start = cleaned.index("```")
                after_fence = cleaned[fence_start + 3 :]
                if after_fence and not after_fence.startswith("\n"):
                    after_fence = after_fence.split("\n", 1)[-1] if "\n" in after_fence else after_fence
                if "```" in after_fence:
                    cleaned = after_fence.rsplit("```", 1)[0].strip()
                else:
                    cleaned = after_fence.strip()
            else:
                brace_start = cleaned.find("{")
                if brace_start > 0:
                    cleaned = cleaned[brace_start:]
            parsed = json.loads(cleaned.strip())

            if isinstance(parsed, dict):
                suggested_tools = parsed.get("tools_needed", [])
                entities_data = parsed.get("entities", [])
                steps_data = parsed.get("steps", [])
                if not isinstance(suggested_tools, list):
                    suggested_tools = []
                if not isinstance(entities_data, list):
                    entities_data = []
                if not isinstance(steps_data, list):
                    steps_data = []

            logger.info(
                "planner_entity_llm_parsed",
                extra={
                    "component": "planner",
                    "intent_type": intent_type,
                    "suggested_tools": suggested_tools,
                    "entity_count": len(entities_data),
                    "step_count": len(steps_data),
                    "entities": [e.get("name") for e in entities_data if isinstance(e, dict)],
                },
            )
        except LLMCallError as e:
            t_llm_ms = int((time.monotonic() - t_llm) * 1000)
            # Rate-limit errors must propagate so the UI can show the
            # exact provider-side 429 instead of degrading into a silent
            # empty entity list (which masks the failure as "collecting").
            if "rate limit" in (e.reason or "").lower():
                logger.warning(
                    "entity_inference_rate_limited",
                    extra={
                        "component": "planner",
                        "intent_type": intent_type,
                        "model": e.model,
                        "llm_ms": t_llm_ms,
                    },
                )
                raise
            logger.warning(
                "entity_inference_llm_failed",
                extra={
                    "component": "planner",
                    "intent_type": intent_type,
                    "error": str(e),
                    "llm_ms": t_llm_ms,
                },
            )
        except CircuitOpenError as e:
            logger.warning(
                "entity_inference_circuit_open",
                extra={"component": "planner", "intent_type": intent_type, "error": str(e)},
            )
        except (json.JSONDecodeError, Exception) as e:
            t_llm_ms = int((time.monotonic() - t_llm) * 1000)
            logger.warning(
                "entity_inference_parse_failed",
                extra={
                    "component": "planner",
                    "intent_type": intent_type,
                    "error": str(e),
                    "llm_ms": t_llm_ms,
                },
            )

        # ── Step 2: Validate tools against ToolCatalog ───────────────
        catalog_available = True
        try:
            all_catalog_tools = []
            if user_id:
                try:
                    user_tools = await self._tool_catalog.get_user_tools(user_id)
                    if user_tools is not None:
                        all_catalog_tools = user_tools
                    else:
                        all_catalog_tools = await self._tool_catalog.refresh_user(user_id)
                except Exception:
                    pass
            if not all_catalog_tools:
                # No user context or fetch failed — use global as last resort
                # for validation only (not for populating user-facing dropdowns)
                all_catalog_tools = self._tool_catalog.get_all_tools()
            registered_names = {t.name for t in all_catalog_tools}
        except Exception as exc:
            logger.warning(
                "catalog_unavailable_for_entities",
                extra={
                    "component": "planner",
                    "intent_type": intent_type,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            registered_names = set()
            all_catalog_tools = []
            catalog_available = False

        resolved_tools: list[str] = []
        if suggested_tools and catalog_available:
            # First pass: exact name match
            resolved_tools = [t for t in suggested_tools if t in registered_names]
            missing_tools = [t for t in suggested_tools if t not in registered_names]

            # Second pass: provider-level matching for unresolved tools.
            if missing_tools:
                still_missing = []
                for suggested in missing_tools:
                    matches = self._match_provider(suggested, all_catalog_tools)
                    if matches:
                        resolved_tools.append(matches[0][1])
                    else:
                        still_missing.append(suggested)

                if still_missing:
                    logger.info(
                        "tools_not_in_catalog",
                        extra={
                            "component": "planner",
                            "intent_type": intent_type,
                            "suggested": suggested_tools,
                            "missing": still_missing,
                        },
                    )

            if not resolved_tools:
                raise ToolNotAvailableError(
                    intent_type=intent_type,
                    required_tools=suggested_tools,
                )
        elif suggested_tools and not catalog_available:
            raise ToolNotAvailableError(
                intent_type=intent_type,
                required_tools=suggested_tools,
            )

        # ── Step 2b: Deterministic override — exact-match keys are never missing ──
        for item in entities_data:
            name = item.get("name")
            if name and name in collected:
                item["missing"] = False

        # ── Step 3: Build EntityRequirement list ─────────────────────────
        required_entities = []
        missing_entities = []
        for item in entities_data:
            if not isinstance(item, dict) or "name" not in item:
                continue
            entity = EntityRequirement(
                name=item["name"],
                description=item.get("description", ""),
                required=item.get("required", True),
                default_preference_key=item.get("default_preference_key"),
            )
            required_entities.append(entity)
            # Include ALL missing entities (required AND optional) so the
            # Intake service can ask the user about them.  Readiness gating
            # (ready vs collecting) is handled downstream — only required
            # missing entities block the flow.
            if item.get("missing", False):
                missing_entities.append(entity)

        # ── Step 4: Build plan step hints from LLM response ──────────────
        plan_steps: list[SkeletonStepHint] = []
        for step_item in steps_data:
            if not isinstance(step_item, dict) or "role" not in step_item:
                continue
            plan_steps.append(SkeletonStepHint(
                role=step_item["role"],
                type=step_item.get("type", "api"),
                tool=step_item.get("tool", "system.echo"),
                description=step_item.get("description", ""),
            ))

        logger.info(
            "get_required_entities_complete",
            extra={
                "component": "planner",
                "op": "get_required_entities",
                "intent_type": intent_type,
                "suggested_tools": suggested_tools,
                "resolved_tools": resolved_tools,
                "required_count": len(required_entities),
                "missing_count": len(missing_entities),
                "plan_steps_count": len(plan_steps),
            },
        )

        return RequiredEntitiesResult(
            intent_type=intent_type,
            resolved_tools=resolved_tools,
            required_entities=required_entities,
            missing_entities=missing_entities,
            plan_steps=plan_steps,
        )

    # ------------------------------------------------------------------
    # Skeleton builder — plan-first entity collection
    # ------------------------------------------------------------------

    async def build_skeleton(
        self,
        intent_type: str,
        partial_entities: dict[str, Any],
        user_id: str,
        sub_intents: list[str] | None = None,
        preference_service: Any | None = None,
    ) -> Any:
        """Build a lightweight PlanSkeleton for the visual plan builder.

        For known intents (registry path): 0 LLM calls.
        For unknown intents (LLM fallback): reuses get_required_entities.
        """
        from shared.schemas.skeleton import (
            PlanSkeleton,
            SkeletonEntityField,
            SkeletonStep,
        )
        from components.Planner.adapters.workflow_registry import (
            compose_workflows,
            decompose_intent,
            merge_entity_requirements,
            parse_entity_refs,
        )

        # ── Known intents: registry path (0 LLM calls) ──────────────
        workflows = None
        if sub_intents:
            from components.Planner.adapters.workflow_registry import get_workflow as _get_wf
            wfs = [_get_wf(si) for si in sub_intents]
            if all(wf is not None for wf in wfs):
                workflows = wfs
        if workflows is None:
            workflows = decompose_intent(intent_type)

        if workflows is not None:
            # Build steps
            if len(workflows) == 1:
                step_templates = list(workflows[0].steps)
            else:
                step_templates, _ = compose_workflows(workflows)

            # Build entity definitions
            if len(workflows) == 1:
                entity_defs = list(workflows[0].entities)
            else:
                entity_defs = merge_entity_requirements(workflows)

            # Build skeleton steps with entity_refs
            skeleton_steps: list[SkeletonStep] = []
            for st in step_templates:
                refs = parse_entity_refs(st.args_template)
                skeleton_steps.append(SkeletonStep(
                    step=st.step,
                    role=st.role,
                    type=st.type,
                    tool=st.tool,
                    call=st.call,
                    after=list(st.after),
                    gate_id=st.gate_id,
                    entity_refs=refs,
                    description=self._humanize_skeleton_step(st.role, st.tool),
                ))

            # Build DAG levels via topological sort
            dag_levels = self._compute_dag_levels(skeleton_steps)

            # Build entity fields with profile defaults
            entity_fields: list[SkeletonEntityField] = []
            for edef in entity_defs:
                # Compute used_by_steps (inverse of entity_refs)
                used_by = [
                    s.step for s in skeleton_steps if edef.name in s.entity_refs
                ]
                default_value = None
                default_source = None
                if edef.default_preference_key and preference_service is not None:
                    try:
                        from uuid import UUID as _UUID
                        evidence = await preference_service.get_preference(
                            user_id=_UUID(user_id),
                            preference_key=edef.default_preference_key,
                            context_tier=2,
                        )
                        if evidence and evidence.value is not None:
                            default_value = evidence.value
                            default_source = "profile"
                    except Exception:
                        pass  # Best-effort — no default is fine

                entity_fields.append(SkeletonEntityField(
                    name=edef.name,
                    description=edef.description,
                    required=edef.required,
                    default_value=default_value,
                    default_source=default_source,
                    used_by_steps=used_by,
                    unit=edef.unit,
                    example=edef.example,
                ))

            return PlanSkeleton(
                intent=intent_type,
                intent_source="registry",
                steps=skeleton_steps,
                entities=entity_fields,
                dag_levels=dag_levels,
                sub_intents=sub_intents or [],
            )

        # ── Unknown intents: LLM fallback ────────────────────────────
        result = await self.get_required_entities(
            intent_type, partial_entities, user_id, sub_intents
        )

        # Use LLM-determined steps if available, else fall back to safe default
        if result.plan_steps:
            skeleton_steps = []
            for i, hint in enumerate(result.plan_steps, start=1):
                skeleton_steps.append(SkeletonStep(
                    step=i,
                    role=hint.role,
                    type=hint.type,
                    tool=hint.tool,
                    call=hint.tool,
                    after=[i - 1] if i > 1 else [],
                    gate_id="gate-confirm" if hint.role in ("Resolver", "Booker") else None,
                    entity_refs=[],
                    description=hint.description,
                ))
        else:
            # Defensive fallback: 2-step read-only (safer than assuming write)
            tool = result.resolved_tools[0] if result.resolved_tools else "system.echo"
            skeleton_steps = [
                SkeletonStep(
                    step=1,
                    role="Fetcher",
                    type="api",
                    tool=tool,
                    call=tool,
                    after=[],
                    gate_id=None,
                    entity_refs=[],
                    description="Gathering relevant data",
                ),
                SkeletonStep(
                    step=2,
                    role="Reasoner",
                    type="llm_reasoning",
                    tool="system.echo",
                    call="system.echo",
                    after=[1],
                    gate_id=None,
                    entity_refs=[],
                    description="Analyzing results",
                ),
            ]

        dag_levels = [[i] for i in range(1, len(skeleton_steps) + 1)]

        entity_fields = []
        for ent in result.required_entities:
            entity_fields.append(SkeletonEntityField(
                name=ent.name,
                description=ent.description,
                required=ent.required,
                default_value=None,
                default_source=None,
                used_by_steps=[],
            ))

        # Cache skeleton structure for plan generation to reuse
        cache_key = f"{intent_type}:{user_id}"
        self._skeleton_cache[cache_key] = {
            "steps": [
                {
                    "step": s.step,
                    "role": s.role,
                    "type": s.type,
                    "tool": s.tool,
                    "after": s.after,
                    "gate_id": s.gate_id,
                    "description": s.description,
                }
                for s in skeleton_steps
            ],
            "resolved_tools": result.resolved_tools,
            "timestamp": time.monotonic(),
        }

        return PlanSkeleton(
            intent=intent_type,
            intent_source="llm",
            steps=skeleton_steps,
            entities=entity_fields,
            dag_levels=dag_levels,
            sub_intents=sub_intents or [],
        )

    @staticmethod
    def _humanize_skeleton_step(role: str, tool: str) -> str:
        """Human-readable description for a skeleton step.

        For registry-sourced skeletons, generates descriptions based on the
        tool action verb (SEARCH_, LIST_, CREATE_, SEND_, etc.) and provider.
        For LLM-sourced skeletons, descriptions come from the LLM response.
        """
        if not tool or tool.startswith("system."):
            role_map = {
                "Fetcher": "Gathering data",
                "Reasoner": "Analyzing and deciding",
                "Resolver": "Confirming with you",
                "Booker": "Executing the action",
                "Notifier": "Sending notification",
                "Analyzer": "Analyzing results",
                "Watcher": "Monitoring changes",
            }
            return role_map.get(role, role)

        # Internal tools (email_validator, notion_summarizer, etc.)
        # are NOT Composio tools — handle them before provider parsing.
        tool_lower = tool.lower()
        if tool_lower.endswith("_validator"):
            return "Validating inputs"
        if tool_lower.endswith("_summarizer"):
            return "Summarizing results"
        if tool_lower.endswith("_resolver"):
            return "Checking for conflicts"
        if tool_lower.endswith("_formatter"):
            return "Formatting output"

        # Extract provider from tool name: GOOGLECALENDAR_CREATE_EVENT → Googlecalendar
        parts = tool.split("_")
        provider = parts[0].title() if len(parts) > 1 else tool
        action = "_".join(parts[1:]).upper() if len(parts) > 1 else ""

        # Action-verb-based descriptions
        if role == "Fetcher":
            if action.startswith("SEARCH") or action.startswith("FIND"):
                return f"Searching {provider}"
            if action.startswith("LIST") or action.startswith("FETCH"):
                return f"Fetching from {provider}"
            if action.startswith("GET"):
                return f"Retrieving from {provider}"
            return f"Gathering data from {provider}"

        if role == "Reasoner":
            if any(kw in action for kw in ("CREATE", "SEND", "UPDATE", "DELETE")):
                return f"Analyzing and planning via {provider}"
            return f"Summarizing results from {provider}"

        if role == "Booker":
            if action.startswith("CREATE"):
                return f"Creating via {provider}"
            if action.startswith("SEND"):
                return f"Sending via {provider}"
            if action.startswith("UPDATE") or action.startswith("APPEND"):
                return f"Updating via {provider}"
            if action.startswith("DELETE"):
                return f"Deleting via {provider}"
            if action.startswith("UPLOAD"):
                return f"Uploading to {provider}"
            return f"Executing action via {provider}"

        if role == "Resolver":
            return "Confirming with you"
        if role == "Notifier":
            return f"Sending notification via {provider}"

        return f"{role} via {provider}"

    @staticmethod
    def _compute_dag_levels(steps: list) -> list[list[int]]:
        """Topological sort into parallel levels using Kahn's algorithm.

        Returns e.g. [[1], [2,3], [4]] where steps in the same inner list
        can execute in parallel.
        """
        step_map = {s.step: s for s in steps}
        in_degree: dict[int, int] = {s.step: 0 for s in steps}
        for s in steps:
            for dep in s.after:
                if dep in step_map:
                    in_degree[s.step] = in_degree.get(s.step, 0) + 1

        # Recalculate properly
        in_degree = {s.step: 0 for s in steps}
        for s in steps:
            for dep in s.after:
                if dep in step_map:
                    in_degree[s.step] += 1

        levels: list[list[int]] = []
        queue = sorted(s for s in in_degree if in_degree[s] == 0)

        while queue:
            levels.append(queue)
            next_queue: list[int] = []
            for node in queue:
                for s in steps:
                    if node in s.after:
                        in_degree[s.step] -= 1
                        if in_degree[s.step] == 0:
                            next_queue.append(s.step)
            queue = sorted(next_queue)

        return levels

    def _build_plan_from_skeleton(
        self,
        cached: dict,
        intent: Intent,
        tools: list,
    ) -> Plan | None:
        """Build a Plan from a cached skeleton structure + collected entities."""
        plan_steps: list[PlanStep] = []
        for step_data in cached["steps"]:
            role = step_data["role"]
            tool_name = step_data.get("tool", "system.echo")

            # Apply user tool selection from tool_overrides
            override = intent.tool_overrides.get(step_data["step"])
            if override:
                tool_name = override

            # Resolve tool name against catalog
            resolved = self._resolve_skeleton_tool(tool_name, tools)

            plan_steps.append(PlanStep(
                step=step_data["step"],
                mode="interactive",
                role=role,
                type=step_data.get("type", "api"),
                uses=resolved or tool_name,
                call=resolved or tool_name,
                args=self._build_skeleton_args(role, intent.entities, resolved),
                after=step_data.get("after", []),
                timeout_s=30,
                gate_id=step_data.get("gate_id"),
                dry_run=True,
                can_spawn=role == "Reasoner",
                max_spawned_steps=3 if role == "Reasoner" else None,
                trust_level="trusted" if role == "Reasoner" else None,
                policy_ref="policy-reasoning-v1" if role == "Reasoner" else None,
                reasoning_config={
                    "model": self._fallback_model,
                    "temperature": 0.0,
                    "max_tokens": 1024,
                    "system_prompt_ref": f"Analyze and process for {intent.intent}",
                } if step_data.get("type") == "llm_reasoning" else None,
            ))

        if not plan_steps:
            return None

        return Plan(
            plan_id=str(ulid.new()),
            intent=intent,
            trace_id=intent.trace_id,
            graph=plan_steps,
            constraints=PlanConstraints(),
            plugins=list({s.uses for s in plan_steps}),
            meta=PlanMeta(
                created_at=datetime.now(UTC).isoformat(),
                canonical_hash="0" * 64,
            ),
        )

    @staticmethod
    def _resolve_skeleton_tool(tool_name: str, tools: list) -> str | None:
        """Resolve a skeleton tool name against the catalog tool list."""
        if not tool_name or tool_name.startswith("system."):
            return tool_name
        # Exact match
        for t in tools:
            if t.name == tool_name:
                return t.name
        # Prefix match (e.g. "summarizer" matches a tool containing it)
        tool_lower = tool_name.lower()
        for t in tools:
            if tool_lower in t.name.lower():
                return t.name
        return None

    @staticmethod
    def _build_skeleton_args(
        role: str,
        entities: dict[str, Any],
        resolved_tool: str | None,
    ) -> dict[str, Any]:
        """Build args for a skeleton-derived plan step from collected entities."""
        if role == "Resolver":
            return {"message": "Please confirm this action"}
        if role == "Reasoner":
            return {"context": entities}
        # For Fetcher/Booker: pass all entities as args
        return dict(entities) if entities else {}

    @staticmethod
    def _build_preference_key_reference() -> str:
        """Build a compact reference of known preference keys for the LLM prompt."""
        try:
            from shared.schemas.preference_registry import get_preference_registry

            registry = get_preference_registry()
            lines: list[str] = []
            for key in registry.list_preference_keys():
                defn = registry.get_preference_definition(key)
                if defn.sensitive:
                    continue  # Don't expose sensitive preference keys to LLM
                desc = defn.description or key
                lines.append(f"- {key}: {desc}")
            return "\n".join(lines) if lines else "- (none registered)"
        except Exception:
            return "- (preference registry unavailable)"

    @staticmethod
    def _normalize_provider(name: str) -> str:
        """Normalize a provider name for fuzzy matching.

        Strips separators (dots, underscores, hyphens) and lowercases.
        ``"google.calendar"`` → ``"googlecalendar"``
        ``"google_calendar"`` → ``"googlecalendar"``
        """
        return re.sub(r"[._\-]", "", name).lower()

    def _match_provider(
        self,
        suggested: str,
        catalog_tools: list,
    ) -> list[tuple[str, str]]:
        """Match an LLM-suggested tool name against catalog tools by provider.

        The LLM suggests ``"google.calendar"``; the catalog has tools like
        ``GOOGLECALENDAR_CREATE_EVENT`` with provider_name ``"googlecalendar"``.

        Returns list of ``(suggested, catalog_tool_name)`` pairs.
        """
        prefix = self._normalize_provider(suggested.split(".")[0])
        full_norm = self._normalize_provider(suggested)

        matches: list[tuple[str, str]] = []
        seen_providers: set[str] = set()

        for tool in catalog_tools:
            pn = self._normalize_provider(tool.provider_name)
            if pn in seen_providers:
                continue
            if pn.startswith(prefix) or pn == full_norm:
                matches.append((suggested, tool.name))
                seen_providers.add(pn)

        return matches

    async def _pre_resolve_tool_schemas(
        self, intent_type: str, user_id: str | None = None
    ) -> list[dict]:
        """Best-effort: find relevant tool schemas by keyword matching.

        Checks per-user tools first (reflects connected apps), then global.
        """
        try:
            all_tools = []
            if user_id:
                try:
                    user_tools = await self._tool_catalog.get_user_tools(user_id)
                    if user_tools is not None:
                        all_tools = user_tools
                    else:
                        all_tools = await self._tool_catalog.refresh_user(user_id)
                except Exception:
                    pass

            keywords = set(intent_type.replace("_", " ").lower().split())
            matches = []
            for tool in all_tools:
                searchable = (tool.description + " " + tool.name).lower()
                if any(kw in searchable for kw in keywords) and tool.input_schema:
                    matches.append(
                        {
                            "tool": tool.name,
                            "description": tool.description,
                            "input_schema": tool.input_schema,
                        }
                    )
            return matches[:5]
        except Exception:
            return []

    async def _try_plan_cache(
        self, intent: Intent, tool_ids: list[str]
    ) -> PlannerResult | None:
        """Check PlanLibrary for a cached plan matching this intent signature."""
        try:
            sig = json.dumps(
                {
                    "intent": intent.intent,
                    "entity_keys": sorted(intent.entities.keys()),
                    "tool_ids": sorted(tool_ids),
                },
                sort_keys=True,
            )
            plan_hash = hashlib.sha256(sig.encode()).hexdigest()[:16]

            cached = await self._plan_service.db.get_plan_by_hash(plan_hash)
            if cached and cached.get("success"):
                cached_plan = Plan.model_validate(cached["canonical_json"])
                # Re-finalize with current intent
                cached_plan = await self._finalize_plan(cached_plan, intent)
                return PlannerResult(
                    plan=cached_plan,
                    fallback_level=0,
                    context_degraded=False,
                    generation_duration_ms=0,
                    registry_version=self._tool_catalog.version
                    if hasattr(self._tool_catalog, "version")
                    else 0,
                )
        except Exception as e:
            logger.debug(
                "plan_cache_miss",
                extra={"component": "planner", "error": str(e)},
            )
        return None

    async def generate_plan(self, intent: Intent) -> PlannerResult:
        """Generate a validated execution plan."""
        start = time.monotonic()

        logger.info(
            "plan_generation_start",
            extra={
                "component": "planner",
                "op": "generate_plan",
                "intent_type": intent.intent,
                "user_id": intent.user_id,
            },
        )

        # 1. Gather evidence from ContextRAG
        context_degraded = False
        try:
            context_result = await self._context_rag.gather_evidence(intent)
            evidence = context_result.evidence
            if context_result.degraded_sources:
                context_degraded = True
        except Exception:
            logger.warning("context_rag_failed", extra={"component": "planner"})
            evidence = []
            context_degraded = True

        # 2. Get tool catalog — per-user tools only (FR-014: never fall back
        #    to get_all_tools() which shows tools the user hasn't connected).
        tools = []
        try:
            # a) Per-user cached tools (survives container restarts via Redis)
            user_tools = await self._tool_catalog.get_user_tools(intent.user_id)
            if user_tools is not None:
                tools = user_tools
            else:
                # b) Not cached — live refresh from Composio for this user
                tools = await self._tool_catalog.refresh_user(intent.user_id)

            # d) Tool Discovery pipeline (3-tier) or legacy keyword filter
            full_count = len(tools)
            tool_discovery_enabled = os.environ.get(
                "TOOL_DISCOVERY_ENABLED", "true"
            ).lower() in ("true", "1", "yes")

            if tool_discovery_enabled and self._tool_discovery is not None:
                # 3-tier hybrid retrieval: embedding + reranking + fallback
                try:
                    from components.Planner.domain.tool_discovery_models import (
                        ToolNotConnectedError,
                    )
                    discovery_result = await self._tool_discovery.discover_tools(
                        intent_text=intent.intent,
                        available_tools=tools,
                        intent_entities=intent.entities,
                    )
                    tools = discovery_result.tools
                    logger.info(
                        "tool_discovery_used intent=%s tier=%d candidates=%d final=%d ms=%d",
                        intent.intent,
                        discovery_result.discovery_tier,
                        discovery_result.candidate_count,
                        len(tools),
                        discovery_result.discovery_ms,
                    )
                except ToolNotConnectedError:
                    raise  # Propagate to API layer as 422
                except Exception:
                    # Discovery failed — fall back to legacy keyword filter
                    logger.warning(
                        "tool_discovery_failed_using_keyword_filter",
                        extra={"component": "planner", "intent": intent.intent},
                        exc_info=True,
                    )
                    tools = self._legacy_keyword_filter(tools, intent)
            else:
                # Legacy keyword filter pipeline
                tools = self._legacy_keyword_filter(tools, intent)

            # Ensure user-selected tools are in the tool list for LLM context
            if intent.tool_overrides:
                override_names = set(intent.tool_overrides.values())
                existing_names = {t.name for t in tools}
                missing = override_names - existing_names
                if missing:
                    all_user_tools = await self._tool_catalog.get_user_tools(intent.user_id)
                    if all_user_tools is None:
                        all_user_tools = await self._tool_catalog.refresh_user(intent.user_id)
                    for t in (all_user_tools or []):
                        if t.name in missing:
                            tools.append(t)

            # Compact each tool's input_schema to property names/types only,
            # dropping verbose per-property descriptions.
            tools = compact_tool_schemas(tools)

            if len(tools) != full_count:
                logger.info(
                    "tool_catalog_filtered intent=%s full=%d final=%d",
                    intent.intent, full_count, len(tools),
                )

            tool_names = {t.name for t in tools}
        except Exception as exc:
            # Re-raise ToolNotConnectedError so the API layer can handle it
            from components.Planner.domain.tool_discovery_models import (
                ToolNotConnectedError,
            )
            if isinstance(exc, ToolNotConnectedError):
                raise
            tools = []
            tool_names = set()
        registry_version = 0  # ToolCatalog has no versioning (TTL-based refresh)

        # 2b. Check plan cache before LLM call
        cached_result = await self._try_plan_cache(intent, list(tool_names))
        if cached_result is not None:
            duration_ms = int((time.monotonic() - start) * 1000)
            cached_result = cached_result.model_copy(
                update={
                    "generation_duration_ms": duration_ms,
                    "context_degraded": context_degraded,
                }
            )
            logger.info(
                "plan_cache_hit intent=%s duration_ms=%d",
                intent.intent, duration_ms,
            )
            return cached_result

        # 2c. Try deterministic planner for known intents (no LLM)
        if (
            self._deterministic_planner is not None
            and self._deterministic_planner.can_handle(intent)
        ):
            det_plan = self._deterministic_planner.build_plan(intent, tools)
            if det_plan is not None:
                det_plan = await self._finalize_plan(det_plan, intent)
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "deterministic_plan_used intent=%s duration_ms=%d",
                    intent.intent, duration_ms,
                )
                return PlannerResult(
                    plan=det_plan,
                    fallback_level=0,
                    context_degraded=context_degraded,
                    generation_duration_ms=duration_ms,
                    registry_version=registry_version,
                )

        # 2d. Try cached skeleton structure for unknown intents (no LLM)
        cache_key = f"{intent.intent}:{intent.user_id}"
        cached_skel = self._skeleton_cache.pop(cache_key, None)  # pop = one-time use
        if cached_skel and (time.monotonic() - cached_skel["timestamp"]) < self._skeleton_cache_ttl_s:
            skel_plan = self._build_plan_from_skeleton(cached_skel, intent, tools)
            if skel_plan is not None:
                skel_plan = await self._finalize_plan(skel_plan, intent)
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "skeleton_plan_used intent=%s duration_ms=%d",
                    intent.intent, duration_ms,
                )
                return PlannerResult(
                    plan=skel_plan,
                    fallback_level=0,
                    context_degraded=context_degraded,
                    generation_duration_ms=duration_ms,
                    registry_version=registry_version,
                )

        # 3. Build prompts
        system_prompt = self._prompt.build_system_prompt()
        user_prompt = self._prompt.build_user_prompt(intent, evidence, tools)

        # 4. Generate plan with fallback hierarchy
        plan, fallback_level = await self._generate_with_fallback(
            system_prompt, user_prompt, intent, registry_version, tool_names
        )

        # 5. Finalize plan (plan_id, intent, plugins, meta with hash)
        plan = await self._finalize_plan(plan, intent)

        duration_ms = int((time.monotonic() - start) * 1000)

        logger.info(
            "plan_generation_complete",
            extra={
                "component": "planner",
                "op": "generate_plan",
                "plan_id": plan.plan_id,
                "intent_type": intent.intent,
                "fallback_level": fallback_level,
                "context_degraded": context_degraded,
                "duration_ms": duration_ms,
                "steps": len(plan.graph),
            },
        )

        return PlannerResult(
            plan=plan,
            fallback_level=fallback_level,
            context_degraded=context_degraded,
            generation_duration_ms=duration_ms,
            registry_version=registry_version,
        )

    @staticmethod
    def _legacy_keyword_filter(tools: list[Any], intent: Intent) -> list[Any]:
        """Legacy keyword-based tool filter (provider + action)."""
        tools = filter_tools_by_intent(tools, intent.intent)
        tools = filter_tools_by_action(tools, intent.intent, intent.entities)
        return tools

    async def _generate_with_fallback(
        self,
        system_prompt: str,
        user_prompt: str,
        intent: Intent,
        registry_version: int,
        tool_ids: set[str],
    ) -> tuple[Plan, int]:
        """4-level fallback hierarchy. Returns (plan, fallback_level)."""

        # Fast path: if both LLM circuits are open, skip straight to templates
        from components.Planner.adapters.circuit_breaker import CircuitState

        primary_state = self._primary_breaker.get_state()
        fallback_state = self._fallback_breaker.get_state()
        if primary_state == CircuitState.OPEN and fallback_state == CircuitState.OPEN:
            logger.warning(
                "both_circuits_open_skipping_llm",
                extra={
                    "component": "planner",
                    "primary_model": self._primary_model,
                    "fallback_model": self._fallback_model,
                    "intent_type": intent.intent,
                },
            )
            plan = await self._try_template_level(intent)
            if plan is not None:
                return plan, 3
            return self._create_minimal_plan(intent), 4

        # Level 1: Primary model (Anthropic adapter)
        plan = await self._try_llm_level(
            self._primary_breaker,
            self._primary_model,
            system_prompt,
            user_prompt,
            intent,
            registry_version,
            tool_ids,
            level=1,
            llm_adapter=self._llm,
        )
        if plan is not None:
            return plan, 1

        # Level 2: Fallback model (OpenAI adapter if available, else primary adapter)
        fallback_adapter = self._fallback_llm if self._fallback_llm else self._llm
        plan = await self._try_llm_level(
            self._fallback_breaker,
            self._fallback_model,
            system_prompt,
            user_prompt,
            intent,
            registry_version,
            tool_ids,
            level=2,
            llm_adapter=fallback_adapter,
        )
        if plan is not None:
            return plan, 2

        # Level 3: PlanLibrary template
        plan = await self._try_template_level(intent)
        if plan is not None:
            return plan, 3

        # Level 4: Minimal safe plan
        logger.warning(
            "fallback_triggered",
            extra={
                "component": "planner",
                "from_level": 3,
                "to_level": 4,
                "intent_type": intent.intent,
            },
        )
        return self._create_minimal_plan(intent), 4

    async def _try_llm_level(
        self,
        breaker: CircuitBreaker,
        model: str,
        system_prompt: str,
        user_prompt: str,
        intent: Intent,
        registry_version: int,
        tool_ids: set[str],
        level: int,
        llm_adapter: LLMAdapter | None = None,
    ) -> Plan | None:
        """Try generating a plan via LLM with circuit breaker. Returns None on failure."""
        adapter = llm_adapter or self._llm
        try:
            raw_output = await breaker.call(
                adapter.generate,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self._max_output_tokens,
                temperature=0.0,
            )
            plan = await self._validator.validate(raw_output, intent, registry_version, tool_ids)
            return plan
        except CircuitOpenError:
            logger.info(
                "circuit_open_skip",
                extra={"component": "planner", "model": model, "level": level},
            )
        except (LLMCallError, PlanValidationError) as e:
            next_level = level + 1
            logger.warning(
                "fallback_triggered",
                extra={
                    "component": "planner",
                    "from_level": level,
                    "to_level": next_level,
                    "model": model,
                    "reason": str(e),
                },
            )
        except Exception as e:
            logger.warning(
                "llm_unexpected_error",
                extra={"component": "planner", "model": model, "error": str(e)},
            )
        return None

    async def _try_template_level(
        self,
        intent: Intent,
    ) -> Plan | None:
        """Try retrieving a plan template from PlanLibrary."""
        try:
            templates = await self._plan_service.get_plans_by_intent(
                intent_type=intent.intent,
                success_threshold=0.7,
                limit=5,
            )
            if not templates:
                logger.info(
                    "no_templates_found",
                    extra={"component": "planner", "intent_type": intent.intent},
                )
                return None

            # Use the highest-confidence template
            best = max(templates, key=lambda t: t.confidence)
            plan = self._instantiate_template(best, intent)
            return plan
        except Exception as e:
            logger.warning(
                "template_retrieval_failed",
                extra={"component": "planner", "error": str(e)},
            )
            return None

    def _instantiate_template(self, template_evidence: Any, intent: Intent) -> Plan:
        """Fill a template plan from PlanLibrary with current intent data."""
        template_data = template_evidence.value
        if isinstance(template_data, str):
            template_data = json.loads(template_data)

        # Extract graph from template
        graph_data = template_data.get("graph", [])
        steps = []
        for step_data in graph_data:
            steps.append(PlanStep.model_validate(step_data))

        plan_id = str(ulid.new())
        now = datetime.now(UTC).isoformat()

        return Plan(
            plan_id=plan_id,
            intent=intent,
            trace_id=intent.trace_id,
            graph=steps if steps else [self._make_echo_step()],
            constraints=PlanConstraints(),
            plugins=list({s.uses for s in steps}),
            meta=PlanMeta(
                created_at=now,
                canonical_hash="0" * 64,  # Will be replaced in _finalize_plan
            ),
        )

    def _create_minimal_plan(self, intent: Intent) -> Plan:
        """Level 4: Single Fetcher step with system.echo."""
        plan_id = str(ulid.new())
        now = datetime.now(UTC).isoformat()

        return Plan(
            plan_id=plan_id,
            intent=intent,
            trace_id=intent.trace_id,
            graph=[self._make_echo_step()],
            constraints=PlanConstraints(),
            plugins=["system.echo"],
            meta=PlanMeta(
                created_at=now,
                canonical_hash="0" * 64,  # Will be replaced in _finalize_plan
            ),
        )

    @staticmethod
    def _make_echo_step() -> PlanStep:
        return PlanStep(
            step=1,
            mode="interactive",
            role="Fetcher",
            uses="system.echo",
            call="echo",
            args={"message": "Plan generation fell back to minimal safe plan"},
            after=[],
            timeout_s=30,
            dry_run=True,
        )

    async def _finalize_plan(self, plan: Plan, intent: Intent, available_tools: list[Any] | None = None) -> Plan:
        """Populate plan_id, intent, meta, plugins, and canonical_hash.

        Also resolves LLM-generated tool names (e.g. ``google.calendar``)
        to their actual Composio catalog names (e.g.
        ``GOOGLECALENDAR_LIST_EVENTS``) so downstream execution can use
        simple exact-match lookups.
        """
        # Always generate a real plan_id (LLM output uses a placeholder)
        plan = plan.model_copy(update={"plan_id": str(ulid.new())})

        # Always set intent and trace_id
        plan = plan.model_copy(update={"intent": intent, "trace_id": intent.trace_id})

        # Always set meta with real timestamp
        now = datetime.now(UTC).isoformat()
        plan = plan.model_copy(update={"meta": PlanMeta(created_at=now, canonical_hash="0" * 64)})

        # ── Resolve tool names to Composio catalog names ──────────────
        updated_steps = []
        unresolved: list[str] = []
        for step in plan.graph:
            # Only resolve API steps that use real MCP tools.
            # Skip: Resolvers (pass-through gates), system.* virtual tools,
            # llm_reasoning and policy_check steps (descriptive names).
            is_virtual = step.uses.startswith("system.") or step.uses.startswith("system_")
            if step.type == "api" and step.role != "Resolver" and not is_virtual:
                tool_def = self._tool_catalog.resolve_tool(step.uses, step.call)
                if tool_def is not None and tool_def.name != step.uses:
                    logger.info(
                        "plan_tool_name_resolved",
                        extra={
                            "component": "planner",
                            "step": step.step,
                            "original": step.uses,
                            "resolved": tool_def.name,
                        },
                    )
                    step = step.model_copy(
                        update={"uses": tool_def.name, "call": tool_def.name}
                    )
                elif tool_def is None:
                    unresolved.append(step.uses)

            if not step.dry_run:
                step = step.model_copy(update={"dry_run": True})
            updated_steps.append(step)

        # ── Tier 3: Agentic fallback for unresolved tools ──────────────
        if unresolved and self._tool_discovery is not None and available_tools:
            still_unresolved: list[str] = []
            for tool_name in unresolved:
                try:
                    expanded = await self._tool_discovery.agentic_expand(
                        missing_tool_name=tool_name,
                        available_tools=available_tools,
                        current_selected=[],
                    )
                    if expanded:
                        # Re-resolve the step with the newly found tool
                        resolved_tool = expanded[0]
                        resolved_name = getattr(resolved_tool, "name", "")
                        for i, step in enumerate(updated_steps):
                            if step.uses == tool_name:
                                updated_steps[i] = step.model_copy(
                                    update={"uses": resolved_name, "call": resolved_name}
                                )
                                logger.info(
                                    "agentic_tool_resolved",
                                    extra={
                                        "component": "planner",
                                        "original": tool_name,
                                        "resolved": resolved_name,
                                    },
                                )
                                break
                        else:
                            still_unresolved.append(tool_name)
                    else:
                        still_unresolved.append(tool_name)
                except Exception:
                    still_unresolved.append(tool_name)
            unresolved = still_unresolved

        if unresolved:
            logger.warning(
                "plan_tool_resolution_failed",
                extra={
                    "component": "planner",
                    "intent_type": intent.intent,
                    "unresolved_tools": unresolved,
                },
            )
            raise ToolNotAvailableError(
                intent_type=intent.intent,
                required_tools=unresolved,
            )

        plan = plan.model_copy(update={"graph": updated_steps})

        # Populate plugins from (now-resolved) graph
        plugins = list({s.uses for s in plan.graph})
        plan = plan.model_copy(update={"plugins": plugins})

        # Compute canonical hash — exclude identity/derived fields:
        # plan_id (unique ULID per call) and meta (created_at + canonical_hash)
        plan_dict = plan.model_dump(mode="json")
        hashable_dict = {k: v for k, v in plan_dict.items() if k not in ("plan_id", "meta")}
        canonical_hash = compute_plan_hash(hashable_dict)

        meta = plan.meta.model_copy(update={"canonical_hash": canonical_hash})
        plan = plan.model_copy(update={"meta": meta})

        return plan


def create_planner_service(
    context_rag_service: Any,
    tool_catalog: Any,
    plan_service: Any,
    llm_adapter: LLMAdapter | None = None,
    fallback_llm_adapter: LLMAdapter | None = None,
    deterministic_planner: Any | None = None,
    tool_discovery: Any | None = None,
) -> PlannerService:
    """Factory function for PlannerService. Reads config from env vars."""
    from components.Planner.adapters.deterministic_planner import DeterministicPlanner

    primary_model = os.environ.get("PLANNER_PRIMARY_MODEL", "claude-sonnet-4-5-20250929")
    fallback_model = os.environ.get("PLANNER_FALLBACK_MODEL", "claude-sonnet-4-5-20250929")
    max_output_tokens = int(os.environ.get("PLANNER_MAX_OUTPUT_TOKENS", "4096"))

    # Primary adapter: resolved via LLM_PROVIDER env var
    # (defaults to Anthropic API; "claude_code" selects the headless CLI).
    if llm_adapter is None:
        llm_adapter = LLMAdapterFactory.from_env()

    # Fallback adapter: same provider selection
    if fallback_llm_adapter is None:
        fallback_llm_adapter = LLMAdapterFactory.from_env()

    # Deterministic planner for known intents (no LLM)
    if deterministic_planner is None:
        deterministic_planner = DeterministicPlanner()

    prompt_builder = PromptBuilder()
    validator = PlanValidator()
    primary_breaker = CircuitBreaker(model_name=primary_model)
    fallback_breaker = CircuitBreaker(model_name=fallback_model)

    return PlannerService(
        context_rag_service=context_rag_service,
        tool_catalog=tool_catalog,
        plan_service=plan_service,
        llm_adapter=llm_adapter,
        prompt_builder=prompt_builder,
        validator=validator,
        primary_breaker=primary_breaker,
        fallback_breaker=fallback_breaker,
        primary_model=primary_model,
        fallback_model=fallback_model,
        max_output_tokens=max_output_tokens,
        fallback_llm_adapter=fallback_llm_adapter,
        deterministic_planner=deterministic_planner,
        tool_discovery=tool_discovery,
    )
