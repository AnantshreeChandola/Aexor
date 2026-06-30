"""
Auth Middleware — JWT Bearer Token Validation

Validates Authorization: Bearer <jwt> header on every request.
Extracts claims and populates request.state identically to Phase 1,
so all downstream components are unchanged.

Reference: SHARED_INFRASTRUCTURE.md §2.1 Phase 2
"""

import logging
import os
from uuid import UUID

from fastapi import Request, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"

# Paths that bypass authentication entirely
_BYPASS_PATHS = frozenset(
    [
        "/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/auth/token",
        "/auth/register",
        # OAuth landing page — hit by the browser after a Composio redirect
        # without any Authorization header. Returns static HTML only, no
        # user state is read or written.
        "/api/integrations/callback",
    ]
)

# Path prefixes that bypass authentication
_BYPASS_PREFIXES = ("/static",)


def _get_jwt_secret() -> str:
    """Get JWT secret from environment."""
    return os.environ.get("JWT_SECRET", "")


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Validate JWT Bearer token and populate request.state.

    Populates:
        request.state.user_id     : UUID
        request.state.context_tier: int
        request.state.email       : str

    Returns HTTP 401 for missing/invalid/expired tokens.
    Bypasses auth for health, docs, and auth endpoints.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public endpoints
        if request.url.path in _BYPASS_PATHS:
            return await call_next(request)

        # Skip auth for static file paths
        if request.url.path.startswith(_BYPASS_PREFIXES):
            return await call_next(request)

        # Also bypass component-level health endpoints
        if request.url.path.endswith("/health"):
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning(
                "Missing or malformed Authorization header",
                extra={"path": request.url.path},
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.split(" ", 1)[1]
        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Decode and validate JWT
        secret = _get_jwt_secret()
        if not secret:
            logger.error("JWT_SECRET not configured")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Authentication not configured"},
            )

        try:
            payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        except JWTError as exc:
            logger.warning(
                "JWT validation failed",
                extra={"path": request.url.path, "error": str(exc)},
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or expired token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract required claims
        sub = payload.get("sub")
        email = payload.get("email")
        context_tier = payload.get("context_tier")

        if not sub or not email or context_tier is None:
            logger.warning(
                "JWT missing required claims",
                extra={"path": request.url.path},
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token missing required claims"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate sub is a valid UUID
        try:
            user_id = UUID(sub)
        except ValueError:
            logger.warning(
                "JWT sub claim is not a valid UUID",
                extra={"sub": sub},
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid token subject"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Clamp context_tier to valid range
        if not isinstance(context_tier, int) or not (1 <= context_tier <= 4):
            context_tier = 1

        # Populate request.state — same interface as Phase 1
        request.state.user_id = user_id
        request.state.context_tier = context_tier
        request.state.email = email

        logger.debug(
            "JWT authenticated",
            extra={
                "user_id": str(user_id),
                "context_tier": context_tier,
                "path": request.url.path,
            },
        )

        return await call_next(request)
