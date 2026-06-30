"""Shared API utilities, error handlers, and authentication."""

from .auth import (
    RequireTier2,
    RequireTier3,
    RequireTier4,
    get_auth_context,
    get_user_id,
    require_context_tier,
    verify_user_access,
)
from .error_handlers import APIErrorHandler, ErrorHandlerMixin, ErrorResponse

__all__ = [
    # Error handling
    "APIErrorHandler",
    "ErrorHandlerMixin",
    "ErrorResponse",
    "RequireTier2",
    "RequireTier3",
    "RequireTier4",
    # Authentication
    "get_auth_context",
    "get_user_id",
    "require_context_tier",
    "verify_user_access",
]
