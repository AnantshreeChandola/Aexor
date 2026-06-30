"""
Tests for History API Routes

Test all endpoints with mocked services.

Reference: tasks.md T402
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ..api.routes import router
from ..domain.models import (
    InvalidFactError,
    PatternsResponse,
    QueryFactsResponse,
    StoreFactResponse,
)


@pytest.fixture
def mock_fact_service():
    """Mock FactService."""
    mock = MagicMock()
    mock.store_fact = AsyncMock()
    mock.get_facts_by_intent = AsyncMock()
    return mock


@pytest.fixture
def mock_pattern_service():
    """Mock PatternService."""
    mock = MagicMock()
    mock.get_patterns = AsyncMock()
    return mock


@pytest.fixture
def mock_db_adapter():
    """Mock DatabaseAdapter."""
    mock = MagicMock()
    mock.health_check = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def app(mock_fact_service, mock_pattern_service, mock_db_adapter, monkeypatch):
    """Create test FastAPI app with mocked services."""
    from shared.api import auth
    from shared.api.auth import RequireTier3, get_auth_context

    from ..api.routes import get_db_adapter, get_fact_service, get_pattern_service

    # Patch verify_user_access at module level
    monkeypatch.setattr(auth, "verify_user_access", mock_verify_user_access)

    test_app = FastAPI()
    test_app.include_router(router)

    # Inject mocked services via state (for health check route)
    test_app.state.fact_service = mock_fact_service
    test_app.state.pattern_service = mock_pattern_service
    test_app.state.history_db_adapter = mock_db_adapter

    # Override dependencies
    test_app.dependency_overrides[get_auth_context] = mock_get_auth_context
    test_app.dependency_overrides[RequireTier3] = mock_require_tier3
    test_app.dependency_overrides[get_fact_service] = lambda: mock_fact_service
    test_app.dependency_overrides[get_pattern_service] = lambda: mock_pattern_service
    test_app.dependency_overrides[get_db_adapter] = lambda: mock_db_adapter

    return test_app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Create auth headers for testing."""
    return {
        "X-User-ID": str(uuid4()),
        "X-Context-Tier": "3",
        "X-Email": "test@example.com",
    }


# Mock auth dependencies for FastAPI
# Use a consistent test user ID across all tests
TEST_USER_ID = uuid4()


def mock_get_auth_context():
    """Mock auth context for testing."""
    return {
        "user_id": TEST_USER_ID,
        "context_tier": 3,
        "email": "test@example.com",
    }


def mock_require_tier3():
    """Mock tier 3 requirement for testing."""
    return None


def mock_verify_user_access(target_user_id, auth_context):
    """Mock verify_user_access for testing - always allow."""
    # For tests, allow all access
    pass


# POST /history/{user_id}/facts with valid data - 200 success


