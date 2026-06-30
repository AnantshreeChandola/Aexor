"""
VectorIndex Contract Tests

Schema compliance tests for domain models (T101) and SPEC acceptance
scenario contract tests (T702). Uses mocked adapters -- no DB or ONNX
model required.
"""

import pytest

from components.VectorIndex.domain.models import (
    EmbeddingModelError,
    HybridSearchResult,
    VectorIndexError,
    VectorIndexUnavailableError,
)

# =============================================================================
# T101 -- Domain model unit tests
# =============================================================================


class TestHybridSearchResult:
    """Tests for HybridSearchResult Pydantic model validation."""

    def test_valid_data_produces_correct_fields(self):
        """HybridSearchResult accepts valid data and produces correct values."""
        result = HybridSearchResult(
            plan_id="plan_01HXYZ",
            intent_type="book_travel",
            rrf_score=0.032,
            keyword_rank=1,
            semantic_rank=2,
        )
        assert result.plan_id == "plan_01HXYZ"
        assert result.intent_type == "book_travel"
        assert result.rrf_score == 0.032
        assert result.keyword_rank == 1
        assert result.semantic_rank == 2

    def test_keyword_rank_defaults_to_none(self):
        """keyword_rank defaults to None when not provided."""
        result = HybridSearchResult(
            plan_id="plan_01",
            intent_type="test",
            rrf_score=0.01,
        )
        assert result.keyword_rank is None

    def test_semantic_rank_defaults_to_none(self):
        """semantic_rank defaults to None when not provided."""
        result = HybridSearchResult(
            plan_id="plan_01",
            intent_type="test",
            rrf_score=0.01,
        )
        assert result.semantic_rank is None

    def test_rrf_score_is_float(self):
        """rrf_score is stored as a float."""
        result = HybridSearchResult(
            plan_id="plan_01",
            intent_type="test",
            rrf_score=1,  # Pass int, should coerce to float
        )
        assert isinstance(result.rrf_score, float)

    def test_partial_ranks_allowed(self):
        """A result can have keyword_rank but not semantic_rank."""
        result = HybridSearchResult(
            plan_id="plan_01",
            intent_type="test",
            rrf_score=0.016,
            keyword_rank=3,
            semantic_rank=None,
        )
        assert result.keyword_rank == 3
        assert result.semantic_rank is None


class TestErrorClasses:
    """Tests for VectorIndex error class instantiation and inheritance."""

    def test_vector_index_error_is_base(self):
        """VectorIndexError is the base exception."""
        err = VectorIndexError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"

    def test_unavailable_error_inherits_base(self):
        """VectorIndexUnavailableError subclasses VectorIndexError."""
        err = VectorIndexUnavailableError("pgvector missing")
        assert isinstance(err, VectorIndexError)
        assert err.reason == "pgvector missing"
        assert "pgvector missing" in str(err)

    def test_unavailable_error_default_reason(self):
        """VectorIndexUnavailableError has a default reason."""
        err = VectorIndexUnavailableError()
        assert err.reason == "pgvector extension not installed"

    def test_embedding_model_error_inherits_base(self):
        """EmbeddingModelError subclasses VectorIndexError."""
        err = EmbeddingModelError(
            model_name="all-MiniLM-L6-v2",
            reason="file not found",
        )
        assert isinstance(err, VectorIndexError)
        assert err.model_name == "all-MiniLM-L6-v2"
        assert err.reason == "file not found"
        assert "all-MiniLM-L6-v2" in str(err)

    def test_embedding_model_error_empty_reason(self):
        """EmbeddingModelError works with empty reason."""
        err = EmbeddingModelError(model_name="test-model")
        assert err.model_name == "test-model"
        assert err.reason == ""


# =============================================================================
# T702 -- SPEC acceptance scenario contract tests
# =============================================================================


