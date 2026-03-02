"""
Tests for History Domain Models

Test all Pydantic model validation, error classes, and hash computation.

Reference: tasks.md T103
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from ..domain.models import (
    ConsentRequiredError,
    Fact,
    FactPattern,
    FactTooLargeError,
    HistoryError,
    InvalidFactError,
    InvalidQueryError,
    InvalidTimestampError,
    PatternsResponse,
    QueryFactsResponse,
    StorageError,
    StoreFactRequest,
    StoreFactResponse,
    compute_fact_hash,
)

# Fact Model Tests


def test_fact_model_valid_creation():
    """Test creating a valid Fact instance."""
    user_id = uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=30)

    fact = Fact(
        user_id=user_id,
        fact_text="Booked 30min meeting with Alice",
        intent_type="schedule_meeting",
        entities={"person": "Alice"},
        outcome=True,
        source_plan_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        fact_hash="abc123",
        ttl_days=30,
        created_at=now,
        expires_at=expires_at,
    )

    assert fact.user_id == user_id
    assert fact.fact_text == "Booked 30min meeting with Alice"
    assert fact.intent_type == "schedule_meeting"
    assert fact.entities == {"person": "Alice"}
    assert fact.outcome is True
    assert fact.ttl_days == 30


def test_fact_text_max_length_enforcement():
    """Test fact_text max_length validation (>4096 rejected)."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # 4097 characters (exceeds 4096 limit)
    long_text = "a" * 4097

    with pytest.raises(ValueError):
        Fact(
            user_id=user_id,
            fact_text=long_text,
            intent_type="test",
            entities={},
            outcome=True,
            fact_hash="test",
            created_at=now,
            expires_at=now + timedelta(days=30),
        )


def test_fact_intent_type_max_length_enforcement():
    """Test intent_type max_length validation."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # 65 characters (exceeds 64 limit)
    long_intent = "a" * 65

    with pytest.raises(ValueError):
        Fact(
            user_id=user_id,
            fact_text="test",
            intent_type=long_intent,
            entities={},
            outcome=True,
            fact_hash="test",
            created_at=now,
            expires_at=now + timedelta(days=30),
        )


def test_fact_source_plan_id_ulid_pattern():
    """Test source_plan_id ULID pattern validation."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Invalid ULID format
    with pytest.raises(ValueError):
        Fact(
            user_id=user_id,
            fact_text="test",
            intent_type="test",
            entities={},
            outcome=True,
            source_plan_id="invalid-ulid",
            fact_hash="test",
            created_at=now,
            expires_at=now + timedelta(days=30),
        )


def test_fact_ttl_days_range_validation():
    """Test ttl_days minimum validation."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # ttl_days < 1 rejected
    with pytest.raises(ValueError):
        Fact(
            user_id=user_id,
            fact_text="test",
            intent_type="test",
            entities={},
            outcome=True,
            fact_hash="test",
            ttl_days=0,
            created_at=now,
            expires_at=now,
        )


# FactPattern Model Tests


def test_fact_pattern_valid_creation():
    """Test creating a valid FactPattern instance."""
    user_id = uuid4()
    now = datetime.now(UTC)

    pattern = FactPattern(
        user_id=user_id,
        intent_type="schedule_meeting",
        pattern_key="schedule_meeting:person:Alice:Tuesday",
        pattern_description="Usually meets Alice on Tuesdays",
        entity_pattern={"person": "Alice", "day": "Tuesday"},
        occurrence_count=5,
        last_seen=now,
        confidence=1.0,
    )

    assert pattern.user_id == user_id
    assert pattern.occurrence_count == 5
    assert pattern.confidence == 1.0


def test_fact_pattern_confidence_range():
    """Test confidence range (0.0-1.0)."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # confidence > 1.0 rejected
    with pytest.raises(ValueError):
        FactPattern(
            user_id=user_id,
            intent_type="test",
            pattern_key="test",
            pattern_description="test",
            entity_pattern={},
            occurrence_count=1,
            last_seen=now,
            confidence=1.5,
        )

    # confidence < 0.0 rejected
    with pytest.raises(ValueError):
        FactPattern(
            user_id=user_id,
            intent_type="test",
            pattern_key="test",
            pattern_description="test",
            entity_pattern={},
            occurrence_count=1,
            last_seen=now,
            confidence=-0.1,
        )


def test_fact_pattern_occurrence_count_minimum():
    """Test occurrence_count minimum (>=1)."""
    user_id = uuid4()
    now = datetime.now(UTC)

    with pytest.raises(ValueError):
        FactPattern(
            user_id=user_id,
            intent_type="test",
            pattern_key="test",
            pattern_description="test",
            entity_pattern={},
            occurrence_count=0,
            last_seen=now,
            confidence=0.5,
        )


# StoreFactRequest Model Tests


def test_store_fact_request_valid_creation():
    """Test creating a valid StoreFactRequest instance."""
    request = StoreFactRequest(
        fact_text="Booked meeting",
        intent_type="schedule_meeting",
        entities={"person": "Alice"},
        outcome=True,
        source_plan_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        ttl_days=30,
    )

    assert request.fact_text == "Booked meeting"
    assert request.ttl_days == 30


