"""
VectorIndex Observability Tests (T601)

Verify structured logging fields and log safety (no embedding vectors
or raw plan content in logs).
"""

import logging

import pytest


class TestStoreEmbeddingLogging:
    """Verify store_embedding produces correct structured log events."""

    @pytest.mark.asyncio
    async def test_store_logs_embedding_stored(
        self,
        vector_index_service,
        sample_plan_data,
        caplog,
    ):
        """store_embedding logs 'embedding_stored' with expected fields."""
        with caplog.at_level(logging.INFO, logger="vectorindex"):
            await vector_index_service.store_embedding("plan_01HXYZ", sample_plan_data)

        # Find the embedding_stored log record
        stored_records = [r for r in caplog.records if r.message == "embedding_stored"]
        assert len(stored_records) == 1
        record = stored_records[0]
        assert record.plan_id == "plan_01HXYZ"
        assert record.intent_type == "book_travel"
        assert hasattr(record, "embedding_latency_ms")
        assert hasattr(record, "total_latency_ms")


class TestSearchLogging:
    """Verify search produces correct structured log events."""

    @pytest.mark.asyncio
    async def test_search_logs_hybrid_search(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        sample_search_results,
        caplog,
    ):
        """search logs 'hybrid_search' with expected fields."""
        mock_pgvector_adapter.hybrid_search.return_value = sample_search_results
        with caplog.at_level(logging.INFO, logger="vectorindex"):
            await vector_index_service.search("book flight", intent_type="book_travel", top_k=5)

        search_records = [r for r in caplog.records if r.message == "hybrid_search"]
        assert len(search_records) == 1
        record = search_records[0]
        assert record.intent_type == "book_travel"
        assert record.top_k == 5
        assert hasattr(record, "result_count")
        assert hasattr(record, "keyword_hits")
        assert hasattr(record, "semantic_hits")
        assert hasattr(record, "embedding_latency_ms")
        assert hasattr(record, "search_latency_ms")
        assert hasattr(record, "total_latency_ms")


class TestDeleteLogging:
    """Verify delete_embedding produces correct log event."""

    @pytest.mark.asyncio
    async def test_delete_logs_embedding_deleted(
        self,
        vector_index_service,
        caplog,
    ):
        """delete_embedding logs 'embedding_deleted' with plan_id."""
        with caplog.at_level(logging.INFO, logger="vectorindex"):
            await vector_index_service.delete_embedding("plan_01HXYZ")

        deleted_records = [r for r in caplog.records if r.message == "embedding_deleted"]
        assert len(deleted_records) == 1
        assert deleted_records[0].plan_id == "plan_01HXYZ"


class TestLogSafety:
    """Verify no embedding vectors or raw plan content appear in logs."""

    @pytest.mark.asyncio
    async def test_no_embedding_vector_in_store_logs(
        self,
        vector_index_service,
        sample_plan_data,
        caplog,
    ):
        """No 384-element list (embedding vector) in store log records."""
        with caplog.at_level(logging.DEBUG, logger="vectorindex"):
            await vector_index_service.store_embedding("plan_01", sample_plan_data)

        for record in caplog.records:
            log_text = str(record.__dict__)
            # A 384-element vector would contain many comma-separated floats
            # Check that no record contains a suspiciously long float list
            assert log_text.count("0.") < 50, "Possible embedding vector found in log record"

    @pytest.mark.asyncio
    async def test_no_raw_plan_data_in_store_logs(
        self,
        vector_index_service,
        sample_plan_data,
        caplog,
    ):
        """No raw plan_data dict appears in store log records."""
        with caplog.at_level(logging.DEBUG, logger="vectorindex"):
            await vector_index_service.store_embedding("plan_01", sample_plan_data)

        for record in caplog.records:
            log_text = str(record.__dict__)
            # The sample plan has 'search_flights' in its graph
            assert "search_flights" not in log_text, "Raw plan content found in log record"

    @pytest.mark.asyncio
    async def test_no_embedding_vector_in_search_logs(
        self,
        vector_index_service,
        mock_pgvector_adapter,
        caplog,
    ):
        """No embedding vector appears in search log records."""
        mock_pgvector_adapter.hybrid_search.return_value = []
        with caplog.at_level(logging.DEBUG, logger="vectorindex"):
            await vector_index_service.search("test query")

        for record in caplog.records:
            log_text = str(record.__dict__)
            assert log_text.count("0.") < 50, "Possible embedding vector found in search log record"
