"""
Tests for EvidenceService

Test Evidence Item conversion and GLOBAL_SPEC §2.2 compliance.

Reference: tasks.md T203
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ..domain.models import Fact
from ..service.evidence_service import EvidenceService


@pytest.fixture
def evidence_service():
    """Create EvidenceService instance."""
    return EvidenceService()


# Test fact_to_evidence returns correct Evidence Item format


def test_fact_to_evidence_format(evidence_service):
    """Test fact_to_evidence returns correct Evidence Item format."""
    user_id = uuid4()
    fact_id = uuid4()
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=fact_id,
        user_id=user_id,
        fact_text="Booked 30min meeting with Alice",
        intent_type="schedule_meeting",
        entities={"person": "Alice", "duration": 30},
        outcome=True,
        source_plan_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        fact_hash="abc123",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    # Verify structure
    assert isinstance(evidence_item, dict)
    assert "type" in evidence_item
    assert "key" in evidence_item
    assert "value" in evidence_item
    assert "confidence" in evidence_item
    assert "source_ref" in evidence_item
    assert "ttl_days" in evidence_item
    assert "tier" in evidence_item


# Test Evidence Item type is always "history"


def test_evidence_item_type_is_history(evidence_service):
    """Test Evidence Item type is always 'history'."""
    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Test fact",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    assert evidence_item["type"] == "history"


# Test Evidence Item tier is always 3


def test_evidence_item_tier_is_3(evidence_service):
    """Test Evidence Item tier is always 3 (GLOBAL_SPEC §7)."""
    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Test fact",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    assert evidence_item["tier"] == 3


# Test confidence decay - new fact (age=0) has confidence ~1.0


def test_confidence_decay_new_fact(evidence_service):
    """Test new fact has confidence ~1.0."""
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Just created",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    # New fact should have confidence close to 1.0
    assert evidence_item["confidence"] >= 0.99
    assert evidence_item["confidence"] <= 1.0


# Test confidence decay - fact at 50% of TTL has confidence ~0.5


def test_confidence_decay_half_ttl(evidence_service):
    """Test fact at 50% of TTL has confidence ~0.5."""
    now = datetime.now(UTC)
    created_at = now - timedelta(days=15)  # 15 days ago, TTL is 30 days

    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Half expired",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=created_at,
        expires_at=created_at + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    # At 50% of TTL, confidence should be around 0.5
    assert 0.45 <= evidence_item["confidence"] <= 0.55


# Test confidence decay - expired fact has confidence 0.0


def test_confidence_decay_expired_fact(evidence_service):
    """Test expired fact has confidence 0.0."""
    now = datetime.now(UTC)
    created_at = now - timedelta(days=31)  # 31 days ago, TTL is 30 days

    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Expired",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=created_at,
        expires_at=created_at + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    # Expired fact should have confidence 0.0
    assert evidence_item["confidence"] == 0.0


# Test source_ref follows "history:facts/{fact_id}" format


def test_source_ref_format(evidence_service):
    """Test source_ref follows 'history:facts/{fact_id}' format."""
    fact_id = uuid4()

    fact = Fact(
        fact_id=fact_id,
        user_id=uuid4(),
        fact_text="Test",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    expected_source_ref = f"history:facts/{fact_id}"
    assert evidence_item["source_ref"] == expected_source_ref


# Test ttl_days reflects remaining TTL


def test_ttl_days_remaining(evidence_service):
    """Test ttl_days reflects remaining TTL."""
    now = datetime.now(UTC)
    created_at = now - timedelta(days=10)  # 10 days ago

    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Test",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=created_at,
        expires_at=created_at + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    # Remaining TTL should be around 20 days (30 - 10)
    # Allow some tolerance for test execution time
    assert 19 <= evidence_item["ttl_days"] <= 21


# Test ttl_days minimum is 1


def test_ttl_days_minimum_one(evidence_service):
    """Test ttl_days has minimum value of 1."""
    now = datetime.now(UTC)
    created_at = now - timedelta(days=30)  # Exactly at TTL boundary

    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Test",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=created_at,
        expires_at=created_at + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    # Even expired/near-expired facts should have minimum ttl_days of 1
    assert evidence_item["ttl_days"] >= 1


# Test Evidence Item key format: {intent_type}_{date}


def test_evidence_item_key_format(evidence_service):
    """Test Evidence Item key format is {intent_type}_{date}."""
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Test",
        intent_type="schedule_meeting",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    expected_key = f"schedule_meeting_{now.date().isoformat()}"
    assert evidence_item["key"] == expected_key


# Test Evidence Item value structure


def test_evidence_item_value_structure(evidence_service):
    """Test Evidence Item value contains required fields."""
    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Booked meeting",
        intent_type="schedule_meeting",
        entities={"person": "Alice"},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    value = evidence_item["value"]
    assert "fact" in value
    assert "intent_type" in value
    assert "outcome" in value
    assert "entities" in value
    assert "age_days" in value

    assert value["fact"] == "Booked meeting"
    assert value["intent_type"] == "schedule_meeting"
    assert value["outcome"] is True
    assert value["entities"] == {"person": "Alice"}
    assert isinstance(value["age_days"], int)


# Test confidence decay formula accuracy


def test_confidence_decay_formula(evidence_service):
    """Test confidence decay formula: max(0.0, 1.0 - age_days / ttl_days)."""
    now = datetime.now(UTC)

    test_cases = [
        (0, 30, 1.0),  # Brand new fact
        (10, 30, 0.67),  # 1/3 through TTL
        (15, 30, 0.5),  # Half through TTL
        (20, 30, 0.33),  # 2/3 through TTL
        (30, 30, 0.0),  # Exactly at TTL
        (35, 30, 0.0),  # Past TTL
    ]

    for age_days, ttl_days, expected_confidence in test_cases:
        created_at = now - timedelta(days=age_days)

        fact = Fact(
            fact_id=uuid4(),
            user_id=uuid4(),
            fact_text="Test",
            intent_type="test",
            entities={},
            outcome=True,
            fact_hash="hash1",
            ttl_days=ttl_days,
            created_at=created_at,
            expires_at=created_at + timedelta(days=ttl_days),
        )

        evidence_item = evidence_service.fact_to_evidence(fact)

        # Allow small tolerance for floating point arithmetic
        assert abs(evidence_item["confidence"] - expected_confidence) < 0.05


# Test zero TTL edge case


def test_low_ttl_confidence(evidence_service):
    """Test confidence calculation with minimum TTL (1 day)."""
    now = datetime.now(UTC)

    # Use ttl_days=1 (minimum valid value) and age=1 day
    fact = Fact(
        fact_id=uuid4(),
        user_id=uuid4(),
        fact_text="Test",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=1,  # Minimum TTL
        created_at=now - timedelta(days=1),  # 1 day old
        expires_at=now,  # About to expire
    )

    evidence_item = evidence_service.fact_to_evidence(fact)

    # age_days=1, ttl_days=1: confidence = max(0.0, 1.0 - 1/1) = 0.0
    assert evidence_item["confidence"] == 0.0
