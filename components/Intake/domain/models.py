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
    profile_defaults_offered: dict[str, Any] = Field(default_factory=dict)
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
        super().__init__(
            f"User {user_id} does not own session {session_id}"
        )


class MaxTurnsExceededError(IntakeError):
    """Session has exceeded the maximum number of turns."""

    def __init__(self, session_id: str, max_turns: int = 20) -> None:
        self.session_id = session_id
        self.max_turns = max_turns
        super().__init__(
            f"Session {session_id} exceeded max turns ({max_turns})"
        )


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


class ToolNotAvailableError(IntakeError):
    """Intent requires tools not registered in PluginRegistry."""

    def __init__(
        self,
        intent_type: str,
        required_tools: list[str],
    ) -> None:
        self.intent_type = intent_type
        self.required_tools = required_tools
        tools_str = ", ".join(required_tools) if required_tools else "unknown"
        super().__init__(
            f"No registered tools can fulfill intent '{intent_type}'. "
            f"Required tools: {tools_str}"
        )
