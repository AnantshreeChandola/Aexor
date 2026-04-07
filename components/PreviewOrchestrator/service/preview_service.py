"""
PreviewOrchestrator Service

Core orchestration: DAG resolution, step classification, parallel
dispatch via MCP dry-run, template resolution, preview state caching.

Reference: LLD.md Sections 9.1-9.4
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from components.ExecuteOrchestrator.adapters.dag_resolver import DAGResolver
from components.ExecuteOrchestrator.adapters.mcp_client import MCPClient
from components.ExecuteOrchestrator.adapters.template_resolver import (
    TemplateResolver,
)
from components.ExecuteOrchestrator.domain.models import (
    CycleDetectedError,
    StepResult,
)
from shared.schemas.plan import PlanStep

from ..adapters.preview_cache import PreviewCacheAdapter
from ..adapters.previewability_checker import PreviewabilityChecker
from ..domain.models import (
    PreviewError,
    PreviewRequest,
    PreviewResult,
    PreviewStepResult,
)

logger = logging.getLogger(__name__)


class PreviewService:
    """Read-only plan preview engine."""

    def __init__(
        self,
        dag_resolver: DAGResolver,
        template_resolver: TemplateResolver,
        mcp_client: MCPClient,
        checker: PreviewabilityChecker,
        cache: PreviewCacheAdapter,
        tool_catalog: Any,
    ) -> None:
        self._dag_resolver = dag_resolver
        self._template_resolver = template_resolver
        self._mcp = mcp_client
        self._checker = checker
        self._cache = cache
        self._tool_catalog = tool_catalog

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def preview(self, request: PreviewRequest) -> PreviewResult:
        """Execute plan preview in read-only mode.

        Flow:
            1. Resolve DAG levels via DAGResolver
            2. For each level, dispatch previewable steps in parallel
            3. Cache preview state in Redis (best-effort)
            4. Return PreviewResult
        """
        start = time.monotonic()
        plan = request.plan

        logger.info(
            "preview_started",
            extra={
                "plan_id": plan.plan_id,
                "user_id": request.user_id,
                "trace_id": request.trace_id,
                "total_steps": len(plan.graph),
            },
        )

        # 1. Resolve DAG levels
        try:
            levels = self._dag_resolver.resolve(plan.graph)
        except CycleDetectedError as exc:
            raise PreviewError(f"DAG cycle: {exc}") from exc

        # 2. Build step status tracking
        step_results: dict[int, PreviewStepResult] = {}
        deferred_steps: set[int] = set()
        failed_steps: set[int] = set()

        # 3. Process each level
        for level in levels:
            await self._process_level(level, request, step_results, deferred_steps, failed_steps)

        # 4. Determine result flags
        has_failures = len(failed_steps) > 0
        # can_execute is False only when ALL previewable steps failed
        # (not just deferred). Per spec edge case: zero previewable
        # steps -> can_execute=True (approval still needed).
        has_any_completed = any(sr.status == "completed" for sr in step_results.values())
        all_deferred_only = all(sr.status == "deferred" for sr in step_results.values())
        # can_execute: True if some completed, or all deferred (nothing failed)
        can_execute = has_any_completed or all_deferred_only

        # 5. Cache preview state (best-effort)
        cache_key = await self._cache_state(plan.plan_id, request.user_id, step_results)

        # 6. Build and return PreviewResult
        duration_ms = int((time.monotonic() - start) * 1000)
        result = PreviewResult(
            plan_id=plan.plan_id,
            normalized={
                "steps": [
                    sr.model_dump() for sr in sorted(step_results.values(), key=lambda s: s.step)
                ]
            },
            source="preview",
            can_execute=can_execute,
            partial=has_failures,
            cached_state_key=cache_key,
            evidence=[],
        )

        completed = sum(1 for sr in step_results.values() if sr.status == "completed")
        deferred_count = sum(1 for sr in step_results.values() if sr.status == "deferred")
        failed_count = sum(1 for sr in step_results.values() if sr.status in ("failed", "skipped"))

        logger.info(
            "preview_completed",
            extra={
                "plan_id": plan.plan_id,
                "total_steps": len(plan.graph),
                "completed": completed,
                "deferred": deferred_count,
                "failed": failed_count,
                "partial": result.partial,
                "duration_ms": duration_ms,
            },
        )

        return result

    async def get_preview_state(
        self,
        plan_id: str,
        user_id: str,
    ) -> dict[int, PreviewStepResult] | None:
        """Retrieve cached preview state for downstream consumers."""
        raw = await self._cache.retrieve(plan_id, user_id)
        if raw is None:
            return None
        # Reconstruct PreviewStepResult models from cached dicts
        return {step_num: PreviewStepResult.model_validate(data) for step_num, data in raw.items()}

    # ------------------------------------------------------------------
    # Level processing
    # ------------------------------------------------------------------

    async def _process_level(
        self,
        level: list[PlanStep],
        request: PreviewRequest,
        step_results: dict[int, PreviewStepResult],
        deferred_steps: set[int],
        failed_steps: set[int],
    ) -> None:
        """Process one DAG level: classify then dispatch."""
        to_dispatch: list[PlanStep] = []

        for step in level:
            classification, reason = await self._classify_step(step, deferred_steps, failed_steps)

            if classification == "deferred":
                result_data = None
                if reason == "write_action":
                    result_data = {"summary": self._build_action_summary(step)}
                step_results[step.step] = PreviewStepResult(
                    step=step.step, status="deferred", result=result_data, reason=reason
                )
                deferred_steps.add(step.step)
                logger.info(
                    "step_deferred",
                    extra={
                        "plan_id": request.plan.plan_id,
                        "step": step.step,
                        "reason": reason,
                    },
                )
            elif classification == "skipped":
                step_results[step.step] = PreviewStepResult(
                    step=step.step, status="skipped", reason=reason
                )
                failed_steps.add(step.step)
                logger.info(
                    "step_skipped",
                    extra={
                        "plan_id": request.plan.plan_id,
                        "step": step.step,
                        "reason": reason,
                    },
                )
            else:
                to_dispatch.append(step)

        if not to_dispatch:
            return

        # Parallel dispatch via asyncio.gather
        outcomes = await asyncio.gather(
            *[self._dispatch_step(step, request, step_results) for step in to_dispatch],
            return_exceptions=True,
        )

        for step, outcome in zip(to_dispatch, outcomes, strict=True):
            if isinstance(outcome, Exception):
                step_results[step.step] = PreviewStepResult(
                    step=step.step,
                    status="failed",
                    error={
                        "error_type": type(outcome).__name__,
                        "message": str(outcome),
                    },
                )
                failed_steps.add(step.step)
                logger.warning(
                    "step_failed",
                    extra={
                        "plan_id": request.plan.plan_id,
                        "step": step.step,
                        "error_type": type(outcome).__name__,
                    },
                )
            else:
                step_results[step.step] = outcome

    # ------------------------------------------------------------------
    # Step classification (LLD Section 9.2 priority order)
    # ------------------------------------------------------------------

    async def _classify_step(
        self,
        step: PlanStep,
        deferred_steps: set[int],
        failed_steps: set[int],
    ) -> tuple[str, str | None]:
        """Classify a step into dispatch/deferred/skipped.

        Returns (classification, reason).
        Priority order per LLD Section 9.2:
            1. Dependency on deferred -> deferred
            2. Dependency on failed -> skipped
            3. Non-API type -> deferred
            4. gate_id set -> deferred
            5. Not previewable -> deferred
            6. Otherwise -> dispatch
        """
        # 1. Dependency cascade: deferred
        for dep in step.after:
            if dep in deferred_steps:
                return ("deferred", "dependency_deferred")

        # 2. Dependency cascade: failed
        for dep in step.after:
            if dep in failed_steps:
                return ("skipped", "dependency_failed")

        # 3. Non-API step types
        if step.type in ("llm_reasoning", "policy_check"):
            return ("deferred", step.type)

        # 4. Gated steps
        if step.gate_id is not None:
            return ("deferred", "gated")

        # 5. Check previewability via ToolCatalog
        previewable = await self._checker.is_previewable(step.uses, step.call)
        if not previewable:
            return ("deferred", "non_previewable")

        # 5.5 Write actions — defer (don't execute mutations in preview)
        if self._checker.is_write_action(step.uses, step.call):
            return ("deferred", "write_action")

        # 6. Previewable -- dispatch via MCP
        return ("dispatch", None)

    # ------------------------------------------------------------------
    # MCP dispatch (read-only)
    # ------------------------------------------------------------------

    async def _dispatch_step(
        self,
        step: PlanStep,
        request: PreviewRequest,
        step_results: dict[int, PreviewStepResult],
    ) -> PreviewStepResult:
        """Dispatch a single step via MCP in read-only mode."""
        start = time.monotonic()

        logger.info(
            "step_dispatched",
            extra={
                "plan_id": request.plan.plan_id,
                "step": step.step,
                "role": step.role,
                "uses": step.uses,
                "call": step.call,
            },
        )

        # Convert completed PreviewStepResults to StepResult for
        # TemplateResolver compatibility
        exec_results = self._to_exec_step_results(step_results)

        # Resolve template args
        resolved_args = self._template_resolver.resolve(step.args, exec_results)

        # Add dry_run flag
        resolved_args["dry_run"] = True

        # Resolve tool info from ToolCatalog
        tool_def = self._tool_catalog.get_tool(step.uses)
        mcp_server = tool_def.server_name if tool_def else step.uses
        mcp_tool = step.uses  # In MCP model, tool name IS the operation

        # Invoke MCP — pass user_id so Composio resolves the per-user account
        result = await self._mcp.invoke(
            server=mcp_server,
            tool=mcp_tool,
            args=resolved_args,
            credentials={"user_id": request.user_id},
            timeout_s=step.timeout_s,
        )

        latency_ms = int((time.monotonic() - start) * 1000)

        logger.info(
            "step_completed",
            extra={
                "plan_id": request.plan.plan_id,
                "step": step.step,
                "latency_ms": latency_ms,
                "status": "completed",
            },
        )

        return PreviewStepResult(
            step=step.step,
            status="completed",
            result=result,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_action_summary(step: PlanStep) -> str:
        """Build human-readable summary for a deferred write action."""
        parts = [f"Action: {step.uses}"]
        if step.args:
            for key, value in step.args.items():
                if key == "dry_run":
                    continue
                parts.append(f"  {key}: {value}")
        return "\n".join(parts)

    @staticmethod
    def _to_exec_step_results(
        preview_results: dict[int, PreviewStepResult],
    ) -> dict[int, StepResult]:
        """Convert completed preview results to StepResult for TemplateResolver."""
        exec_results: dict[int, StepResult] = {}
        for step_num, psr in preview_results.items():
            if psr.status == "completed" and psr.result is not None:
                exec_results[step_num] = StepResult(
                    step=step_num,
                    status="completed",
                    result=psr.result,
                )
        return exec_results

    async def _cache_state(
        self,
        plan_id: str,
        user_id: str,
        step_results: dict[int, PreviewStepResult],
    ) -> str | None:
        """Cache preview state in Redis (best-effort)."""
        state = {step_num: sr.model_dump() for step_num, sr in step_results.items()}
        return await self._cache.store(plan_id, user_id, state)


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def create_preview_service(
    mcp_client: MCPClient,
    tool_catalog: Any,
    redis_client: Any | None = None,
) -> PreviewService:
    """Create PreviewService with all dependencies.

    Called once during app lifespan startup in shared/app.py.
    """
    ttl_s = int(os.environ.get("PREVIEW_CACHE_TTL_S", "900"))
    dag_resolver = DAGResolver()
    template_resolver = TemplateResolver()
    cache = PreviewCacheAdapter(redis_client, ttl_s=ttl_s)
    checker = PreviewabilityChecker(tool_catalog)

    return PreviewService(
        dag_resolver=dag_resolver,
        template_resolver=template_resolver,
        mcp_client=mcp_client,
        checker=checker,
        cache=cache,
        tool_catalog=tool_catalog,
    )
