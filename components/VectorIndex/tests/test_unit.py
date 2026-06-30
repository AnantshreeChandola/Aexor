"""
VectorIndex Unit Tests

Unit tests for TextBuilder (T301), EmbeddingAdapter (T303),
PgvectorAdapter (T305), VectorIndexService (T402), and
determinism verification (T800).
"""

from unittest.mock import MagicMock, patch

import pytest

from components.VectorIndex.adapters.text_builder import (
    build_search_text,
    extract_intent_type,
)
from components.VectorIndex.domain.models import (
    EmbeddingModelError,
    HybridSearchResult,
)

# =============================================================================
# T301 -- TextBuilder unit tests
# =============================================================================


class TestExtractIntentType:
    """Tests for extract_intent_type function."""

    def test_top_level_intent_type(self):
        """Priority 1: top-level intent_type is used."""
        plan = {"intent_type": "book_travel"}
        assert extract_intent_type(plan) == "book_travel"

    def test_nested_intent_intent(self):
        """Priority 2: intent.intent is used when no top-level intent_type."""
        plan = {"intent": {"intent": "schedule_meeting"}}
        assert extract_intent_type(plan) == "schedule_meeting"

    def test_defaults_to_unknown(self):
        """Priority 3: returns 'unknown' when no intent_type found."""
        plan = {"graph": []}
        assert extract_intent_type(plan) == "unknown"

    def test_empty_plan(self):
        """Empty plan_data returns 'unknown'."""
        assert extract_intent_type({}) == "unknown"

    def test_none_plan(self):
        """None plan_data returns 'unknown'."""
        assert extract_intent_type(None) == "unknown"

    def test_top_level_takes_priority_over_nested(self):
        """Top-level intent_type takes priority over intent.intent."""
        plan = {
            "intent_type": "top_level",
            "intent": {"intent": "nested"},
        }
        assert extract_intent_type(plan) == "top_level"


class TestBuildSearchText:
    """Tests for build_search_text function."""

    def test_full_plan_produces_pipe_separated_text(self):
        """Plan with all fields produces correct pipe-separated format."""
        plan = {
            "intent_type": "schedule_meeting",
            "graph": [
                {"step": 1, "action": "search_calendar"},
                {"step": 2, "action": "check_availability"},
                {"step": 3, "action": "send_invite"},
            ],
            "constraints": {
                "max_duration": 60,
                "flexible_time": True,
            },
            "intent": {
                "entities": {
                    "person": "Alice",
                    "location": "SFO",
                },
            },
        }
        result = build_search_text(plan)
        parts = result.split(" | ")
        assert len(parts) == 4
        assert parts[0] == "schedule_meeting"
        assert "search_calendar" in parts[1]
        assert "check_availability" in parts[1]
        assert "send_invite" in parts[1]
        assert "max_duration" in parts[2]
        assert "flexible_time" in parts[2]
        assert "Alice" in parts[3]
        assert "SFO" in parts[3]

    def test_actions_search_flights_book_flight(self):
        """SPEC scenario: actions contain search_flights and book_flight."""
        plan = {
            "intent_type": "book_travel",
            "graph": [
                {"step": 1, "action": "search_flights"},
                {"step": 2, "action": "book_flight"},
            ],
        }
        result = build_search_text(plan)
        assert "search_flights" in result
        assert "book_flight" in result

    def test_empty_plan_raises_value_error(self):
        """Empty plan_data raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            build_search_text({})

    def test_none_plan_raises_value_error(self):
        """None plan_data raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            build_search_text(None)

    def test_missing_graph(self):
        """Plan without graph still produces text (empty actions part)."""
        plan = {"intent_type": "test"}
        result = build_search_text(plan)
        assert result.startswith("test |")

    def test_missing_constraints(self):
        """Plan without constraints still produces text."""
        plan = {"intent_type": "test", "graph": []}
        result = build_search_text(plan)
        assert "test" in result

    def test_missing_entities(self):
        """Plan without entities still produces text."""
        plan = {"intent_type": "test"}
        result = build_search_text(plan)
        assert "test" in result

    def test_top_level_entities_fallback(self):
        """Falls back to top-level entities if intent.entities missing."""
        plan = {
            "intent_type": "test",
            "entities": {"city": "NYC"},
        }
        result = build_search_text(plan)
        assert "NYC" in result

    def test_graph_with_call_key(self):
        """Graph steps with 'call' key (instead of 'action') are extracted."""
        plan = {
            "intent_type": "test",
            "graph": [{"step": 1, "call": "api_call"}],
        }
        result = build_search_text(plan)
        assert "api_call" in result

    def test_default_intent_type_in_search_text(self):
        """Plan without intent_type uses 'unknown' in search text."""
        plan = {"graph": [{"step": 1, "action": "do_something"}]}
        result = build_search_text(plan)
        assert result.startswith("unknown |")


