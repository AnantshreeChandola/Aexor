"""
PlannerService — deterministic plan generation orchestrator.

Coordinates: ContextRAG → ToolCatalog → LLM (with fallbacks) → Validator → Hasher

Reference: LLD SS7
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import ulid

from components.Planner.adapters.circuit_breaker import CircuitBreaker
from components.Planner.adapters.llm_adapter import AnthropicAdapter, LLMAdapter
from components.Planner.adapters.plan_hasher import compute_plan_hash
from components.Planner.adapters.plan_validator import PlanValidator
from components.Planner.adapters.prompt_builder import PromptBuilder
from components.Planner.domain.models import (
    CircuitOpenError,
    EntityRequirement,
    LLMCallError,
    PlannerResult,
    PlanValidationError,
    RequiredEntitiesResult,
    ToolNotAvailableError,
)
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._context_rag = context_rag_service
        self._tool_catalog = tool_catalog
        self._plan_service = plan_service
        self._llm = llm_adapter
        self._prompt = prompt_builder
        self._validator = validator
        self._primary_breaker = primary_breaker
        self._fallback_breaker = fallback_breaker
        self._primary_model = primary_model
        self._fallback_model = fallback_model
        self._max_output_tokens = max_output_tokens

    async def get_required_entities(
        self,
        intent_type: str,
        collected_entities: dict[str, Any] | None = None,
    ) -> RequiredEntitiesResult:
        """Lightweight query: determine required entities for an intent type.

        Step 1: Ask LLM what tools and entities are needed (no catalog context).
        Step 2: Validate the LLM's tool suggestions against the ToolCatalog.

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
            },
        )

        # ── Step 1: Ask LLM (no catalog knowledge) ──────────────────────
        system_prompt = (
            "You are an intent analysis engine. Given an intent type, determine:\n"
            "1. What kind of tool(s) would be needed (use provider.service format, "
            "e.g. 'google.calendar', 'slack.messaging')\n"
            "2. What entities (parameters) are required to fulfill this intent\n\n"
            "Return ONLY valid JSON with this structure:\n"
            "{\n"
            '  "tools_needed": ["provider.service", ...],\n'
            '  "entities": [\n'
            "    {\n"
            '      "name": "entity_name (snake_case)",\n'
            '      "description": "brief human-readable description",\n'
            '      "required": true,\n'
            '      "default_preference_key": "profile_store_key_or_null"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- tools_needed: list the tool IDs you think are needed "
            "(provider.service format)\n"
            "- default_preference_key: a plausible user preference key "
            "(e.g. 'default_meeting_duration'), or null if not applicable"
        )

        user_prompt = (
            f"Intent type: {intent_type}\n"
            f"Already collected entities: {json.dumps(list(collected.keys()))}\n\n"
            "Analyze and return JSON."
        )

        suggested_tools: list[str] = []
        entities_data: list[dict[str, Any]] = []

        try:
            raw = await self._primary_breaker.call(
                self._llm.generate,
                model=self._fallback_model,  # Cheaper model for lightweight query
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.0,
            )
            # Strip markdown fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
            parsed = json.loads(cleaned.strip())

            if isinstance(parsed, dict):
                suggested_tools = parsed.get("tools_needed", [])
                entities_data = parsed.get("entities", [])
                if not isinstance(suggested_tools, list):
                    suggested_tools = []
                if not isinstance(entities_data, list):
                    entities_data = []
        except (CircuitOpenError, LLMCallError) as e:
            logger.warning(
                "entity_inference_llm_failed",
                extra={"component": "planner", "intent_type": intent_type, "error": str(e)},
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(
                "entity_inference_parse_failed",
                extra={"component": "planner", "intent_type": intent_type, "error": str(e)},
            )

        # ── Step 2: Validate tools against ToolCatalog ───────────────
        catalog_available = True
        try:
            tools = self._tool_catalog.get_all_tools()
            registered_names = {t.name for t in tools}
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
            # Catalog unavailable — skip tool validation, let it through
            registered_names = set()
            catalog_available = False

        resolved_tools: list[str] = []
        if suggested_tools and catalog_available:
            resolved_tools = [t for t in suggested_tools if t in registered_names]
            missing_tools = [t for t in suggested_tools if t not in registered_names]

            if missing_tools:
                logger.info(
                    "tools_not_in_catalog",
                    extra={
                        "component": "planner",
                        "intent_type": intent_type,
                        "suggested": suggested_tools,
                        "missing": missing_tools,
                    },
                )

            if not resolved_tools:
                # None of the LLM-suggested tools exist in the catalog
                raise ToolNotAvailableError(
                    intent_type=intent_type,
                    required_tools=suggested_tools,
                )
        elif suggested_tools and not catalog_available:
            # Catalog unavailable — cannot verify tools exist
            raise ToolNotAvailableError(
                intent_type=intent_type,
                required_tools=suggested_tools,
            )

        # ── Step 3: Build EntityRequirement list ─────────────────────────
        required_entities = []
        for item in entities_data:
            if not isinstance(item, dict) or "name" not in item:
                continue
            required_entities.append(
                EntityRequirement(
                    name=item["name"],
                    description=item.get("description", ""),
                    required=item.get("required", True),
                    default_preference_key=item.get("default_preference_key"),
                )
            )

        # ── Step 4: Compute missing entities ─────────────────────────────
        collected_keys = set(collected.keys())
        missing_entities = [e for e in required_entities if e.name not in collected_keys]

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
            },
        )

        return RequiredEntitiesResult(
            intent_type=intent_type,
            resolved_tools=resolved_tools,
            required_entities=required_entities,
            missing_entities=missing_entities,
        )

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

        # 2. Get tool catalog from ToolCatalog
        try:
            tools = self._tool_catalog.get_all_tools()
            tool_names = {t.name for t in tools}
        except Exception:
            logger.warning("catalog_unavailable", extra={"component": "planner"})
            tools = []
            tool_names = set()
        registry_version = 0  # ToolCatalog has no versioning (TTL-based refresh)

        # 3. Build prompts
        system_prompt = self._prompt.build_system_prompt()
        user_prompt = self._prompt.build_user_prompt(intent, evidence, tools)

        # 4. Generate plan with fallback hierarchy
        plan, fallback_level = await self._generate_with_fallback(
            system_prompt, user_prompt, intent, registry_version, tool_names
        )

        # 5. Finalize plan (plan_id, intent, plugins, meta with hash)
        plan = self._finalize_plan(plan, intent)

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

    async def _generate_with_fallback(
        self,
        system_prompt: str,
        user_prompt: str,
        intent: Intent,
        registry_version: int,
        tool_ids: set[str],
    ) -> tuple[Plan, int]:
        """4-level fallback hierarchy. Returns (plan, fallback_level)."""

        # Level 1: Primary model
        plan = await self._try_llm_level(
            self._primary_breaker,
            self._primary_model,
            system_prompt,
            user_prompt,
            intent,
            registry_version,
            tool_ids,
            level=1,
        )
        if plan is not None:
            return plan, 1

        # Level 2: Fallback model
        plan = await self._try_llm_level(
            self._fallback_breaker,
            self._fallback_model,
            system_prompt,
            user_prompt,
            intent,
            registry_version,
            tool_ids,
            level=2,
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
    ) -> Plan | None:
        """Try generating a plan via LLM with circuit breaker. Returns None on failure."""
        try:
            raw_output = await breaker.call(
                self._llm.generate,
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

    def _finalize_plan(self, plan: Plan, intent: Intent) -> Plan:
        """Populate plan_id, intent, plugins, and meta.canonical_hash."""
        # Ensure plan_id is set
        if not plan.plan_id or len(plan.plan_id) != 26:
            plan = plan.model_copy(update={"plan_id": str(ulid.new())})

        # Ensure intent is set
        plan = plan.model_copy(update={"intent": intent, "trace_id": intent.trace_id})

        # Populate plugins from graph
        plugins = list({s.uses for s in plan.graph})
        plan = plan.model_copy(update={"plugins": plugins})

        # Enforce dry_run on all steps
        updated_steps = []
        for step in plan.graph:
            if not step.dry_run:
                step = step.model_copy(update={"dry_run": True})
            updated_steps.append(step)
        plan = plan.model_copy(update={"graph": updated_steps})

        # Compute canonical hash
        plan_dict = plan.model_dump(mode="json")
        # Remove meta.canonical_hash before hashing (avoid circular hash)
        meta_for_hash = {
            k: v for k, v in plan_dict.get("meta", {}).items() if k != "canonical_hash"
        }
        hashable_dict = {**plan_dict, "meta": meta_for_hash}
        canonical_hash = compute_plan_hash(hashable_dict)

        meta = plan.meta.model_copy(update={"canonical_hash": canonical_hash})
        plan = plan.model_copy(update={"meta": meta})

        return plan


def create_planner_service(
    context_rag_service: Any,
    tool_catalog: Any,
    plan_service: Any,
    llm_adapter: LLMAdapter | None = None,
) -> PlannerService:
    """Factory function for PlannerService. Reads config from env vars."""
    primary_model = os.environ.get("PLANNER_PRIMARY_MODEL", "claude-sonnet-4-5-20250929")
    fallback_model = os.environ.get("PLANNER_FALLBACK_MODEL", "claude-haiku-4-5-20251001")
    max_output_tokens = int(os.environ.get("PLANNER_MAX_OUTPUT_TOKENS", "4096"))

    if llm_adapter is None:
        llm_adapter = AnthropicAdapter()

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
    )
