"""Shared API utilities, error handlers, and authentication."""

from .error_handlers import APIErrorHandler, ErrorHandlerMixin, ErrorResponse
from .auth import (
    get_auth_context, 
    get_user_id, 
    require_context_tier,
    verify_user_access,
    RequireTier2,
    RequireTier3, 
    RequireTier4
)

__all__ = [
    # Error handling
    "APIErrorHandler",
    "ErrorHandlerMixin", 
    "ErrorResponse",
    
    # Authentication
    "get_auth_context",
    "get_user_id",
    "require_context_tier", 
    "verify_user_access",
    "RequireTier2",
    "RequireTier3",
    "RequireTier4",
]