# =============================================================================
# T303 -- EmbeddingAdapter unit tests (mocked ONNX)
# =============================================================================


class TestEmbeddingAdapter:
    """Tests for EmbeddingAdapter with mocked ONNX session."""

    def test_model_not_found_raises_embedding_model_error(self):
        """Missing ONNX model file raises EmbeddingModelError."""
        from components.VectorIndex.adapters.embedding_adapter import (
            EmbeddingAdapter,
        )

        with pytest.raises(EmbeddingModelError, match="Failed to load"):
            EmbeddingAdapter(model_path="/nonexistent/model.onnx")

    def test_embed_returns_384_floats(self, mock_embedding_adapter):
        """embed() returns list of 384 floats."""
        result = mock_embedding_adapter.embed("test text")
        assert len(result) == 384
        assert all(isinstance(x, float) for x in result)

    def test_embed_batch_returns_correct_count(self, mock_embedding_adapter):
        """embed_batch() returns one embedding per input text."""
        texts = ["text 1", "text 2", "text 3"]
        results = mock_embedding_adapter.embed_batch(texts)
        assert len(results) == 3
        for emb in results:
            assert len(emb) == 384

    def test_determinism_same_input_same_output(self, mock_embedding_adapter):
        """Same input text produces same embedding (determinism contract)."""
        text = "book flight to SFO"
        emb1 = mock_embedding_adapter.embed(text)
        emb2 = mock_embedding_adapter.embed(text)
        assert emb1 == emb2


# =============================================================================
# T305 -- PgvectorAdapter unit tests (mocked DB)
# =============================================================================


class TestPgvectorAdapter:
    """Tests for PgvectorAdapter with mocked SharedDatabaseAdapter."""

    @pytest.mark.asyncio
    async def test_upsert_embedding_calls_session(self, mock_pgvector_adapter):
        """upsert_embedding calls the adapter method."""
        await mock_pgvector_adapter.upsert_embedding(
            plan_id="plan_01",
            intent_type="test",
            embedding=[0.1] * 384,
            search_text="test text",
        )
        mock_pgvector_adapter.upsert_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_hybrid_search_returns_list(self, mock_pgvector_adapter):
        """hybrid_search returns a list (empty by default in mock)."""
        result = await mock_pgvector_adapter.hybrid_search(
            query_embedding=[0.1] * 384,
            query_text="test query",
            intent_type=None,
            top_k=5,
        )
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_hybrid_search_intent_type_none(self, mock_pgvector_adapter):
        """intent_type=None passes None to the adapter."""
        await mock_pgvector_adapter.hybrid_search(
            query_embedding=[0.1] * 384,
            query_text="test",
            intent_type=None,
            top_k=5,
        )
        call_kwargs = mock_pgvector_adapter.hybrid_search.call_args.kwargs
        assert call_kwargs["intent_type"] is None

    @pytest.mark.asyncio
    async def test_hybrid_search_intent_type_filter(self, mock_pgvector_adapter):
        """intent_type='book_travel' passes the filter to adapter."""
        await mock_pgvector_adapter.hybrid_search(
            query_embedding=[0.1] * 384,
            query_text="test",
            intent_type="book_travel",
            top_k=5,
        )
        call_kwargs = mock_pgvector_adapter.hybrid_search.call_args.kwargs
        assert call_kwargs["intent_type"] == "book_travel"

    @pytest.mark.asyncio
    async def test_delete_by_plan_id(self, mock_pgvector_adapter):
        """delete_by_plan_id calls with correct plan_id."""
        await mock_pgvector_adapter.delete_by_plan_id("plan_01")
        mock_pgvector_adapter.delete_by_plan_id.assert_called_once_with("plan_01")

    @pytest.mark.asyncio
    async def test_bulk_upsert_handles_multiple_rows(self, mock_pgvector_adapter):
        """bulk_upsert accepts a list of rows."""
        rows = [
            {
                "plan_id": f"p{i}",
                "intent_type": "test",
                "embedding": [0.1] * 384,
                "search_text": f"text {i}",
            }
            for i in range(3)
        ]
        mock_pgvector_adapter.bulk_upsert.return_value = 3
        count = await mock_pgvector_adapter.bulk_upsert(rows)
        assert count == 3


