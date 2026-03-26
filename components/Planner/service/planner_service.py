"""
PlannerService — deterministic plan generation orchestrator.

Coordinates: ContextRAG → PluginRegistry → LLM (with fallbacks) → Validator → Hasher → Signer

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
    LLMCallError,
    PlannerResult,
    PlanValidationError,
)
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan, PlanConstraints, PlanMeta, PlanStep

logger = logging.getLogger(__name__)


class PlannerService:
    """Orchestrates deterministic plan generation with 4-level fallback."""

    def __init__(
        self,
        context_rag_service: Any,
        registry_service: Any,
        signer_service: Any,
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
        self._registry = registry_service
        self._signer = signer_service
        self._plan_service = plan_service
        self._llm = llm_adapter
        self._prompt = prompt_builder
        self._validator = validator
        self._primary_breaker = primary_breaker
        self._fallback_breaker = fallback_breaker
        self._primary_model = primary_model
        self._fallback_model = fallback_model
        self._max_output_tokens = max_output_tokens

    async def generate_plan(self, intent: Intent) -> PlannerResult:
        """Generate a validated, signed execution plan."""
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

        # 2. Get tool catalog from PluginRegistry
        try:
            catalog = await self._registry.list_catalog()
            registry_version = catalog.registry_version
            tool_ids = {t.tool_id for t in catalog.tools}
        except Exception:
            logger.warning("registry_unavailable", extra={"component": "planner"})
            catalog = None
            registry_version = 0
            tool_ids = set()

        # 3. Build prompts
        system_prompt = self._prompt.build_system_prompt()
        user_prompt = self._prompt.build_user_prompt(intent, evidence, catalog)

        # 4. Generate plan with fallback hierarchy
        plan, fallback_level = await self._generate_with_fallback(
            system_prompt, user_prompt, intent, registry_version, tool_ids
        )

        # 5. Finalize plan (plan_id, intent, plugins, meta with hash)
        plan = self._finalize_plan(plan, intent)

        # 6. Sign via Signer
        plan_dict = plan.model_dump(mode="json")
        sig = await self._signer.sign_plan(plan_dict)
        from shared.schemas.signature import Signature

        signature = Signature(
            algo=sig.algo,
            signer=sig.signer,
            signature=sig.signature,
            pubkey_id=sig.pubkey_id,
            plan_hash=sig.plan_hash,
        )

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
            signature=signature,
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
    registry_service: Any,
    signer_service: Any,
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
    validator = PlanValidator(registry_service=registry_service)
    primary_breaker = CircuitBreaker(model_name=primary_model)
    fallback_breaker = CircuitBreaker(model_name=fallback_model)

    return PlannerService(
        context_rag_service=context_rag_service,
        registry_service=registry_service,
        signer_service=signer_service,
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
