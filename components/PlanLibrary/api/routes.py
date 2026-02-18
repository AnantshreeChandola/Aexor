"""
PlanLibrary API Routes

FastAPI endpoints for plan storage, querying, and analytics.
Thin wrappers around service layer with proper error handling.

Reference: LLD.md, tasks.md T400
"""

import logging

from fastapi import APIRouter, Depends, Query, Request

from shared.dependencies import get_analytics_service, get_plan_service

from ..domain.models import (
    DuplicatePlanError,
    InvalidQueryError,
    InvalidSignatureError,
    PlanNotFoundError,
    PlanTooLargeError,
    StorePlanRequest,
    StorePlanResponse,
    SuccessResponse,
)
from ..service.analytics_service import AnalyticsService
from ..service.plan_service import PlanService
from .error_handlers import PlanLibraryErrorHandler

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/plans", tags=["plans"])

# Error handler instance
error_handler = PlanLibraryErrorHandler()


@router.post("", response_model=StorePlanResponse)
async def store_plan_endpoint(
    request_body: StorePlanRequest,
    service: PlanService = Depends(get_plan_service),
) -> StorePlanResponse:
    """
    Store executed plan with outcome and metrics.

    Thin wrapper: delegates to PlanService.store_plan().
    """
    try:
        response = await service.store_plan(
            plan=request_body.plan,
            signature=request_body.signature,
            outcome=request_body.outcome,
            metrics=request_body.metrics,
        )

        logger.info(
            "POST /plans success",
            extra={
                "plan_id": response.plan_id,
                "component": "PlanLibrary",
            },
        )
        return response

    except (
        InvalidSignatureError,
        DuplicatePlanError,
        PlanTooLargeError,
        ValueError,
    ) as e:
        return error_handler.handle_service_errors(e)


@router.get("/by-intent/{intent_type}")
async def get_plans_by_intent_endpoint(
    intent_type: str,
    service: PlanService = Depends(get_plan_service),
    success_threshold: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=1000),
    recency_days: int | None = Query(default=None, ge=1),
) -> SuccessResponse:
    """
    Query plans by intent type with success filtering.

    Returns Evidence Items (type="plan", tier=3).
    """
    try:
        evidence_items = await service.get_plans_by_intent(
            intent_type=intent_type,
            success_threshold=success_threshold,
            limit=limit,
            recency_days=recency_days,
        )

        return SuccessResponse(
            data=[item.model_dump() for item in evidence_items],
        )

    except (InvalidQueryError, ValueError) as e:
        return error_handler.handle_service_errors(e)


@router.get("/health")
async def health_check(request: Request) -> dict:
    """
    Health check endpoint for PlanLibrary.

    Checks database health via shared adapter.
    No authentication required.
    """
    try:
        db = request.app.state.db
        db_healthy = await db.health_check()

        return {
            "overall": "healthy" if db_healthy else "degraded",
            "database": "healthy" if db_healthy else "unhealthy",
            "component": "PlanLibrary",
        }
    except Exception as e:
        logger.error(
            "Health check failed",
            extra={
                "error": str(e),
                "component": "PlanLibrary",
            },
        )
        return {
            "overall": "unhealthy",
            "error": str(e),
            "component": "PlanLibrary",
        }


@router.get("/{plan_id}")
async def get_plan_endpoint(
    plan_id: str,
    service: PlanService = Depends(get_plan_service),
) -> SuccessResponse:
    """
    Retrieve specific plan by ID.

    Returns plan data or 404 if not found.
    """
    try:
        plan = await service.get_plan_by_id(plan_id)

        if plan is None:
            return error_handler.handle_plan_not_found(PlanNotFoundError(plan_id=plan_id))

        return SuccessResponse(data=plan.model_dump())

    except Exception as e:
        return error_handler.handle_service_errors(e)


@router.get("/analytics/success-rates")
async def get_success_rates_endpoint(
    analytics_svc: AnalyticsService = Depends(get_analytics_service),
    timeframe_days: int = Query(default=30, ge=1, le=365),
) -> SuccessResponse:
    """
    Get plan success rates by intent type.

    Returns success rates for the specified timeframe.
    """
    try:
        rates = await analytics_svc.calculate_success_rates(timeframe_days=timeframe_days)

        return SuccessResponse(data=rates)

    except Exception as e:
        return error_handler.handle_service_errors(e)