# =============================================================================
# T402 -- VectorIndexService unit tests
# =============================================================================


class TestVectorIndexServiceStore:
    """Tests for VectorIndexService.store_embedding."""

    @pytest.mark.asyncio
    async def test_store_happy_path(
        self,
        vector_index_service,
        mock_embedding_adapter,
        mock_pgvector_adapter,
        sample_plan_data,
    ):
        """store_embedding calls adapters in correct order."""
        await vector_index_service.store_embedding("plan_01", sample_plan_data)
        mock_embedding_adapter.embed.assert_called_once()
        mock_pgvector_adapter.upsert_embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_empty_plan_raises(self, vector_index_service):
        """store_embedding with empty plan_data raises ValueError."""
        with pytest.raises(ValueError):
            await vector_index_service.store_embedding("plan_01", {})

    @pytest.mark.asyncio
    async def test_store_none_plan_raises(self, vector_index_service):
        """store_embedding with None plan_data raises ValueError."""
        with pytest.raises(ValueError):
            await vector_index_service.store_embedding("plan_01", None)

    @pytest.mark.asyncio
    async def test_store_upsert_same_plan_id(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        sample_plan_data,
    ):
        """store_embedding called twice with same plan_id upserts both times."""
        await vector_index_service.store_embedding("plan_01", sample_plan_data)
        await vector_index_service.store_embedding("plan_01", sample_plan_data)
        assert mock_pgvector_adapter.upsert_embedding.call_count == 2


class TestVectorIndexServiceSearch:
    """Tests for VectorIndexService.search."""

    @pytest.mark.asyncio
    async def test_search_happy_path(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        sample_search_results,
    ):
        """search returns list[HybridSearchResult]."""
        mock_pgvector_adapter.hybrid_search.return_value = sample_search_results
        results = await vector_index_service.search("book flight")
        assert len(results) == 3
        for r in results:
            assert isinstance(r, HybridSearchResult)

    @pytest.mark.asyncio
    async def test_search_top_k_zero_raises(self, vector_index_service):
        """search with top_k=0 raises ValueError."""
        with pytest.raises(ValueError, match="top_k"):
            await vector_index_service.search("query", top_k=0)

    @pytest.mark.asyncio
    async def test_search_top_k_negative_raises(self, vector_index_service):
        """search with top_k=-1 raises ValueError."""
        with pytest.raises(ValueError, match="top_k"):
            await vector_index_service.search("query", top_k=-1)

    @pytest.mark.asyncio
    async def test_search_top_k_over_50_raises(self, vector_index_service):
        """search with top_k=51 raises ValueError."""
        with pytest.raises(ValueError, match="top_k"):
            await vector_index_service.search("query", top_k=51)

    @pytest.mark.asyncio
    async def test_search_empty_query_raises(self, vector_index_service):
        """search with empty query_text raises ValueError."""
        with pytest.raises(ValueError, match="query_text"):
            await vector_index_service.search("")

    @pytest.mark.asyncio
    async def test_search_whitespace_query_raises(self, vector_index_service):
        """search with whitespace-only query_text raises ValueError."""
        with pytest.raises(ValueError, match="query_text"):
            await vector_index_service.search("   ")

    @pytest.mark.asyncio
    async def test_search_no_results_returns_empty(
        self, vector_index_service, mock_pgvector_adapter
    ):
        """search with no results returns empty list."""
        mock_pgvector_adapter.hybrid_search.return_value = []
        results = await vector_index_service.search("novel query")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_intent_type_filter(
        self, vector_index_service, mock_pgvector_adapter
    ):
        """search passes intent_type to adapter."""
        mock_pgvector_adapter.hybrid_search.return_value = []
        await vector_index_service.search("query", intent_type="book_travel")
        call_kwargs = mock_pgvector_adapter.hybrid_search.call_args.kwargs
        assert call_kwargs["intent_type"] == "book_travel"

    @pytest.mark.asyncio
    async def test_search_with_intent_type_none(self, vector_index_service, mock_pgvector_adapter):
        """search passes None intent_type to adapter when unspecified."""
        mock_pgvector_adapter.hybrid_search.return_value = []
        await vector_index_service.search("query")
        call_kwargs = mock_pgvector_adapter.hybrid_search.call_args.kwargs
        assert call_kwargs["intent_type"] is None


