"""
Tests for PatternService

Test pattern detection, confidence scoring, and stale pattern handling.

Reference: tasks.md T203
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ..domain.models import Fact, FactPattern, PatternsResponse
from ..service.pattern_service import PatternService


@pytest.fixture
def mock_db_adapter():
    """Mock DatabaseAdapter."""
    mock = MagicMock()
    mock.query_patterns = AsyncMock()
    mock.upsert_pattern = AsyncMock()
    return mock


@pytest.fixture
def pattern_service(mock_db_adapter):
    """Create PatternService with mocked dependencies."""
    return PatternService(db_adapter=mock_db_adapter)


# US-3 Scenario 1: Get patterns with confidence above threshold


@pytest.mark.asyncio
async def test_get_patterns_with_confidence_threshold(pattern_service, mock_db_adapter):
    """Test getting patterns above confidence threshold."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Mock database returns patterns above threshold
    patterns = [
        FactPattern(
            pattern_id=uuid4(),
            user_id=user_id,
            intent_type="schedule_meeting",
            pattern_key="schedule_meeting:person:Alice:Tuesday",
            pattern_description="Meets Alice on Tuesdays",
            entity_pattern={"person": "Alice", "day_of_week": "Tuesday"},
            occurrence_count=5,
            last_seen=now - timedelta(days=1),
            confidence=1.0,
        ),
        FactPattern(
            pattern_id=uuid4(),
            user_id=user_id,
            intent_type="schedule_meeting",
            pattern_key="schedule_meeting:person:Bob:Monday",
            pattern_description="Meets Bob on Mondays",
            entity_pattern={"person": "Bob", "day_of_week": "Monday"},
            occurrence_count=3,
            last_seen=now - timedelta(days=2),
            confidence=0.6,
        ),
    ]
    mock_db_adapter.query_patterns.return_value = patterns

    response = await pattern_service.get_patterns(
        user_id=user_id,
        intent_type="schedule_meeting",
        min_confidence=0.5,
    )

    assert isinstance(response, PatternsResponse)
    assert response.total_count == 2
    assert len(response.patterns) == 2


# US-3 Scenario 2: Stale pattern (>30 days) excluded


@pytest.mark.asyncio
async def test_get_patterns_excludes_stale(pattern_service, mock_db_adapter):
    """Test stale patterns (>30 days) are excluded."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Mock database returns patterns including stale one
    patterns = [
        FactPattern(
            pattern_id=uuid4(),
            user_id=user_id,
            intent_type="test",
            pattern_key="test:key1",
            pattern_description="Recent pattern",
            entity_pattern={},
            occurrence_count=5,
            last_seen=now - timedelta(days=10),  # Recent
            confidence=1.0,
        ),
        FactPattern(
            pattern_id=uuid4(),
            user_id=user_id,
            intent_type="test",
            pattern_key="test:key2",
            pattern_description="Stale pattern",
            entity_pattern={},
            occurrence_count=5,
            last_seen=now - timedelta(days=31),  # Stale (>30 days)
            confidence=1.0,
        ),
    ]
    mock_db_adapter.query_patterns.return_value = patterns

    response = await pattern_service.get_patterns(
        user_id=user_id,
        min_confidence=0.5,
    )

    # Only recent pattern should be returned
    assert response.total_count == 1
    assert len(response.patterns) == 1
    assert response.patterns[0]["pattern_description"] == "Recent pattern"


# US-3 Scenario 3: Patterns filtered by intent_type


@pytest.mark.asyncio
async def test_get_patterns_filtered_by_intent(pattern_service, mock_db_adapter):
    """Test patterns are filtered by intent_type."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Mock database returns patterns for specific intent
    patterns = [
        FactPattern(
            pattern_id=uuid4(),
            user_id=user_id,
            intent_type="schedule_meeting",
            pattern_key="schedule_meeting:person:Alice:Tuesday",
            pattern_description="Meeting pattern",
            entity_pattern={"person": "Alice"},
            occurrence_count=5,
            last_seen=now,
            confidence=1.0,
        ),
    ]
    mock_db_adapter.query_patterns.return_value = patterns

    await pattern_service.get_patterns(
        user_id=user_id,
        intent_type="schedule_meeting",
        min_confidence=0.5,
    )

    # Verify database was called with intent filter
    mock_db_adapter.query_patterns.assert_called_once()
    call_args = mock_db_adapter.query_patterns.call_args
    assert call_args[1]["intent_type"] == "schedule_meeting"


# Test update_patterns_on_store - new pattern created


@pytest.mark.asyncio
async def test_update_patterns_on_store_new_pattern(pattern_service, mock_db_adapter):
    """Test creating a new pattern when fact is stored."""
    user_id = uuid4()
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Met with Alice",
        intent_type="schedule_meeting",
        entities={"person": "Alice"},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    # Mock database returns no existing patterns
    mock_db_adapter.query_patterns.return_value = []

    await pattern_service.update_patterns_on_store(user_id=user_id, fact=fact)

    # Verify upsert was called with new pattern
    mock_db_adapter.upsert_pattern.assert_called_once()
    call_args = mock_db_adapter.upsert_pattern.call_args
    pattern = call_args[0][0]

    assert pattern.user_id == user_id
    assert pattern.intent_type == "schedule_meeting"
    assert pattern.occurrence_count == 1
    assert pattern.confidence == 0.2  # min(1.0, 1/5) = 0.2


# Test update_patterns_on_store - existing pattern incremented


