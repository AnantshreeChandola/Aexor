"""
History API Routes

Thin FastAPI route wrappers for fact storage, querying, and patterns.

Reference: LLD.md §3, tasks.md T400
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from shared.api.auth import RequireTier3, get_auth_context, verify_user_access

from ..domain.models import (
    FactTooLargeError,
    InvalidFactError,
    InvalidQueryError,
    InvalidTimestampError,
    PatternsResponse,
    QueryFactsResponse,
    StorageError,
    StoreFactRequest,
    StoreFactResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/history", tags=["history"])


# Dependency injection functions
def get_fact_service(request: Request):
    """Get FactService from app state."""
    return request.app.state.fact_service


def get_pattern_service(request: Request):
    """Get PatternService from app state."""
    return request.app.state.pattern_service


def get_db_adapter(request: Request):
    """Get DatabaseAdapter from app state."""
    return request.app.state.history_db_adapter


class ErrorHandlerMixin:
    """Centralized error handling for History API routes."""

    def handle_service_errors(self, error: Exception) -> JSONResponse:
        """
        Handle common service exceptions.

        Maps domain exceptions to HTTP responses.

        Args:
            error: Exception from service layer

        Returns:
            JSONResponse with appropriate status code and error details
        """
        error_type = type(error).__name__

        if error_type == "InvalidFactError":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error_code": "INVALID_FACT",
                    "message": str(error),
                    "details": {"reason": error.reason},
                },
            )

        if error_type == "FactTooLargeError":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error_code": "FACT_TOO_LARGE",
                    "message": str(error),
                    "details": {"size": error.size},
                },
            )

        if error_type == "InvalidTimestampError":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error_code": "INVALID_TIMESTAMP",
                    "message": str(error),
                    "details": {"timestamp": str(error.timestamp)},
                },
            )

        if error_type == "InvalidQueryError":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error_code": "INVALID_QUERY",
                    "message": str(error),
                    "details": {"reason": error.reason},
                },
            )

        if error_type == "StorageError":
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error_code": "STORAGE_ERROR",
                    "message": str(error),
                    "details": {"reason": error.reason},
                },
            )

        # Unknown error
        logger.error(f"Unexpected error in History API: {error}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error_code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
            },
        )


error_handler = ErrorHandlerMixin()


@router.post(
    "/{user_id}/facts",
    response_model=StoreFactResponse,
    summary="Store a derived fact from plan execution",
    status_code=status.HTTP_201_CREATED,
)
async def store_fact_endpoint(
    user_id: UUID,
    request: StoreFactRequest,
    auth_context: dict = Depends(get_auth_context),
    _: None = Depends(RequireTier3),
    fact_service=Depends(get_fact_service),
) -> StoreFactResponse:
    """
    Store a derived fact from plan execution.

    Requires Tier 3 consent. Idempotent: duplicate facts return existing record.

    Args:
        user_id: User UUID
        request: StoreFactRequest with fact data
        auth_context: Authentication context
        _: Tier 3 consent check
        fact_service: Injected FactService

    Returns:
        StoreFactResponse with fact_id and status

    Raises:
        403: Tier 3 consent required
        400: Invalid fact data
        500: Storage error
    """
    verify_user_access(user_id, auth_context)

    try:
        response = await fact_service.store_fact(user_id=user_id, request=request)
        return response
    except (
        InvalidFactError,
        FactTooLargeError,
        InvalidTimestampError,
        StorageError,
    ) as e:
        return error_handler.handle_service_errors(e)


@router.get(
    "/{user_id}/facts",
    response_model=QueryFactsResponse,
    summary="Query facts for a user",
)
async def query_facts_endpoint(
    user_id: UUID,
    intent_type: str | None = None,
    limit: int = Query(default=50, le=500, ge=1),
    recency_days: int | None = Query(default=None, ge=1),
    auth_context: dict = Depends(get_auth_context),
    _: None = Depends(RequireTier3),
    fact_service=Depends(get_fact_service),
) -> QueryFactsResponse:
    """
    Query facts for a user, returning Evidence Items.

    Requires Tier 3 consent. Excludes expired facts. Returns newest first.

    Args:
        user_id: User UUID
        intent_type: Filter by intent type (optional)
        limit: Maximum results (default 50, max 500)
        recency_days: Only facts from last N days (optional)
        auth_context: Authentication context
        _: Tier 3 consent check
        fact_service: Injected FactService

    Returns:
        QueryFactsResponse with Evidence Items and counts

    Raises:
        403: Tier 3 consent required
        400: Invalid query parameters
    """
    verify_user_access(user_id, auth_context)

    try:
        response = await fact_service.get_facts_by_intent(
            user_id=user_id,
            intent_type=intent_type,
            limit=limit,
            recency_days=recency_days,
        )
        return response
    except InvalidQueryError as e:
        return error_handler.handle_service_errors(e)


@router.get(
    "/{user_id}/patterns",
    response_model=PatternsResponse,
    summary="Get detected recurring patterns",
)
async def query_patterns_endpoint(
    user_id: UUID,
    intent_type: str | None = None,
    min_confidence: float = Query(default=0.5, ge=0.0, le=1.0),
    auth_context: dict = Depends(get_auth_context),
    _: None = Depends(RequireTier3),
    pattern_service=Depends(get_pattern_service),
) -> PatternsResponse:
    """
    Get detected recurring patterns for a user.

    Requires Tier 3 consent. Filters by confidence threshold.

    Args:
        user_id: User UUID
        intent_type: Filter by intent type (optional)
        min_confidence: Minimum confidence threshold (0.0-1.0)
        auth_context: Authentication context
        _: Tier 3 consent check
        pattern_service: Injected PatternService

    Returns:
        PatternsResponse with patterns and count

    Raises:
        403: Tier 3 consent required
    """
    verify_user_access(user_id, auth_context)

    response = await pattern_service.get_patterns(
        user_id=user_id,
        intent_type=intent_type,
        min_confidence=min_confidence,
    )
    return response


@router.get(
    "/health",
    summary="Health check",
    status_code=status.HTTP_200_OK,
)
async def health_check(
    db_adapter=Depends(get_db_adapter),
) -> dict:
    """
    Check database connectivity.

    No authentication required.

    Args:
        db_adapter: Injected DatabaseAdapter

    Returns:
        Health status
    """
    is_healthy = await db_adapter.health_check()

    if is_healthy:
        return {"status": "healthy", "component": "History"}
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection unavailable",
        )
