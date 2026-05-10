"""
Intake Domain Models and Error Hierarchy

Pydantic v2 models for session management, message parsing,
and response formatting. Error hierarchy for domain exceptions.

Reference: LLD Section 5, SPEC FR-001/FR-002/FR-005/FR-008
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import ulid
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Value objects / DTOs
# ---------------------------------------------------------------------------


class SessionTurn(BaseModel):
    """A single turn in a conversation session."""

    message: str
    assistant_response: str | None = None
    timestamp: datetime
    extracted_intent: str | None = None
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    extracted_constraints: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """Multi-turn conversation session stored in Redis."""

    session_id: str = Field(
        default_factory=lambda: f"ses_{ulid.new()!s}",
    )
    user_id: str
    turns: list[SessionTurn] = Field(default_factory=list)
    detected_intent: str | None = None
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    extracted_constraints: dict[str, Any] = Field(default_factory=dict)
    sub_intents: list[str] = Field(default_factory=list)
    routing_tier: str | None = None  # "local" or "remote" (which LLM served Turn 1)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )


class IntakeMessage(BaseModel):
    """Incoming user message (request body)."""

    message: str = Field(..., min_length=1, max_length=10_000)
    session_id: str | None = None


class IntakeResponse(BaseModel):
    """Response to user (collecting or ready)."""

    status: Literal["collecting", "ready"]
    session_id: str
    detected_intent: str | None = None
    collected_entities: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    follow_up: str | None = None
    turn_count: int = 0
    intent: dict[str, Any] | None = None


class SessionResetResponse(BaseModel):
    """Response for session reset."""

    status: Literal["reset"] = "reset"
    session_id: str


class ParseResult(BaseModel):
    """Result from intent parser (LLM extraction)."""

    intent: str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    sub_intents: list[str] = Field(default_factory=list)
    confidence: float | None = None  # 0.0-1.0, None if LLM didn't provide
    escalated: bool = False  # True if remote LLM was used due to low confidence


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class IntakeError(Exception):
    """Base error for Intake component."""


class SessionNotFoundError(IntakeError):
    """Session does not exist."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class SessionOwnershipError(IntakeError):
    """User does not own the requested session."""

    def __init__(self, session_id: str, user_id: str) -> None:
        self.session_id = session_id
        self.user_id = user_id
        super().__init__(f"User {user_id} does not own session {session_id}")


class MaxTurnsExceededError(IntakeError):
    """Session has exceeded the maximum number of turns."""

    def __init__(self, session_id: str, max_turns: int = 20) -> None:
        self.session_id = session_id
        self.max_turns = max_turns
        super().__init__(f"Session {session_id} exceeded max turns ({max_turns})")


class SessionStoreUnavailableError(IntakeError):
    """Redis session store is unreachable."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Session store unavailable: {reason}")


class IntentParserError(IntakeError):
    """LLM-based intent parser failed."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Intent parser error: {reason}")


class RateLimitedError(IntakeError):
    """Upstream LLM provider rejected the request due to rate limiting.

    Surfaced to the API layer so the UI can show the exact provider-side
    error (HTTP 429 with Retry-After) instead of silently falling back to
    a "Gathering information" loop.
    """

    def __init__(
        self,
        provider: str,
        model: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.retry_after_s = retry_after_s
        parts = [f"{provider} rate limited"]
        if model:
            parts.append(f"(model={model})")
        if retry_after_s is not None:
            parts.append(f"retry in {retry_after_s:.0f}s")
        super().__init__(" ".join(parts))


