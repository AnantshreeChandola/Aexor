"""
VectorIndex Test Fixtures

Shared fixtures for all VectorIndex component tests. Provides mock
EmbeddingAdapter, mock PgvectorAdapter, sample plan data, and a
pre-configured VectorIndexService with mocked adapters. No real ONNX
model or pgvector required.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from components.VectorIndex.service.vector_index_service import (
    VectorIndexService,
)

_EMBEDDING_DIM = 384


def _deterministic_embedding(text: str) -> list[float]:
    """Generate a deterministic 384-dim pseudo-embedding from text hash.

    Same text always produces the same vector. Not a real embedding,
    but sufficient for testing adapter contracts and determinism.
    """
    digest = hashlib.sha384(text.encode()).digest()
    raw = [float(b) / 255.0 for b in digest]
    # Pad or truncate to 384
    while len(raw) < _EMBEDDING_DIM:
        raw.extend(raw)
    raw = raw[:_EMBEDDING_DIM]
    # Normalize to unit vector
    norm = sum(x * x for x in raw) ** 0.5
    if norm > 0:
        raw = [x / norm for x in raw]
    return raw


@pytest.fixture()
def mock_embedding_adapter():
    """Mock EmbeddingAdapter that returns deterministic 384-dim vectors.

    Uses SHA-384 hash of input text to produce consistent output.
    """
    adapter = MagicMock()
    adapter.embed = MagicMock(side_effect=_deterministic_embedding)
    adapter.embed_batch = MagicMock(
        side_effect=lambda texts: [_deterministic_embedding(t) for t in texts]
    )
    return adapter


@pytest.fixture()
def mock_pgvector_adapter():
    """Mock PgvectorAdapter with async stub methods."""
    adapter = MagicMock()
    adapter.check_pgvector_extension = AsyncMock(return_value=True)
    adapter.upsert_embedding = AsyncMock(return_value=None)
    adapter.hybrid_search = AsyncMock(return_value=[])
    adapter.delete_by_plan_id = AsyncMock(return_value=None)
    adapter.bulk_upsert = AsyncMock(return_value=0)
    return adapter


@pytest.fixture()
def sample_plan_data() -> dict:
    """Realistic plan dict with intent_type, graph, constraints, entities."""
    return {
        "plan_id": "plan_01HXYZ",
        "intent_type": "book_travel",
        "intent": {
            "action": "book_flight",
            "entities": {
                "origin": "SFO",
                "destination": "JFK",
            },
        },
        "graph": [
            {"step": 1, "action": "search_flights", "args": {"origin": "SFO"}},
            {"step": 2, "action": "book_flight", "args": {"flight_id": "UA123"}},
        ],
        "constraints": {
            "max_price": 500,
            "currency": "USD",
        },
    }


@pytest.fixture()
def sample_plans_batch() -> list[dict]:
    """List of 10 plan dicts with 3 different intent_types."""
    intent_types = [
        "book_travel",
        "schedule_meeting",
        "send_notification",
    ]
    plans = []
    for i in range(10):
        intent = intent_types[i % len(intent_types)]
        plans.append(
            {
                "plan_id": f"plan_{i:05d}",
                "intent_type": intent,
                "intent": {
                    "intent": intent,
                    "entities": {"entity_key": f"entity_val_{i}"},
                },
                "graph": [
                    {"step": 1, "action": f"action_{intent}_{i}"},
                ],
                "constraints": {"limit": i * 10},
            }
        )
    return plans


@pytest.fixture()
def vector_index_service(mock_embedding_adapter, mock_pgvector_adapter):
    """VectorIndexService with mocked adapters (no real ONNX or pgvector)."""
    return VectorIndexService(
        embedding_adapter=mock_embedding_adapter,
        pgvector_adapter=mock_pgvector_adapter,
    )


@pytest.fixture()
def sample_search_results() -> list[dict]:
    """Sample raw search result rows from PgvectorAdapter."""
    return [
        {
            "plan_id": "plan_00001",
            "intent_type": "book_travel",
            "rrf_score": 0.032,
            "keyword_rank": 1,
            "semantic_rank": 2,
        },
        {
            "plan_id": "plan_00002",
            "intent_type": "book_travel",
            "rrf_score": 0.028,
            "keyword_rank": None,
            "semantic_rank": 1,
        },
        {
            "plan_id": "plan_00003",
            "intent_type": "schedule_meeting",
            "rrf_score": 0.016,
            "keyword_rank": 3,
            "semantic_rank": None,
        },
    ]
