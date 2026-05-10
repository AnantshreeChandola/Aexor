"""
Intake API Routes

Thin FastAPI wrappers around IntakeService for message submission,
session reset, and health checking.

Reference: LLD Section 4.1
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from shared.api.auth import get_auth_context
from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import get_intake_service

from ..domain.models import (
    IntakeMessage,
    IntentParserError,
    MaxTurnsExceededError,
    RateLimitedError,
    SessionNotFoundError,
    SessionOwnershipError,
    SessionResetResponse,
    SessionStoreUnavailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intake", tags=["intake"])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _handle_domain_error(exc: Exception) -> JSONResponse:
    """Map Intake domain exceptions to HTTP error responses."""
    if isinstance(exc, SessionNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error_code="SESSION_NOT_FOUND",
                message=str(exc),
                details={"session_id": exc.session_id},
            ).model_dump(),
        )
    if isinstance(exc, SessionOwnershipError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=ErrorResponse(
                error_code="SESSION_OWNERSHIP_DENIED",
                message=str(exc),
                details={
                    "session_id": exc.session_id,
                    "user_id": exc.user_id,
                },
            ).model_dump(),
        )
    if isinstance(exc, MaxTurnsExceededError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error_code="MAX_TURNS_EXCEEDED",
                message=str(exc),
                details={
                    "session_id": exc.session_id,
                    "max_turns": exc.max_turns,
                },
            ).model_dump(),
        )
    if isinstance(exc, SessionStoreUnavailableError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ErrorResponse(
                error_code="SESSION_STORE_UNAVAILABLE",
                message=str(exc),
                details={"reason": exc.reason},
            ).model_dump(),
        )
    if isinstance(exc, RateLimitedError):
        headers: dict[str, str] = {}
        if exc.retry_after_s is not None:
            headers["Retry-After"] = str(int(exc.retry_after_s))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=ErrorResponse(
                error_code="LLM_RATE_LIMITED",
                message=(
                    f"The {exc.provider} API rate-limited this request. "
                    "Please wait or switch providers via LLM_PROVIDER / LLM_API_KEY."
                ),
                details={
                    "provider": exc.provider,
                    "model": exc.model,
                    "retry_after_s": exc.retry_after_s,
                },
            ).model_dump(),
            headers=headers,
        )
    if isinstance(exc, IntentParserError):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error_code="INTENT_PARSER_ERROR",
                message="Intent parsing failed",
                details={"reason": exc.reason},
            ).model_dump(),
        )
    return APIErrorHandler.handle_generic_error(exc)


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@router.post("/message")
async def submit_message(
    request: Request,
    body: IntakeMessage,
    auth_context: dict = Depends(get_auth_context),
    service=Depends(get_intake_service),
):
    """Submit a user message for intent detection / clarification."""
    user_id = str(auth_context["user_id"])
    tz = request.headers.get("X-Timezone", "America/Chicago")

    t_start = time.monotonic()
    logger.info(
        "intake_api_request",
        extra={
            "user_id": user_id,
            "message_length": len(body.message),
            "session_id": body.session_id,
            "tz": tz,
        },
    )

    try:
        response = await service.process_message(
            user_id=user_id,
            message=body.message,
            session_id=body.session_id,
            tz=tz,
        )
        t_total = int((time.monotonic() - t_start) * 1000)
        logger.info(
            "intake_api_response",
            extra={
                "user_id": user_id,
                "session_id": response.session_id,
                "status": response.status,
                "total_api_ms": t_total,
            },
        )
        return response.model_dump(mode="json")
    except (
        SessionNotFoundError,
        SessionOwnershipError,
        MaxTurnsExceededError,
        SessionStoreUnavailableError,
        RateLimitedError,
        IntentParserError,
    ) as exc:
        t_total = int((time.monotonic() - t_start) * 1000)
        logger.warning(
            "intake_api_error",
            extra={
                "user_id": user_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "total_api_ms": t_total,
            },
        )
        return _handle_domain_error(exc)


@router.delete("/session/{session_id}")
async def reset_session(
    session_id: str,
    auth_context: dict = Depends(get_auth_context),
    service=Depends(get_intake_service),
):
    """Delete a conversation session."""
    user_id = str(auth_context["user_id"])

    try:
        await service.reset_session(user_id, session_id)
        return SessionResetResponse(session_id=session_id).model_dump(mode="json")
    except (
        SessionNotFoundError,
        SessionOwnershipError,
        SessionStoreUnavailableError,
    ) as exc:
        return _handle_domain_error(exc)


@router.get("/health")
async def health_check():
    """Health check for Intake service."""
    return {"status": "ok", "service": "intake"}
