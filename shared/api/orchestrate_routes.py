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
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.api.auth import get_auth_context
from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import (
    get_approval_service,
    get_execute_service,
    get_planner_service,
    get_preview_service,
)
from shared.schemas.intent import Intent
from shared.schemas.plan import Plan

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

    plan: dict[str, Any] = Field(
        ..., description="The plan dict from /orchestrate/plan"
    )
    scopes: list[str] = Field(
        ...,
        min_length=1,
        description="User-approved OAuth scopes (e.g. ['calendar.events.create'])",
    )
    selected_option: dict[str, Any] | None = Field(
        default=None, description="Optional user selection from preview"
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


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


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
                trace_id=intent.trace_id or "",
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
            trace_id=plan.trace_id or "",
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

    # 3. Retrieve preview state (best-effort)
    preview_state: dict[str, Any] | None = None
    if preview_service is not None:
        try:
            cached = await preview_service._cache.get(plan.plan_id, user_id)
            if cached is not None:
                preview_state = cached
        except Exception as exc:
            logger.debug("Preview state retrieval failed (non-fatal): %s", exc)

    # 4. Execute plan
    try:
        execute_request = ExecuteRequest(
            plan=plan,
            approval_token=approval_token.token,
            user_id=user_id,
            trace_id=plan.trace_id or "",
            preview_state=preview_state,
        )
        outcome = await execute_service.execute_plan(execute_request)
    except (ApprovalTokenError, PlanExpiredError) as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        logger.error("Execution failed: %s", exc)
        return APIErrorHandler.handle_generic_error(exc)

    return outcome.model_dump(mode="json")
