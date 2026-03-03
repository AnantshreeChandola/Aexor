"""
Tests for Integration Flows

End-to-end flow tests with mocked database.

Reference: tasks.md T601
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ..adapters.db import DatabaseAdapter
from ..domain.models import (
    Fact,
    FactPattern,
    StoreFactRequest,
    compute_fact_hash,
)
from ..service.evidence_service import EvidenceService
from ..service.fact_service import FactService
from ..service.pattern_service import PatternService


@pytest.fixture
def mock_db_adapter():
    """Mock DatabaseAdapter for integration tests."""
    mock = MagicMock(spec=DatabaseAdapter)
    mock.insert_fact = AsyncMock()
    mock.query_facts = AsyncMock()
    mock.count_facts = AsyncMock()
    mock.upsert_pattern = AsyncMock()
    mock.query_patterns = AsyncMock()
    return mock


@pytest.fixture
def evidence_service():
    """Create real EvidenceService."""
    return EvidenceService()


@pytest.fixture
def pattern_service(mock_db_adapter):
    """Create PatternService with mocked adapter."""
    return PatternService(db_adapter=mock_db_adapter)


@pytest.fixture
def fact_service(mock_db_adapter, evidence_service, pattern_service):
    """Create FactService with dependencies."""
    return FactService(
        db_adapter=mock_db_adapter,
        evidence_service=evidence_service,
        pattern_service=pattern_service,
    )


# Full storage-query flow


@pytest.mark.asyncio
async def test_full_storage_query_flow(fact_service, mock_db_adapter):
    """Test full flow: store_fact -> query_facts -> verify Evidence Items."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Step 1: Store fact
    request = StoreFactRequest(
        fact_text="Booked meeting with Alice",
        intent_type="schedule_meeting",
        entities={"person": "Alice"},
        outcome=True,
    )

    fact_hash = compute_fact_hash(user_id, request.intent_type, request.fact_text, now.date())
    stored_fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text=request.fact_text,
        intent_type=request.intent_type,
        entities=request.entities,
        outcome=True,
        fact_hash=fact_hash,
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )
    mock_db_adapter.insert_fact.return_value = (stored_fact, True)

    store_response = await fact_service.store_fact(user_id=user_id, request=request)

    assert store_response.status == "ok"
    assert store_response.fact_id == stored_fact.fact_id

    # Step 2: Query facts
    mock_db_adapter.query_facts.return_value = [stored_fact]
    mock_db_adapter.count_facts.return_value = 1

    query_response = await fact_service.get_facts_by_intent(
        user_id=user_id,
        intent_type="schedule_meeting",
        limit=50,
    )

    # Step 3: Verify Evidence Items returned correctly
    assert query_response.total_count == 1
    assert query_response.returned_count == 1
    assert len(query_response.evidence) == 1

    evidence_item = query_response.evidence[0]
    assert evidence_item["type"] == "history"
    assert evidence_item["tier"] == 3
    assert evidence_item["value"]["fact"] == "Booked meeting with Alice"
    assert evidence_item["value"]["intent_type"] == "schedule_meeting"


# Pattern accumulation flow


