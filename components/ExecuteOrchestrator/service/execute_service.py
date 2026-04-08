"""
ExecuteOrchestrator Service

Core orchestration: DAG resolution, step dispatch, parallel grouping,
idempotency, compensation, spawning, and outcome assembly.

Reference: LLD.md Sections 7.1-7.7
"""

from __future__ import annotations

import asyncio
import logging
import os
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
    CycleDetectedError,
    ExecuteRequest,
    ExecutionContext,
    GateApprovalRequired,
    MCPInvocationError,
    PlanExpiredError,
    RecoveryExhaustedError,
    SpawnDeniedError,
    StepExecutionError,
    StepResult,
)

logger = logging.getLogger(__name__)

# Approval token secret key (should be env-configured in production)
_APPROVAL_TOKEN_SECRET = os.environ.get("APPROVAL_TOKEN_SECRET", "approval-gate-secret")
_MAX_RECOVERY_ACTIONS = 5


class ExecuteService:
    """Pure agentic plan execution engine."""

    def __init__(
        self,
        policy_service: Any,
        tool_catalog: Any,
        plan_writer_service: Any,
        mcp_client: MCPClient,
        llm_client: LLMClient,
        credential_vault: Any,
        idempotency: IdempotencyAdapter,
        resource_lock: ResourceLockAdapter,
        dag_resolver: DAGResolver,
        template_resolver: TemplateResolver,
        retry_policy: RetryPolicy,
        tracker: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self._policy = policy_service
        self._tracker = tracker
        self._audit = audit
        self._tool_catalog = tool_catalog
        self._plan_writer = plan_writer_service
        self._mcp = mcp_client
        self._llm = llm_client
        self._credential_vault = credential_vault
        self._idempotency = idempotency
        self._resource_lock = resource_lock
        self._dag_resolver = dag_resolver
        self._template_resolver = template_resolver
        self._retry = retry_policy

    async def _emit_audit(
        self,
        event_type: str,
        plan_id: str,
        user_id: str | None = None,
        trace_id: str | None = None,
        step_number: int | None = None,
        **extra: Any,
    ) -> None:
        """Fire-and-forget audit event. Never raises."""
        if self._audit is None:
            return
        try:
            import ulid as _ulid

            from components.Audit.domain.models import AuditEvent, AuditEventType

            event = AuditEvent(
                event_id=_ulid.new().str,
                event_type=AuditEventType(event_type),
                plan_id=plan_id,
                user_id=user_id,
                trace_id=trace_id,
                step_number=step_number,
                event_data=extra,
            )
            await self._audit.record(event)
        except Exception:
            pass  # fire-and-forget

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

        # Audit: execution_started
        await self._emit_audit(
            "execution_started",
            plan_id=request.plan.plan_id,
            user_id=request.user_id,
            trace_id=request.trace_id,
            total_steps=len(request.plan.graph),
        )

        # Tracker: register execution (non-fatal)
        if self._tracker is not None:
            await self._tracker.register(
                plan_id=request.plan.plan_id,
                user_id=request.user_id,
                trace_id=request.trace_id,
                total_steps=len(request.plan.graph),
            )

        try:
            # Phase 1: Pre-execution verification
            self._validate_approval_token(request.approval_token, request.plan)
            self._check_plan_ttl(request.plan)

            # Phase 2: DAG resolution
            levels = self._dag_resolver.resolve(request.plan.graph)

            # Phase 3: Level-by-level execution
            completed_count = 0
            for level in levels:
                await self._execute_level(level, ctx, request)
                completed_count += len(level)
                # Tracker: report progress (non-fatal)
                if self._tracker is not None:
                    await self._tracker.report_progress(
                        plan_id=request.plan.plan_id,
                        completed_steps=completed_count,
                    )

            # Phase 4: Build success outcome
            outcome = self._build_outcome(ctx, now_iso)

        except GateApprovalRequired:
            raise  # Let the route handler return the gate context to the user
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

        # Phase 5: Audit outcome
        audit_type = "execution_completed" if outcome.success else "execution_failed"
        await self._emit_audit(
            audit_type,
            plan_id=request.plan.plan_id,
            user_id=request.user_id,
            trace_id=request.trace_id,
            success=outcome.success,
            total_steps=outcome.total_steps,
            error_type=outcome.error_type,
        )

        # Phase 6: Persist outcome (non-fatal)
        await self._persist_outcome(request, outcome, start)

        # Tracker: mark completion (non-fatal)
        if self._tracker is not None:
            error_type = outcome.error_type if not outcome.success else None
            error_details = outcome.error_details if not outcome.success else None
            await self._tracker.complete(
                plan_id=request.plan.plan_id,
                success=outcome.success,
                error_type=error_type,
                error_details=error_details,
            )

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
            if isinstance(result, GateApprovalRequired):
                raise result  # Propagate to execute_plan for 202 response
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
        """Check if step should be skipped (preview_only or already completed in a prior gate round)."""
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
        # Multi-gate replay: skip steps whose results were carried from a prior round
        step_key = f"completed_step_{step.step}"
        if request.preview_state and step_key in request.preview_state:
            cached = request.preview_state[step_key]
            ctx.step_results[step.step] = StepResult(
                step=step.step,
                status="completed",
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

        # Gate enforcement: any step with gate_id requires explicit user
        # approval before execution.  The frontend accumulates gate
        # approvals in preview_state[gate_id].  If the gate has not been
        # approved yet, halt execution and return partial results so the
        # user can review context from prior steps and decide.
        if step.gate_id:
            gate_token = request.preview_state.get(step.gate_id) if request.preview_state else None
            if not gate_token:
                partial: dict[str, Any] = {}
                for step_num, sr in ctx.step_results.items():
                    partial[f"step_{step_num}"] = {
                        "status": sr.status,
                        "result": sr.result,
                        "latency_ms": sr.latency_ms,
                    }
                # Resolve templates in args for display (best-effort)
                try:
                    display_args = self._template_resolver.resolve(
                        step.args, ctx.step_results, request.preview_state
                    )
                except Exception:
                    display_args = step.args
                raise GateApprovalRequired(
                    gate_id=step.gate_id,
                    step=step.step,
                    context_data={
                        "role": step.role,
                        "uses": step.uses,
                        "args": display_args,
                    },
                    partial_results=partial,
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

        # Audit: step_completed
        await self._emit_audit(
            "step_completed",
            plan_id=ctx.plan.plan_id,
            user_id=ctx.user_id,
            trace_id=ctx.trace_id,
            step_number=step.step,
            role=step.role,
            status="completed",
            latency_ms=latency_ms,
        )

        return step_result

    async def _execute_api_step(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> dict[str, Any]:
        """Execute an API step via MCP with idempotency and locking."""
        # 0. Pass-through for Resolver/gate-only steps whose tool doesn't
        #    exist in the catalog (they serve only as HITL checkpoints).
        #    Forward the upstream Reasoner's data so downstream Booker steps
        #    can reference either step_N (Reasoner) or step_M (Resolver).
        tool_def = self._tool_catalog.get_tool(step.uses)
        if tool_def is None and step.role == "Resolver":
            gate_choice = (
                request.preview_state.get(step.gate_id) if request.preview_state and step.gate_id else None
            )
            result: dict[str, Any] = {"approved": True, "choice": gate_choice, "step": step.step}
            # Copy Reasoner recommendation fields from context_from steps,
            # falling back to ALL prior completed steps when context_from is empty
            # (the Planner LLM often omits context_from for Resolver steps).
            _skip = {"content", "_raw_content", "spawn_requests", "_note", "_truncated", "_summary"}
            refs = step.context_from if step.context_from else sorted(ctx.step_results.keys())
            for ref in refs:
                if ref in ctx.step_results and ctx.step_results[ref].result:
                    upstream = ctx.step_results[ref].result
                    if isinstance(upstream, dict):
                        for k, v in upstream.items():
                            if k not in _skip:
                                result[k] = v
            # If gate_choice is an ISO datetime (user picked a free slot),
            # overwrite recommended_time so Booker templates use it.
            if gate_choice and len(gate_choice) > 10 and "T" in gate_choice:
                result["recommended_time"] = gate_choice
            return result

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

            # 5. Resolve tool info from ToolCatalog (already fetched above)
            mcp_server = tool_def.server_name if tool_def else step.uses
            mcp_tool = step.uses  # In MCP model, tool name IS the operation

            # 6. MCP invocation with retry
            # Always include user_id so MCPClientAdapter can generate
            # per-user Composio URLs when in Composio mode.
            cred_dict: dict[str, str] = {"user_id": ctx.user_id}
            if plaintext_cred:
                cred_dict["token"] = plaintext_cred

            result = await self._retry.execute_with_retry(
                lambda: self._mcp.invoke(
                    server=mcp_server,
                    tool=mcp_tool,
                    args=resolved_args,
                    credentials=cred_dict,
                    timeout_s=step.timeout_s,
                ),
                step,
                plan_id=ctx.plan.plan_id,
            )

            # 7. Zero credential
            plaintext_cred = None

            # 8. Mark idempotency succeeded (Booker only)
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

    @staticmethod
    def _summarize_context(result: Any, max_bytes: int = 8_000) -> Any:
        """Summarize a step result to fit within the LLM token budget.

        MCP tool responses (e.g. Google Calendar event lists) can be
        enormous.  We recursively search for event-like data and extract
        only scheduling-relevant fields.  Falls back to hard truncation.
        """
        import json as _json

        if result is None:
            return result

        serialized = _json.dumps(result, default=str) if isinstance(result, (dict, list)) else str(result)

        # Small enough already — return as-is
        if len(serialized) <= max_bytes:
            return result

        # ── Try to find calendar events anywhere in the structure ──
        def _find_events(obj: Any) -> list[dict] | None:
            """Recursively search for a list of event-like dicts."""
            if isinstance(obj, list) and obj:
                # Check if this list contains event-like dicts
                if isinstance(obj[0], dict) and any(
                    k in obj[0] for k in ("summary", "start", "end", "eventType", "title")
                ):
                    return obj
                # Search inside list items
                for item in obj[:5]:  # limit depth
                    found = _find_events(item)
                    if found:
                        return found
            elif isinstance(obj, dict):
                # Check common container keys
                for key in ("items", "events", "data", "results", "result"):
                    if key in obj:
                        found = _find_events(obj[key])
                        if found:
                            return found
                # Composio MCP: content[].text contains JSON string
                if "content" in obj and isinstance(obj["content"], list):
                    for item in obj["content"]:
                        if isinstance(item, dict) and "text" in item:
                            try:
                                parsed = _json.loads(item["text"])
                                found = _find_events(parsed)
                                if found:
                                    return found
                            except (ValueError, _json.JSONDecodeError):
                                pass
                # Composio: {"successfull": true, "data": {...}}
                if "data" in obj and isinstance(obj["data"], dict):
                    found = _find_events(obj["data"])
                    if found:
                        return found
            elif isinstance(obj, str):
                try:
                    parsed = _json.loads(obj)
                    return _find_events(parsed)
                except (ValueError, _json.JSONDecodeError):
                    pass
            return None

        events = _find_events(result)
        if events:
            slim = []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                slim.append({
                    "summary": ev.get("summary") or ev.get("title", ""),
                    "start": ev.get("start"),
                    "end": ev.get("end"),
                    "status": ev.get("status"),
                    "id": ev.get("id"),
                })
            return {"events": slim, "_note": f"Summarized {len(events)} events"}

        # ── Generic fallback: hard truncate ──
        return {"_summary": serialized[:max_bytes], "_truncated": True}

    @staticmethod
    def _extract_json_from_content(content: str) -> dict[str, Any] | None:
        """Extract a JSON object from LLM content, tolerating preamble/postamble.

        Tries multiple strategies:
        1. Direct parse of full content
        2. Strip markdown code fences
        3. Find outermost { ... } substring
        """
        import json as _json

        cleaned = content.strip()

        # Strategy 1: direct parse
        try:
            parsed = _json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, _json.JSONDecodeError):
            pass

        # Strategy 2: strip markdown fences
        if "```" in cleaned:
            # Handle ```json\n{...}\n``` or ```\n{...}\n```
            parts = cleaned.split("```")
            for part in parts:
                candidate = part.strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()
                if candidate.startswith("{"):
                    try:
                        parsed = _json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except (ValueError, _json.JSONDecodeError):
                        pass

        # Strategy 3: find outermost { ... } in the text
        start = cleaned.find("{")
        if start != -1:
            # Find matching closing brace
            depth = 0
            for i in range(start, len(cleaned)):
                if cleaned[i] == "{":
                    depth += 1
                elif cleaned[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = _json.loads(cleaned[start : i + 1])
                            if isinstance(parsed, dict):
                                return parsed
                        except (ValueError, _json.JSONDecodeError):
                            pass
                        break

        return None

    # Prefix injected into every Reasoner system prompt to guarantee JSON output.
    _REASONER_JSON_PREFIX = (
        "CRITICAL INSTRUCTION: You MUST return ONLY a valid JSON object. "
        "No prose, no explanation, no markdown fences. The JSON MUST include "
        'ALL of these fields:\n'
        '- "recommended_time": ISO 8601 datetime string (e.g. "2026-04-08T10:00:00")\n'
        '- "has_conflict": boolean (true if any existing event overlaps)\n'
        '- "conflicts": array of strings describing each conflicting event (empty array if none)\n'
        '- "free_slots": array of objects, each with "start" (ISO datetime), "end" (ISO datetime), '
        '"label" (human-readable string, e.g. "11:00 AM - 11:30 AM, Tue Apr 8"). '
        "Include 3-5 nearest free slots during work hours. "
        "Required when has_conflict=true, empty array when has_conflict=false.\n"
        '- "reason": string explaining why this time was chosen\n\n'
        "Example output:\n"
        '{"recommended_time":"2026-04-08T10:00:00","has_conflict":true,'
        '"conflicts":["Team standup 10:00-10:30"],'
        '"free_slots":[{"start":"2026-04-08T11:00:00","end":"2026-04-08T11:30:00",'
        '"label":"11:00 AM - 11:30 AM, Tue Apr 8"}],'
        '"reason":"Conflict with team standup; nearest free slot is 11:00 AM"}\n\n'
    )

    async def _execute_reasoning_step(
        self,
        step: PlanStep,
        ctx: ExecutionContext,
        request: ExecuteRequest,
    ) -> dict[str, Any]:
        """Execute an LLM reasoning step with trust enforcement."""
        # 1. Gather context from context_from steps (summarized to fit token budget).
        #    If context_from is empty, use ALL prior completed steps — the Planner
        #    LLM often omits context_from for Reasoner steps.
        refs = step.context_from if step.context_from else sorted(ctx.step_results.keys())
        context = [
            {"step": ref, "result": self._summarize_context(ctx.step_results[ref].result)}
            for ref in refs
            if ref in ctx.step_results
        ]
        # Always include the intent so the Reasoner knows what to schedule
        intent_data = ctx.plan.intent.model_dump(mode="json") if ctx.plan.intent else {}
        context.append({"step": "intent", "result": {
            "intent": intent_data.get("intent", ""),
            "entities": intent_data.get("entities", {}),
            "tz": intent_data.get("tz", "UTC"),
        }})

        # 2. Wrap system_prompt_ref with explicit JSON instruction.
        #    The Planner LLM may set system_prompt_ref to a short label
        #    (e.g. "calendar_conflict_analyzer") instead of actual instructions.
        config = step.reasoning_config
        if config:
            wrapped_prompt = self._REASONER_JSON_PREFIX + (config.system_prompt_ref or "")
            config = config.model_copy(update={
                "system_prompt_ref": wrapped_prompt,
                "max_tokens": max(config.max_tokens, 1024),
            })

        # 3. Dispatch with trust tier
        trust = step.trust_level or "untrusted_input"
        response = await self._llm.reason(
            config=config or step.reasoning_config,
            context=context,
            trust_level=trust,
        )

        # 4. Tier 2 + can_spawn: handle spawn requests
        if trust == "trusted" and step.can_spawn:
            spawn_reqs = response.get("spawn_requests", [])
            for spawn_req in spawn_reqs:
                await self._handle_spawn(spawn_req, step, ctx, request)

        # 5. Parse structured JSON from LLM content so downstream steps can
        #    reference fields via {{step_N.result.field}} templates.
        content = response.get("content", "")
        if content:
            parsed = self._extract_json_from_content(content)
            if parsed is not None:
                response["_raw_content"] = content
                response.update(parsed)
                logger.info(
                    "reasoner_json_parsed",
                    extra={
                        "plan_id": ctx.plan.plan_id,
                        "step": step.step,
                        "fields": list(parsed.keys()),
                    },
                )
            else:
                logger.warning(
                    "reasoner_json_parse_failed",
                    extra={
                        "plan_id": ctx.plan.plan_id,
                        "step": step.step,
                        "content_preview": content[:500],
                    },
                )

        # 6. Fallback: if no recommended_time was extracted, construct one from
        #    the intent's entities so downstream templates don't fail.
        if "recommended_time" not in response:
            fallback = self._build_reasoner_fallback(ctx.plan.intent)
            if fallback:
                logger.warning(
                    "reasoner_using_intent_fallback",
                    extra={
                        "plan_id": ctx.plan.plan_id,
                        "step": step.step,
                        "fallback_time": fallback.get("recommended_time"),
                    },
                )
                for k, v in fallback.items():
                    if k not in response:
                        response[k] = v

        return response

    @staticmethod
    def _build_reasoner_fallback(intent: Any) -> dict[str, Any] | None:
        """Build a fallback Reasoner result from the intent's entities.

        Used when the Reasoner LLM fails to return structured JSON.
        Returns None if the intent doesn't have enough date/time info.
        """
        entities = getattr(intent, "entities", None) or {}
        if not entities:
            return None

        date_val = entities.get("date", "")
        time_val = entities.get("time", entities.get("start_time", ""))

        if not date_val:
            return None

        # Build ISO datetime from entities
        if time_val:
            # Normalize time format
            if len(time_val) <= 5:  # "10:00" → "10:00:00"
                time_val = time_val + ":00" if time_val.count(":") < 2 else time_val
            recommended_time = f"{date_val}T{time_val}"
        else:
            recommended_time = f"{date_val}T09:00:00"  # default to 9 AM

        return {
            "recommended_time": recommended_time,
            "has_conflict": False,
            "conflicts": [],
            "reason": "Using requested time (Reasoner analysis unavailable)",
        }

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
            # Audit: policy_denial
            await self._emit_audit(
                "policy_denial",
                plan_id=ctx.plan.plan_id,
                user_id=ctx.user_id,
                trace_id=ctx.trace_id,
                step_number=parent.step,
                reason=decision.reason,
                violations=decision.violations,
            )
            raise SpawnDeniedError(decision.reason, decision.violations)

        # 6. Create attestation
        ctx.plan_revision += 1
        attestation = self._create_attestation(ctx, parent, spawned, decision)
        ctx.attestations.append(attestation)
        ctx.spawned_steps.append(spawned)

        # Audit: policy_attestation
        await self._emit_audit(
            "policy_attestation",
            plan_id=ctx.plan.plan_id,
            user_id=ctx.user_id,
            trace_id=ctx.trace_id,
            step_number=parent.step,
            attestation_id=attestation.attestation_id,
            plan_revision=ctx.plan_revision,
            spawned_step=spawned.step,
        )

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

        # Audit: step_failed
        await self._emit_audit(
            "step_failed",
            plan_id=ctx.plan.plan_id,
            user_id=ctx.user_id,
            trace_id=ctx.trace_id,
            step_number=step.step,
            role=step.role,
            error_type=type(error).__name__,
            error_details=str(error)[:500],
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
        # Build a step_num → PlanStep lookup for role/uses metadata
        all_steps = {s.step: s for s in ctx.plan.graph}
        for s in ctx.spawned_steps:
            all_steps[s.step] = s
        for step_num, sr in ctx.step_results.items():
            entry: dict[str, Any] = {
                "status": sr.status,
                "latency_ms": sr.latency_ms,
            }
            plan_step = all_steps.get(step_num)
            if plan_step:
                entry["role"] = plan_step.role
                entry["uses"] = plan_step.uses
            context_data[f"step_{step_num}"] = entry

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
            GateApprovalRequired: "gate_approval_required",
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
        """Persist outcome via PlanWriter (non-fatal). Sets persist_status on outcome."""
        try:
            execute_ms = int((time.monotonic() - start) * 1000)
            metrics = PlanMetrics(execute_latency_ms=execute_ms)
            try:
                user_uuid = UUID(request.user_id)
            except ValueError:
                user_uuid = UUID("00000000-0000-0000-0000-000000000000")
            result = await self._plan_writer.persist_outcome(
                user_id=user_uuid,
                plan=request.plan,
                outcome=outcome,
                metrics=metrics,
            )
            outcome.persist_status = result.status
        except Exception as exc:
            outcome.persist_status = "error"
            logger.error(
                "persist_outcome_failed: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
                extra={
                    "plan_id": request.plan.plan_id,
                    "user_id": request.user_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )


def create_execute_service(
    policy_service: Any,
    tool_catalog: Any,
    plan_writer_service: Any,
    mcp_client: MCPClient,
    llm_client: LLMClient,
    credential_vault: Any,
    redis_client: Any,
    tracker: Any | None = None,
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
        tool_catalog=tool_catalog,
        plan_writer_service=plan_writer_service,
        mcp_client=mcp_client,
        llm_client=llm_client,
        credential_vault=credential_vault,
        idempotency=idempotency,
        resource_lock=resource_lock,
        dag_resolver=dag_resolver,
        template_resolver=template_resolver,
        retry_policy=retry_policy,
        tracker=tracker,
    )
