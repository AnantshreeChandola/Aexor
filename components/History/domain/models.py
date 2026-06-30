"""
History Domain Models

Pydantic models for facts, patterns, requests, and responses.
Used for API validation and business logic.

Reference: LLD.md §2, tasks.md T101
"""

import hashlib
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

# Entity Models


class Fact(BaseModel):
    """
    Immutable record of a past action.

    Append-only with soft-delete on expiry.
    """

    fact_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    fact_text: str = Field(..., max_length=4096)
    intent_type: str = Field(..., max_length=64)
    entities: dict = Field(default_factory=dict)
    outcome: bool
    source_plan_id: str | None = Field(default=None, pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    fact_hash: str = Field(..., max_length=64)
    ttl_days: int = Field(default=30, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    deleted_at: datetime | None = None


class FactPattern(BaseModel):
    """
    Detected recurring behavioral pattern.

    Derived from Facts, updated incrementally on fact storage.
    """

    pattern_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    intent_type: str = Field(..., max_length=64)
    pattern_key: str = Field(..., max_length=128)
    pattern_description: str = Field(..., max_length=512)
    entity_pattern: dict = Field(default_factory=dict)
    occurrence_count: int = Field(default=1, ge=1)
    last_seen: datetime
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# Request/Response Models


class StoreFactRequest(BaseModel):
    """Request body for storing a fact."""

    fact_text: str = Field(..., min_length=1, max_length=4096)
    intent_type: str = Field(..., min_length=1, max_length=64)
    entities: dict = Field(default_factory=dict)
    outcome: bool
    source_plan_id: str | None = Field(default=None, pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    ttl_days: int = Field(default=30, ge=1, le=365)


class StoreFactResponse(BaseModel):
    """Response from storing a fact."""

    status: str = Field(..., pattern=r"^(ok|duplicate)$")
    fact_id: UUID
    stored_at: datetime

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Ensure status is ok or duplicate."""
        if v not in ("ok", "duplicate"):
            raise ValueError("status must be 'ok' or 'duplicate'")
        return v


class QueryFactsResponse(BaseModel):
    """Response containing queried facts as Evidence Items."""

    evidence: list = Field(default_factory=list)
    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)


class PatternsResponse(BaseModel):
    """Response containing detected patterns."""

    patterns: list = Field(default_factory=list)
    total_count: int = Field(ge=0)


# Error Classes


class HistoryError(Exception):
    """Base exception for History component."""


class FactTooLargeError(HistoryError):
    """Fact text exceeds 4KB limit."""

    def __init__(self, size: int):
        self.size = size
        super().__init__(f"Fact text exceeds 4KB limit: {size} bytes")


class InvalidTimestampError(HistoryError):
    """Timestamp is in the future beyond tolerance."""

    def __init__(self, timestamp: datetime):
        self.timestamp = timestamp
        super().__init__(f"Timestamp is in the future: {timestamp}")


class ConsentRequiredError(HistoryError):
    """User context_tier < 3."""

    def __init__(self, user_id: UUID, current_tier: int):
        self.user_id = user_id
        self.current_tier = current_tier
        super().__init__(f"User {user_id} requires Tier 3 consent (current: Tier {current_tier})")


class InvalidFactError(HistoryError):
    """Fact text is empty or invalid."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Invalid fact: {reason}")


class StorageError(HistoryError):
    """Database operation failed after retries."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Storage failed: {reason}")


class InvalidQueryError(HistoryError):
    """Invalid query parameters."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Invalid query: {reason}")


# Hash Computation Helper


def compute_fact_hash(user_id: UUID, intent_type: str, fact_text: str, date_val: date) -> str:
    """
    Compute SHA256 hash for fact deduplication.

    Date granularity is calendar day (not timestamp). Same fact on same day
    from retried plan execution deduplicates. Different days produce distinct facts.

    Args:
        user_id: User UUID
        intent_type: Intent type string
        fact_text: Fact text content
        date_val: Date (calendar day, not timestamp)

    Returns:
        SHA256 hex digest (64 characters)
    """
    hash_input = f"{user_id}{intent_type}{fact_text}{date_val.isoformat()}"
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
