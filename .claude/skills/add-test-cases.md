---
description: Generate comprehensive test cases for a component based on its SPEC.md acceptance criteria.
---

Read first:
- Component's `SPEC.md` for acceptance criteria
- Component's `LLD.md` for implementation details
- `/docs/dev/PYTHON_GUIDE.md` for testing conventions

## Test Categories

### 1. Unit Tests (Fast, Isolated)
Mock all external dependencies (database, APIs, other components)

```python
# components/<Name>/tests/test_service.py
import pytest
from unittest.mock import Mock, AsyncMock

@pytest.mark.asyncio
async def test_preview_happy_path():
    """AC1: [Copy acceptance criterion text from SPEC.md]"""
    # Arrange
    service = MyService()
    mock_adapter = AsyncMock()
    mock_adapter.get_data.return_value = {"slots": ["10 AM"]}
    service.adapter = mock_adapter

    # Act
    result = await service.preview(intent)

    # Assert
    assert result.normalized.available_slots == ["10 AM"]
    assert result.source == "preview"
    assert result.can_execute is True
    mock_adapter.get_data.assert_called_once_with(mock=True)

@pytest.mark.asyncio
async def test_preview_edge_case_no_slots():
    """AC2: [Acceptance criterion for edge case]"""
    # Test when no slots available
    service = MyService()
    mock_adapter = AsyncMock()
    mock_adapter.get_data.return_value = {"slots": []}
    service.adapter = mock_adapter

    result = await service.preview(intent)

    assert result.can_execute is False
    assert "no_slots_available" in result.normalized.get("reason", "")

@pytest.mark.asyncio
async def test_execute_with_idempotency():
    """AC3: [Acceptance criterion for idempotency]"""
    # Test that duplicate execution returns cached result
    service = MyService()
    redis_mock = Mock()
    redis_mock.exists.return_value = True
    redis_mock.get.return_value = {"event_id": "cached_123"}

    result = await service.execute(approved_preview, redis=redis_mock)

    assert result.result.id == "cached_123"
    # Verify external API was NOT called
```

### 2. Integration Tests (Real Dependencies)
Use testcontainers for PostgreSQL, real Redis

```python
# components/<Name>/tests/test_integration.py
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

@pytest.fixture(scope="module")
def postgres():
    with PostgresContainer("postgres:16") as pg:
        yield pg

@pytest.fixture(scope="module")
def redis():
    with RedisContainer("redis:7") as r:
        yield r

@pytest.mark.integration
async def test_execute_end_to_end(postgres, redis):
    """AC4: [End-to-end acceptance criterion]"""
    # Setup: Create database tables
    db_url = postgres.get_connection_url()
    await setup_database(db_url)

    # Execute with real database and Redis
    service = MyService(db_url=db_url, redis_url=redis.get_connection_url())
    result = await service.execute(approved_preview)

    # Verify database state
    async with get_db_session(db_url) as session:
        record = await session.get(Resource, result.result.id)
        assert record is not None
        assert record.status == "created"
```

### 3. Contract Tests (Validate Against SPEC)
Ensure API matches SPEC.md contract

```python
# components/<Name>/tests/test_contract.py
import pytest
from pydantic import ValidationError

def test_preview_wrapper_schema():
    """Validate Preview wrapper matches GLOBAL_SPEC"""
    result = service.preview(intent)

    # Required fields from GLOBAL_SPEC
    assert hasattr(result, "normalized")
    assert result.source == "preview"
    assert isinstance(result.can_execute, bool)
    assert hasattr(result, "evidence")

def test_execute_wrapper_schema():
    """Validate Execute wrapper matches GLOBAL_SPEC"""
    result = service.execute(approved_preview)

    # Required fields from GLOBAL_SPEC
    assert hasattr(result, "provider")
    assert hasattr(result, "result")
    assert result.status in ["created", "updated", "error"]

def test_acceptance_criterion_1():
    """AC1: [Map directly to SPEC.md criterion]"""
    # Test maps 1:1 to acceptance criterion
    pass
```

## Test Data Fixtures

```python
# components/<Name>/tests/conftest.py
import pytest

@pytest.fixture
def sample_intent():
    """Realistic test intent"""
    return Intent(
        intent="schedule_meeting",
        entities={"attendee": "Alice"},
        constraints={"timeframe": "next week", "duration_min": 30},
        tz="America/Chicago",
        user_id="user_123"
    )

@pytest.fixture
def sample_approved_preview():
    """Approved preview for execute tests"""
    return ApprovedPreview(
        plan_id="01HX...",
        step=5,
        cached_state={"selected_slot": "Tue 10 AM"},
        approval_token="jwt:eyJ..."
    )
```

## Coverage Requirements

- [ ] Every acceptance criterion has at least one test
- [ ] Happy path covered (main use case works)
- [ ] Error cases covered (validation, external failures)
- [ ] Edge cases covered (empty results, boundary conditions)
- [ ] Integration with dependencies covered (database, Redis, APIs)
- [ ] Idempotency tested (retry safety)
- [ ] Compensation tested (undo on failure, if applicable)

## Example Mapping

**If SPEC.md says**:
```markdown
## Acceptance Criteria
- [ ] AC1: System parses "Book a meeting with Alice" and extracts contact name
- [ ] AC2: System handles invalid contact names gracefully
- [ ] AC3: System prevents duplicate calendar events (idempotency)
```

**Generate**:
```python
def test_parse_intent_extracts_contact_name():
    """AC1: System parses 'Book a meeting with Alice' and extracts contact name"""
    intent = intake.parse("Book a meeting with Alice")
    assert intent.entities.attendee == "Alice"

def test_parse_intent_invalid_contact_name():
    """AC2: System handles invalid contact names gracefully"""
    with pytest.raises(ValidationError, match="Invalid contact"):
        intake.parse("Book a meeting with @#$%")

async def test_idempotency_prevents_duplicate_events():
    """AC3: System prevents duplicate calendar events (idempotency)"""
    # First execution
    result1 = await service.execute(approved_preview)

    # Second execution with same args
    result2 = await service.execute(approved_preview)

    # Should return cached result, not create duplicate
    assert result1.result.id == result2.result.id
    # Verify only 1 event created (mock assertion)
```

## Running Tests

```bash
# Unit tests (fast)
pytest components/<Name>/tests/test_service.py -v

# Integration tests (slower, needs Docker)
pytest components/<Name>/tests/test_integration.py -v --integration

# Contract tests
pytest components/<Name>/tests/test_contract.py -v

# All tests with coverage
pytest components/<Name>/tests/ -v --cov=components/<Name> --cov-report=term-missing
```
