"""
ProfileStore API Routes

FastAPI endpoints for preference operations.
Thin wrappers around PreferenceService with proper error handling.

Reference: LLD.md §3.1
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from shared.api.auth import get_auth_context, verify_user_access
from shared.api.error_handlers import ErrorHandlerMixin
from shared.database.error_handler import UserNotFoundError
from shared.dependencies import get_preference_service

from ..domain.models import (
    ConsentDeniedError,
    PreferenceRequest,
    SuccessResponse,
    UnknownPreferenceError,
    ValidationError,
)
from ..service.preference_service import PreferenceService

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/preferences", tags=["preferences"])

# Error handler instance for centralized error handling
error_handler = ErrorHandlerMixin()


@router.get("/{user_id}/{preference_key}")
async def get_preference(
    user_id: UUID,
    preference_key: str,
    request: Request,
    auth_context: dict = Depends(get_auth_context),
    service: PreferenceService = Depends(get_preference_service),
) -> SuccessResponse:
    """
    Retrieve a single preference value.

    Returns preference in Evidence Item format for ContextRAG integration.
    Requires context_tier >= 2 (Tier 2 consent).
    """
    try:
        # Verify user can only access their own preferences
        verify_user_access(user_id, auth_context)

        # Get plan_id for correlation logging
        plan_id = request.headers.get("X-Plan-ID")

        # Get preference via service
        evidence = await service.get_preference(
            user_id=user_id,
            preference_key=preference_key,
            context_tier=auth_context["context_tier"],
            plan_id=plan_id,
        )

        logger.info(
            f"GET preference success: user={user_id}, key={preference_key}, plan_id={plan_id}"
        )

        return SuccessResponse(
            data=evidence.model_dump(),
            tier=2,
            sensitive=False,  # Evidence Item doesn't expose sensitivity
        )

    except (ConsentDeniedError, UserNotFoundError, UnknownPreferenceError) as e:
        return error_handler.handle_service_errors(e)


@router.post("/{user_id}")
async def set_preference(
    user_id: UUID,
    request_body: PreferenceRequest,
    request: Request,
    auth_context: dict = Depends(get_auth_context),
    service: PreferenceService = Depends(get_preference_service),
) -> SuccessResponse:
    """
    Create or update a preference (upsert).

    Validates against schema and encrypts if marked as sensitive.
    Idempotent operation - safe to retry with same parameters.
    """
    try:
        # Verify user can only modify their own preferences
        verify_user_access(user_id, auth_context)

        # Get plan_id for correlation logging
        plan_id = request.headers.get("X-Plan-ID")

        # Set preference via service
        response = await service.set_preference(
            user_id=user_id,
            preference_key=request_body.preference_key,
            preference_value=request_body.preference_value,
            sensitive=request_body.sensitive,
            plan_id=plan_id,
        )

        logger.info(
            f"SET preference success: user={user_id}, "
            f"key={request_body.preference_key}, plan_id={plan_id}"
        )

        return SuccessResponse(data=response.model_dump(), tier=2, sensitive=response.sensitive)

    except (UserNotFoundError, UnknownPreferenceError, ValidationError) as e:
        return error_handler.handle_service_errors(e)


@router.delete("/{user_id}/{preference_key}")
async def delete_preference(
    user_id: UUID,
    preference_key: str,
    request: Request,
    auth_context: dict = Depends(get_auth_context),
    service: PreferenceService = Depends(get_preference_service),
) -> SuccessResponse:
    """
    Delete a preference (reset to schema default).

    Performs soft delete - preference will return to schema default value.
    No compensation available for this operation.
    """
    try:
        # Verify user can only delete their own preferences
        verify_user_access(user_id, auth_context)

        # Get plan_id for correlation logging
        plan_id = request.headers.get("X-Plan-ID")

        # Delete preference via service
        response = await service.delete_preference(
            user_id=user_id, preference_key=preference_key, plan_id=plan_id
        )

        logger.info(
            f"DELETE preference success: user={user_id}, key={preference_key}, plan_id={plan_id}"
        )

        return SuccessResponse(data=response.model_dump(), tier=2, sensitive=False)

    except (UserNotFoundError, UnknownPreferenceError) as e:
        return error_handler.handle_service_errors(e)


@router.get("/{user_id}")
async def get_all_preferences(
    user_id: UUID,
    request: Request,
    auth_context: dict = Depends(get_auth_context),
    service: PreferenceService = Depends(get_preference_service),
) -> SuccessResponse:
    """
    Get all preferences for a user.

    Returns preferences in Evidence Item format.
    Includes both explicitly set preferences and schema defaults.
    """
    try:
        # Verify user can only access their own preferences
        verify_user_access(user_id, auth_context)

        # Get plan_id for correlation logging
        plan_id = request.headers.get("X-Plan-ID")

        # Get all preferences via service
        evidence_items = await service.get_all_preferences(
            user_id=user_id, context_tier=auth_context["context_tier"], plan_id=plan_id
        )

        logger.info(
            f"GET all preferences success: user={user_id}, "
            f"count={len(evidence_items)}, plan_id={plan_id}"
        )

        return SuccessResponse(
            data=[item.model_dump() for item in evidence_items], tier=2, sensitive=False
        )

    except (ConsentDeniedError, UserNotFoundError) as e:
        return error_handler.handle_service_errors(e)


@router.get("/health")
async def health_check(service: PreferenceService = Depends(get_preference_service)) -> dict:
    """
    Health check endpoint for ProfileStore service.

    Checks database connectivity, schema registry, and encryption.
    Does not require authentication.
    """
    try:
        health = await service.health_check()

        # Determine overall health
        overall_healthy = all("healthy" in str(status) for status in health.values())

        health["overall"] = "healthy" if overall_healthy else "degraded"

        return health

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"overall": "unhealthy", "error": str(e)}