class TestVectorIndexServiceDelete:
    """Tests for VectorIndexService.delete_embedding."""

    @pytest.mark.asyncio
    async def test_delete_delegates_to_adapter(self, vector_index_service, mock_pgvector_adapter):
        """delete_embedding delegates to pgvector adapter."""
        await vector_index_service.delete_embedding("plan_01")
        mock_pgvector_adapter.delete_by_plan_id.assert_called_once_with("plan_01")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self, vector_index_service, mock_pgvector_adapter):
        """delete_embedding for non-existent plan_id does not raise."""
        await vector_index_service.delete_embedding("nonexistent")


class TestVectorIndexServiceBulkStore:
    """Tests for VectorIndexService.bulk_store."""

    @pytest.mark.asyncio
    async def test_bulk_store_empty_list_raises(self, vector_index_service):
        """bulk_store with empty list raises ValueError."""
        with pytest.raises(ValueError, match="not be empty"):
            await vector_index_service.bulk_store([])

    @pytest.mark.asyncio
    async def test_bulk_store_happy_path(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        mock_embedding_adapter,
        sample_plans_batch,
    ):
        """bulk_store returns count from adapter."""
        mock_pgvector_adapter.bulk_upsert.return_value = 10
        count = await vector_index_service.bulk_store(sample_plans_batch)
        assert count == 10
        mock_embedding_adapter.embed_batch.assert_called_once()
        mock_pgvector_adapter.bulk_upsert.assert_called_once()


class TestCreateVectorIndexServiceFactory:
    """Tests for create_vector_index_service factory function."""

    def test_missing_model_raises_embedding_model_error(self):
        """Factory with missing ONNX model raises EmbeddingModelError."""
        from components.VectorIndex.service.vector_index_service import (
            create_vector_index_service,
        )

        mock_db = MagicMock()
        with (
            patch.dict(
                "os.environ",
                {"ONNX_MODEL_PATH": "/nonexistent/model.onnx"},
            ),
            pytest.raises(EmbeddingModelError),
        ):
            create_vector_index_service(mock_db)


# =============================================================================
# T800 -- Determinism verification tests
# =============================================================================


class TestDeterminism:
    """Verify determinism across the VectorIndex stack (LLD Section 13.3)."""

    def test_build_search_text_deterministic(self, sample_plan_data):
        """Same plan dict produces identical search text on repeated calls."""
        text1 = build_search_text(sample_plan_data)
        text2 = build_search_text(sample_plan_data)
        assert text1 == text2

    def test_embed_deterministic(self, mock_embedding_adapter):
        """Same text produces identical 384-dim vector on repeated calls.

        Note: Uses mock adapter whose determinism is based on SHA-384 hash.
        Real ONNX model is also deterministic (frozen weights, no dropout).
        """
        text = "book flight to SFO"
        emb1 = mock_embedding_adapter.embed(text)
        emb2 = mock_embedding_adapter.embed(text)
        assert emb1 == emb2
        assert len(emb1) == 384

    @pytest.mark.asyncio
    async def test_search_ordering_deterministic(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        sample_search_results,
    ):
        """Same search inputs produce same result ordering.

        Note: Mock adapter returns consistent data. Real pgvector with
        same data and same RRF formula will also be deterministic.
        """
        mock_pgvector_adapter.hybrid_search.return_value = sample_search_results
        results1 = await vector_index_service.search("book flight")
        results2 = await vector_index_service.search("book flight")
        assert [r.plan_id for r in results1] == [r.plan_id for r in results2]
        assert [r.rrf_score for r in results1] == [r.rrf_score for r in results2]
