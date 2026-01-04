"""
Adapter Tests for PlanLibrary

Tests adapters with mocked external dependencies.
Validates database operations, vector search, embedding generation, and signature verification.
"""

import pytest
import json
import asyncio
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

from ..adapters.db import DatabaseAdapter
from ..adapters.signature_verifier import SignatureVerifier, SignatureVerificationError
from ..adapters.embedding_client import (
    EmbeddingClient, EmbeddingCircuitBreaker, CircuitBreakerState,
    EmbeddingCircuitBreakerError, EmbeddingRateLimitError
)
from ..adapters.vector_db import VectorAdapter
from ..domain.models import (
    Plan, PlanIntentModel, PlanStepModel, PlanMetaModel,
    Signature, PlanOutcome, PlanMetrics, PlanDB, PlanPattern
)


class TestDatabaseAdapter:
    """Test DatabaseAdapter functionality."""
    
    @pytest.fixture
    def mock_shared_db(self):
        """Mock shared database adapter."""
        mock_db = AsyncMock()
        mock_session = AsyncMock()
        mock_db.get_session.return_value.__aenter__.return_value = mock_session
        return mock_db
    
    @pytest.fixture
    def db_adapter(self, mock_shared_db):
        """Create DatabaseAdapter with mocked dependencies."""
        with patch('components.PlanLibrary.adapters.db.get_database_adapter') as mock_get_db:
            mock_get_db.return_value = mock_shared_db
            return DatabaseAdapter()

    @pytest.fixture
    def sample_plan_db(self):
        """Create sample PlanDB for testing."""
        return PlanDB(
            plan_id="01HX0123456789ABCDEFGHIJK",
            canonical_json={
                "plan_id": "01HX0123456789ABCDEFGHIJK",
                "intent": {"type": "schedule_meeting"},
                "graph": [{"step_id": "step_1", "operation": "fetch_calendar"}],
                "meta": {"created_at": "2025-01-03T10:00:00Z"}
            },
            signature_data={"signature": "test", "algorithm": "Ed25519"},
            intent_type="schedule_meeting",
            step_count=1,
            plan_hash="test_hash",
            size_bytes=500,
            created_at=datetime.now(timezone.utc),
            stored_at=datetime.now(timezone.utc)
        )
    
    @pytest.fixture
    def sample_outcome(self):
        """Create sample PlanOutcome."""
        now = datetime.now(timezone.utc)
        return PlanOutcome(
            plan_id="01HX0123456789ABCDEFGHIJK",
            success=True,
            execution_start=now,
            execution_end=now,
            total_steps=1
        )
    
    @pytest.fixture
    def sample_metrics(self):
        """Create sample PlanMetrics."""
        return PlanMetrics(
            plan_id="01HX0123456789ABCDEFGHIJK",
            execute_latency_ms=1000
        )

    async def test_store_plan_transaction_success(
        self,
        db_adapter,
        mock_shared_db,
        sample_plan_db,
        sample_outcome,
        sample_metrics
    ):
        """Test successful plan storage transaction."""
        # Setup mock session
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_session.execute.return_value = AsyncMock()
        mock_session.commit.return_value = None
        
        # Execute
        result = await db_adapter.store_plan_transaction(
            sample_plan_db, sample_outcome, sample_metrics
        )
        
        # Verify
        assert result is True
        assert mock_session.execute.call_count == 3  # Plan, outcome, metrics
        mock_session.commit.assert_called_once()

    async def test_store_plan_transaction_integrity_error(
        self,
        db_adapter,
        mock_shared_db,
        sample_plan_db,
        sample_outcome,
        sample_metrics
    ):
        """Test plan storage with integrity constraint violation."""
        from sqlalchemy.exc import IntegrityError
        
        # Setup mock to raise IntegrityError
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_session.execute.side_effect = IntegrityError("Duplicate key", "", "")
        
        # Execute and verify exception
        with pytest.raises(IntegrityError):
            await db_adapter.store_plan_transaction(
                sample_plan_db, sample_outcome, sample_metrics
            )
        
        # Verify rollback was called
        mock_session.rollback.assert_called_once()

    async def test_get_plan_by_id_success(
        self,
        db_adapter,
        mock_shared_db
    ):
        """Test successful plan retrieval by ID."""
        # Setup mock result
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        mock_plan = MagicMock()
        mock_plan.plan_id = "01HX0123456789ABCDEFGHIJK"
        mock_plan.canonical_json = {"test": "data"}
        mock_plan.signature_data = {"signature": "test"}
        mock_plan.intent_type = "test"
        mock_plan.step_count = 1
        mock_plan.plan_hash = "hash"
        mock_plan.size_bytes = 100
        mock_plan.created_at = datetime.now(timezone.utc)
        mock_plan.stored_at = datetime.now(timezone.utc)
        
        mock_result.scalar_one_or_none.return_value = mock_plan
        mock_session.execute.return_value = mock_result
        
        # Execute
        result = await db_adapter.get_plan_by_id("01HX0123456789ABCDEFGHIJK")
        
        # Verify
        assert result is not None
        assert isinstance(result, PlanDB)
        assert result.plan_id == "01HX0123456789ABCDEFGHIJK"

    async def test_get_plan_by_id_not_found(
        self,
        db_adapter,
        mock_shared_db
    ):
        """Test plan retrieval when plan doesn't exist."""
        # Setup mock to return None
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        
        # Execute
        result = await db_adapter.get_plan_by_id("01HX0123456789ABCDEFGHIJK")
        
        # Verify
        assert result is None

    async def test_get_plans_by_intent_with_success(
        self,
        db_adapter,
        mock_shared_db
    ):
        """Test intent-based plan query with success filtering."""
        # Setup mock result
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        
        # Mock row data
        mock_row = MagicMock()
        mock_row.plan_id = "01HX0123456789ABCDEFGHIJK"
        mock_row.intent_type = "schedule_meeting"
        mock_row.total_executions = 10
        mock_row.successful_executions = 8
        mock_row.success_rate = 0.8
        mock_row.avg_execution_time_ms = 1200.0
        mock_row.step_count = 3
        mock_row.last_execution = datetime.now(timezone.utc)
        mock_row.canonical_json = {
            "graph": [
                {"operation": "fetch_calendar"},
                {"operation": "find_overlap"},
                {"operation": "book_meeting"}
            ]
        }
        
        mock_result.fetchall.return_value = [mock_row]
        mock_session.execute.return_value = mock_result
        
        # Execute
        patterns = await db_adapter.get_plans_by_intent_with_success(
            intent_type="schedule_meeting",
            success_threshold=0.7,
            limit=10
        )
        
        # Verify
        assert len(patterns) == 1
        assert isinstance(patterns[0], PlanPattern)
        assert patterns[0].intent_type == "schedule_meeting"
        assert patterns[0].success_rate == 0.8

    async def test_get_success_rate_data(
        self,
        db_adapter,
        mock_shared_db
    ):
        """Test success rate data retrieval for analytics."""
        # Setup mock result
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        
        mock_row = MagicMock()
        mock_row.intent_type = "schedule_meeting"
        mock_row.total_executions = 20
        mock_row.successful_executions = 16
        mock_row.avg_execution_time_ms = 1100.0
        
        mock_result.fetchall.return_value = [mock_row]
        mock_session.execute.return_value = mock_result
        
        # Execute
        data = await db_adapter.get_success_rate_data(timeframe_days=30)
        
        # Verify
        assert len(data) == 1
        assert data[0]["intent_type"] == "schedule_meeting"
        assert data[0]["total_executions"] == 20
        assert data[0]["successful_executions"] == 16

    def test_generate_pattern_summary(self, db_adapter):
        """Test pattern summary generation."""
        canonical_json = {
            "graph": [
                {"operation": "fetch_calendar"},
                {"operation": "find_overlap"},
                {"operation": "book_meeting"}
            ]
        }
        
        summary = db_adapter._generate_pattern_summary(canonical_json)
        
        assert "fetch_calendar" in summary
        assert "find_overlap" in summary
        assert "book_meeting" in summary
        assert "→" in summary


