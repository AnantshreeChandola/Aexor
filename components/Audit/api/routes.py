"""
Audit API Routes

Read-only query endpoint for audit events. Events are recorded
internally via DI-injected AuditService -- no POST endpoint.

Reference: LLD.md Section 4.2
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import get_audit_service

from ..domain.models import AuditError

router = APIRouter(prefix="/audit", tags=["audit"])


def _handle_domain_error(error: Exception) -> JSONResponse:
    """Map Audit domain errors to HTTP responses."""
    if isinstance(error, AuditError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="AUDIT_ERROR",
                message=str(error),
                details={},
            ).model_dump(),
        )
    return APIErrorHandler.handle_generic_error(error)


@router.get("/events")
async def query_events(
    plan_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    trace_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    audit_service=Depends(get_audit_service),
):
    """Query audit events with filters and cursor pagination."""
    try:
        result = await audit_service.query(
            plan_id=plan_id,
            user_id=user_id,
            trace_id=trace_id,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            cursor=cursor,
            limit=limit,
        )
        return result.model_dump(mode="json")
    except AuditError as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        return APIErrorHandler.handle_generic_error(exc)
