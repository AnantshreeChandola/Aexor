"""
ProfileStore API Routes (Refactored)

FastAPI endpoints using shared error handling utilities.
Demonstrates DRY principles with minimal code duplication.

Reference: LLD.md §3.1
"""

import logging
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Request, HTTPException, status, Depends

from shared.schemas.evidence import EvidenceItem
from shared.api.error_handlers import APIErrorHandler, ErrorHandlerMixin
from shared.database.error_handler import UserNotFoundError
from ..domain.models import (
    PreferenceRequest, SuccessResponse,
    ConsentDeniedError, UnknownPreferenceError, ValidationError
)
from ..service.preference_service import PreferenceService
from ..adapters.db import DatabaseAdapter
from ..adapters.schema_registry import get_schema_registry
from ..adapters.encryption import get_encryption_adapter

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/preferences", tags=["preferences"])


class ProfileStoreRoutes(ErrorHandlerMixin):
    """Route handler class with shared error handling."""
    
    @staticmethod
    def get_preference_service() -> PreferenceService:
        """Get PreferenceService instance with all dependencies."""
        db_adapter = DatabaseAdapter()
        schema_registry = get_schema_registry()
        encryption_adapter = get_encryption_adapter()
        
        return PreferenceService(
            db_adapter=db_adapter,
            schema_registry=schema_registry,
            encryption_adapter=encryption_adapter
        )

    @staticmethod
    def get_auth_context(request: Request) -> dict:
        """Extract authentication context from request."""
        if not hasattr(request.state, 'user_id'):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
        
        return {
            "user_id": request.state.user_id,
            "context_tier": request.state.context_tier,
            "email": request.state.email
        }

    def check_user_authorization(self, auth_context: dict, target_user_id: UUID):
        """Check if user can access target user's data."""
        if auth_context["user_id"] != target_user_id:
            logger.warning(
                f"User {auth_context['user_id']} attempted to access "
                f"data for user {target_user_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot access other users' data"
            )


# Route handler instance
route_handler = ProfileStoreRoutes()


@router.get("/{user_id}/{preference_key}")
async def get_preference(
    user_id: UUID,
    preference_key: str,
    request: Request,
    auth_context: dict = Depends(route_handler.get_auth_context),
    service: PreferenceService = Depends(route_handler.get_preference_service)
) -> SuccessResponse:
    """Get a single preference value."""
    try:
        route_handler.check_user_authorization(auth_context, user_id)
        
        plan_id = request.headers.get("X-Plan-ID")
        
        evidence = await service.get_preference(
            user_id=user_id,
            preference_key=preference_key,
            context_tier=auth_context["context_tier"],
            plan_id=plan_id
        )
        
        logger.info(
            f"GET preference success: user={user_id}, key={preference_key}, "
            f"plan_id={plan_id}"
        )
        
        return SuccessResponse(
            data=evidence.model_dump(),
            tier=2,
            sensitive=False
        )
        
    except (ConsentDeniedError, UserNotFoundError, UnknownPreferenceError) as e:
        return route_handler.handle_service_errors(e)


@router.post("/{user_id}")
async def set_preference(
    user_id: UUID,
    request_body: PreferenceRequest,
    request: Request,
    auth_context: dict = Depends(route_handler.get_auth_context),
    service: PreferenceService = Depends(route_handler.get_preference_service)
) -> SuccessResponse:
    """Set a preference value."""
    try:
        route_handler.check_user_authorization(auth_context, user_id)
        
        plan_id = request.headers.get("X-Plan-ID")
        
        response = await service.set_preference(
            user_id=user_id,
            preference_key=request_body.preference_key,
            preference_value=request_body.preference_value,
            sensitive=request_body.sensitive,
            plan_id=plan_id
        )
        
        logger.info(
            f"SET preference success: user={user_id}, "
            f"key={request_body.preference_key}, plan_id={plan_id}"
        )
        
        return SuccessResponse(
            data=response.model_dump(),
            tier=2,
            sensitive=response.sensitive
        )
        
    except (UserNotFoundError, UnknownPreferenceError, ValidationError) as e:
        return route_handler.handle_service_errors(e)


@router.delete("/{user_id}/{preference_key}")
async def delete_preference(
    user_id: UUID,
    preference_key: str,
    request: Request,
    auth_context: dict = Depends(route_handler.get_auth_context),
    service: PreferenceService = Depends(route_handler.get_preference_service)
) -> SuccessResponse:
    """Delete a preference."""
    try:
        route_handler.check_user_authorization(auth_context, user_id)
        
        plan_id = request.headers.get("X-Plan-ID")
        
        response = await service.delete_preference(
            user_id=user_id,
            preference_key=preference_key,
            plan_id=plan_id
        )
        
        logger.info(
            f"DELETE preference success: user={user_id}, "
            f"key={preference_key}, plan_id={plan_id}"
        )
        
        return SuccessResponse(
            data=response.model_dump(),
            tier=2,
            sensitive=False
        )
        
    except (UserNotFoundError, UnknownPreferenceError) as e:
        return route_handler.handle_service_errors(e)


@router.get("/{user_id}")
async def get_all_preferences(
    user_id: UUID,
    request: Request,
    auth_context: dict = Depends(route_handler.get_auth_context),
    service: PreferenceService = Depends(route_handler.get_preference_service)
) -> SuccessResponse:
    """Get all preferences for a user."""
    try:
        route_handler.check_user_authorization(auth_context, user_id)
        
        plan_id = request.headers.get("X-Plan-ID")
        
        evidence_items = await service.get_all_preferences(
            user_id=user_id,
            context_tier=auth_context["context_tier"],
            plan_id=plan_id
        )
        
        logger.info(
            f"GET all preferences success: user={user_id}, "
            f"count={len(evidence_items)}, plan_id={plan_id}"
        )
        
        return SuccessResponse(
            data=[item.model_dump() for item in evidence_items],
            tier=2,
            sensitive=False
        )
        
    except (ConsentDeniedError, UserNotFoundError) as e:
        return route_handler.handle_service_errors(e)


@router.get("/health")
async def health_check(
    service: PreferenceService = Depends(route_handler.get_preference_service)
) -> dict:
    """Health check endpoint."""
    try:
        health = await service.health_check()
        
        overall_healthy = all(
            "healthy" in str(status) for status in health.values()
        )
        
        health["overall"] = "healthy" if overall_healthy else "degraded"
        return health
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "overall": "unhealthy",
            "error": str(e)
        }