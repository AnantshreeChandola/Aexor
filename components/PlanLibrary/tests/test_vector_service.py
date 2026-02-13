"""
VectorService Unit Tests

Tests for similarity search and embedding generation.
Uses mocked adapters following ProfileStore test patterns.

Reference: tasks.md T204
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from shared.schemas.evidence import EvidenceItem

from components.PlanLibrary.domain.models import (
    EmbeddingServiceError,
    SimilarityMatch,
)
from components.PlanLibrary.service.vector_service import VectorService


VALID_ULID = "01HX1234567890ABCDEFGHJKMN"


@pytest.fixture
def mock_vector_adapter():
    """Create mock vector adapter."""
    adapter = MagicMock()
    adapter.similarity_search = AsyncMock(return_value=[])
    adapter.store_embedding = AsyncMock(return_value=True)
    adapter.health_check = AsyncMock(return_value=True)
    return adapter


@pytest.fixture
def mock_embedding_client():
    """Create mock embedding client."""
    client = MagicMock()
    client.generate_embedding = AsyncMock(return_value=[0.1] * 1536)
    return client


@pytest.fixture
def vector_service(mock_vector_adapter, mock_embedding_client):
    """Create VectorService with mocked dependencies."""
    return VectorService(
        vector_adapter=mock_vector_adapter,
        embedding_client=mock_embedding_client,
    )


class TestSimilaritySearch:
    """Tests for VectorService.similarity_search()."""

    @pytest.mark.asyncio
    async def test_similarity_search_returns_results(
        self, vector_service, mock_vector_adapter
    ):
        """Similarity search returns similar plans (US-3 scenario 1)."""
        mock_vector_adapter.similarity_search.return_value = [
            SimilarityMatch(
                plan_id=VALID_ULID,
                similarity_score=0.85,
                success_rate=0.9,
                intent_type="book_restaurant",
                pattern_summary="Book restaurant plan with 4 steps",
            ),
        ]

        result = await vector_service.similarity_search(
            query_text="reserve a dinner table",
            similarity_threshold=0.5,
        )

        assert len(result) == 1
        assert isinstance(result[0], EvidenceItem)
        assert result[0].type == "plan"
        assert result[0].tier == 3

    @pytest.mark.asyncio
    async def test_similarity_search_empty_below_threshold(
        self, vector_service, mock_vector_adapter
    ):
        """Similarity search returns empty below threshold (US-3 scenario 3)."""
        mock_vector_adapter.similarity_search.return_value = []

        result = await vector_service.similarity_search(
            query_text="completely unrelated query",
            similarity_threshold=0.9,
        )

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_similarity_search_filters_low_success(
        self, vector_service, mock_vector_adapter
    ):
        """Similarity search filters by success_threshold."""
        mock_vector_adapter.similarity_search.return_value = [
            SimilarityMatch(
                plan_id=VALID_ULID,
                similarity_score=0.9,
                success_rate=0.3,  # Below default 0.5 threshold
                intent_type="test",
                pattern_summary="Low success plan",
            ),
        ]

        result = await vector_service.similarity_search(
            query_text="test query",
            success_threshold=0.5,
        )

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_similarity_search_embedding_failure_returns_empty(
        self, vector_service, mock_embedding_client
    ):
        """Embedding failure returns empty results gracefully."""
        mock_embedding_client.generate_embedding.side_effect = (
            EmbeddingServiceError(reason="API timeout")
        )

        result = await vector_service.similarity_search(
            query_text="test query",
        )

        assert len(result) == 0


class TestEmbeddingGeneration:
    """Tests for VectorService.queue_embedding_generation()."""

    @pytest.mark.asyncio
    async def test_embedding_queued_successfully(
        self, vector_service, mock_embedding_client, mock_vector_adapter
    ):
        """Embedding generation queued successfully."""
        result = await vector_service.queue_embedding_generation(
            plan_id=VALID_ULID,
            plan_text="schedule_meeting plan text",
        )

        assert result is True
        mock_embedding_client.generate_embedding.assert_called_once()
        mock_vector_adapter.store_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_embedding_failure_does_not_block(
        self, vector_service, mock_embedding_client
    ):
        """Embedding generation failure does not block plan storage."""
        mock_embedding_client.generate_embedding.side_effect = (
            EmbeddingServiceError(reason="Circuit breaker open")
        )

        result = await vector_service.queue_embedding_generation(
            plan_id=VALID_ULID,
            plan_text="test plan text",
        )

        # Returns False but does not raise
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
