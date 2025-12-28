"""
Auth Middleware - Header-Based MVP Implementation

Extracts user identity and context tier from request headers.
This is a simple MVP implementation for development.

TODO: Replace with JWT-based authentication in Phase 2 (production).

Reference: SHARED_INFRASTRUCTURE.md §2.1
"""

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Extract user context from request headers (MVP implementation).

    Reads the following headers:
    - X-User-ID: UUID of authenticated user (required)
    - X-Context-Tier: Consent tier (1-4, default: 1)
    - X-User-Email: User email (optional, for logging)

    Adds to request.state:
    - user_id: UUID
    - context_tier: int
    - email: str

    Returns 401 if X-User-ID header is missing or invalid.

    Example:
        >>> # In client request:
        >>> headers = {
        >>>     "X-User-ID": "b14025d0-e491-4558-a4d2-ce70609a6a92",
        >>>     "X-Context-Tier": "3",
        >>>     "X-User-Email": "test@example.com"
        >>> }

        >>> # In route handler:
        >>> user_id = request.state.user_id  # UUID
        >>> context_tier = request.state.context_tier  # int
    """

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health check and docs
        if request.url.path in ["/health", "/docs", "/redoc", "/openapi.json"]:
            return await call_next(request)

        # Extract user ID (required)
        user_id_str = request.headers.get("X-User-ID")
        if not user_id_str:
            logger.warning(f"Missing X-User-ID header for {request.url.path}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing X-User-ID header"},
            )

        # Validate and parse user ID
        try:
            user_id = UUID(user_id_str)
        except ValueError:
            logger.warning(f"Invalid X-User-ID format: {user_id_str}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid X-User-ID format"},
            )

        # Extract context tier (optional, default: 1)
        context_tier_str = request.headers.get("X-Context-Tier", "1")
        try:
            context_tier = int(context_tier_str)
            if context_tier < 1 or context_tier > 4:
                raise ValueError("Context tier must be between 1 and 4")
        except ValueError as e:
            logger.warning(f"Invalid X-Context-Tier: {context_tier_str}, using default tier 1")
            context_tier = 1

        # Extract email (optional)
        email = request.headers.get("X-User-Email", "unknown@example.com")

        # Add to request state
        request.state.user_id = user_id
        request.state.context_tier = context_tier
        request.state.email = email

        logger.debug(
            f"Authenticated request: user_id={user_id}, "
            f"context_tier={context_tier}, path={request.url.path}"
        )

        # Continue processing request
        response = await call_next(request)
        return response
