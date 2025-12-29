"""
Tests for Evidence Item Schema (GLOBAL_SPEC §2.2)

Tests Pydantic validation and serialization of Evidence Items.
"""

import pytest
from pydantic import ValidationError

from shared.schemas.evidence import EvidenceItem


def test_evidence_item_valid():
    """Test creating valid evidence item."""
    evidence = EvidenceItem(
        type="preference",
        key="meeting_duration_min",
        value=30,
        confidence=1.0,
        source_ref="profilestore:prefs/meeting_duration_min",
        ttl_days=None,
        tier=2,
    )

    assert evidence.type == "preference"
    assert evidence.key == "meeting_duration_min"
    assert evidence.value == 30
    assert evidence.confidence == 1.0
    assert evidence.tier == 2


def test_evidence_item_all_types():
    """Test all valid evidence types."""
    types = ["preference", "history", "contact", "plan", "exemplar"]

    for evidence_type in types:
        evidence = EvidenceItem(
            type=evidence_type,
            key="test_key",
            value="test_value",
            confidence=0.8,
            source_ref="test:source",
            tier=1,
        )
        assert evidence.type == evidence_type


def test_evidence_item_invalid_type():
    """Test that invalid type raises ValidationError."""
    with pytest.raises(ValidationError):
        EvidenceItem(
            type="invalid_type",  # Not in allowed types
            key="test",
            value="value",
            confidence=0.5,
            source_ref="test",
            tier=1,
        )


def test_evidence_item_confidence_validation():
    """Test confidence must be between 0.0 and 1.0."""
    # Valid confidences
    for conf in [0.0, 0.5, 1.0]:
        evidence = EvidenceItem(
            type="preference",
            key="test",
            value="value",
            confidence=conf,
            source_ref="test",
            tier=1,
        )
        assert evidence.confidence == conf

    # Invalid confidences
    for conf in [-0.1, 1.1, 2.0]:
        with pytest.raises(ValidationError):
            EvidenceItem(
                type="preference",
                key="test",
                value="value",
                confidence=conf,
                source_ref="test",
                tier=1,
            )


def test_evidence_item_tier_validation():
    """Test tier must be between 1 and 4."""
    # Valid tiers
    for tier in [1, 2, 3, 4]:
        evidence = EvidenceItem(
            type="preference",
            key="test",
            value="value",
            confidence=0.5,
            source_ref="test",
            tier=tier,
        )
        assert evidence.tier == tier

    # Invalid tiers
    for tier in [0, 5, 10]:
        with pytest.raises(ValidationError):
            EvidenceItem(
                type="preference",
                key="test",
                value="value",
                confidence=0.5,
                source_ref="test",
                tier=tier,
            )


def test_evidence_item_ttl_days():
    """Test ttl_days can be None or positive integer."""
    # None (no expiry)
    evidence = EvidenceItem(
        type="preference",
        key="test",
        value="value",
        confidence=0.5,
        source_ref="test",
        tier=1,
        ttl_days=None,
    )
    assert evidence.ttl_days is None

    # Positive integers
    for ttl in [1, 30, 365]:
        evidence = EvidenceItem(
            type="preference",
            key="test",
            value="value",
            confidence=0.5,
            source_ref="test",
            tier=1,
            ttl_days=ttl,
        )
        assert evidence.ttl_days == ttl

    # Negative or zero ttl should fail
    for ttl in [0, -1]:
        with pytest.raises(ValidationError):
            EvidenceItem(
                type="preference",
                key="test",
                value="value",
                confidence=0.5,
                source_ref="test",
                tier=1,
                ttl_days=ttl,
            )


def test_evidence_item_value_types():
    """Test that value can be any JSON-serializable type."""
    test_values = [
        42,  # int
        3.14,  # float
        "string value",  # str
        True,  # bool
        ["list", "of", "values"],  # list
        {"key": "value"},  # dict
        None,  # null
    ]

    for test_value in test_values:
        evidence = EvidenceItem(
            type="preference",
            key="test",
            value=test_value,
            confidence=0.5,
            source_ref="test",
            tier=1,
        )
        assert evidence.value == test_value


def test_evidence_item_json_serialization():
    """Test JSON serialization/deserialization."""
    evidence = EvidenceItem(
        type="history",
        key="last_meeting",
        value="2025-12-20T10:00:00Z",
        confidence=0.95,
        source_ref="history:interactions/123",
        ttl_days=30,
        tier=3,
    )

    # Serialize to JSON
    json_str = evidence.model_dump_json()
    assert isinstance(json_str, str)
    assert "history" in json_str

    # Deserialize from JSON
    evidence_copy = EvidenceItem.model_validate_json(json_str)
    assert evidence_copy.type == evidence.type
    assert evidence_copy.key == evidence.key
    assert evidence_copy.value == evidence.value


def test_evidence_item_dict_conversion():
    """Test conversion to/from dict."""
    evidence = EvidenceItem(
        type="contact",
        key="alice_email",
        value="alice@company.com",
        confidence=1.0,
        source_ref="contacts:alice",
        tier=2,
    )

    # Convert to dict
    evidence_dict = evidence.model_dump()
    assert evidence_dict["type"] == "contact"
    assert evidence_dict["key"] == "alice_email"

    # Create from dict
    evidence_copy = EvidenceItem(**evidence_dict)
    assert evidence_copy.type == evidence.type
    assert evidence_copy.key == evidence.key


def test_evidence_item_missing_required_fields():
    """Test that missing required fields raises ValidationError."""
    with pytest.raises(ValidationError):
        EvidenceItem(
            type="preference",
            # Missing: key, value, confidence, source_ref, tier
        )


def test_evidence_item_key_length_validation():
    """Test key length constraints."""
    # Empty key should fail
    with pytest.raises(ValidationError):
        EvidenceItem(
            type="preference",
            key="",  # Empty
            value="value",
            confidence=0.5,
            source_ref="test",
            tier=1,
        )

    # Very long key (max 128 chars) should pass
    long_key = "a" * 128
    evidence = EvidenceItem(
        type="preference",
        key=long_key,
        value="value",
        confidence=0.5,
        source_ref="test",
        tier=1,
    )
    assert len(evidence.key) == 128

    # Exceeding max length should fail
    with pytest.raises(ValidationError):
        EvidenceItem(
            type="preference",
            key="a" * 129,  # Too long
            value="value",
            confidence=0.5,
            source_ref="test",
            tier=1,
        )