@pytest.mark.asyncio
async def test_pattern_accumulation_flow(fact_service, pattern_service, mock_db_adapter):
    """Test pattern detection after storing 5 facts with same pattern."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Ensure all facts are on Tuesday
    tuesday = now.replace(hour=12, minute=0, second=0, microsecond=0)
    while tuesday.strftime("%A") != "Tuesday":
        tuesday += timedelta(days=1)

    # Store 5 facts with same pattern
    for i in range(5):
        fact_date = tuesday - timedelta(weeks=i)

        request = StoreFactRequest(
            fact_text=f"Met with Alice (week {i})",
            intent_type="schedule_meeting",
            entities={"person": "Alice"},
            outcome=True,
        )

        fact_hash = compute_fact_hash(
            user_id, request.intent_type, request.fact_text, fact_date.date()
        )
        fact = Fact(
            fact_id=uuid4(),
            user_id=user_id,
            fact_text=request.fact_text,
            intent_type=request.intent_type,
            entities=request.entities,
            outcome=True,
            fact_hash=fact_hash,
            ttl_days=30,
            created_at=fact_date,
            expires_at=fact_date + timedelta(days=30),
        )

        # Mock existing patterns for subsequent stores
        if i == 0:
            mock_db_adapter.query_patterns.return_value = []
        else:
            existing_pattern = FactPattern(
                pattern_id=uuid4(),
                user_id=user_id,
                intent_type="schedule_meeting",
                pattern_key="schedule_meeting:person:Alice:Tuesday",
                pattern_description="Meets Alice on Tuesdays",
                entity_pattern={"person": "Alice", "day_of_week": "Tuesday"},
                occurrence_count=i,
                last_seen=fact_date - timedelta(weeks=1),
                confidence=min(1.0, i / 5.0),
            )
            mock_db_adapter.query_patterns.return_value = [existing_pattern]

        mock_db_adapter.insert_fact.return_value = (fact, True)

        await fact_service.store_fact(user_id=user_id, request=request)

    # Verify pattern was updated 5 times
    assert mock_db_adapter.upsert_pattern.call_count == 5

    # Final pattern should have occurrence_count=5, confidence=1.0
    final_pattern_call = mock_db_adapter.upsert_pattern.call_args_list[-1]
    final_pattern = final_pattern_call[0][0]
    assert final_pattern.occurrence_count == 5
    assert final_pattern.confidence == 1.0


# Deduplication flow


@pytest.mark.asyncio
async def test_deduplication_flow(fact_service, mock_db_adapter, pattern_service):
    """Test storing same fact twice returns duplicate status."""
    user_id = uuid4()
    now = datetime.now(UTC)

    request = StoreFactRequest(
        fact_text="Same fact",
        intent_type="test",
        entities={},
        outcome=True,
    )

    fact_hash = compute_fact_hash(user_id, request.intent_type, request.fact_text, now.date())
    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text=request.fact_text,
        intent_type=request.intent_type,
        entities=request.entities,
        outcome=True,
        fact_hash=fact_hash,
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    # First store - new fact
    mock_db_adapter.insert_fact.return_value = (fact, True)
    response1 = await fact_service.store_fact(user_id=user_id, request=request)
    assert response1.status == "ok"

    # Second store - duplicate
    mock_db_adapter.insert_fact.return_value = (fact, False)
    response2 = await fact_service.store_fact(user_id=user_id, request=request)
    assert response2.status == "duplicate"
    assert response2.fact_id == fact.fact_id


# TTL flow


@pytest.mark.asyncio
async def test_ttl_flow(fact_service, mock_db_adapter):
    """Test fact with short TTL is excluded after expiration."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Store fact with short TTL
    request = StoreFactRequest(
        fact_text="Short-lived fact",
        intent_type="test",
        entities={},
        outcome=True,
        ttl_days=1,  # 1 day TTL
    )

    fact_hash = compute_fact_hash(user_id, request.intent_type, request.fact_text, now.date())
    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text=request.fact_text,
        intent_type=request.intent_type,
        entities=request.entities,
        outcome=True,
        fact_hash=fact_hash,
        ttl_days=1,
        created_at=now,
        expires_at=now + timedelta(days=1),
    )

    mock_db_adapter.insert_fact.return_value = (fact, True)
    await fact_service.store_fact(user_id=user_id, request=request)

    # Query immediately - fact should be present
    mock_db_adapter.query_facts.return_value = [fact]
    mock_db_adapter.count_facts.return_value = 1

    response = await fact_service.get_facts_by_intent(user_id=user_id, limit=50)
    assert response.total_count == 1

    # Simulate time passage - query after expiration
    # Database would exclude expired facts via WHERE expires_at > NOW()
    mock_db_adapter.query_facts.return_value = []
    mock_db_adapter.count_facts.return_value = 0

    response = await fact_service.get_facts_by_intent(user_id=user_id, limit=50)
    assert response.total_count == 0


# Cross-intent isolation