def test_store_fact_request_empty_fact_text_rejected():
    """Test empty fact_text rejected (min_length=1)."""
    with pytest.raises(ValueError):
        StoreFactRequest(
            fact_text="",
            intent_type="test",
            entities={},
            outcome=True,
        )


def test_store_fact_request_ttl_days_range():
    """Test ttl_days range (1-365)."""
    # ttl_days < 1 rejected
    with pytest.raises(ValueError):
        StoreFactRequest(
            fact_text="test",
            intent_type="test",
            entities={},
            outcome=True,
            ttl_days=0,
        )

    # ttl_days > 365 rejected
    with pytest.raises(ValueError):
        StoreFactRequest(
            fact_text="test",
            intent_type="test",
            entities={},
            outcome=True,
            ttl_days=366,
        )


# StoreFactResponse Model Tests


def test_store_fact_response_status_ok():
    """Test StoreFactResponse with status 'ok'."""
    fact_id = uuid4()
    now = datetime.now(UTC)

    response = StoreFactResponse(
        status="ok",
        fact_id=fact_id,
        stored_at=now,
    )

    assert response.status == "ok"
    assert response.fact_id == fact_id


def test_store_fact_response_status_duplicate():
    """Test StoreFactResponse with status 'duplicate'."""
    fact_id = uuid4()
    now = datetime.now(UTC)

    response = StoreFactResponse(
        status="duplicate",
        fact_id=fact_id,
        stored_at=now,
    )

    assert response.status == "duplicate"


def test_store_fact_response_serialization_roundtrip():
    """Test serialization roundtrip."""
    fact_id = uuid4()
    now = datetime.now(UTC)

    response = StoreFactResponse(
        status="ok",
        fact_id=fact_id,
        stored_at=now,
    )

    # Serialize to dict
    data = response.model_dump()

    # Deserialize from dict
    restored = StoreFactResponse(**data)

    assert restored.status == response.status
    assert restored.fact_id == response.fact_id


# QueryFactsResponse Model Tests


def test_query_facts_response_empty_evidence():
    """Test QueryFactsResponse with empty evidence list."""
    response = QueryFactsResponse(
        evidence=[],
        total_count=0,
        returned_count=0,
    )

    assert response.evidence == []
    assert response.total_count == 0


def test_query_facts_response_counts_non_negative():
    """Test counts are non-negative."""
    with pytest.raises(ValueError):
        QueryFactsResponse(
            evidence=[],
            total_count=-1,
            returned_count=0,
        )


# PatternsResponse Model Tests


def test_patterns_response_empty_patterns():
    """Test PatternsResponse with empty patterns list."""
    response = PatternsResponse(
        patterns=[],
        total_count=0,
    )

    assert response.patterns == []
    assert response.total_count == 0


# Error Class Tests


def test_error_class_hierarchy():
    """Test all error classes inherit from HistoryError."""
    assert issubclass(FactTooLargeError, HistoryError)
    assert issubclass(InvalidTimestampError, HistoryError)
    assert issubclass(ConsentRequiredError, HistoryError)
    assert issubclass(InvalidFactError, HistoryError)
    assert issubclass(StorageError, HistoryError)
    assert issubclass(InvalidQueryError, HistoryError)


def test_error_messages_descriptive():
    """Test error messages are descriptive."""
    error = FactTooLargeError(5000)
    assert "5000" in str(error)

    error = InvalidTimestampError(datetime.now(UTC))
    assert "future" in str(error)

    user_id = uuid4()
    error = ConsentRequiredError(user_id, 2)
    assert str(user_id) in str(error)
    assert "Tier 3" in str(error)

    error = InvalidFactError("empty")
    assert "empty" in str(error)


# Hash Computation Tests


def test_compute_fact_hash_deterministic():
    """Test hash computation is deterministic."""
    user_id = uuid4()
    intent_type = "test"
    fact_text = "test fact"
    date_val = date(2026, 1, 1)

    hash1 = compute_fact_hash(user_id, intent_type, fact_text, date_val)
    hash2 = compute_fact_hash(user_id, intent_type, fact_text, date_val)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA256 hex


def test_compute_fact_hash_same_inputs_same_hash():
    """Test same inputs produce same hash."""
    user_id = uuid4()
    hash1 = compute_fact_hash(user_id, "test", "fact", date(2026, 1, 1))
    hash2 = compute_fact_hash(user_id, "test", "fact", date(2026, 1, 1))

    assert hash1 == hash2


def test_compute_fact_hash_different_dates_different_hashes():
    """Test different dates produce different hashes."""
    user_id = uuid4()
    hash1 = compute_fact_hash(user_id, "test", "fact", date(2026, 1, 1))
    hash2 = compute_fact_hash(user_id, "test", "fact", date(2026, 1, 2))

    assert hash1 != hash2


def test_compute_fact_hash_same_fact_different_days():
    """Test same fact_text on different days produces different hashes."""
    user_id = uuid4()
    fact_text = "Same action repeated"

    hash_day1 = compute_fact_hash(user_id, "test", fact_text, date(2026, 1, 1))
    hash_day2 = compute_fact_hash(user_id, "test", fact_text, date(2026, 1, 2))

    assert hash_day1 != hash_day2
