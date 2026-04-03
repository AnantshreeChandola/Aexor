"""
ExecuteOrchestrator Service

Core orchestration: DAG resolution, step dispatch, parallel grouping,
idempotency, compensation, spawning, and outcome assembly.

Reference: LLD.md Sections 7.1-7.7
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from shared.schemas.metrics import PlanMetrics
from shared.schemas.outcome import PlanOutcome
from shared.schemas.plan import Plan, PlanStep

from ..adapters.dag_resolver import DAGResolver
from ..adapters.idempotency import IdempotencyAdapter
from ..adapters.llm_client import LLMClient
from ..adapters.mcp_client import MCPClient
from ..adapters.resource_lock import ResourceLockAdapter
from ..adapters.retry import RetryPolicy
from ..adapters.template_resolver import TemplateResolver
from ..domain.models import (
    ApprovalTokenError,
    CompensationRecord,
    CycleDetectedError,
    ExecuteRequest,
    ExecutionContext,
    MCPInvocationError,
    PlanExpiredError,
    RecoveryExhaustedError,
    SpawnDeniedError,
    StepExecutionError,
    StepResult,
)

logger = logging.getLogger(__name__)

# Approval token secret key (should be env-configured in production)
_APPROVAL_TOKEN_SECRET = "approval-gate-secret"
_MAX_RECOVERY_ACTIONS = 5


class ExecuteService:
    """Pure agentic plan execution engine."""

    def __init__(
        self,
        policy_service: Any,
        registry_service: Any,
        plan_writer_service: Any,
        mcp_client: MCPClient,
        llm_client: LLMClient,
        credential_vault: Any,
        idempotency: IdempotencyAdapter,
        resource_lock: ResourceLockAdapter,
        dag_resolver: DAGResolver,
        template_resolver: TemplateResolver,
        retry_policy: RetryPolicy,
    ) -> None:
        self._policy = policy_service
        self._registry = registry_service
        self._plan_writer = plan_writer_service
        self._mcp = mcp_client
        self._llm = llm_client
        self._credential_vault = credential_vault
        self._idempotency = idempotency
        self._resource_lock = resource_lock
        self._dag_resolver = dag_resolver
        self._template_resolver = template_resolver
        self._retry = retry_policy

    async def execute_plan(self, request: ExecuteRequest) -> PlanOutcome:
        """Execute an approved plan end-to-end."""
        start = time.monotonic()
        now_iso = datetime.now(UTC).isoformat()
        ctx = ExecutionContext(
            plan=request.plan,
            user_id=request.user_id,
            trace_id=request.trace_id,
        )

        logger.info(
            "execution_started",
            extra={
                "plan_id": request.plan.plan_id,
                "user_id": request.user_id,
                "trace_id": request.trace_id,
                "total_steps": len(request.plan.graph),
                "step_types": [s.type for s in request.plan.graph],
            },
        )

        try:
            # Phase 1: Pre-execution verification
            self._validate_approval_token(request.approval_token, request.plan)
            self._check_plan_ttl(request.plan)

            # Phase 2: DAG resolution
            levels = self._dag_resolver.resolve(request.plan.graph)

            # Phase 3: Level-by-level execution
            for level in levels:
                await self._execute_level(level, ctx, request)

            # Phase 4: Build success outcome
            outcome = self._build_outcome(ctx, now_iso)

        except (
            ApprovalTokenError,
            PlanExpiredError,
            CycleDetectedError,
        ) as exc:
            outcome = self._build_error_outcome(exc, now_iso, ctx)
        except (StepExecutionError, RecoveryExhaustedError) as exc:
            outcome = self._build_error_outcome(exc, now_iso, ctx)
        except Exception as exc:
            outcome = self._build_error_outcome(exc, now_iso, ctx)

        # Phase 5: Persist outcome (non-fatal)
        await self._persist_outcome(request, outcome, start)

        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "execution_completed",
            extra={
                "plan_id": request.plan.plan_id,
                "success": outcome.success,
                "total_steps": outcome.total_steps,
                "duration_ms": duration_ms,
                "plan_revision": outcome.plan_revision,
            },
        )

        return outcome

    # ------------------------------------------------------------------
    # Pre-execution verification
    # ------------------------------------------------------------------

    def _validate_approval_token(self, token: str, plan: Plan) -> None:
        """Validate JWT approval token."""
        try:
            payload = jwt.decode(
                token,
                _APPROVAL_TOKEN_SECRET,
                algorithms=["HS256"],
            )
            if payload.get("plan_id") != plan.plan_id:
                raise ApprovalTokenError("plan_id mismatch")
        except JWTError as exc:
            raise ApprovalTokenError(str(exc))

    def _check_plan_ttl(self, plan: Plan) -> None:
        """Check if plan has expired based on TTL."""
        from datetime import datetime as dt

        try:
            created = dt.fromisoformat(plan.meta.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            elapsed = (now - created).total_seconds()
            if elapsed > plan.constraints.ttl_s:
                raise PlanExpiredError(plan.plan_id, plan.constraints.ttl_s)
        except PlanExpiredError:
            raise
        except Exception:
            pass  # If we cannot parse, allow execution

    # ------------------------------------------------------------------
    # Level execution
    # ------------------------------------------------------------------

    async def _execute_level(
        self,
        level: list[PlanStep],
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> None:
        """Execute a parallel level of steps."""
        executable = [s for s in level if not self._should_skip(s, ctx, request)]
        if not executable:
            return

        results = await asyncio.gather(
            *[self._execute_step(s, ctx, request) for s in executable],
            return_exceptions=True,
        )

        for step, result in zip(executable, results, strict=True):
            if isinstance(result, Exception):
                await self._handle_step_failure(step, result, ctx, request)
            else:
                ctx.step_results[step.step] = result

    def _should_skip(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> bool:
        """Check if step should be skipped (preview_only)."""
        if step.execute_mode == "preview_only":
            cached = None
            if request.preview_state:
                cached = request.preview_state.get(str(step.step))
            ctx.step_results[step.step] = StepResult(
                step=step.step,
                status="skipped",
                result=cached,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Step dispatch
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> StepResult:
        """Dispatch a single step by type."""
        start = time.monotonic()

        logger.info(
            "step_dispatched",
            extra={
                "plan_id": ctx.plan.plan_id,
                "step": step.step,
                "role": step.role,
                "type": step.type,
                "trust_level": step.trust_level,
                "uses": step.uses,
            },
        )

        if step.type == "api":
            result = await self._execute_api_step(step, ctx, request)
        elif step.type == "llm_reasoning":
            result = await self._execute_reasoning_step(step, ctx, request)
        elif step.type == "policy_check":
            result = await self._execute_policy_check(step, ctx)
        else:
            raise StepExecutionError(step.step, f"Unknown type: {step.type}")

        latency_ms = int((time.monotonic() - start) * 1000)
        step_result = StepResult(
            step=step.step,
            status="completed",
            result=result,
            latency_ms=latency_ms,
        )

        logger.info(
            "step_completed",
            extra={
                "plan_id": ctx.plan.plan_id,
                "step": step.step,
                "role": step.role,
                "latency_ms": latency_ms,
                "status": "completed",
            },
        )

        return step_result

    async def _execute_api_step(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> dict[str, Any]:
        """Execute an API step via MCP with idempotency and locking."""
        # 1. Resolve template args
        resolved_args = self._template_resolver.resolve(
            step.args, ctx.step_results, request.preview_state
        )

        # 2. Idempotency check (Booker only)
        idem_key: str | None = None
        if step.role == "Booker":
            idem_key = self._idempotency.build_key(
                user_id=request.user_id,
                integration_id=step.uses,
                plan_id=ctx.plan.plan_id,
                step=step.step,
                call=step.call,
                args=resolved_args,
            )
            cached = await self._idempotency.check_and_claim(idem_key, request.trace_id)
            if cached and cached.result:
                return cached.result

        # 3. Resource lock (Booker only)
        lock_key: str | None = None
        if step.role == "Booker":
            lock_key = f"lock:resource:{request.user_id}:{step.uses}:{step.call}"
            await self._resource_lock.acquire(lock_key)

        try:
            # 4. Decrypt credentials
            plaintext_cred: str | None = None
            cred_id = request.integration_credentials.get(step.uses)
            if cred_id:
                plaintext_cred = await self._credential_vault.decrypt(cred_id, request.user_id)
                logger.debug(
                    "credential_decrypted",
                    extra={
                        "plan_id": ctx.plan.plan_id,
                        "step": step.step,
                        "tool_id": step.uses,
                    },
                )

            # 5. Resolve tool info from PluginRegistry
            tool = await self._registry.get_tool(step.uses)
            mcp_server = getattr(tool, "mcp_server", step.uses)
            op = tool.operations.get(step.call)
            mcp_tool = getattr(op, "n8n_node", step.call) if op else step.call

            # 6. MCP invocation with retry
            result = await self._retry.execute_with_retry(
                lambda: self._mcp.invoke(
                    server=mcp_server,
                    tool=mcp_tool,
                    args=resolved_args,
                    credentials=({"token": plaintext_cred} if plaintext_cred else None),
                    timeout_s=step.timeout_s,
                ),
                step,
                plan_id=ctx.plan.plan_id,
            )

            # 7. Zero credential
            plaintext_cred = None

            # 8. Record compensation (Booker only)
            if step.role == "Booker" and op:
                comp_op = getattr(op, "compensation", None)
                ctx.compensation_stack.append(
                    CompensationRecord(
                        step=step.step,
                        tool_id=step.uses,
                        operation=step.call,
                        result=result,
                        compensation_operation=comp_op,
                    )
                )

            # 9. Mark idempotency succeeded (Booker only)
            if idem_key:
                await self._idempotency.mark_succeeded(idem_key, result)

            return result

        except Exception:
            if idem_key:
                await self._idempotency.mark_failed(idem_key, "execution_failed")
            raise
        finally:
            if lock_key:
                await self._resource_lock.release(lock_key)

    async def _execute_reasoning_step(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> dict[str, Any]:
        """Execute an LLM reasoning step with trust enforcement."""
        # 1. Gather context from context_from steps
        context = [
            {"step": ref, "result": ctx.step_results[ref].result}
            for ref in step.context_from
            if ref in ctx.step_results
        ]

        # 2. Dispatch with trust tier
        trust = step.trust_level or "untrusted_input"
        response = await self._llm.reason(
            config=step.reasoning_config,
            context=context,
            trust_level=trust,
        )

        # 3. Tier 2 + can_spawn: handle spawn requests
        if trust == "trusted" and step.can_spawn:
            spawn_reqs = response.get("spawn_requests", [])
            for spawn_req in spawn_reqs:
                await self._handle_spawn(spawn_req, step, ctx, request)

        return response

    async def _execute_policy_check(self, step: PlanStep, ctx: ExecutionContext) -> dict[str, Any]:
        """Execute a policy evaluation step."""
        from components.PolicyEngine.domain.models import SpawnRequest

        spawn_req = SpawnRequest(
            plan_id=ctx.plan.plan_id,
            plan_revision=max(ctx.plan_revision, 1),
            spawning_step=step.step,
            proposed_steps=[step.model_dump()],
            current_step_count=len(ctx.plan.graph) + len(ctx.spawned_steps),
            plan_plugins=ctx.plan.plugins,
            policy_ref=step.policy_ref,
        )
        decision = await self._policy.evaluate_spawn(spawn_req)
        return decision.model_dump()

    # ------------------------------------------------------------------
    # Spawn handling
    # ------------------------------------------------------------------

    async def _handle_spawn(
        self,
        spawn_req: dict[str, Any],
        parent: PlanStep,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> None:
        """Handle a spawn request from a Tier 2 Reasoner."""
        from components.PolicyEngine.domain.models import SpawnRequest

        logger.info(
            "spawn_requested",
            extra={
                "plan_id": ctx.plan.plan_id,
                "parent_step": parent.step,
                "proposed_role": spawn_req.get("role"),
                "proposed_tool": spawn_req.get("uses"),
            },
        )

        # 1. Per-step spawn limit
        parent_count = sum(1 for s in ctx.spawned_steps if s.spawned_by == parent.step)
        limit = parent.max_spawned_steps or 3
        if parent_count >= limit:
            raise SpawnDeniedError("spawn limit exceeded", [])

        # 2. Plan-level limit
        total = len(ctx.plan.graph) + len(ctx.spawned_steps)
        if total >= 100:
            raise SpawnDeniedError("plan step limit (100) exceeded", [])

        # 3. Build spawned step
        max_existing = max(s.step for s in ctx.plan.graph)
        new_num = max_existing + len(ctx.spawned_steps) + 1
        spawned = PlanStep(
            step=new_num,
            type=spawn_req.get("step_type", "api"),
            role=spawn_req["role"],
            uses=spawn_req["uses"],
            call=spawn_req["call"],
            args=spawn_req.get("args", {}),
            spawned_by=parent.step,
            can_spawn=False,
            mode="interactive",
            after=[parent.step],
        )

        # 4. Inject gate_id for Booker
        if spawned.role == "Booker":
            spawned.gate_id = f"gate-spawn-{new_num}"

        # 5. PolicyEngine evaluation
        policy_req = SpawnRequest(
            plan_id=ctx.plan.plan_id,
            plan_revision=max(ctx.plan_revision, 1),
            spawning_step=parent.step,
            proposed_steps=[spawned.model_dump()],
            current_step_count=total + 1,
            plan_plugins=ctx.plan.plugins,
            policy_ref=parent.policy_ref,
        )
        decision = await self._policy.evaluate_spawn(policy_req)

        if not decision.allowed:
            logger.warning(
                "spawn_denied",
                extra={
                    "plan_id": ctx.plan.plan_id,
                    "parent_step": parent.step,
                    "reason": decision.reason,
                    "violations": decision.violations,
                },
            )
            raise SpawnDeniedError(decision.reason, decision.violations)

        # 6. Create attestation
        ctx.plan_revision += 1
        attestation = self._create_attestation(ctx, parent, spawned, decision)
        ctx.attestations.append(attestation)
        ctx.spawned_steps.append(spawned)

        logger.info(
            "spawn_approved",
            extra={
                "plan_id": ctx.plan.plan_id,
                "spawned_step": new_num,
                "attestation_id": attestation.attestation_id,
                "plan_revision": ctx.plan_revision,
            },
        )

        # 7. Execute spawned step (if not gated)
        if not decision.requires_approval and spawned.gate_id is None:
            result = await self._execute_step(spawned, ctx, request)
            ctx.step_results[spawned.step] = result

    def _create_attestation(
        self,
        ctx: ExecutionContext,
        parent: PlanStep,
        spawned: PlanStep,
        decision: Any,
    ) -> Any:
        """Create a PolicyAttestation record."""
        import ulid

        from shared.schemas.policy import PolicyAttestation

        reason = decision.reason if hasattr(decision, "reason") else ""
        policy_id = "unknown"
        if "policy" in reason:
            parts = reason.split("'")
            if len(parts) >= 2:
                policy_id = parts[1]

        return PolicyAttestation(
            attestation_id=str(ulid.new()),
            plan_id=ctx.plan.plan_id,
            plan_revision=ctx.plan_revision,
            spawned_by_step=parent.step,
            new_steps=[spawned.model_dump()],
            policy_id=policy_id,
            policy_version=max(ctx.plan.constraints.policy_version, 1),
            decision=decision,
            attested_at=datetime.now(UTC).isoformat(),
        )

    # ------------------------------------------------------------------
    # Failure recovery
    # ------------------------------------------------------------------

    async def _handle_step_failure(
        self,
        step: PlanStep,
        error: Exception,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> None:
        """Handle a step failure with optional Reasoner recovery."""
        logger.warning(
            "step_failed",
            extra={
                "plan_id": ctx.plan.plan_id,
                "step": step.step,
                "role": step.role,
                "error_type": type(error).__name__,
                "retries": getattr(error, "retries", 0),
            },
        )

        reasoner = self._find_recovery_reasoner(ctx.plan.graph)

        if reasoner is None:
            await self._run_compensation(ctx)
            raise StepExecutionError(step.step, str(error))

        if ctx.recovery_action_count >= _MAX_RECOVERY_ACTIONS:
            await self._run_compensation(ctx)
            raise RecoveryExhaustedError(step.step, ctx.recovery_action_count)

        # Record failure and route to Reasoner
        ctx.step_results[step.step] = StepResult(
            step=step.step,
            status="failed",
            error={
                "failed_step": step.step,
                "error_type": type(error).__name__,
                "error_details": str(error),
                "step_role": step.role,
                "step_tool": step.uses,
            },
        )
        ctx.recovery_action_count += 1

        try:
            await self._execute_step(reasoner, ctx, request)
        except Exception:
            await self._run_compensation(ctx)
            raise StepExecutionError(step.step, str(error))

    def _find_recovery_reasoner(self, graph: list[PlanStep]) -> PlanStep | None:
        """Find nearest Reasoner with can_spawn in graph."""
        for step in graph:
            if step.role == "Reasoner" and step.can_spawn:
                return step
        return None

    # ------------------------------------------------------------------
    # Compensation (Saga)
    # ------------------------------------------------------------------

    async def _run_compensation(self, ctx: ExecutionContext) -> None:
        """Execute compensation in reverse order."""
        for record in reversed(ctx.compensation_stack):
            if record.compensation_operation is None:
                continue
            try:
                await self._mcp.invoke(
                    server=record.tool_id,
                    tool=record.compensation_operation,
                    args=record.compensation_args or {},
                    timeout_s=30,
                )
                logger.info(
                    "compensation_executed",
                    extra={
                        "plan_id": ctx.plan.plan_id,
                        "step": record.step,
                        "operation": record.compensation_operation,
                    },
                )
            except Exception as exc:
                logger.error(
                    "compensation_failed",
                    extra={
                        "plan_id": ctx.plan.plan_id,
                        "step": record.step,
                        "operation": record.compensation_operation,
                        "error": str(exc),
                    },
                )

    # ------------------------------------------------------------------
    # Outcome building
    # ------------------------------------------------------------------

    def _build_outcome(
        self,
        ctx: ExecutionContext,
        start_iso: str,
    ) -> PlanOutcome:
        """Build a successful PlanOutcome."""
        end_iso = datetime.now(UTC).isoformat()
        total = len(ctx.plan.graph) + len(ctx.spawned_steps)

        context_data: dict[str, Any] = {}
        for step_num, sr in ctx.step_results.items():
            context_data[f"step_{step_num}"] = {
                "status": sr.status,
                "latency_ms": sr.latency_ms,
            }

        final_graph = None
        if ctx.spawned_steps:
            all_steps = ctx.plan.graph + ctx.spawned_steps
            final_graph = {"steps": [s.model_dump() for s in all_steps]}

        return PlanOutcome(
            success=True,
            execution_start=start_iso,
            execution_end=end_iso,
            total_steps=total,
            context_data=context_data,
            final_graph_json=final_graph,
            plan_revision=ctx.plan_revision,
            policy_attestations=ctx.attestations,
        )

    def _build_error_outcome(
        self,
        error: Exception,
        start_iso: str,
        ctx: ExecutionContext | None = None,
    ) -> PlanOutcome:
        """Build an error PlanOutcome."""
        end_iso = datetime.now(UTC).isoformat()
        error_type = self._classify_error(error)
        failed_step = getattr(error, "step", None)
        total = 0
        revision = 0
        attestations = []
        if ctx:
            total = len(ctx.plan.graph) + len(ctx.spawned_steps)
            revision = ctx.plan_revision
            attestations = ctx.attestations

        return PlanOutcome(
            success=False,
            error_type=error_type,
            error_details={"message": str(error)},
            execution_start=start_iso,
            execution_end=end_iso,
            total_steps=total,
            failed_step=failed_step,
            plan_revision=revision,
            policy_attestations=attestations,
        )

    def _classify_error(self, error: Exception) -> str:
        """Map exception type to error_type string."""
        mapping = {
            ApprovalTokenError: "token_expired",
            PlanExpiredError: "plan_expired",
            CycleDetectedError: "cycle_detected",
            StepExecutionError: "step_failure",
            RecoveryExhaustedError: "recovery_exhausted",
            MCPInvocationError: "mcp_error",
            SpawnDeniedError: "spawn_denied",
        }
        for cls, name in mapping.items():
            if isinstance(error, cls):
                return name
        return "internal_error"

    # ------------------------------------------------------------------
    # Persistence (non-fatal)
    # ------------------------------------------------------------------

    async def _persist_outcome(
        self,
        request: ExecuteRequest,
        outcome: PlanOutcome,
        start: float,
    ) -> None:
        """Persist outcome via PlanWriter (non-fatal)."""
        try:
            execute_ms = int((time.monotonic() - start) * 1000)
            metrics = PlanMetrics(execute_latency_ms=execute_ms)
            try:
                user_uuid = UUID(request.user_id)
            except ValueError:
                user_uuid = UUID("00000000-0000-0000-0000-000000000000")
            await self._plan_writer.persist_outcome(
                user_id=user_uuid,
                plan=request.plan,
                outcome=outcome,
                metrics=metrics,
            )
        except Exception as exc:
            logger.warning(
                "persist_outcome_failed",
                extra={
                    "plan_id": request.plan.plan_id,
                    "error": str(exc),
                },
            )


def create_execute_service(
    policy_service: Any,
    registry_service: Any,
    plan_writer_service: Any,
    mcp_client: MCPClient,
    llm_client: LLMClient,
    credential_vault: Any,
    redis_client: Any,
) -> ExecuteService:
    """Create ExecuteService with all dependencies.

    Called once during app lifespan startup in shared/app.py.
    """
    idempotency = IdempotencyAdapter(redis_client)
    resource_lock = ResourceLockAdapter(redis_client)
    dag_resolver = DAGResolver()
    template_resolver = TemplateResolver()
    retry_policy = RetryPolicy()

    return ExecuteService(
        policy_service=policy_service,
        registry_service=registry_service,
        plan_writer_service=plan_writer_service,
        mcp_client=mcp_client,
        llm_client=llm_client,
        credential_vault=credential_vault,
        idempotency=idempotency,
        resource_lock=resource_lock,
        dag_resolver=dag_resolver,
        template_resolver=template_resolver,
        retry_policy=retry_policy,
    )