@pytest.mark.asyncio
async def test_store_fact_success(client, mock_fact_service):
    """Test storing a valid fact returns 201 with StoreFactResponse."""
    fact_id = uuid4()
    now = datetime.now(UTC)

    # Mock service response
    mock_fact_service.store_fact.return_value = StoreFactResponse(
        status="ok",
        fact_id=fact_id,
        stored_at=now,
    )

    request_data = {
        "fact_text": "Booked 30min meeting with Alice",
        "intent_type": "schedule_meeting",
        "entities": {"person": "Alice"},
        "outcome": True,
        "source_plan_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "ttl_days": 30,
    }

    response = client.post(
        f"/history/{TEST_USER_ID}/facts",
        json=request_data,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "ok"
    assert "fact_id" in data
    assert "stored_at" in data


# POST /history/{user_id}/facts with empty fact_text - 400 INVALID_FACT


@pytest.mark.asyncio
async def test_store_fact_empty_text(client, mock_fact_service):
    """Test storing fact with empty fact_text returns 422 (Pydantic validation)."""
    # Use TEST_USER_ID from module

    request_data = {
        "fact_text": "",
        "intent_type": "test",
        "entities": {},
        "outcome": True,
    }

    response = client.post(
        f"/history/{TEST_USER_ID}/facts",
        json=request_data,
    )

    # Pydantic validates min_length before hitting service layer
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data  # FastAPI validation error format


# POST /history/{user_id}/facts with oversized fact_text - 400 FACT_TOO_LARGE


@pytest.mark.asyncio
async def test_store_fact_too_large(client, mock_fact_service):
    """Test storing oversized fact returns 422 (Pydantic validation)."""
    # Use TEST_USER_ID from module

    request_data = {
        "fact_text": "a" * 5000,  # Exceeds max_length=4096
        "intent_type": "test",
        "entities": {},
        "outcome": True,
    }

    response = client.post(
        f"/history/{TEST_USER_ID}/facts",
        json=request_data,
    )

    # Pydantic validates max_length before hitting service layer
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data  # FastAPI validation error format


# GET /history/{user_id}/facts - returns QueryFactsResponse


@pytest.mark.asyncio
async def test_query_facts_success(client, mock_fact_service):
    """Test querying facts returns QueryFactsResponse."""
    # Use TEST_USER_ID from module

    # Mock service response
    mock_fact_service.get_facts_by_intent.return_value = QueryFactsResponse(
        evidence=[
            {
                "type": "history",
                "key": "meeting_1",
                "value": {"fact": "Meeting with Alice"},
                "confidence": 0.9,
                "source_ref": "history:facts/123",
                "ttl_days": 27,
                "tier": 3,
            }
        ],
        total_count=1,
        returned_count=1,
    )

    response = client.get(f"/history/{TEST_USER_ID}/facts")

    assert response.status_code == 200
    data = response.json()
    assert "evidence" in data
    assert "total_count" in data
    assert "returned_count" in data
    assert data["total_count"] == 1


# GET /history/{user_id}/facts?intent_type=schedule_meeting - filters by intent


@pytest.mark.asyncio
async def test_query_facts_with_intent_filter(client, mock_fact_service):
    """Test querying facts with intent_type filter."""
    # Use TEST_USER_ID from module

    mock_fact_service.get_facts_by_intent.return_value = QueryFactsResponse(
        evidence=[],
        total_count=0,
        returned_count=0,
    )

    response = client.get(
        f"/history/{TEST_USER_ID}/facts",
        params={"intent_type": "schedule_meeting"},
    )

    assert response.status_code == 200
    # Verify service was called with intent_type
    mock_fact_service.get_facts_by_intent.assert_called_once()
    call_kwargs = mock_fact_service.get_facts_by_intent.call_args[1]
    assert call_kwargs["intent_type"] == "schedule_meeting"


# GET /history/{user_id}/facts?limit=5 - respects limit parameter


@pytest.mark.asyncio
async def test_query_facts_with_limit(client, mock_fact_service):
    """Test querying facts with limit parameter."""
    # Use TEST_USER_ID from module

    mock_fact_service.get_facts_by_intent.return_value = QueryFactsResponse(
        evidence=[],
        total_count=0,
        returned_count=0,
    )

    response = client.get(
        f"/history/{TEST_USER_ID}/facts",
        params={"limit": 5},
    )

    assert response.status_code == 200
    # Verify service was called with limit
    call_kwargs = mock_fact_service.get_facts_by_intent.call_args[1]
    assert call_kwargs["limit"] == 5


# GET /history/{user_id}/facts with invalid query - 400 INVALID_QUERY


@pytest.mark.asyncio
async def test_query_facts_invalid_params(client, mock_fact_service):
    """Test querying facts with invalid limit returns 422 (Pydantic validation)."""
    # Use TEST_USER_ID from module

    response = client.get(
        f"/history/{TEST_USER_ID}/facts",
        params={"limit": 1000},  # Exceeds le=500 constraint
    )

    # Pydantic/FastAPI validates limit before hitting service layer
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data  # FastAPI validation error format


# GET /history/{user_id}/patterns - returns PatternsResponse


@pytest.mark.asyncio
async def test_query_patterns_success(client, mock_pattern_service):
    """Test querying patterns returns PatternsResponse."""
    # Use TEST_USER_ID from module

    # Mock service response
    mock_pattern_service.get_patterns.return_value = PatternsResponse(
        patterns=[
            {
                "pattern_id": str(uuid4()),
                "user_id": str(TEST_USER_ID),
                "intent_type": "schedule_meeting",
                "pattern_key": "schedule_meeting:person:Alice:Tuesday",
                "pattern_description": "Meets Alice on Tuesdays",
                "entity_pattern": {"person": "Alice"},
                "occurrence_count": 5,
                "last_seen": datetime.now(UTC).isoformat(),
                "confidence": 1.0,
            }
        ],
        total_count=1,
    )

    response = client.get(f"/history/{TEST_USER_ID}/patterns")

    assert response.status_code == 200
    data = response.json()
    assert "patterns" in data
    assert "total_count" in data
    assert data["total_count"] == 1


# GET /history/{user_id}/patterns?min_confidence=0.8 - filters by confidence


@pytest.mark.asyncio
async def test_query_patterns_with_confidence_filter(client, mock_pattern_service):
    """Test querying patterns with min_confidence filter."""
    # Use TEST_USER_ID from module

    mock_pattern_service.get_patterns.return_value = PatternsResponse(
        patterns=[],
        total_count=0,
    )

    response = client.get(
        f"/history/{TEST_USER_ID}/patterns",
        params={"min_confidence": 0.8},
    )

    assert response.status_code == 200
    # Verify service was called with min_confidence
    call_kwargs = mock_pattern_service.get_patterns.call_args[1]
    assert call_kwargs["min_confidence"] == 0.8


# GET /history/health - returns health status


@pytest.mark.asyncio
async def test_health_check_success(client, mock_db_adapter):
    """Test health check returns healthy status."""
    mock_db_adapter.health_check.return_value = True

    response = client.get("/history/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["component"] == "History"


# GET /history/health - returns 503 when database unavailable


@pytest.mark.asyncio
async def test_health_check_failure(client, mock_db_adapter):
    """Test health check returns 503 when database unavailable."""
    mock_db_adapter.health_check.return_value = False

    response = client.get("/history/health")

    assert response.status_code == 503


# Test error response format


@pytest.mark.asyncio
async def test_error_response_format(client, mock_fact_service):
    """Test service error responses match ErrorResponse schema."""
    # Use TEST_USER_ID from module

    # Mock service raises domain error (not Pydantic validation error)
    mock_fact_service.store_fact.side_effect = InvalidFactError("test error")

    request_data = {
        "fact_text": "Valid text but service will reject",
        "intent_type": "test",
        "entities": {},
        "outcome": True,
    }

    response = client.post(
        f"/history/{TEST_USER_ID}/facts",
        json=request_data,
    )

    assert response.status_code == 400
    data = response.json()

    # Verify error response structure
    assert "error_code" in data
    assert "message" in data
    assert "details" in data


# Test duplicate fact returns 201 with status='duplicate'


@pytest.mark.asyncio
async def test_store_duplicate_fact(client, mock_fact_service):
    """Test storing duplicate fact returns status='duplicate'."""
    # Use TEST_USER_ID from module
    fact_id = uuid4()
    now = datetime.now(UTC)

    # Mock service returns duplicate status
    mock_fact_service.store_fact.return_value = StoreFactResponse(
        status="duplicate",
        fact_id=fact_id,
        stored_at=now,
    )

    request_data = {
        "fact_text": "Same fact",
        "intent_type": "test",
        "entities": {},
        "outcome": True,
    }

    response = client.post(
        f"/history/{TEST_USER_ID}/facts",
        json=request_data,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "duplicate"


# Test query with recency_days parameter


@pytest.mark.asyncio
async def test_query_facts_with_recency_days(client, mock_fact_service):
    """Test querying facts with recency_days parameter."""
    # Use TEST_USER_ID from module

    mock_fact_service.get_facts_by_intent.return_value = QueryFactsResponse(
        evidence=[],
        total_count=0,
        returned_count=0,
    )

    response = client.get(
        f"/history/{TEST_USER_ID}/facts",
        params={"recency_days": 7},
    )

    assert response.status_code == 200
    # Verify service was called with recency_days
    call_kwargs = mock_fact_service.get_facts_by_intent.call_args[1]
    assert call_kwargs["recency_days"] == 7


# Test query patterns with intent_type filter


@pytest.mark.asyncio
async def test_query_patterns_with_intent_filter(client, mock_pattern_service):
    """Test querying patterns with intent_type filter."""
    # Use TEST_USER_ID from module

    mock_pattern_service.get_patterns.return_value = PatternsResponse(
        patterns=[],
        total_count=0,
    )

    response = client.get(
        f"/history/{TEST_USER_ID}/patterns",
        params={"intent_type": "schedule_meeting"},
    )

    assert response.status_code == 200
    # Verify service was called with intent_type
    call_kwargs = mock_pattern_service.get_patterns.call_args[1]
    assert call_kwargs["intent_type"] == "schedule_meeting"
