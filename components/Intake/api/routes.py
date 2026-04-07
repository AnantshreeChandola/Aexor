"""
Intake API Routes

Thin FastAPI wrappers around IntakeService for message submission,
session reset, and health checking.

Reference: LLD Section 4.1
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from shared.api.auth import get_auth_context
from shared.api.error_handlers import APIErrorHandler, ErrorResponse
from shared.dependencies import get_intake_service

from ..domain.models import (
    IntakeMessage,
    IntentParserError,
    MaxTurnsExceededError,
    SessionNotFoundError,
    SessionOwnershipError,
    SessionResetResponse,
    SessionStoreUnavailableError,
    ToolNotAvailableError,
)
from ..service.intake_service import ProviderNotConnectedError

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
    if isinstance(exc, ToolNotAvailableError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error_code="TOOL_NOT_AVAILABLE",
                message=str(exc),
                details={
                    "intent_type": exc.intent_type,
                    "required_tools": exc.required_tools,
                },
            ).model_dump(),
        )
    if isinstance(exc, ProviderNotConnectedError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                error_code="PROVIDER_NOT_CONNECTED",
                message=str(exc),
                details={"provider_names": exc.provider_names},
            ).model_dump(),
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
    """Submit a user message for intent collection."""
    user_id = str(auth_context["user_id"])
    context_tier = auth_context["context_tier"]
    tz = request.headers.get("X-Timezone", "America/Chicago")

    try:
        response = await service.process_message(
            user_id=user_id,
            message=body.message,
            context_tier=context_tier,
            session_id=body.session_id,
            tz=tz,
        )
        return response.model_dump(mode="json")
    except (
        SessionNotFoundError,
        SessionOwnershipError,
        MaxTurnsExceededError,
        SessionStoreUnavailableError,
        ToolNotAvailableError,
        ProviderNotConnectedError,
        IntentParserError,
    ) as exc:
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