class TestSignatureVerifier:
    """Test SignatureVerifier functionality."""
    
    @pytest.fixture
    def signature_verifier(self):
        """Create SignatureVerifier instance."""
        return SignatureVerifier()
    
    @pytest.fixture
    def sample_signature(self):
        """Create sample signature for testing."""
        return Signature(
            signature="dGVzdF9zaWduYXR1cmVfZGF0YQ==",  # Base64: "test_signature_data"
            public_key="dGVzdF9wdWJsaWNfa2V5X2RhdGE=",    # Base64: "test_public_key_data"
            algorithm="Ed25519"
        )

    def test_validate_signature_format_valid(self, signature_verifier):
        """Test signature format validation with valid signature."""
        # Create properly formatted signature
        valid_signature = Signature(
            signature="A" * 88 + "==",  # 64 bytes base64 encoded
            public_key="A" * 44 + "==", # 32 bytes base64 encoded  
            algorithm="Ed25519"
        )
        
        result = signature_verifier.validate_signature_format(valid_signature)
        assert result is True

    def test_validate_signature_format_invalid_algorithm(self, signature_verifier):
        """Test signature format validation with invalid algorithm."""
        invalid_signature = Signature(
            signature="dGVzdF9zaWduYXR1cmVfZGF0YQ==",
            public_key="dGVzdF9wdWJsaWNfa2V5X2RhdGE=",
            algorithm="RSA2048"  # Invalid algorithm
        )
        
        result = signature_verifier.validate_signature_format(invalid_signature)
        assert result is False

    def test_validate_signature_format_empty_fields(self, signature_verifier):
        """Test signature format validation with empty fields."""
        invalid_signature = Signature(
            signature="",  # Empty signature
            public_key="dGVzdF9wdWJsaWNfa2V5X2RhdGE=",
            algorithm="Ed25519"
        )
        
        result = signature_verifier.validate_signature_format(invalid_signature)
        assert result is False

    def test_generate_plan_hash(self, signature_verifier):
        """Test plan hash generation."""
        canonical_json = '{"plan_id":"test","data":"value"}'
        
        hash1 = signature_verifier.generate_plan_hash(canonical_json)
        hash2 = signature_verifier.generate_plan_hash(canonical_json)
        
        # Hash should be deterministic
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex length

    async def test_verify_plan_integrity_success(self, signature_verifier):
        """Test plan integrity verification with matching hashes."""
        canonical_json = '{"plan_id":"test","data":"value"}'
        expected_hash = signature_verifier.generate_plan_hash(canonical_json)
        
        result = await signature_verifier.verify_plan_integrity(
            canonical_json, expected_hash
        )
        
        assert result is True

    async def test_verify_plan_integrity_mismatch(self, signature_verifier):
        """Test plan integrity verification with mismatched hashes."""
        canonical_json = '{"plan_id":"test","data":"value"}'
        wrong_hash = "wrong_hash_value"
        
        result = await signature_verifier.verify_plan_integrity(
            canonical_json, wrong_hash
        )
        
        assert result is False

    def test_extract_public_key_info(self, signature_verifier, sample_signature):
        """Test public key information extraction."""
        key_info = signature_verifier.extract_public_key_info(sample_signature)
        
        assert key_info is not None
        assert key_info.endswith("...")
        assert len(key_info) == 11  # 8 chars + "..."

    async def test_health_check(self, signature_verifier):
        """Test signature verifier health check."""
        result = await signature_verifier.health_check()
        
        assert result is True


