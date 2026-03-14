"""
Shared Authentication Utilities

Common authentication functions and dependencies for FastAPI routes.
Provides consistent auth context extraction across all components.
"""

import logging
from uuid import UUID

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


def get_auth_context(request: Request) -> dict[str, any]:
    """
    Extract authentication context from request.

    Expects auth middleware to populate request.state with:
    - user_id: UUID
    - context_tier: int
    - email: str

    Args:
        request: FastAPI request object with populated state

    Returns:
        Dict containing user_id, context_tier, and email

    Raises:
        HTTPException: 401 if authentication context missing

    Usage:
        @router.get("/endpoint")
        async def my_endpoint(auth_context: dict = Depends(get_auth_context)):
            user_id = auth_context["user_id"]
            context_tier = auth_context["context_tier"]
    """
    if not hasattr(request.state, "user_id"):
        logger.warning("Authentication required but auth context missing from request.state")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    # Validate required auth state attributes
    required_attrs = ["user_id", "context_tier", "email"]
    missing_attrs = [attr for attr in required_attrs if not hasattr(request.state, attr)]

    if missing_attrs:
        logger.error(f"Incomplete auth context: missing {missing_attrs}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Incomplete authentication context: missing {missing_attrs}",
        )

    return {
        "user_id": request.state.user_id,
        "context_tier": request.state.context_tier,
        "email": request.state.email,
    }


def get_user_id(request: Request) -> UUID:
    """
    Extract just the user_id from auth context.

    Convenience function for endpoints that only need user_id.

    Args:
        request: FastAPI request object

    Returns:
        UUID of authenticated user

    Raises:
        HTTPException: 401 if authentication missing

    Usage:
        @router.get("/endpoint")
        async def my_endpoint(user_id: UUID = Depends(get_user_id)):
            # Use user_id directly
    """
    auth_context = get_auth_context(request)
    return auth_context["user_id"]


def require_context_tier(min_tier: int):
    """
    Dependency factory for context tier enforcement.

    Creates a FastAPI dependency that enforces minimum context tier.

    Args:
        min_tier: Minimum required context tier (1-4)

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: 403 if user's context tier insufficient

    Usage:
        @router.get("/sensitive-endpoint")
        async def sensitive_endpoint(
            auth_context: dict = Depends(get_auth_context),
            _: None = Depends(require_context_tier(2))  # Requires Tier 2+
        ):
            # Endpoint logic here
    """

    def context_tier_dependency(request: Request):
        auth_context = get_auth_context(request)
        current_tier = auth_context["context_tier"]

        if current_tier < min_tier:
            logger.warning(
                f"Context tier insufficient: user {auth_context['user_id']} "
                f"has tier {current_tier}, requires {min_tier}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires context tier {min_tier} or higher (current: {current_tier})",
            )

        return None  # Dependency satisfied

    return context_tier_dependency


def verify_user_access(target_user_id: UUID, auth_context: dict[str, any]) -> None:
    """
    Verify that authenticated user can access target user's resources.

    Prevents users from accessing other users' data.

    Args:
        target_user_id: The user_id being accessed
        auth_context: Authentication context from get_auth_context()

    Raises:
        HTTPException: 403 if user cannot access target user's data

    Usage:
        @router.get("/{user_id}/preferences")
        async def get_preferences(
            user_id: UUID,
            auth_context: dict = Depends(get_auth_context)
        ):
            verify_user_access(user_id, auth_context)
            # Proceed with operation
    """
    if auth_context["user_id"] != target_user_id:
        logger.warning(
            f"User {auth_context['user_id']} attempted to access "
            f"resources for user {target_user_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot access other users' resources"
        )


# Convenience dependencies for common patterns
RequireTier2 = require_context_tier(2)
RequireTier3 = require_context_tier(3)
RequireTier4 = require_context_tier(4)