@pytest.mark.asyncio
async def test_update_patterns_on_store_existing_pattern(pattern_service, mock_db_adapter):
    """Test updating existing pattern when fact is stored."""
    user_id = uuid4()
    now = datetime.now(UTC)
    tuesday = now.replace(hour=12, minute=0, second=0, microsecond=0)
    # Ensure it's a Tuesday
    while tuesday.strftime("%A") != "Tuesday":
        tuesday += timedelta(days=1)

    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Met with Alice",
        intent_type="schedule_meeting",
        entities={"person": "Alice"},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=tuesday,
        expires_at=tuesday + timedelta(days=30),
    )

    # Mock database returns existing pattern
    existing_pattern = FactPattern(
        pattern_id=uuid4(),
        user_id=user_id,
        intent_type="schedule_meeting",
        pattern_key="schedule_meeting:person:Alice:Tuesday",
        pattern_description="Meets Alice on Tuesdays",
        entity_pattern={"person": "Alice", "day_of_week": "Tuesday"},
        occurrence_count=4,
        last_seen=tuesday - timedelta(days=7),
        confidence=0.8,
    )
    mock_db_adapter.query_patterns.return_value = [existing_pattern]

    await pattern_service.update_patterns_on_store(user_id=user_id, fact=fact)

    # Verify upsert was called with incremented pattern
    mock_db_adapter.upsert_pattern.assert_called_once()
    call_args = mock_db_adapter.upsert_pattern.call_args
    pattern = call_args[0][0]

    assert pattern.occurrence_count == 5  # 4 + 1
    assert pattern.confidence == 1.0  # min(1.0, 5/5) = 1.0


# Test pattern confidence formula: min(1.0, count / 5)


@pytest.mark.asyncio
async def test_pattern_confidence_formula(pattern_service, mock_db_adapter):
    """Test pattern confidence formula for various occurrence counts."""
    user_id = uuid4()
    now = datetime.now(UTC)

    test_cases = [
        (1, 0.2),  # min(1.0, 1/5) = 0.2
        (2, 0.4),  # min(1.0, 2/5) = 0.4
        (3, 0.6),  # min(1.0, 3/5) = 0.6
        (4, 0.8),  # min(1.0, 4/5) = 0.8
        (5, 1.0),  # min(1.0, 5/5) = 1.0
        (6, 1.0),  # min(1.0, 6/5) = 1.0 (capped at 1.0)
        (10, 1.0),  # min(1.0, 10/5) = 1.0 (capped at 1.0)
    ]

    for count, expected_confidence in test_cases:
        mock_db_adapter.query_patterns.return_value = []
        mock_db_adapter.upsert_pattern.reset_mock()

        # Create fact with specific occurrence count context
        fact = Fact(
            fact_id=uuid4(),
            user_id=user_id,
            fact_text=f"Fact {count}",
            intent_type="test",
            entities={"key": "value"},
            outcome=True,
            fact_hash=f"hash{count}",
            ttl_days=30,
            created_at=now,
            expires_at=now + timedelta(days=30),
        )

        # For counts > 1, mock existing pattern
        if count > 1:
            existing = FactPattern(
                pattern_id=uuid4(),
                user_id=user_id,
                intent_type="test",
                pattern_key=f"test:key:value:{now.strftime('%A')}",
                pattern_description="Test pattern",
                entity_pattern={},
                occurrence_count=count - 1,
                last_seen=now - timedelta(days=1),
                confidence=min(1.0, (count - 1) / 5.0),
            )
            mock_db_adapter.query_patterns.return_value = [existing]

        await pattern_service.update_patterns_on_store(user_id=user_id, fact=fact)

        # Verify confidence calculation
        if mock_db_adapter.upsert_pattern.called:
            pattern = mock_db_adapter.upsert_pattern.call_args[0][0]
            assert pattern.confidence == expected_confidence


# Test pattern key format


@pytest.mark.asyncio
async def test_pattern_key_format(pattern_service, mock_db_adapter):
    """Test pattern key follows format: {intent_type}:{entity_key}:{day_of_week}."""
    user_id = uuid4()
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Meeting",
        intent_type="schedule_meeting",
        entities={"person": "Alice"},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    mock_db_adapter.query_patterns.return_value = []

    await pattern_service.update_patterns_on_store(user_id=user_id, fact=fact)

    # Verify pattern key format
    pattern = mock_db_adapter.upsert_pattern.call_args[0][0]
    day_of_week = now.strftime("%A")
    expected_key = f"schedule_meeting:person:Alice:{day_of_week}"
    assert pattern.pattern_key == expected_key


# Test no pattern update when no recognizable entity


@pytest.mark.asyncio
async def test_no_pattern_update_without_entities(pattern_service, mock_db_adapter):
    """Test no pattern is created when fact has no recognizable entities."""
    user_id = uuid4()
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Generic fact",
        intent_type="test",
        entities={},  # No entities
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    mock_db_adapter.query_patterns.return_value = []

    await pattern_service.update_patterns_on_store(user_id=user_id, fact=fact)

    # Upsert should not be called when no entities
    mock_db_adapter.upsert_pattern.assert_not_called()


# Test entity priority extraction


@pytest.mark.asyncio
async def test_entity_extraction_priority(pattern_service, mock_db_adapter):
    """Test entity extraction prioritizes person > location > other."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Test with multiple entity types - person should be prioritized
    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Meeting",
        intent_type="schedule_meeting",
        entities={
            "location": "Office",
            "person": "Alice",
            "category": "Work",
        },
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    mock_db_adapter.query_patterns.return_value = []

    await pattern_service.update_patterns_on_store(user_id=user_id, fact=fact)

    # Verify person was prioritized in pattern key
    pattern = mock_db_adapter.upsert_pattern.call_args[0][0]
    assert "person:Alice" in pattern.pattern_key