@pytest.mark.asyncio
async def test_cross_intent_isolation(fact_service, mock_db_adapter):
    """Test querying by specific intent doesn't leak other intents."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Store facts for different intents
    meeting_fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Meeting fact",
        intent_type="schedule_meeting",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    reminder_fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Reminder fact",
        intent_type="set_reminder",
        entities={},
        outcome=True,
        fact_hash="hash2",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    # Query for schedule_meeting - should only return meeting facts
    mock_db_adapter.query_facts.return_value = [meeting_fact]
    mock_db_adapter.count_facts.return_value = 1

    response = await fact_service.get_facts_by_intent(
        user_id=user_id,
        intent_type="schedule_meeting",
        limit=50,
    )

    assert response.total_count == 1
    assert response.evidence[0]["value"]["intent_type"] == "schedule_meeting"

    # Query for set_reminder - should only return reminder facts
    mock_db_adapter.query_facts.return_value = [reminder_fact]
    mock_db_adapter.count_facts.return_value = 1

    response = await fact_service.get_facts_by_intent(
        user_id=user_id,
        intent_type="set_reminder",
        limit=50,
    )

    assert response.total_count == 1
    assert response.evidence[0]["value"]["intent_type"] == "set_reminder"


# Empty user flow


@pytest.mark.asyncio
async def test_empty_user_flow(fact_service, mock_db_adapter):
    """Test querying for user with no facts returns empty list (not error)."""
    user_id = uuid4()

    # Mock database returns no facts
    mock_db_adapter.query_facts.return_value = []
    mock_db_adapter.count_facts.return_value = 0

    response = await fact_service.get_facts_by_intent(
        user_id=user_id,
        limit=50,
    )

    # Should return empty response, not raise error
    assert response.total_count == 0
    assert response.returned_count == 0
    assert response.evidence == []


# Confidence decay flow


@pytest.mark.asyncio
async def test_confidence_decay_flow(fact_service, mock_db_adapter, evidence_service):
    """Test confidence decreases over time."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Store fact
    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Decaying fact",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now - timedelta(days=15),  # 15 days old
        expires_at=now + timedelta(days=15),  # 15 days remaining
    )

    mock_db_adapter.query_facts.return_value = [fact]
    mock_db_adapter.count_facts.return_value = 1

    response = await fact_service.get_facts_by_intent(user_id=user_id, limit=50)

    # At 50% of TTL (15 days into 30-day TTL), confidence should be ~0.5
    evidence_item = response.evidence[0]
    assert 0.45 <= evidence_item["confidence"] <= 0.55

    # Verify confidence formula directly
    direct_evidence = evidence_service.fact_to_evidence(fact)
    assert 0.45 <= direct_evidence["confidence"] <= 0.55


# Test service layer integration


@pytest.mark.asyncio
async def test_fact_service_evidence_service_integration(
    fact_service, mock_db_adapter, evidence_service
):
    """Test FactService and EvidenceService integration."""
    user_id = uuid4()
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Integration test",
        intent_type="test",
        entities={"key": "value"},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    mock_db_adapter.query_facts.return_value = [fact]
    mock_db_adapter.count_facts.return_value = 1

    response = await fact_service.get_facts_by_intent(user_id=user_id, limit=50)

    # Verify Evidence Item was properly formatted
    evidence_item = response.evidence[0]
    assert evidence_item["type"] == "history"
    assert evidence_item["tier"] == 3
    assert evidence_item["value"]["fact"] == "Integration test"
    assert evidence_item["value"]["entities"] == {"key": "value"}


# Test FactService and PatternService integration


@pytest.mark.asyncio
async def test_fact_service_pattern_service_integration(
    fact_service, pattern_service, mock_db_adapter
):
    """Test FactService triggers PatternService updates."""
    user_id = uuid4()
    now = datetime.now(UTC)

    request = StoreFactRequest(
        fact_text="Pattern test",
        intent_type="schedule_meeting",
        entities={"person": "Bob"},
        outcome=True,
    )

    fact_hash = compute_fact_hash(user_id, request.intent_type, request.fact_text, now.date())
    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text=request.fact_text,
        intent_type=request.intent_type,
        entities=request.entities,
        outcome=True,
        fact_hash=fact_hash,
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    mock_db_adapter.insert_fact.return_value = (fact, True)
    mock_db_adapter.query_patterns.return_value = []

    await fact_service.store_fact(user_id=user_id, request=request)

    # Verify pattern was updated
    mock_db_adapter.upsert_pattern.assert_called_once()
    pattern = mock_db_adapter.upsert_pattern.call_args[0][0]
    assert pattern.intent_type == "schedule_meeting"
    assert "person:Bob" in pattern.pattern_key
