"""
ExecuteOrchestrator API Routes

Thin handler: parse/validate -> service -> wrap per GLOBAL_SPEC.

Reference: LLD.md Section 4.3, 9.3
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import get_execute_service

from ..domain.models import (
    ApprovalTokenError,
    ExecuteRequest,
    PlanExpiredError,
    SignatureVerificationError,
)

router = APIRouter(prefix="/api/v1", tags=["execute"])


def _handle_domain_error(error: Exception) -> JSONResponse:
    """Map domain errors to HTTP responses."""
    if isinstance(error, SignatureVerificationError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=ErrorResponse(
                error_code="SIGNATURE_INVALID",
                message=str(error),
                details={"reason": error.reason},
            ).model_dump(),
        )
    if isinstance(error, ApprovalTokenError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                error_code="TOKEN_INVALID",
                message=str(error),
                details={"reason": error.reason},
            ).model_dump(),
        )
    if isinstance(error, PlanExpiredError):
        return JSONResponse(
            status_code=status.HTTP_410_GONE,
            content=ErrorResponse(
                error_code="PLAN_EXPIRED",
                message=str(error),
                details={"plan_id": error.plan_id},
            ).model_dump(),
        )
    return APIErrorHandler.handle_generic_error(error)


@router.post("/execute")
async def execute_plan(
    request: ExecuteRequest,
    service=Depends(get_execute_service),
):
    """Execute a signed, approved plan."""
    try:
        return await service.execute_plan(request)
    except (
        SignatureVerificationError,
        ApprovalTokenError,
        PlanExpiredError,
    ) as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        return APIErrorHandler.handle_generic_error(exc)