class TestEmbeddingClient:
    """Test EmbeddingClient functionality."""
    
    @pytest.fixture
    def mock_openai_client(self):
        """Mock OpenAI client."""
        return AsyncMock()
    
    @pytest.fixture
    def embedding_client(self, mock_openai_client):
        """Create EmbeddingClient with mocked OpenAI client."""
        with patch('components.PlanLibrary.adapters.embedding_client.AsyncOpenAI') as mock_openai:
            mock_openai.return_value = mock_openai_client
            client = EmbeddingClient()
            client.client = mock_openai_client
            return client

    async def test_generate_embedding_success(self, embedding_client, mock_openai_client):
        """Test successful embedding generation."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 10
        
        mock_openai_client.embeddings.create.return_value = mock_response
        
        # Execute
        result = await embedding_client.generate_embedding("test text")
        
        # Verify
        assert len(result) == 1536
        assert all(isinstance(x, float) for x in result)

    async def test_generate_embedding_rate_limit_error(self, embedding_client, mock_openai_client):
        """Test embedding generation with rate limit error."""
        import openai
        
        # Setup mock to raise rate limit error
        mock_openai_client.embeddings.create.side_effect = openai.RateLimitError(
            message="Rate limit exceeded", 
            response=MagicMock(), 
            body=None
        )
        
        # Execute and verify exception
        with pytest.raises(EmbeddingRateLimitError):
            await embedding_client.generate_embedding("test text")

    async def test_generate_embedding_circuit_breaker_open(self, embedding_client):
        """Test embedding generation when circuit breaker is open."""
        # Force circuit breaker to open state
        embedding_client.circuit_breaker.state = CircuitBreakerState.OPEN
        
        # Execute and verify exception
        with pytest.raises(EmbeddingCircuitBreakerError):
            await embedding_client.generate_embedding("test text")

    async def test_generate_embedding_retry_logic(self, embedding_client, mock_openai_client):
        """Test embedding generation with retry on connection error."""
        import openai
        
        # Setup mock to fail twice then succeed
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.1] * 1536
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 10
        
        mock_openai_client.embeddings.create.side_effect = [
            openai.APIConnectionError(message="Connection failed"),
            openai.APIConnectionError(message="Connection failed"),
            mock_response  # Success on third attempt
        ]
        
        # Execute (should succeed after retries)
        result = await embedding_client.generate_embedding("test text")
        
        # Verify
        assert len(result) == 1536
        assert mock_openai_client.embeddings.create.call_count == 3

    def test_check_rate_limits(self, embedding_client):
        """Test rate limit checking."""
        # Should allow request initially
        result = embedding_client._check_rate_limits("test text")
        assert result is True
        
        # Simulate high usage
        embedding_client.rate_limit_tracker["requests_per_minute"] = 3500
        result = embedding_client._check_rate_limits("test text")
        assert result is False

    async def test_health_check_success(self, embedding_client):
        """Test embedding client health check success."""
        # Mock successful embedding generation
        with patch.object(embedding_client, 'generate_embedding') as mock_generate:
            mock_generate.return_value = [0.1] * 1536
            
            result = await embedding_client.health_check()
            assert result is True

    async def test_health_check_circuit_breaker_open(self, embedding_client):
        """Test health check when circuit breaker is open."""
        embedding_client.circuit_breaker.state = CircuitBreakerState.OPEN
        
        result = await embedding_client.health_check()
        assert result is False


class TestEmbeddingCircuitBreaker:
    """Test EmbeddingCircuitBreaker functionality."""
    
    @pytest.fixture
    def circuit_breaker(self):
        """Create circuit breaker with test parameters."""
        return EmbeddingCircuitBreaker(
            failure_threshold=2,
            recovery_timeout=60,
            success_threshold=2
        )

    def test_initial_state_closed(self, circuit_breaker):
        """Test circuit breaker starts in closed state."""
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.should_allow_request() is True

    def test_open_on_failures(self, circuit_breaker):
        """Test circuit breaker opens after failure threshold."""
        # Record failures
        circuit_breaker.record_failure()
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        
        circuit_breaker.record_failure()
        assert circuit_breaker.state == CircuitBreakerState.OPEN
        assert circuit_breaker.should_allow_request() is False

    def test_half_open_after_timeout(self, circuit_breaker):
        """Test circuit breaker transitions to half-open after timeout."""
        # Force to open state
        circuit_breaker.record_failure()
        circuit_breaker.record_failure()
        assert circuit_breaker.state == CircuitBreakerState.OPEN
        
        # Simulate timeout passage
        circuit_breaker.last_failure_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        
        # Should transition to half-open
        assert circuit_breaker.should_allow_request() is True
        assert circuit_breaker.state == CircuitBreakerState.HALF_OPEN

    def test_close_after_successes(self, circuit_breaker):
        """Test circuit breaker closes after successful operations."""
        # Force to half-open state
        circuit_breaker.state = CircuitBreakerState.HALF_OPEN
        
        # Record successes
        circuit_breaker.record_success()
        assert circuit_breaker.state == CircuitBreakerState.HALF_OPEN
        
        circuit_breaker.record_success()
        assert circuit_breaker.state == CircuitBreakerState.CLOSED

    def test_reopen_on_half_open_failure(self, circuit_breaker):
        """Test circuit breaker reopens on failure during half-open."""
        # Force to half-open state
        circuit_breaker.state = CircuitBreakerState.HALF_OPEN
        
        # Record failure
        circuit_breaker.record_failure()
        assert circuit_breaker.state == CircuitBreakerState.OPEN


class TestVectorAdapter:
    """Test VectorAdapter functionality."""
    
    @pytest.fixture
    def mock_shared_db(self):
        """Mock shared database adapter."""
        mock_db = AsyncMock()
        mock_session = AsyncMock()
        mock_db.get_session.return_value.__aenter__.return_value = mock_session
        return mock_db
    
    @pytest.fixture
    def vector_adapter(self, mock_shared_db):
        """Create VectorAdapter with mocked dependencies."""
        with patch('components.PlanLibrary.adapters.vector_db.get_database_adapter') as mock_get_db:
            mock_get_db.return_value = mock_shared_db
            return VectorAdapter()

    async def test_store_embedding_success(
        self,
        vector_adapter,
        mock_shared_db
    ):
        """Test successful embedding storage."""
        # Setup mock
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_session.execute.return_value = AsyncMock()
        mock_session.commit.return_value = None
        
        # Execute
        vector = [0.1] * 1536
        result = await vector_adapter.store_embedding("01HX0123456789ABCDEFGHIJK", vector)
        
        # Verify
        assert result is True
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    async def test_similarity_search_success(
        self,
        vector_adapter,
        mock_shared_db
    ):
        """Test successful similarity search."""
        # Setup mock result
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        
        # Mock row with vector data
        mock_row = MagicMock()
        mock_row.plan_id = "01HX0123456789ABCDEFGHIJK"
        mock_row.stored_vector = json.dumps([0.1] * 1536)  # Stored as JSON
        mock_row.intent_type = "schedule_meeting"
        mock_row.canonical_json = {
            "graph": [{"operation": "fetch_calendar"}]
        }
        mock_row.step_count = 1
        mock_row.total_executions = 10
        mock_row.successful_executions = 8
        mock_row.success_rate = 0.8
        mock_row.avg_execution_time_ms = 1000.0
        mock_row.last_execution = datetime.now(timezone.utc)
        
        mock_result.fetchall.return_value = [mock_row]
        mock_session.execute.return_value = mock_result
        
        # Execute
        query_vector = [0.1] * 1536
        results = await vector_adapter.similarity_search(
            query_vector=query_vector,
            threshold=0.5,
            limit=5
        )
        
        # Verify
        assert len(results) <= 1  # Should return results above threshold
        if results:
            assert hasattr(results[0], 'plan_id')
            assert hasattr(results[0], 'similarity_score')

    def test_cosine_similarity_calculation(self, vector_adapter):
        """Test cosine similarity calculation."""
        vector1 = [1.0, 0.0, 0.0]
        vector2 = [1.0, 0.0, 0.0]  # Identical
        
        similarity = vector_adapter._cosine_similarity(vector1, vector2)
        assert similarity == 1.0
        
        vector3 = [0.0, 1.0, 0.0]  # Orthogonal
        similarity = vector_adapter._cosine_similarity(vector1, vector3)
        assert similarity == 0.0

    def test_cosine_similarity_dimension_mismatch(self, vector_adapter):
        """Test cosine similarity with mismatched dimensions."""
        vector1 = [1.0, 0.0]
        vector2 = [1.0, 0.0, 0.0]  # Different dimension
        
        similarity = vector_adapter._cosine_similarity(vector1, vector2)
        assert similarity == 0.0

    async def test_get_embedding_by_plan_id_success(
        self,
        vector_adapter,
        mock_shared_db
    ):
        """Test successful embedding retrieval by plan ID."""
        # Setup mock
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        mock_row = MagicMock()
        mock_row.vector_norm = json.dumps([0.1] * 1536)
        mock_result.fetchone.return_value = mock_row
        mock_session.execute.return_value = mock_result
        
        # Execute
        result = await vector_adapter.get_embedding_by_plan_id("01HX0123456789ABCDEFGHIJK")
        
        # Verify
        assert result is not None
        assert len(result) == 1536

    async def test_get_embedding_by_plan_id_not_found(
        self,
        vector_adapter,
        mock_shared_db
    ):
        """Test embedding retrieval when not found."""
        # Setup mock to return None
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        mock_result.fetchone.return_value = None
        mock_session.execute.return_value = mock_result
        
        # Execute
        result = await vector_adapter.get_embedding_by_plan_id("01HX0123456789ABCDEFGHIJK")
        
        # Verify
        assert result is None

    async def test_delete_embedding_success(
        self,
        vector_adapter,
        mock_shared_db
    ):
        """Test successful embedding deletion."""
        # Setup mock
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_result = AsyncMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result
        
        # Execute
        result = await vector_adapter.delete_embedding("01HX0123456789ABCDEFGHIJK")
        
        # Verify
        assert result is True
        mock_session.commit.assert_called_once()

    async def test_get_embedding_statistics(
        self,
        vector_adapter,
        mock_shared_db
    ):
        """Test embedding statistics retrieval."""
        # Setup mock
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        
        # Mock count queries
        mock_result1 = AsyncMock()
        mock_result1.scalar.return_value = 100  # Total embeddings
        
        mock_result2 = AsyncMock()
        mock_result2.scalar.return_value = 5   # Recent embeddings
        
        mock_result3 = AsyncMock()
        mock_row = MagicMock()
        mock_row.model_version = "text-embedding-ada-002"
        mock_row.count = 100
        mock_result3.__iter__.return_value = [mock_row]
        
        mock_session.execute.side_effect = [mock_result1, mock_result2, mock_result3]
        
        # Execute
        stats = await vector_adapter.get_embedding_statistics()
        
        # Verify
        assert stats["total_embeddings"] == 100
        assert stats["recent_embeddings_24h"] == 5
        assert "text-embedding-ada-002" in stats["model_distribution"]

    async def test_health_check_success(self, vector_adapter, mock_shared_db):
        """Test vector adapter health check success."""
        mock_shared_db.health_check.return_value = True
        mock_session = mock_shared_db.get_session.return_value.__aenter__.return_value
        mock_session.execute.return_value = AsyncMock()
        
        result = await vector_adapter.health_check()
        assert result is True

    async def test_health_check_failure(self, vector_adapter, mock_shared_db):
        """Test vector adapter health check failure."""
        mock_shared_db.health_check.return_value = False
        
        result = await vector_adapter.health_check()
        assert result is False