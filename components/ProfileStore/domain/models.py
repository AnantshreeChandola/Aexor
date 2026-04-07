"""
ProfileStore Domain Models

Pydantic models for preferences, requests, and responses.
Used for API validation and database operations.

Reference: LLD.md §5.3
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import UUID4, BaseModel, Field, field_validator


class PreferenceDB(BaseModel):
    """
    Database model for preference records.

    Maps to preferences table in PostgreSQL.
    Used by DatabaseAdapter for type-safe queries.
    """

    preference_id: UUID4
    user_id: UUID4
    key: str = Field(..., max_length=64)
    value: Any  # JSONB field
    sensitive: bool = Field(default=False)
    updated_at: datetime
    deleted_at: datetime | None = Field(default=None)

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        """Validate preference key is alphanumeric + underscore."""
        if not v.replace("_", "").isalnum():
            raise ValueError("Preference key must be alphanumeric with underscores")
        return v


class PreferenceRequest(BaseModel):
    """
    Request model for setting preferences.

    Used by API routes for request validation.
    """

    preference_key: str = Field(..., max_length=64)
    preference_value: Any = Field(...)
    sensitive: bool = Field(default=False)

    @field_validator("preference_key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        """Validate preference key format."""
        if not v.replace("_", "").isalnum():
            raise ValueError("Preference key must be alphanumeric with underscores")
        return v


class PreferenceResponse(BaseModel):
    """
    Response model for preference operations.

    Returned by SET and DELETE operations.
    Does not include sensitive preference values.
    """

    preference_id: UUID4
    user_id: UUID4
    preference_key: str
    preference_value: Any
    updated_at: datetime
    sensitive: bool


class DeleteResponse(BaseModel):
    """Response model for DELETE preference operations."""

    user_id: UUID4
    preference_key: str
    deleted_at: datetime
    message: str = "Preference deleted successfully"


class ConsentDeniedError(Exception):
    """Raised when user hasn't granted required consent tier."""

    def __init__(self, user_id: UUID4, required_tier: int, current_tier: int):
        self.user_id = user_id
        self.required_tier = required_tier
        self.current_tier = current_tier
        super().__init__(
            f"User {user_id} has not granted Tier {required_tier} consent "
            f"(current: Tier {current_tier})"
        )


class UnknownPreferenceError(Exception):
    """Raised when preference key is not in schema registry."""

    def __init__(self, preference_key: str):
        self.preference_key = preference_key
        super().__init__(f"Unknown preference key: {preference_key}")


class ValidationError(Exception):
    """Raised when preference value fails schema validation."""

    def __init__(self, preference_key: str, value: Any, reason: str):
        self.preference_key = preference_key
        self.value = value
        self.reason = reason
        super().__init__(f"Validation failed for {preference_key}: {reason}")


class ErrorResponse(BaseModel):
    """Standard error response format."""

    status: Literal["error"] = "error"
    error_code: str
    message: str
    details: dict[str, Any] | None = None


class SuccessResponse(BaseModel):
    """Standard success response wrapper."""

    status: Literal["ok"] = "ok"
    data: Any
