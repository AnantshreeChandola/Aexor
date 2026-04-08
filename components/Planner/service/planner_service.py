"""
PlannerService — deterministic plan generation orchestrator.

Coordinates: ContextRAG → ToolCatalog → LLM (with fallbacks) → Validator → Hasher

Reference: LLD SS7
"""

from __future__ import annotations

import json
import logging
import os
import re
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
        fallback_llm_adapter: LLMAdapter | None = None,
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
            "You are an intent analysis engine for a personal assistant. "
            "Given an intent type and already-collected entities (with values), "
            "determine:\n"
            "1. What tool(s) are needed (provider.service format)\n"
            "2. What entities are needed AND whether each is already satisfied "
            "by the collected data\n\n"
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
            "  ]\n"
            "}\n\n"
            "Rules:\n"
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
            "- default_preference_key: user preference key or null\n"
            "- Be MINIMAL — only mark missing=true for what the user truly "
            "still needs to tell us. Think like a smart assistant."
        )

        user_prompt = (
            f"Intent type: {intent_type}\n"
            f"Already collected entities: {json.dumps(collected)}\n\n"
            "Analyze what's needed and what's already satisfied."
        )

        # Inject relevant tool schemas so the LLM uses actual API parameter names
        tool_schemas = self._pre_resolve_tool_schemas(intent_type)
        if tool_schemas:
            user_prompt += (
                f"\n\nAvailable tool schemas:\n{json.dumps(tool_schemas, indent=2)}\n"
                "Use these schemas to determine exact parameter names and types.\n"
            )

        suggested_tools: list[str] = []
        entities_data: list[dict[str, Any]] = []

        try:
            raw = await self._primary_breaker.call(
                self._llm.generate,
                model=self._fallback_model,  # Sonnet for entity inference
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
            # First pass: exact name match
            resolved_tools = [t for t in suggested_tools if t in registered_names]
            missing_tools = [t for t in suggested_tools if t not in registered_names]

            # Second pass: provider-level matching for unresolved tools.
            # The LLM suggests "google.calendar" but the catalog has
            # "GOOGLECALENDAR_CREATE_EVENT" with provider_name "googlecalendar".
            if missing_tools:
                still_missing = []
                for suggested in missing_tools:
                    matches = self._match_provider(suggested, tools)
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
            # Use LLM's semantic assessment of what's missing
            if item.get("missing", False) and entity.required:
                missing_entities.append(entity)

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

        Matching strategy:
        1. Extract the provider prefix from the suggestion (before first ``.``).
        2. Check if any catalog tool's provider_name starts with that prefix
           (after normalization).

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
            # Match: provider starts with prefix, or normalized full matches
            if pn.startswith(prefix) or pn == full_norm:
                matches.append((suggested, tool.name))
                seen_providers.add(pn)

        return matches

    def _pre_resolve_tool_schemas(self, intent_type: str) -> list[dict]:
        """Best-effort: find relevant tool schemas by keyword matching."""
        try:
            all_tools = self._tool_catalog.get_all_tools()
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

        # 2. Get tool catalog — prefer per-user tools (Redis cache), then
        #    global in-memory catalog, then live refresh as last resort.
        tools = []
        try:
            # a) Per-user cached tools (survives container restarts via Redis)
            user_tools = await self._tool_catalog.get_user_tools(intent.user_id)
            if user_tools:
                tools = user_tools
            else:
                # b) Global in-memory catalog (populated at startup)
                tools = self._tool_catalog.get_all_tools()

            # c) If both empty, try live refresh for this user
            if not tools:
                tools = await self._tool_catalog.refresh_user(intent.user_id)

            tool_names = {t.name for t in tools}
        except Exception:
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

    def _finalize_plan(self, plan: Plan, intent: Intent) -> Plan:
        """Populate plan_id, intent, meta, plugins, and canonical_hash."""
        # Always generate a real plan_id (LLM output uses a placeholder)
        plan = plan.model_copy(update={"plan_id": str(ulid.new())})

        # Always set intent and trace_id
        plan = plan.model_copy(update={"intent": intent, "trace_id": intent.trace_id})

        # Always set meta with real timestamp
        now = datetime.now(UTC).isoformat()
        plan = plan.model_copy(
            update={"meta": PlanMeta(created_at=now, canonical_hash="0" * 64)}
        )

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

        # Compute canonical hash — exclude identity/derived fields:
        # plan_id (unique ULID per call) and meta (created_at + canonical_hash)
        plan_dict = plan.model_dump(mode="json")
        hashable_dict = {
            k: v for k, v in plan_dict.items() if k not in ("plan_id", "meta")
        }
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
) -> PlannerService:
    """Factory function for PlannerService. Reads config from env vars."""
    primary_model = os.environ.get("PLANNER_PRIMARY_MODEL", "claude-sonnet-4-5-20250929")
    fallback_model = os.environ.get("PLANNER_FALLBACK_MODEL", "claude-sonnet-4-5-20250929")
    max_output_tokens = int(os.environ.get("PLANNER_MAX_OUTPUT_TOKENS", "4096"))

    # Primary adapter: Anthropic
    if llm_adapter is None:
        llm_adapter = AnthropicAdapter()

    # Fallback adapter: reuse Anthropic
    if fallback_llm_adapter is None:
        fallback_llm_adapter = AnthropicAdapter()

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
    )
