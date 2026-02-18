"""
PlanLibrary API Error Handlers

Component-specific error handler methods extending shared patterns.

Reference: tasks.md T401
"""

import logging

from fastapi import status
from fastapi.responses import JSONResponse

from shared.api.error_handlers import ErrorResponse

from ..domain.models import (
    DuplicatePlanError,
    InvalidQueryError,
    InvalidSignatureError,
    PlanNotFoundError,
    PlanTooLargeError,
)

logger = logging.getLogger(__name__)


class PlanLibraryErrorHandler:
    """PlanLibrary-specific error handler methods."""

    @staticmethod
    def handle_invalid_signature(error: InvalidSignatureError) -> JSONResponse:
        """Handle InvalidSignatureError (400)."""
        logger.warning(
            "Invalid signature",
            extra={
                "plan_id": error.plan_id,
                "reason": error.reason,
                "component": "PlanLibrary",
            },
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="INVALID_SIGNATURE",
                message=str(error),
                details={
                    "plan_id": error.plan_id,
                    "reason": error.reason,
                },
            ).model_dump(),
        )

    @staticmethod
    def handle_duplicate_plan(error: DuplicatePlanError) -> JSONResponse:
        """Handle DuplicatePlanError (409)."""
        logger.warning(
            "Duplicate plan",
            extra={
                "plan_id": error.plan_id,
                "component": "PlanLibrary",
            },
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(
                error_code="DUPLICATE_PLAN_ID",
                message=str(error),
                details={"plan_id": error.plan_id},
            ).model_dump(),
        )

    @staticmethod
    def handle_plan_too_large(error: PlanTooLargeError) -> JSONResponse:
        """Handle PlanTooLargeError (413)."""
        logger.warning(
            "Plan too large",
            extra={
                "plan_id": error.plan_id,
                "reason": error.reason,
                "component": "PlanLibrary",
            },
        )
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content=ErrorResponse(
                error_code="PLAN_TOO_LARGE",
                message=str(error),
                details={
                    "plan_id": error.plan_id,
                    "reason": error.reason,
                },
            ).model_dump(),
        )

    @staticmethod
    def handle_invalid_query(error: InvalidQueryError) -> JSONResponse:
        """Handle InvalidQueryError (400)."""
        logger.warning(
            "Invalid query",
            extra={
                "reason": error.reason,
                "component": "PlanLibrary",
            },
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="INVALID_QUERY",
                message=str(error),
                details={"reason": error.reason},
            ).model_dump(),
        )

    @staticmethod
    def handle_plan_not_found(error: PlanNotFoundError) -> JSONResponse:
        """Handle PlanNotFoundError (404)."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="PLAN_NOT_FOUND",
                message=str(error),
                details={"plan_id": error.plan_id},
            ).model_dump(),
        )

    def handle_service_errors(self, error: Exception) -> JSONResponse:
        """
        Handle all PlanLibrary service errors.

        Routes to appropriate handler based on error type.

        Args:
            error: Exception from service layer

        Returns:
            JSONResponse with appropriate error details
        """
        if isinstance(error, InvalidSignatureError):
            return self.handle_invalid_signature(error)
        if isinstance(error, DuplicatePlanError):
            return self.handle_duplicate_plan(error)
        if isinstance(error, PlanTooLargeError):
            return self.handle_plan_too_large(error)
        if isinstance(error, InvalidQueryError):
            return self.handle_invalid_query(error)
        if isinstance(error, PlanNotFoundError):
            return self.handle_plan_not_found(error)
        if isinstance(error, ValueError):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=ErrorResponse(
                    error_code="VALIDATION_ERROR",
                    message=str(error),
                ).model_dump(),
            )

        # Fallback to generic error
        from shared.api.error_handlers import APIErrorHandler

        return APIErrorHandler.handle_generic_error(error)
