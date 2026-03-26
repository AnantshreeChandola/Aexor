"""
ContextRAG Test Fixtures

Shared fixtures with mocked downstream services (PreferenceService,
FactService, PatternService, PlanService, VectorIndexService) and
sample data for all ContextRAG tests.

Reference: tasks.md T401
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from components.ContextRAG.service.context_rag_service import ContextRAGService
from components.History.domain.models import PatternsResponse, QueryFactsResponse
from components.VectorIndex.domain.models import HybridSearchResult
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

SAMPLE_USER_ID = str(uuid4())


def _make_preference_evidence() -> list[EvidenceItem]:
    """Two sample preference evidence items (tier=2)."""
    return [
        EvidenceItem(
            type="preference",
            key="meeting_duration_min",
            value=30,
            confidence=1.0,
            source_ref="profilestore:prefs/meeting_duration_min",
            ttl_days=None,
            tier=2,
        ),
        EvidenceItem(
            type="preference",
            key="timezone",
            value="America/Chicago",
            confidence=1.0,
            source_ref="profilestore:prefs/timezone",
            ttl_days=None,
            tier=2,
        ),
    ]


def _make_plan_evidence() -> list[EvidenceItem]:
    """Two sample plan evidence items (tier=3)."""
    return [
        EvidenceItem(
            type="plan",
            key="plan_schedule_meeting_01",
            value={"plan_id": "01HXYZ1234567890ABCDEFGHJK", "success_rate": 0.85},
            confidence=0.85,
            source_ref="planlibrary:plans/01HXYZ1234567890ABCDEFGHJK",
            ttl_days=None,
            tier=3,
        ),
        EvidenceItem(
            type="plan",
            key="plan_schedule_meeting_02",
            value={"plan_id": "01HXYZ1234567890ABCDEFGHJL", "success_rate": 0.75},
            confidence=0.75,
            source_ref="planlibrary:plans/01HXYZ1234567890ABCDEFGHJL",
            ttl_days=None,
            tier=3,
        ),
    ]


SAMPLE_FACT_DICTS = [
    {
        "type": "history",
        "key": "last_meeting_alice",
        "value": "2026-03-20T10:00:00Z",
        "confidence": 0.9,
        "source_ref": "history:facts/abc123",
        "ttl_days": 30,
        "tier": 3,
    },
    {
        "type": "history",
        "key": "meeting_room_preference",
        "value": "Room 42",
        "confidence": 0.8,
        "source_ref": "history:facts/def456",
        "ttl_days": 30,
        "tier": 3,
    },
]

SAMPLE_PATTERN_DICTS = [
    {
        "pattern_id": str(uuid4()),
        "pattern_key": "weekly_standup",
        "pattern_description": "Schedules weekly standup every Monday at 9am",
        "confidence": 0.7,
    },
]


SAMPLE_INTENT = Intent(
    intent="schedule_meeting",
    entities={"person": "Alice"},
    constraints={},
    user_id=SAMPLE_USER_ID,
    context_budget=3,
    trace_id="a" * 32,
)

SAMPLE_TIER2_INTENT = Intent(
    intent="schedule_meeting",
    entities={"person": "Alice"},
    constraints={},
    user_id=SAMPLE_USER_ID,
    context_budget=2,
    trace_id="b" * 32,
)

SAMPLE_TIER1_INTENT = Intent(
    intent="schedule_meeting",
    entities={"person": "Alice"},
    constraints={},
    user_id=SAMPLE_USER_ID,
    context_budget=1,
    trace_id="c" * 32,
)


@pytest.fixture()
def mock_preference_service() -> AsyncMock:
    """PreferenceService mock returning list[EvidenceItem]."""
    service = AsyncMock()
    service.get_all_preferences.return_value = _make_preference_evidence()
    return service


@pytest.fixture()
def mock_fact_service() -> AsyncMock:
    """FactService mock returning QueryFactsResponse."""
    service = AsyncMock()
    service.get_facts_by_intent.return_value = QueryFactsResponse(
        evidence=SAMPLE_FACT_DICTS,
        total_count=2,
        returned_count=2,
    )
    return service


@pytest.fixture()
def mock_pattern_service() -> AsyncMock:
    """PatternService mock returning PatternsResponse."""
    service = AsyncMock()
    service.get_patterns.return_value = PatternsResponse(
        patterns=SAMPLE_PATTERN_DICTS,
        total_count=1,
    )
    return service


@pytest.fixture()
def mock_plan_service() -> AsyncMock:
    """PlanService mock returning list[EvidenceItem]."""
    service = AsyncMock()
    service.get_plans_by_intent.return_value = _make_plan_evidence()
    return service


@pytest.fixture()
def mock_vector_index_service() -> AsyncMock:
    """VectorIndexService mock returning list[HybridSearchResult]."""
    service = AsyncMock()
    service.search.return_value = [
        HybridSearchResult(
            plan_id="01HXYZ1234567890ABCDEFGHJK",
            intent_type="schedule_meeting",
            rrf_score=0.82,
            keyword_rank=1,
            semantic_rank=2,
        ),
        HybridSearchResult(
            plan_id="01HXYZ1234567890ABCDEFGHJL",
            intent_type="schedule_meeting",
            rrf_score=0.65,
            keyword_rank=3,
            semantic_rank=1,
        ),
    ]
    return service


@pytest.fixture()
def context_rag_service(
    mock_preference_service: AsyncMock,
    mock_fact_service: AsyncMock,
    mock_pattern_service: AsyncMock,
    mock_plan_service: AsyncMock,
    mock_vector_index_service: AsyncMock,
) -> ContextRAGService:
    """ContextRAGService with all mocked services."""
    return ContextRAGService(
        preference_service=mock_preference_service,
        fact_service=mock_fact_service,
        pattern_service=mock_pattern_service,
        plan_service=mock_plan_service,
        vector_index_service=mock_vector_index_service,
    )


@pytest.fixture()
def context_rag_service_no_vectorindex(
    mock_preference_service: AsyncMock,
    mock_fact_service: AsyncMock,
    mock_pattern_service: AsyncMock,
    mock_plan_service: AsyncMock,
) -> ContextRAGService:
    """ContextRAGService with VectorIndex set to None."""
    return ContextRAGService(
        preference_service=mock_preference_service,
        fact_service=mock_fact_service,
        pattern_service=mock_pattern_service,
        plan_service=mock_plan_service,
        vector_index_service=None,
    )
