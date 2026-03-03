"""
Tests for DatabaseAdapter

Test database operations with mocked SQLAlchemy sessions.

Reference: tasks.md T302
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ..adapters.db import DatabaseAdapter
from ..domain.models import Fact, FactPattern


@pytest.fixture
def mock_shared_db():
    """Mock shared database adapter."""
    mock = MagicMock()
    mock.get_session = MagicMock()
    mock.health_check = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def db_adapter(mock_shared_db):
    """Create DatabaseAdapter with mocked shared database."""
    # Directly create adapter and inject mocked shared_db
    adapter = object.__new__(DatabaseAdapter)
    adapter.shared_db = mock_shared_db
    return adapter


# Test insert_fact - success path, new fact returns (fact, True)


@pytest.mark.asyncio
async def test_insert_fact_new_fact(db_adapter, mock_shared_db):
    """Test inserting a new fact returns (fact, True)."""
    user_id = uuid4()
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="New fact",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    # Mock session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1  # Indicates new row inserted
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    inserted_fact, is_new = await db_adapter.insert_fact(fact)

    assert inserted_fact == fact
    assert is_new is True
    mock_session.execute.assert_called()
    mock_session.commit.assert_called_once()


# Test insert_fact - duplicate fact_hash returns (existing_fact, False)


@pytest.mark.asyncio
async def test_insert_fact_duplicate(db_adapter, mock_shared_db):
    """Test inserting duplicate fact_hash returns (existing_fact, False)."""
    user_id = uuid4()
    now = datetime.now(UTC)

    fact = Fact(
        fact_id=uuid4(),
        user_id=user_id,
        fact_text="Duplicate fact",
        intent_type="test",
        entities={},
        outcome=True,
        fact_hash="hash1",
        ttl_days=30,
        created_at=now,
        expires_at=now + timedelta(days=30),
    )

    # Mock session
    mock_session = AsyncMock()

    # First execute (INSERT) - returns rowcount=0 (conflict)
    mock_insert_result = MagicMock()
    mock_insert_result.rowcount = 0

    # Second execute (SELECT) - returns existing row
    mock_row = MagicMock()
    mock_row.fact_id = fact.fact_id
    mock_row.user_id = fact.user_id
    mock_row.fact_text = fact.fact_text
    mock_row.intent_type = fact.intent_type
    mock_row.entities = fact.entities
    mock_row.outcome = fact.outcome
    mock_row.source_plan_id = fact.source_plan_id
    mock_row.fact_hash = fact.fact_hash
    mock_row.ttl_days = fact.ttl_days
    mock_row.created_at = fact.created_at
    mock_row.expires_at = fact.expires_at
    mock_row.deleted_at = fact.deleted_at

    mock_select_result = MagicMock()
    mock_select_result.scalar_one.return_value = mock_row

    mock_session.execute = AsyncMock(side_effect=[mock_insert_result, mock_select_result])
    mock_session.commit = AsyncMock()

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    existing_fact, is_new = await db_adapter.insert_fact(fact)

    assert existing_fact.fact_hash == fact.fact_hash
    assert is_new is False
    assert mock_session.execute.call_count == 2  # INSERT + SELECT


# Test query_facts - returns facts sorted by created_at DESC


@pytest.mark.asyncio
async def test_query_facts_sorted_by_recency(db_adapter, mock_shared_db):
    """Test query_facts returns facts sorted by created_at DESC."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Mock session
    mock_session = AsyncMock()

    # Mock rows (already sorted DESC)
    mock_rows = [
        MagicMock(
            fact_id=uuid4(),
            user_id=user_id,
            fact_text="Recent fact",
            intent_type="test",
            entities={},
            outcome=True,
            source_plan_id=None,
            fact_hash="hash2",
            ttl_days=30,
            created_at=now - timedelta(hours=1),
            expires_at=now + timedelta(days=30),
            deleted_at=None,
        ),
        MagicMock(
            fact_id=uuid4(),
            user_id=user_id,
            fact_text="Older fact",
            intent_type="test",
            entities={},
            outcome=True,
            source_plan_id=None,
            fact_hash="hash1",
            ttl_days=30,
            created_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            deleted_at=None,
        ),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_rows
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    facts = await db_adapter.query_facts(
        user_id=user_id,
        intent_type=None,
        limit=50,
        recency_cutoff=None,
    )

    assert len(facts) == 2
    assert facts[0].fact_text == "Recent fact"
    assert facts[1].fact_text == "Older fact"


# Test query_facts - excludes expired facts


@pytest.mark.asyncio
async def test_query_facts_excludes_expired(db_adapter, mock_shared_db):
    """Test query_facts excludes expired facts (expires_at <= now)."""
    user_id = uuid4()

    # Mock session - returns only non-expired facts
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []  # No expired facts returned
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    facts = await db_adapter.query_facts(
        user_id=user_id,
        intent_type=None,
        limit=50,
        recency_cutoff=None,
    )

    assert len(facts) == 0


# Test query_facts - filters by intent_type when provided


@pytest.mark.asyncio
async def test_query_facts_filters_by_intent(db_adapter, mock_shared_db):
    """Test query_facts filters by intent_type."""
    user_id = uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    await db_adapter.query_facts(
        user_id=user_id,
        intent_type="schedule_meeting",
        limit=50,
        recency_cutoff=None,
    )

    # Verify execute was called (actual filtering happens in SQL)
    mock_session.execute.assert_called_once()


# Test query_facts - respects limit parameter


@pytest.mark.asyncio
async def test_query_facts_respects_limit(db_adapter, mock_shared_db):
    """Test query_facts respects limit parameter."""
    user_id = uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    await db_adapter.query_facts(
        user_id=user_id,
        intent_type=None,
        limit=10,
        recency_cutoff=None,
    )

    # Verify execute was called (limit applied in SQL)
    mock_session.execute.assert_called_once()


# Test query_facts - applies recency_cutoff filter


@pytest.mark.asyncio
async def test_query_facts_recency_cutoff(db_adapter, mock_shared_db):
    """Test query_facts applies recency_cutoff filter."""
    user_id = uuid4()
    cutoff = datetime.now(UTC) - timedelta(days=7)

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    await db_adapter.query_facts(
        user_id=user_id,
        intent_type=None,
        limit=50,
        recency_cutoff=cutoff,
    )

    mock_session.execute.assert_called_once()


# Test upsert_pattern - creates new pattern


@pytest.mark.asyncio
async def test_upsert_pattern_new(db_adapter, mock_shared_db):
    """Test upsert_pattern creates new pattern."""
    user_id = uuid4()
    now = datetime.now(UTC)

    pattern = FactPattern(
        pattern_id=uuid4(),
        user_id=user_id,
        intent_type="test",
        pattern_key="test:key",
        pattern_description="Test pattern",
        entity_pattern={},
        occurrence_count=1,
        last_seen=now,
        confidence=0.2,
    )

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    await db_adapter.upsert_pattern(pattern)

    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()


# Test upsert_pattern - increments existing pattern occurrence_count


@pytest.mark.asyncio
async def test_upsert_pattern_existing(db_adapter, mock_shared_db):
    """Test upsert_pattern updates existing pattern."""
    user_id = uuid4()
    now = datetime.now(UTC)

    # Pattern with incremented count
    pattern = FactPattern(
        pattern_id=uuid4(),
        user_id=user_id,
        intent_type="test",
        pattern_key="test:key",
        pattern_description="Test pattern",
        entity_pattern={},
        occurrence_count=5,  # Incremented
        last_seen=now,
        confidence=1.0,
    )

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    await db_adapter.upsert_pattern(pattern)

    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()


# Test query_patterns - returns patterns above confidence threshold


@pytest.mark.asyncio
async def test_query_patterns_confidence_filter(db_adapter, mock_shared_db):
    """Test query_patterns filters by confidence threshold."""
    user_id = uuid4()
    now = datetime.now(UTC)

    mock_session = AsyncMock()

    mock_rows = [
        MagicMock(
            pattern_id=uuid4(),
            user_id=user_id,
            intent_type="test",
            pattern_key="test:key1",
            pattern_description="Pattern 1",
            entity_pattern={},
            occurrence_count=5,
            last_seen=now,
            confidence=1.0,
        ),
    ]

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_rows
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    patterns = await db_adapter.query_patterns(
        user_id=user_id,
        intent_type=None,
        min_confidence=0.5,
    )

    assert len(patterns) == 1
    assert patterns[0].confidence == 1.0


# Test query_patterns - filters by intent_type when provided


@pytest.mark.asyncio
async def test_query_patterns_intent_filter(db_adapter, mock_shared_db):
    """Test query_patterns filters by intent_type."""
    user_id = uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    await db_adapter.query_patterns(
        user_id=user_id,
        intent_type="schedule_meeting",
        min_confidence=0.5,
    )

    mock_session.execute.assert_called_once()


# Test cleanup_expired_facts - soft-deletes expired rows, returns count


@pytest.mark.asyncio
async def test_cleanup_expired_facts(db_adapter, mock_shared_db):
    """Test cleanup_expired_facts soft-deletes expired facts."""
    # Create async context manager mock
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 47  # Number of rows soft-deleted
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.in_transaction = MagicMock(return_value=False)  # Disable decorator's commit

    # Mock the context manager properly
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_cm.__aexit__.return_value = None
    mock_shared_db.get_session.return_value = mock_cm

    count = await db_adapter.cleanup_expired_facts(batch_size=500)

    assert count == 47
    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()


# Test hard_delete_old_facts - hard-deletes old soft-deleted rows, returns count


@pytest.mark.asyncio
async def test_hard_delete_old_facts(db_adapter, mock_shared_db):
    """Test hard_delete_old_facts removes old soft-deleted facts."""
    # Create async context manager mock
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 12  # Number of rows hard-deleted
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.in_transaction = MagicMock(return_value=False)  # Disable decorator's commit

    # Mock the context manager properly
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_cm.__aexit__.return_value = None
    mock_shared_db.get_session.return_value = mock_cm

    count = await db_adapter.hard_delete_old_facts(days_after_expiry=90, batch_size=500)

    assert count == 12
    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()


# Test health_check - passes


@pytest.mark.asyncio
async def test_health_check_passes(db_adapter, mock_shared_db):
    """Test health_check returns True when database is accessible."""
    mock_shared_db.health_check = AsyncMock(return_value=True)

    result = await db_adapter.health_check()

    assert result is True
    mock_shared_db.health_check.assert_called_once()


# Test health_check - fails


@pytest.mark.asyncio
async def test_health_check_fails(db_adapter, mock_shared_db):
    """Test health_check returns False when database is inaccessible."""
    mock_shared_db.health_check = AsyncMock(return_value=False)

    result = await db_adapter.health_check()

    assert result is False
    mock_shared_db.health_check.assert_called_once()


# Test count_facts


@pytest.mark.asyncio
async def test_count_facts(db_adapter, mock_shared_db):
    """Test count_facts returns total count."""
    user_id = uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 42
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_shared_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_shared_db.get_session.return_value.__aexit__ = AsyncMock()

    count = await db_adapter.count_facts(user_id=user_id, intent_type=None)

    assert count == 42
    mock_session.execute.assert_called_once()