class TestContractUS1StoreEmbedding:
    """US1: Store Plan Embedding + Text Index."""

    @pytest.mark.asyncio
    async def test_ac1_store_valid_plan(
        self, vector_index_service, mock_pgvector_adapter, sample_plan_data
    ):
        """US1-AC1: store_embedding with valid plan succeeds.

        Verifies upsert called with correct plan_id and intent_type.
        """
        await vector_index_service.store_embedding(
            plan_id="plan_01HXYZ",
            plan_data=sample_plan_data,
        )
        mock_pgvector_adapter.upsert_embedding.assert_called_once()
        call_kwargs = mock_pgvector_adapter.upsert_embedding.call_args
        assert call_kwargs.kwargs["plan_id"] == "plan_01HXYZ"
        assert call_kwargs.kwargs["intent_type"] == "book_travel"
        # Verify 384-dim embedding
        embedding = call_kwargs.kwargs["embedding"]
        assert len(embedding) == 384
        # Verify search_text is non-empty
        assert len(call_kwargs.kwargs["search_text"]) > 0

    @pytest.mark.asyncio
    async def test_ac2_upsert_same_plan_id(
        self, vector_index_service, mock_pgvector_adapter, sample_plan_data
    ):
        """US1-AC2: Calling store_embedding twice with same plan_id upserts."""
        await vector_index_service.store_embedding("plan_01HXYZ", sample_plan_data)
        await vector_index_service.store_embedding("plan_01HXYZ", sample_plan_data)
        assert mock_pgvector_adapter.upsert_embedding.call_count == 2

    @pytest.mark.asyncio
    async def test_ac3_empty_plan_raises_value_error(self, vector_index_service):
        """US1-AC3: store_embedding with empty plan_data raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            await vector_index_service.store_embedding("plan_01", {})

    @pytest.mark.asyncio
    async def test_ac3_none_plan_raises_value_error(self, vector_index_service):
        """US1-AC3: store_embedding with None plan_data raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            await vector_index_service.store_embedding("plan_01", None)


class TestContractUS2HybridSearch:
    """US2: Hybrid Search: BM25 + Semantic via RRF."""

    @pytest.mark.asyncio
    async def test_ac1_search_returns_hybrid_results(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        sample_search_results,
    ):
        """US2-AC1: search returns at most top_k HybridSearchResult items."""
        mock_pgvector_adapter.hybrid_search.return_value = sample_search_results[:2]
        results = await vector_index_service.search(
            query_text="book flight to SFO",
            top_k=5,
        )
        assert len(results) <= 5
        for r in results:
            assert isinstance(r, HybridSearchResult)
            assert r.plan_id
            assert r.intent_type
            assert isinstance(r.rrf_score, float)

    @pytest.mark.asyncio
    async def test_ac5_semantic_only_results(self, vector_index_service, mock_pgvector_adapter):
        """US2-AC5: Semantic-only results have keyword_rank=None."""
        mock_pgvector_adapter.hybrid_search.return_value = [
            {
                "plan_id": "plan_sem",
                "intent_type": "test",
                "rrf_score": 0.016,
                "keyword_rank": None,
                "semantic_rank": 1,
            }
        ]
        results = await vector_index_service.search("novel query")
        assert len(results) == 1
        assert results[0].keyword_rank is None
        assert results[0].semantic_rank == 1

    @pytest.mark.asyncio
    async def test_ac7_top_k_zero_raises_value_error(self, vector_index_service):
        """US2-AC7: search with top_k=0 raises ValueError."""
        with pytest.raises(ValueError, match="top_k"):
            await vector_index_service.search("query", top_k=0)

    @pytest.mark.asyncio
    async def test_ac8_no_stored_returns_empty(self, vector_index_service, mock_pgvector_adapter):
        """US2-AC8: search with no stored embeddings returns empty list."""
        mock_pgvector_adapter.hybrid_search.return_value = []
        results = await vector_index_service.search("anything")
        assert results == []


class TestContractUS3DeleteEmbedding:
    """US3: Delete Plan Embedding."""

    @pytest.mark.asyncio
    async def test_ac1_delete_removes_row(self, vector_index_service, mock_pgvector_adapter):
        """US3-AC1: delete_embedding calls adapter delete."""
        await vector_index_service.delete_embedding("plan_01HXYZ")
        mock_pgvector_adapter.delete_by_plan_id.assert_called_once_with("plan_01HXYZ")

    @pytest.mark.asyncio
    async def test_ac2_delete_nonexistent_no_error(
        self, vector_index_service, mock_pgvector_adapter
    ):
        """US3-AC2: delete_embedding for non-existent plan_id is idempotent."""
        mock_pgvector_adapter.delete_by_plan_id.return_value = None
        # Should not raise
        await vector_index_service.delete_embedding("nonexistent_plan")


class TestContractUS4BulkStore:
    """US4: Bulk Store Embeddings."""

    @pytest.mark.asyncio
    async def test_ac1_bulk_store_returns_count(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        sample_plans_batch,
    ):
        """US4-AC1: bulk_store with 10 plans returns count 10."""
        mock_pgvector_adapter.bulk_upsert.return_value = 10
        count = await vector_index_service.bulk_store(sample_plans_batch)
        assert count == 10
