"""
Orchestrate Routes — Full Pipeline: Intent → Plan → Preview → Approve → Execute

Two thin API endpoints that chain existing service singletons:
- POST /orchestrate/plan   — Intent → Plan → Preview
- POST /orchestrate/execute — Plan + approval → Execute

All services are initialized in shared/app.py lifespan. This is purely
a routing/coordination layer — no new services.

Reference: GLOBAL_SPEC.md §2.1-2.7
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.api.auth import get_auth_context
from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import (
    get_approval_service,
    get_execute_service,
    get_intake_service,
    get_plan_service,
    get_planner_service,
    get_preference_service,
    get_preview_service,
)
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan
from shared.schemas.skeleton import SkeletonRequest, SkeletonResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrate", tags=["orchestrate"])

# ------------------------------------------------------------------
# Request schemas
# ------------------------------------------------------------------


class PlanRequest(BaseModel):
    """Request body for POST /orchestrate/plan."""

    intent: dict[str, Any] = Field(
        ..., description="The intent dict from Intake's 'ready' response"
    )


class ExecuteApprovalRequest(BaseModel):
    """Request body for POST /orchestrate/execute."""

    plan: dict[str, Any] = Field(..., description="The plan dict from /orchestrate/plan")
    scopes: list[str] = Field(
        ...,
        min_length=1,
        description="User-approved OAuth scopes (e.g. ['calendar.events.create'])",
    )
    selected_option: dict[str, Any] | None = Field(
        default=None, description="Optional user selection from preview"
    )
    preview_state: dict[str, Any] | None = Field(
        default=None,
        description="Accumulated gate approvals and step results for multi-gate replay",
    )


class RerunRequest(BaseModel):
    """Request body for POST /orchestrate/rerun."""

    source_plan_id: str = Field(
        ...,
        min_length=26,
        max_length=26,
        description="Plan ID of the previously executed plan to rerun",
    )
    entities: dict[str, Any] = Field(
        ..., description="Fresh entities for the rerun"
    )
    constraints: dict[str, Any] | None = Field(
        default=None, description="Optional constraint overrides"
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _handle_domain_error(exc: Exception) -> JSONResponse:
    """Map orchestration domain exceptions to HTTP error responses."""
    from components.ApprovalGate.domain.models import (
        ApprovalConfigError,
        ApprovalError,
        TokenConsumedError,
        TokenExpiredError,
        TokenValidationError,
    )
    from components.ExecuteOrchestrator.domain.models import (
        ApprovalTokenError,
        PlanExpiredError,
    )

    # ApprovalGate errors
    if isinstance(exc, TokenExpiredError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                error_code="TOKEN_EXPIRED",
                message=str(exc),
            ).model_dump(),
        )
    if isinstance(exc, TokenConsumedError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                error_code="TOKEN_CONSUMED",
                message=str(exc),
            ).model_dump(),
        )
    if isinstance(exc, TokenValidationError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=ErrorResponse(
                error_code="TOKEN_VALIDATION_FAILED",
                message=str(exc),
                details={"reason": exc.reason},
            ).model_dump(),
        )
    if isinstance(exc, ApprovalConfigError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="APPROVAL_CONFIG_ERROR",
                message=str(exc),
            ).model_dump(),
        )
    if isinstance(exc, ApprovalError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="APPROVAL_ERROR",
                message=str(exc),
            ).model_dump(),
        )

    # ExecuteOrchestrator errors
    if isinstance(exc, ApprovalTokenError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                error_code="TOKEN_INVALID",
                message=str(exc),
                details={"reason": exc.reason},
            ).model_dump(),
        )
    if isinstance(exc, PlanExpiredError):
        return JSONResponse(
            status_code=status.HTTP_410_GONE,
            content=ErrorResponse(
                error_code="PLAN_EXPIRED",
                message=str(exc),
                details={"plan_id": exc.plan_id},
            ).model_dump(),
        )

    return APIErrorHandler.handle_generic_error(exc)


# Action verbs that indicate read-only operations
_READ_VERBS = frozenset({
    "LIST", "FETCH", "GET", "FIND", "SEARCH", "READ",
    "QUERY", "CHECK", "SHOW", "VIEW", "LOOKUP",
})

# Action verbs that indicate write/mutating operations
_WRITE_VERBS = frozenset({
    "CREATE", "SEND", "UPDATE", "DELETE", "APPEND",
    "UPLOAD", "POST", "PUT", "PATCH", "REMOVE",
    "ADD", "SET", "WRITE", "DRAFT", "BOOK",
})



def _filter_tools_for_role(tools: list[str | dict], role: str) -> list[str | dict]:
    """Filter tools to those relevant to a skeleton step's role.

    Accepts either plain tool name strings or {name, description} dicts.
    Fetcher → read/list/search/fetch actions only
    Booker  → write/create/send/update actions only
    Other   → all tools (no filtering)
    """
    if role not in ("Fetcher", "Booker"):
        return tools

    target_verbs = _READ_VERBS if role == "Fetcher" else _WRITE_VERBS
    filtered: list[str | dict] = []
    for entry in tools:
        name = entry["name"] if isinstance(entry, dict) else entry
        # Tool names follow PROVIDER_ACTION pattern (e.g. GOOGLECALENDAR_LIST_EVENTS)
        parts = name.split("_", 1)
        if len(parts) < 2:
            continue
        action = parts[1].upper()
        # Check if any target verb appears at the start of the action portion
        if any(action.startswith(verb) for verb in target_verbs):
            filtered.append(entry)
    return filtered if filtered else tools  # fallback to all if filter is empty


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@router.get("/workflows")
async def list_workflows():
    """Return all registered template workflows for the workflows page."""
    from components.Planner.adapters.workflow_registry import (
        _WORKFLOWS,
    )

    result = []
    for wf in _WORKFLOWS:
        # Build a concise step summary for each visible step
        steps = []
        for st in wf.steps:
            # Derive a human label for internal tools
            tool_display = ""
            if st.tool and not st.tool.startswith("system."):
                if any(
                    st.tool.endswith(suffix)
                    for suffix in ("_validator", "_summarizer", "_resolver", "_formatter")
                ):
                    tool_display = ""
                else:
                    # NOTION_SEARCH_NOTION → Notion: Search Notion
                    parts = st.tool.split("_", 1)
                    provider_part = parts[0].capitalize() if parts else st.tool
                    action_part = (
                        parts[1].replace("_", " ").title() if len(parts) > 1 else ""
                    )
                    tool_display = f"{provider_part}: {action_part}" if action_part else provider_part

            steps.append({
                "step": st.step,
                "role": st.role,
                "type": st.type,
                "tool": st.tool,
                "tool_display": tool_display,
                "has_gate": st.gate_id is not None,
                "after": list(st.after),
            })

        entities = []
        for e in wf.entities:
            entities.append({
                "name": e.name,
                "description": e.description,
                "required": e.required,
                "example": e.example,
            })

        # Count user-visible steps (exclude internal validators/system tools)
        api_steps = [s for s in steps if s["tool_display"] or s["type"] == "llm_reasoning"]
        has_approval = any(s["has_gate"] for s in steps)

        result.append({
            "intent": wf.intent,
            "provider": wf.provider,
            "steps": steps,
            "entities": entities,
            "step_count": len(wf.steps),
            "has_approval": has_approval,
        })
    return {"workflows": result, "total": len(result)}


@router.post("/skeleton")
async def orchestrate_skeleton(
    request: Request,
    body: SkeletonRequest,
    auth_context: dict = Depends(get_auth_context),
    intake_service=Depends(get_intake_service),
    planner_service=Depends(get_planner_service),
    preference_service=Depends(get_preference_service),
):
    """Build a plan skeleton for the visual plan builder.

    Two modes:
    A. Fresh flow (message provided): parse intent via LLM, then build skeleton
    B. Rerun flow (intent_type provided): skip LLM parse, build skeleton directly

    Steps:
    1. Detect intent (LLM call or from intent_type param)
    2. Build skeleton from workflow registry (0 LLM calls for known intents)
    3. Query ProfileStore for entity default values
    4. Return skeleton + entity fields + DAG levels
    """
    user_id = str(auth_context["user_id"])
    tz = request.headers.get("X-Timezone", "America/Chicago")

    # Guard: planner must be available
    if planner_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="PLANNER_UNAVAILABLE",
                message="Planner service is not available",
            ).model_dump(),
        )

    # Mode B: intent_type provided directly (rerun flow) — skip LLM parse
    if body.intent_type:
        intent = body.intent_type
        partial = body.entities or {}
    else:
        # Mode A: fresh flow — parse message via LLM
        if not body.message:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=ErrorResponse(
                    error_code="MISSING_MESSAGE",
                    message="Either 'message' or 'intent_type' must be provided",
                ).model_dump(),
            )

        if intake_service is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=ErrorResponse(
                    error_code="INTAKE_UNAVAILABLE",
                    message="Intake service is not available",
                ).model_dump(),
            )

        # 1. Single-turn parse
        try:
            parse_result = await intake_service.parse_once(body.message, user_id, tz)
        except Exception as exc:
            logger.error("Skeleton parse failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=ErrorResponse(
                    error_code="PARSE_FAILED",
                    message="Failed to parse message",
                    details={"error": str(exc)},
                ).model_dump(),
            )

        # 2. Check if intent was detected
        if parse_result.intent is None:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=ErrorResponse(
                    error_code="NO_INTENT_DETECTED",
                    message="Could not detect intent from the message",
                ).model_dump(),
            )

        intent = parse_result.intent
        partial = parse_result.entities

    # 3. Build skeleton
    try:
        skeleton = await planner_service.build_skeleton(
            intent_type=intent,
            partial_entities=partial,
            user_id=user_id,
            sub_intents=getattr(body, '_sub_intents', None),
            preference_service=preference_service,
        )
    except Exception as exc:
        logger.error("Skeleton build failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="SKELETON_BUILD_FAILED",
                message="Failed to build plan skeleton",
                details={"error": str(exc)},
            ).model_dump(),
        )

    # Populate available_tools for empty-tool steps from the user's
    # connected tools only (FR-014: never fall back to get_all_tools()).
    if any(s.tool == "" and s.type == "api" for s in skeleton.steps):
        tool_catalog = getattr(request.app.state, "tool_catalog", None)
        if tool_catalog:
            try:
                user_tools = await tool_catalog.get_user_tools(user_id)
                if user_tools is None:
                    # Not cached — live refresh from Composio
                    user_tools = await tool_catalog.refresh_user(user_id)
                if user_tools:
                    tool_info = [
                        {"name": getattr(t, "name", ""), "description": getattr(t, "description", "")}
                        for t in user_tools
                    ]
                else:
                    tool_info = []
                for s in skeleton.steps:
                    if s.tool == "" and s.type == "api":
                        s.available_tools = _filter_tools_for_role(tool_info, s.role)
            except Exception as exc:
                logger.warning("Failed to populate available_tools: %s", exc)

    session_id = f"skel_{intent}_{user_id[:8]}"

    # Auto-fill timezone entity from the request header so the builder
    # form shows it pre-populated and it flows through to the Reasoner.
    entities = {**partial}
    if "timezone" not in entities:
        entities["timezone"] = tz

    return SkeletonResponse(
        skeleton=skeleton,
        session_id=session_id,
        partial_entities=entities,
    ).model_dump(mode="json")


@router.post("/plan")
async def orchestrate_plan(
    body: PlanRequest,
    auth_context: dict = Depends(get_auth_context),
    planner_service=Depends(get_planner_service),
    preview_service=Depends(get_preview_service),
):
    """Generate a plan from an Intent and run preview.

    1. Validate & parse intent
    2. PlannerService.generate_plan(intent) → plan
    3. PreviewService.preview(plan) → preview results
    4. Return plan + preview for user review
    """
    from components.PreviewOrchestrator.domain.models import PreviewRequest

    # Guard: planner must be available
    if planner_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="PLANNER_UNAVAILABLE",
                message="Planner service is not available",
            ).model_dump(),
        )

    # 1. Parse intent
    try:
        intent = Intent.model_validate(body.intent)
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="INVALID_INTENT",
                message="Failed to parse intent",
                details={"error": str(exc)},
            ).model_dump(),
        )

    # 2. Generate plan
    try:
        planner_result = await planner_service.generate_plan(intent)
    except Exception as exc:
        from components.Planner.domain.models import ToolNotAvailableError
        from components.Planner.domain.tool_discovery_models import (
            NoToolsConnectedError,
            ToolNotConnectedError,
        )

        if isinstance(exc, ToolNotConnectedError):
            logger.warning("Plan generation failed — required tools not connected: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=ErrorResponse(
                    error_code="TOOL_NOT_CONNECTED",
                    message=exc.message,
                    details={"missing_tools": exc.missing_tools},
                ).model_dump(),
            )
        if isinstance(exc, NoToolsConnectedError):
            logger.warning("Plan generation failed — no tools connected: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=ErrorResponse(
                    error_code="NO_TOOLS_CONNECTED",
                    message=exc.message,
                    details={"user_id": exc.user_id},
                ).model_dump(),
            )
        if isinstance(exc, ToolNotAvailableError):
            logger.warning("Plan generation failed — tools not available: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=ErrorResponse(
                    error_code="TOOL_NOT_AVAILABLE",
                    message=str(exc),
                    details={
                        "intent_type": exc.intent_type,
                        "required_tools": exc.required_tools,
                    },
                ).model_dump(),
            )
        logger.error("Plan generation failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="PLAN_GENERATION_FAILED",
                message="Plan generation failed",
                details={"error": str(exc)},
            ).model_dump(),
        )

    plan = planner_result.plan

    # 3. Run preview (best-effort — return plan anyway on failure)
    preview_data = None
    if preview_service is not None:
        try:
            preview_request = PreviewRequest(
                plan=plan,
                user_id=intent.user_id,
                trace_id=intent.trace_id or uuid.uuid4().hex,
            )
            preview_result = await preview_service.preview(preview_request)
            preview_data = preview_result.model_dump(mode="json")
        except Exception as exc:
            logger.warning("Preview failed (returning plan without preview): %s", exc)

    return {
        "plan": plan.model_dump(mode="json"),
        "preview": preview_data,
        "plan_id": plan.plan_id,
    }


@router.post("/rerun")
async def orchestrate_rerun(
    body: RerunRequest,
    auth_context: dict = Depends(get_auth_context),
    plan_service=Depends(get_plan_service),
    preview_service=Depends(get_preview_service),
):
    """Rerun a previously executed plan with fresh entities.

    1. Clone the source plan's graph structure with new entities
    2. Run preview on the cloned plan
    3. Return plan + preview for user approval (same shape as POST /orchestrate/plan)
    """
    from components.PlanLibrary.domain.models import PlanNotFoundError
    from components.PreviewOrchestrator.domain.models import PreviewRequest

    user_id = str(auth_context["user_id"])
    trace_id = f"rerun-{body.source_plan_id[:8]}"

    # Guard: plan service must be available
    if plan_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="PLAN_SERVICE_UNAVAILABLE",
                message="Plan service is not available",
            ).model_dump(),
        )

    # 1. Clone plan
    try:
        plan = await plan_service.clone_plan_for_rerun(
            source_plan_id=body.source_plan_id,
            fresh_entities=body.entities,
            user_id=user_id,
            trace_id=trace_id,
            constraints_override=body.constraints,
        )
    except PlanNotFoundError:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="PLAN_NOT_FOUND",
                message=f"Source plan {body.source_plan_id} not found",
                details={"source_plan_id": body.source_plan_id},
            ).model_dump(),
        )
    except Exception as exc:
        logger.error("Rerun clone failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="RERUN_CLONE_FAILED",
                message="Failed to clone plan for rerun",
                details={"error": str(exc)},
            ).model_dump(),
        )

    # 2. Run preview (best-effort)
    preview_data = None
    if preview_service is not None:
        try:
            preview_request = PreviewRequest(
                plan=plan,
                user_id=user_id,
                trace_id=trace_id,
            )
            preview_result = await preview_service.preview(preview_request)
            preview_data = preview_result.model_dump(mode="json")
        except Exception as exc:
            logger.warning("Preview failed during rerun (returning plan without preview): %s", exc)

    return {
        "plan": plan.model_dump(mode="json"),
        "preview": preview_data,
        "plan_id": plan.plan_id,
    }


@router.post("/execute")
async def orchestrate_execute(
    body: ExecuteApprovalRequest,
    auth_context: dict = Depends(get_auth_context),
    approval_service=Depends(get_approval_service),
    execute_service=Depends(get_execute_service),
    preview_service=Depends(get_preview_service),
):
    """Approve and execute a plan.

    1. Parse plan
    2. Issue approval token via ApprovalGate
    3. Execute plan via ExecuteOrchestrator
    4. Return outcome
    """
    from components.ApprovalGate.domain.models import (
        ApprovalError,
        ApprovalRequest,
        TokenConsumedError,
        TokenExpiredError,
        TokenValidationError,
    )
    from components.ExecuteOrchestrator.domain.models import (
        ApprovalTokenError,
        ExecuteRequest,
        GateApprovalRequired,
        IntegrationNotConnectedError,
        PlanExpiredError,
    )

    user_id = str(auth_context["user_id"])

    # Guard: services must be available
    if approval_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="APPROVAL_SERVICE_UNAVAILABLE",
                message="Approval service is not available",
            ).model_dump(),
        )
    if execute_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="EXECUTE_SERVICE_UNAVAILABLE",
                message="Execute service is not available",
            ).model_dump(),
        )

    # 1. Parse plan
    try:
        plan = Plan.model_validate(body.plan)
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="INVALID_PLAN",
                message="Failed to parse plan",
                details={"error": str(exc)},
            ).model_dump(),
        )

    # 2. Issue approval token
    try:
        approval_request = ApprovalRequest(
            plan_id=plan.plan_id,
            user_id=user_id,
            gate_id="gate-A",
            scopes=body.scopes,
            selected_option=body.selected_option,
            trace_id=plan.trace_id or uuid.uuid4().hex,
        )
        approval_token = await approval_service.approve(approval_request)
    except (
        TokenExpiredError,
        TokenConsumedError,
        TokenValidationError,
        ApprovalError,
    ) as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        logger.error("Approval failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="APPROVAL_FAILED",
                message="Approval token issuance failed",
                details={"error": str(exc)},
            ).model_dump(),
        )

    # 3. Build preview_state: merge cached preview + frontend gate approvals
    preview_state: dict[str, Any] = {}
    if preview_service is not None:
        try:
            cached = await preview_service._cache.get(plan.plan_id, user_id)
            if cached is not None:
                preview_state.update(cached)
        except Exception as exc:
            logger.debug("Preview state retrieval failed (non-fatal): %s", exc)
    # Merge gate approvals and completed step results from the frontend
    if body.preview_state:
        preview_state.update(body.preview_state)

    # 4. Execute plan
    try:
        execute_request = ExecuteRequest(
            plan=plan,
            approval_token=approval_token.token,
            user_id=user_id,
            trace_id=plan.trace_id or uuid.uuid4().hex,
            preview_state=preview_state or None,
        )
        outcome = await execute_service.execute_plan(execute_request)
    except GateApprovalRequired as exc:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "approval_required",
                "gate_id": exc.gate_id,
                "step": exc.step,
                "message": str(exc),
                "context_data": exc.context_data,
                "partial_results": exc.partial_results,
            },
        )
    except (ApprovalTokenError, PlanExpiredError) as exc:
        return _handle_domain_error(exc)
    except IntegrationNotConnectedError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error_code="INTEGRATION_NOT_CONNECTED",
                message=str(exc),
                details={"provider": exc.provider, "step": exc.step},
            ).model_dump(),
        )
    except Exception as exc:
        logger.error("Execution failed: %s", exc)
        return APIErrorHandler.handle_generic_error(exc)

    return outcome.model_dump(mode="json")
