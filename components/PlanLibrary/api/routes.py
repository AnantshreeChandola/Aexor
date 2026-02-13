"""
PlanLibrary API Routes

FastAPI endpoints for plan storage, querying, and analytics.
Thin wrappers around service layer with proper error handling.

Reference: LLD.md, tasks.md T400
"""

import logging

from fastapi import APIRouter, Query, Request

from ..domain.models import (
    DuplicatePlanError,
    EmbeddingServiceError,
    InvalidQueryError,
    InvalidSignatureError,
    PlanNotFoundError,
    PlanTooLargeError,
    SimilaritySearchRequest,
    StorePlanRequest,
    StorePlanResponse,
    SuccessResponse,
)
from ..service.analytics_service import AnalyticsService
from ..service.plan_service import PlanService
from ..service.vector_service import VectorService
from .error_handlers import PlanLibraryErrorHandler

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/plans", tags=["plans"])

# Error handler instance
error_handler = PlanLibraryErrorHandler()

# Service instances (lazily initialized)
_plan_service: PlanService | None = None
_vector_service: VectorService | None = None
_analytics_service: AnalyticsService | None = None


def get_plan_service() -> PlanService:
    """Get PlanService instance with all dependencies."""
    global _plan_service
    if _plan_service is None:
        from ..adapters.db import DatabaseAdapter
        from ..adapters.signature_verifier import SignatureVerifier

        db_adapter = DatabaseAdapter()
        signature_verifier = SignatureVerifier()

        _plan_service = PlanService(
            db_adapter=db_adapter,
            vector_service=_get_vector_service_safe(),
            signature_verifier=signature_verifier,
        )
    return _plan_service


def _get_vector_service_safe() -> VectorService | None:
    """Get VectorService, returning None if dependencies unavailable."""
    try:
        return get_vector_service()
    except Exception as e:
        logger.warning(
            "Vector service unavailable",
            extra={
                "error": str(e),
                "component": "PlanLibrary",
            },
        )
        return None


def get_vector_service() -> VectorService:
    """Get VectorService instance with all dependencies."""
    global _vector_service
    if _vector_service is None:
        from ..adapters.embedding_client import EmbeddingClient
        from ..adapters.vector_db import VectorAdapter

        _vector_service = VectorService(
            vector_adapter=VectorAdapter(),
            embedding_client=EmbeddingClient(),
        )
    return _vector_service


def get_analytics_service() -> AnalyticsService:
    """Get AnalyticsService instance."""
    global _analytics_service
    if _analytics_service is None:
        from ..adapters.db import DatabaseAdapter

        _analytics_service = AnalyticsService(
            db_adapter=DatabaseAdapter()
        )
    return _analytics_service


@router.post("", response_model=StorePlanResponse)
async def store_plan_endpoint(
    request_body: StorePlanRequest,
    request: Request,
) -> StorePlanResponse:
    """
    Store executed plan with outcome and metrics.

    Thin wrapper: delegates to PlanService.store_plan().
    """
    try:
        plan_id = request.headers.get("X-Plan-ID", "")
        service = get_plan_service()

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
    request: Request,
    success_threshold: float = Query(default=0.7, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=1000),
    recency_days: int | None = Query(default=None, ge=1),
) -> SuccessResponse:
    """
    Query plans by intent type with success filtering.

    Returns Evidence Items (type="plan", tier=3).
    """
    try:
        service = get_plan_service()
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
async def health_check() -> dict:
    """
    Health check endpoint for PlanLibrary.

    Checks database and vector adapter health.
    No authentication required.
    """
    try:
        from ..adapters.db import DatabaseAdapter

        db = DatabaseAdapter()
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
    request: Request,
) -> SuccessResponse:
    """
    Retrieve specific plan by ID.

    Returns plan data or 404 if not found.
    """
    try:
        service = get_plan_service()
        plan = await service.get_plan_by_id(plan_id)

        if plan is None:
            return error_handler.handle_plan_not_found(
                PlanNotFoundError(plan_id=plan_id)
            )

        return SuccessResponse(data=plan.model_dump())

    except Exception as e:
        return error_handler.handle_service_errors(e)


@router.post("/search/similar")
async def similarity_search_endpoint(
    request_body: SimilaritySearchRequest,
    request: Request,
) -> SuccessResponse:
    """
    Find similar plans using vector search.

    Returns Evidence Items sorted by relevance score.
    """
    try:
        vector_svc = get_vector_service()
        evidence_items = await vector_svc.similarity_search(
            query_text=request_body.query_text,
            similarity_threshold=request_body.similarity_threshold,
            limit=request_body.limit,
            success_threshold=request_body.success_threshold,
        )

        return SuccessResponse(
            data=[item.model_dump() for item in evidence_items],
        )

    except (EmbeddingServiceError, InvalidQueryError) as e:
        return error_handler.handle_service_errors(e)


@router.get("/analytics/success-rates")
async def get_success_rates_endpoint(
    timeframe_days: int = Query(default=30, ge=1, le=365),
) -> SuccessResponse:
    """
    Get plan success rates by intent type.

    Returns success rates for the specified timeframe.
    """
    try:
        analytics_svc = get_analytics_service()
        rates = await analytics_svc.calculate_success_rates(
            timeframe_days=timeframe_days
        )

        return SuccessResponse(data=rates)

    except Exception as e:
        return error_handler.handle_service_errors(e)
