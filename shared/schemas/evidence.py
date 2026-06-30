"""
Evidence Item Schema - GLOBAL_SPEC §2.2 Implementation

Pydantic model for Evidence Items used across the system.
Used by ProfileStore, History, ContextRAG, and Planner.

Reference: GLOBAL_SPEC.md §2.2
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class EvidenceItem(BaseModel):
    """
    Evidence Item contract (GLOBAL_SPEC §2.2).

    Used by ContextRAG to provide typed context to the Planner.
    Components like ProfileStore, History return data in this format.

    Fields:
        type: Evidence type (preference, history, contact, plan, exemplar)
        key: Unique key for this evidence (e.g., "meeting_duration_min")
        value: Actual value (can be any JSON-serializable type)
        confidence: Confidence score (0.0 to 1.0)
        source_ref: Reference to data source (e.g., "profilestore:prefs/key")
        ttl_days: Time-to-live in days (None = no expiry)
        tier: Context tier (1-4, see GLOBAL_SPEC §7)

    Example:
        >>> evidence = EvidenceItem(
        >>>     type="preference",
        >>>     key="meeting_duration_min",
        >>>     value=30,
        >>>     confidence=1.0,
        >>>     source_ref="profilestore:prefs/meeting_duration_min",
        >>>     ttl_days=None,
        >>>     tier=2
        >>> )
        >>> print(evidence.model_dump_json())
    """

    type: Literal["preference", "history", "contact", "plan", "exemplar"] = Field(
        ..., description="Type of evidence item"
    )

    key: str = Field(
        ..., min_length=1, max_length=128, description="Unique key identifying this evidence"
    )

    value: Any = Field(..., description="Evidence value (can be any JSON-serializable type)")

    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score (0.0 = uncertain, 1.0 = certain)"
    )

    source_ref: str = Field(
        ..., min_length=1, description="Reference to data source (e.g., 'profilestore:prefs/key')"
    )

    ttl_days: int | None = Field(
        default=None, ge=1, description="Time-to-live in days (None = no expiry)"
    )

    tier: int = Field(
        ..., ge=1, le=4, description="Context tier (1=session, 2=prefs, 3=history, 4=live)"
    )

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: int) -> int:
        """Validate context tier is within valid range."""
        if v < 1 or v > 4:
            raise ValueError(f"Context tier must be between 1 and 4, got {v}")
        return v

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Validate confidence is between 0.0 and 1.0."""
        if v < 0.0 or v > 1.0:
            raise ValueError(f"Confidence must be between 0.0 and 1.0, got {v}")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "type": "preference",
                    "key": "meeting_duration_min",
                    "value": 30,
                    "confidence": 1.0,
                    "source_ref": "profilestore:prefs/meeting_duration_min",
                    "ttl_days": None,
                    "tier": 2,
                },
                {
                    "type": "history",
                    "key": "last_meeting_with_alice",
                    "value": "2025-12-20T10:00:00Z",
                    "confidence": 0.95,
                    "source_ref": "history:interactions/alice-123",
                    "ttl_days": 30,
                    "tier": 3,
                },
                {
                    "type": "contact",
                    "key": "alice_email",
                    "value": "alice@company.com",
                    "confidence": 1.0,
                    "source_ref": "contacts:alice",
                    "ttl_days": None,
                    "tier": 2,
                },
            ]
        }
    }
