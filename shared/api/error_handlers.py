"""
Shared API Error Handlers

Centralized error handling for FastAPI routes.
Provides consistent error responses across all components.
"""

import logging
from typing import Dict, Any
from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    """Standard error response format."""
    status: str = "error"
    error_code: str
    message: str
    details: Dict[str, Any] | None = None


class APIErrorHandler:
    """
    Centralized error handler for API routes.
    
    Provides consistent error responses and reduces code duplication.
    """
    
    @staticmethod
    def handle_user_not_found(error, user_id=None) -> JSONResponse:
        """Handle UserNotFoundError."""
        user_id = user_id or getattr(error, 'user_id', 'unknown')
        logger.warning(f"User not found: {error}")
        
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="USER_NOT_FOUND",
                message=str(error),
                details={"user_id": str(user_id)}
            ).model_dump()
        )
    
    @staticmethod
    def handle_unknown_preference(error, preference_key=None) -> JSONResponse:
        """Handle UnknownPreferenceError."""
        preference_key = preference_key or getattr(error, 'preference_key', 'unknown')
        logger.warning(f"Unknown preference: {error}")
        
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="UNKNOWN_PREFERENCE",
                message=str(error),
                details={"preference_key": preference_key}
            ).model_dump()
        )
    
    @staticmethod
    def handle_consent_denied(error) -> JSONResponse:
        """Handle ConsentDeniedError."""
        logger.warning(f"Consent denied: {error}")
        
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=ErrorResponse(
                error_code="CONSENT_DENIED",
                message=str(error),
                details={
                    "user_id": str(getattr(error, 'user_id', 'unknown')),
                    "required_tier": getattr(error, 'required_tier', 0),
                    "current_tier": getattr(error, 'current_tier', 0)
                }
            ).model_dump()
        )
    
    @staticmethod
    def handle_validation_error(error) -> JSONResponse:
        """Handle ValidationError."""
        logger.warning(f"Validation error: {error}")
        
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="VALIDATION_ERROR",
                message=str(error),
                details={
                    "preference_key": getattr(error, 'preference_key', 'unknown'),
                    "value": getattr(error, 'value', None),
                    "reason": getattr(error, 'reason', 'validation failed')
                }
            ).model_dump()
        )
    
    @staticmethod
    def handle_database_error(error) -> JSONResponse:
        """Handle database-related errors."""
        logger.error(f"Database error: {error}")
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="DATABASE_ERROR",
                message="Database operation failed",
                details={"error": str(error)}
            ).model_dump()
        )
    
    @staticmethod
    def handle_generic_error(error, error_code="INTERNAL_ERROR") -> JSONResponse:
        """Handle generic/unexpected errors."""
        logger.error(f"Unexpected error: {error}")
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code=error_code,
                message="An unexpected error occurred",
                details={"error": str(error)}
            ).model_dump()
        )


def create_error_handler_middleware():
    """
    Create error handler middleware for FastAPI applications.
    
    This middleware catches unhandled exceptions and provides
    consistent error responses.
    
    Usage:
        app = FastAPI()
        app.middleware("http")(create_error_handler_middleware())
    """
    async def error_handler_middleware(request, call_next):
        try:
            response = await call_next(request)
            return response
            
        except Exception as e:
            # Import here to avoid circular imports
            from shared.database.error_handler import (
                UserNotFoundError, DatabaseError
            )
            
            # Handle known error types
            if isinstance(e, UserNotFoundError):
                return APIErrorHandler.handle_user_not_found(e)
            elif isinstance(e, DatabaseError):
                return APIErrorHandler.handle_database_error(e)
            else:
                return APIErrorHandler.handle_generic_error(e)
    
    return error_handler_middleware


class ErrorHandlerMixin:
    """
    Mixin class for API route classes.
    
    Provides common error handling methods that can be used
    in route handlers to reduce code duplication.
    """
    
    def handle_service_errors(self, error) -> JSONResponse:
        """
        Handle errors from service layer.
        
        Args:
            error: Exception from service layer
            
        Returns:
            JSONResponse with appropriate error details
        """
        # Import here to avoid circular imports
        from shared.database.error_handler import (
            UserNotFoundError, DatabaseError
        )
        
        # Map service errors to appropriate HTTP responses
        error_type = type(error).__name__
        
        if isinstance(error, UserNotFoundError):
            return APIErrorHandler.handle_user_not_found(error)
        elif error_type == 'ConsentDeniedError':
            return APIErrorHandler.handle_consent_denied(error)
        elif error_type == 'UnknownPreferenceError':
            return APIErrorHandler.handle_unknown_preference(error)
        elif error_type == 'ValidationError':
            return APIErrorHandler.handle_validation_error(error)
        elif isinstance(error, DatabaseError):
            return APIErrorHandler.handle_database_error(error)
        else:
            return APIErrorHandler.handle_generic_error(error)


def api_error_handler(
    exceptions: tuple = None,
    error_code: str = None,
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
):
    """
    Decorator for route functions to handle specific exceptions.
    
    Args:
        exceptions: Tuple of exception types to catch
        error_code: Error code to return
        status_code: HTTP status code
        
    Usage:
        @api_error_handler(exceptions=(ValueError,), error_code="INVALID_INPUT")
        async def some_route():
            # Route logic here
            pass
    """
    if exceptions is None:
        exceptions = (Exception,)
    
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except exceptions as e:
                if error_code:
                    return JSONResponse(
                        status_code=status_code,
                        content=ErrorResponse(
                            error_code=error_code,
                            message=str(e)
                        ).model_dump()
                    )
                else:
                    return APIErrorHandler.handle_generic_error(e)
        return wrapper
    return decorator