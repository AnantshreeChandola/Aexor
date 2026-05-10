"""
Scheduler API Routes

REST endpoints for managing scheduled plans (one-time and recurring).
All routes are user-scoped via get_auth_context.

Prefix: /api/scheduled-plans
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from shared.api.auth import get_auth_context
from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import get_scheduler_service

from ..domain.models import (
    CreateScheduledPlanRequest,
    ScheduledPlanNotFoundError,
    ScheduleValidationError,
    UpdateScheduledPlanRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduled-plans", tags=["scheduled-plans"])


def _handle_domain_error(error: Exception) -> JSONResponse:
    """Map Scheduler domain errors to HTTP responses."""
    if isinstance(error, ScheduledPlanNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="SCHEDULE_NOT_FOUND",
                message=str(error),
            ).model_dump(),
        )
    if isinstance(error, ScheduleValidationError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="SCHEDULE_VALIDATION_ERROR",
                message=str(error),
            ).model_dump(),
        )
    return APIErrorHandler.handle_generic_error(error)


@router.post("")
async def create_scheduled_plan(
    body: CreateScheduledPlanRequest,
    auth_context: dict = Depends(get_auth_context),
    scheduler_service=Depends(get_scheduler_service),
):
    """Create a new scheduled plan (one-time or recurring)."""
    if scheduler_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="SCHEDULER_UNAVAILABLE",
                message="Scheduler service is not available",
            ).model_dump(),
        )

    try:
        user_id = auth_context["user_id"]
        schedule = await scheduler_service.create(user_id, body)
        return {"schedule": schedule.model_dump(mode="json")}
    except (ScheduleValidationError, ScheduledPlanNotFoundError) as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        logger.error("Failed to create schedule: %s", exc)
        return APIErrorHandler.handle_generic_error(exc)


@router.get("")
async def list_scheduled_plans(
    auth_context: dict = Depends(get_auth_context),
    scheduler_service=Depends(get_scheduler_service),
    status_filter: str | None = Query(default=None, alias="status"),
):
    """List all scheduled plans for the authenticated user."""
    if scheduler_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="SCHEDULER_UNAVAILABLE",
                message="Scheduler service is not available",
            ).model_dump(),
        )

    try:
        user_id = auth_context["user_id"]
        schedules = await scheduler_service.list(user_id, status_filter)
        return {
            "schedules": [s.model_dump(mode="json") for s in schedules],
            "total": len(schedules),
        }
    except Exception as exc:
        logger.error("Failed to list schedules: %s", exc)
        return APIErrorHandler.handle_generic_error(exc)


@router.get("/{schedule_id}")
async def get_scheduled_plan(
    schedule_id: UUID,
    auth_context: dict = Depends(get_auth_context),
    scheduler_service=Depends(get_scheduler_service),
):
    """Get a specific scheduled plan."""
    if scheduler_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="SCHEDULER_UNAVAILABLE",
                message="Scheduler service is not available",
            ).model_dump(),
        )

    try:
        user_id = auth_context["user_id"]
        schedule = await scheduler_service.get(schedule_id, user_id)
        return {"schedule": schedule.model_dump(mode="json")}
    except ScheduledPlanNotFoundError as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        logger.error("Failed to get schedule: %s", exc)
        return APIErrorHandler.handle_generic_error(exc)


@router.patch("/{schedule_id}")
async def update_scheduled_plan(
    schedule_id: UUID,
    body: UpdateScheduledPlanRequest,
    auth_context: dict = Depends(get_auth_context),
    scheduler_service=Depends(get_scheduler_service),
):
    """Update a scheduled plan (pause/resume/edit)."""
    if scheduler_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="SCHEDULER_UNAVAILABLE",
                message="Scheduler service is not available",
            ).model_dump(),
        )

    try:
        user_id = auth_context["user_id"]
        schedule = await scheduler_service.update(schedule_id, user_id, body)
        return {"schedule": schedule.model_dump(mode="json")}
    except (ScheduledPlanNotFoundError, ScheduleValidationError) as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        logger.error("Failed to update schedule: %s", exc)
        return APIErrorHandler.handle_generic_error(exc)


@router.delete("/{schedule_id}")
async def delete_scheduled_plan(
    schedule_id: UUID,
    auth_context: dict = Depends(get_auth_context),
    scheduler_service=Depends(get_scheduler_service),
):
    """Delete a scheduled plan and its APScheduler job."""
    if scheduler_service is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="SCHEDULER_UNAVAILABLE",
                message="Scheduler service is not available",
            ).model_dump(),
        )

    try:
        user_id = auth_context["user_id"]
        await scheduler_service.delete(schedule_id, user_id)
        return {"deleted": True}
    except ScheduledPlanNotFoundError as exc:
        return _handle_domain_error(exc)
    except Exception as exc:
        logger.error("Failed to delete schedule: %s", exc)
        return APIErrorHandler.handle_generic_error(exc)
