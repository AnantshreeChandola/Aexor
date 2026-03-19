"""
PlanWriter Test Fixtures

Shared fixtures with mocked downstream services (PlanService,
FactService, VectorIndexService) and sample data for all PlanWriter tests.

Reference: tasks.md T001
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from components.History.domain.models import StoreFactResponse
from components.PlanLibrary.domain.models import StorePlanResponse
from components.PlanWriter.service.plan_writer_service import PlanWriterService

# Valid 26-char ULID for tests
SAMPLE_PLAN_ID = "01HXYZ1234567890ABCDEFGHJK"
SAMPLE_FACT_ID = uuid4()
SAMPLE_USER_ID = uuid4()


@pytest.fixture()
def mock_plan_service() -> AsyncMock:
    """PlanService mock with store_plan returning StorePlanResponse."""
    service = AsyncMock()
    service.store_plan.return_value = StorePlanResponse(
        plan_id=SAMPLE_PLAN_ID,
        stored_at=datetime.now(UTC),
    )
    return service


@pytest.fixture()
def mock_fact_service() -> AsyncMock:
    """FactService mock with store_fact returning StoreFactResponse."""
    service = AsyncMock()
    service.store_fact.return_value = StoreFactResponse(
        status="ok",
        fact_id=SAMPLE_FACT_ID,
        stored_at=datetime.now(UTC),
    )
    return service


@pytest.fixture()
def mock_vector_index_service() -> AsyncMock:
    """VectorIndexService mock with store_embedding returning None."""
    service = AsyncMock()
    service.store_embedding.return_value = None
    return service


@pytest.fixture()
def sample_plan() -> dict:
    """Plan dict matching GLOBAL_SPEC Section 2.3."""
    return {
        "plan_id": SAMPLE_PLAN_ID,
        "intent": {
            "intent": "book_flight",
            "entities": {
                "destination": "NYC",
                "airline": "Delta",
            },
        },
        "graph": [
            {"step": 1, "action": "search_flights", "args": {}},
            {"step": 2, "action": "select_flight", "args": {}},
        ],
        "meta": {
            "intent_type": "book_flight",
            "version": "2.2",
            "created_at": "2026-03-19T10:00:00Z",
        },
        "constraints": {},
    }


@pytest.fixture()
def sample_signature() -> dict:
    """Signature dict matching GLOBAL_SPEC Section 2.4."""
    return {
        "algo": "Ed25519",
        "signer": "planner@system",
        "signature": "dGVzdHNpZ25hdHVyZQ==",
        "pubkey_id": "k1",
        "plan_hash": "abc123def456",
    }


@pytest.fixture()
def sample_outcome_success() -> dict:
    """Successful execution outcome."""
    return {
        "success": True,
        "error_type": None,
        "error_details": None,
        "execution_start": "2026-03-19T10:00:00Z",
        "execution_end": "2026-03-19T10:00:01Z",
        "total_steps": 5,
        "failed_step": None,
        "context_data": {},
    }


@pytest.fixture()
def sample_outcome_failure() -> dict:
    """Failed execution outcome."""
    return {
        "success": False,
        "error_type": "timeout",
        "error_details": {"reason": "No progress for 5 minutes"},
        "execution_start": "2026-03-19T10:00:00Z",
        "execution_end": "2026-03-19T10:05:30Z",
        "total_steps": 5,
        "failed_step": 3,
        "context_data": {},
    }


@pytest.fixture()
def sample_metrics() -> dict:
    """Performance metrics dict."""
    return {
        "preview_latency_ms": 450,
        "execute_latency_ms": 1200,
        "step_timings": [
            {"step": 1, "latency_ms": 200},
            {"step": 2, "latency_ms": 300},
        ],
    }


@pytest.fixture()
def sample_user_id() -> UUID:
    """User UUID for testing."""
    return SAMPLE_USER_ID


@pytest.fixture()
def plan_writer_service(
    mock_plan_service: AsyncMock,
    mock_fact_service: AsyncMock,
    mock_vector_index_service: AsyncMock,
) -> PlanWriterService:
    """PlanWriterService with all three mocked downstream services."""
    return PlanWriterService(
        plan_service=mock_plan_service,
        fact_service=mock_fact_service,
        vector_index_service=mock_vector_index_service,
    )


@pytest.fixture()
def plan_writer_service_no_vectorindex(
    mock_plan_service: AsyncMock,
    mock_fact_service: AsyncMock,
) -> PlanWriterService:
    """PlanWriterService with VectorIndex set to None."""
    return PlanWriterService(
        plan_service=mock_plan_service,
        fact_service=mock_fact_service,
        vector_index_service=None,
    )
