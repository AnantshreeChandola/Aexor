"""
Service Layer Tests for PlanLibrary

Tests plan storage, retrieval, and analytics services with mocked adapters.
Validates business logic and error handling.
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from ..service.plan_service import (
    PlanService, InvalidSignatureError, DuplicatePlanError, 
    PlanTooLargeError, PlanNotFoundError, PlanLibraryError
)
from ..service.vector_service import VectorService, EmbeddingServiceError
from ..service.analytics_service import AnalyticsService
from ..domain.models import (
    Plan, PlanIntentModel, PlanStepModel, PlanMetaModel,
    Signature, PlanOutcome, PlanMetrics, PlanPattern,
    StorePlanResponse, SimilarityMatch, PlanDB
)


class TestPlanService:
    """Test PlanService business logic."""
    
    @pytest.fixture
    def mock_db_adapter(self):
        """Mock database adapter."""
        return AsyncMock()
    
    @pytest.fixture
    def mock_signature_verifier(self):
        """Mock signature verifier."""
        return AsyncMock()
    
    @pytest.fixture
    def mock_vector_service(self):
        """Mock vector service."""
        return AsyncMock()
    
    @pytest.fixture
    def plan_service(self, mock_db_adapter, mock_signature_verifier, mock_vector_service):
        """Create PlanService with mocked dependencies."""
        return PlanService(
            db_adapter=mock_db_adapter,
            signature_verifier=mock_signature_verifier,
            vector_service=mock_vector_service
        )
    
    @pytest.fixture
    def sample_plan(self):
        """Create sample plan for testing."""
        return Plan(
            plan_id="01HX0123456789ABCDEFGHIJK",
            intent=PlanIntentModel(
                type="schedule_meeting",
                description="Schedule a meeting with team",
                parameters={"duration": 30}
            ),
            graph=[
                PlanStepModel(
                    step_id="step_1",
                    operation="fetch_calendar",
                    inputs={"user_id": "123"},
                    outputs={}
                ),
                PlanStepModel(
                    step_id="step_2", 
                    operation="find_overlap",
                    inputs={"calendars": []},
                    outputs={}
                )
            ],
            constraints={"max_duration": 60},
            meta=PlanMetaModel(
                created_at=datetime.now(timezone.utc),
                version="1.0"
            )
        )
    
    @pytest.fixture
    def sample_signature(self):
        """Create sample signature for testing."""
        return Signature(
            signature="base64encodedSignature==",
            public_key="base64encodedPublicKey==",
            algorithm="Ed25519",
            signed_at=datetime.now(timezone.utc)
        )
    
    @pytest.fixture
    def sample_outcome(self, sample_plan):
        """Create sample plan outcome."""
        now = datetime.now(timezone.utc)
        return PlanOutcome(
            plan_id=sample_plan.plan_id,
            success=True,
            execution_start=now - timedelta(minutes=2),
            execution_end=now,
            total_steps=2
        )
    
    @pytest.fixture
    def sample_metrics(self, sample_plan):
        """Create sample plan metrics."""
        return PlanMetrics(
            plan_id=sample_plan.plan_id,
            execute_latency_ms=1200,
            preview_latency_ms=150
        )

    async def test_store_plan_success(
        self, 
        plan_service, 
        mock_db_adapter, 
        mock_signature_verifier,
        mock_vector_service,
        sample_plan, 
        sample_signature, 
        sample_outcome, 
        sample_metrics
    ):
        """Test successful plan storage."""
        # Setup mocks
        mock_signature_verifier.verify_signature.return_value = True
        mock_db_adapter.get_plan_by_id.return_value = None  # Plan doesn't exist
        mock_db_adapter.store_plan_transaction.return_value = True
        mock_vector_service.queue_embedding_generation.return_value = True
        
        # Execute
        result = await plan_service.store_plan(
            plan=sample_plan,
            signature=sample_signature,
            outcome=sample_outcome,
            metrics=sample_metrics
        )
        
        # Verify
        assert isinstance(result, StorePlanResponse)
        assert result.plan_id == sample_plan.plan_id
        assert result.embedding_queued is True
        
        # Verify calls
        mock_signature_verifier.verify_signature.assert_called_once()
        mock_db_adapter.get_plan_by_id.assert_called_once_with(sample_plan.plan_id)
        mock_db_adapter.store_plan_transaction.assert_called_once()
        mock_vector_service.queue_embedding_generation.assert_called_once()

    async def test_store_plan_invalid_signature(
        self,
        plan_service,
        mock_signature_verifier,
        sample_plan,
        sample_signature,
        sample_outcome,
        sample_metrics
    ):
        """Test plan storage with invalid signature."""
        # Setup mock
        mock_signature_verifier.verify_signature.return_value = False
        
        # Execute and verify exception
        with pytest.raises(InvalidSignatureError) as exc_info:
            await plan_service.store_plan(
                plan=sample_plan,
                signature=sample_signature,
                outcome=sample_outcome,
                metrics=sample_metrics
            )
        
        assert exc_info.value.plan_id == sample_plan.plan_id

    async def test_store_plan_duplicate_id(
        self,
        plan_service,
        mock_db_adapter,
        mock_signature_verifier,
        sample_plan,
        sample_signature,
        sample_outcome,
        sample_metrics
    ):
        """Test plan storage with duplicate plan ID."""
        # Setup mocks
        mock_signature_verifier.verify_signature.return_value = True
        mock_db_adapter.get_plan_by_id.return_value = PlanDB(
            plan_id=sample_plan.plan_id,
            canonical_json=sample_plan.model_dump(),
            signature_data=sample_signature.model_dump(),
            intent_type=sample_plan.intent.type,
            step_count=len(sample_plan.graph),
            plan_hash="existing_hash",
            size_bytes=1000,
            created_at=sample_plan.meta.created_at,
            stored_at=datetime.now(timezone.utc)
        )
        
        # Execute and verify exception
        with pytest.raises(DuplicatePlanError) as exc_info:
            await plan_service.store_plan(
                plan=sample_plan,
                signature=sample_signature,
                outcome=sample_outcome,
                metrics=sample_metrics
            )
        
        assert exc_info.value.plan_id == sample_plan.plan_id

    async def test_store_plan_too_large(
        self,
        plan_service,
        sample_signature,
        sample_outcome
    ):
        """Test plan storage with oversized plan."""
        # Create a large plan
        large_steps = [
            PlanStepModel(
                step_id=f"step_{i}",
                operation=f"operation_{i}",
                inputs={"large_data": "x" * 10000}  # Large input data
            )
            for i in range(50)  # Many steps with large data
        ]
        
        large_plan = Plan(
            plan_id="01HX0123456789ABCDEFGHIJK",
            intent=PlanIntentModel(type="large_plan"),
            graph=large_steps,
            meta=PlanMetaModel(created_at=datetime.now(timezone.utc))
        )
        
        large_metrics = PlanMetrics(
            plan_id=large_plan.plan_id,
            execute_latency_ms=5000
        )
        
        # Execute and verify exception
        with pytest.raises(PlanTooLargeError):
            await plan_service.store_plan(
                plan=large_plan,
                signature=sample_signature,
                outcome=sample_outcome,
                metrics=large_metrics
            )

    async def test_get_plans_by_intent_success(
        self,
        plan_service,
        mock_db_adapter
    ):
        """Test successful intent-based plan query."""
        # Setup mock response
        mock_patterns = [
            PlanPattern(
                plan_id="01HX0123456789ABCDEFGHIJK",
                intent_type="schedule_meeting",
                success_rate=0.85,
                avg_execution_time_ms=1200.0,
                steps_count=3,
                pattern_summary="Fetch calendar → Find overlap → Book",
                total_executions=20,
                last_execution=datetime.now(timezone.utc),
                confidence=0.9
            )
        ]
        
        mock_db_adapter.get_plans_by_intent_with_success.return_value = mock_patterns
        
        # Execute
        result = await plan_service.get_plans_by_intent(
            intent_type="schedule_meeting",
            success_threshold=0.8,
            limit=10
        )
        
        # Verify
        assert len(result) == 1
        assert result[0].intent_type == "schedule_meeting"
        assert result[0].success_rate == 0.85
        
        # Verify database call
        mock_db_adapter.get_plans_by_intent_with_success.assert_called_once_with(
            intent_type="schedule_meeting",
            success_threshold=0.8,
            limit=10,
            recency_days=None
        )

    async def test_get_plans_by_intent_invalid_parameters(self, plan_service):
        """Test intent query with invalid parameters."""
        # Empty intent type
        with pytest.raises(ValueError, match="intent_type cannot be empty"):
            await plan_service.get_plans_by_intent(intent_type="")
        
        # Invalid success threshold
        with pytest.raises(ValueError, match="success_threshold must be between"):
            await plan_service.get_plans_by_intent(
                intent_type="test",
                success_threshold=1.5
            )
        
        # Invalid limit
        with pytest.raises(ValueError, match="limit must be between"):
            await plan_service.get_plans_by_intent(
                intent_type="test",
                limit=0
            )

    async def test_get_plan_by_id_success(
        self,
        plan_service,
        mock_db_adapter,
        sample_plan
    ):
        """Test successful plan retrieval by ID."""
        # Setup mock
        mock_plan_db = PlanDB(
            plan_id=sample_plan.plan_id,
            canonical_json=sample_plan.model_dump(),
            signature_data={},
            intent_type=sample_plan.intent.type,
            step_count=len(sample_plan.graph),
            plan_hash="test_hash",
            size_bytes=1000,
            created_at=sample_plan.meta.created_at,
            stored_at=datetime.now(timezone.utc)
        )
        
        mock_db_adapter.get_plan_by_id.return_value = mock_plan_db
        
        # Execute
        result = await plan_service.get_plan_by_id(sample_plan.plan_id)
        
        # Verify
        assert result is not None
        assert result.plan_id == sample_plan.plan_id
        assert result.intent.type == sample_plan.intent.type

    async def test_get_plan_by_id_not_found(
        self,
        plan_service,
        mock_db_adapter
    ):
        """Test plan retrieval when plan doesn't exist."""
        # Setup mock
        mock_db_adapter.get_plan_by_id.return_value = None
        
        # Execute
        result = await plan_service.get_plan_by_id("01HX0123456789ABCDEFGHIJK")
        
        # Verify
        assert result is None

    async def test_get_plan_by_id_invalid_format(self, plan_service):
        """Test plan retrieval with invalid plan ID format."""
        with pytest.raises(ValueError, match="plan_id must be a 26-character ULID"):
            await plan_service.get_plan_by_id("invalid_id")

    async def test_extract_plan_text(self, plan_service, sample_plan):
        """Test plan text extraction for embedding generation."""
        plan_text = plan_service._extract_plan_text(sample_plan)
        
        # Verify text contains key elements
        assert "schedule_meeting" in plan_text
        assert "fetch_calendar" in plan_text
        assert "find_overlap" in plan_text
        assert "Steps: 2" in plan_text

    async def test_health_check(
        self,
        plan_service,
        mock_db_adapter,
        mock_vector_service
    ):
        """Test plan service health check."""
        # Setup mocks
        mock_db_adapter.health_check.return_value = True
        mock_vector_service.health_check.return_value = True
        
        # Execute
        health = await plan_service.health_check()
        
        # Verify
        assert health["service"] == "PlanService"
        assert health["status"] == "healthy"
        assert health["dependencies"]["database"] == "healthy"
        assert health["dependencies"]["vector_service"] == "healthy"


class TestVectorService:
    """Test VectorService functionality."""
    
    @pytest.fixture
    def mock_embedding_client(self):
        """Mock embedding client."""
        return AsyncMock()
    
    @pytest.fixture
    def mock_vector_adapter(self):
        """Mock vector adapter."""
        return AsyncMock()
    
    @pytest.fixture
    def vector_service(self, mock_embedding_client, mock_vector_adapter):
        """Create VectorService with mocked dependencies."""
        with patch('asyncio.create_task') as mock_task:
            service = VectorService(
                embedding_client=mock_embedding_client,
                vector_adapter=mock_vector_adapter
            )
            # Stop background task for testing
            if hasattr(service, '_queue_task') and service._queue_task:
                service._queue_task.cancel()
            return service

    async def test_similarity_search_success(
        self,
        vector_service,
        mock_embedding_client,
        mock_vector_adapter
    ):
        """Test successful similarity search."""
        # Setup mocks
        query_vector = [0.1] * 1536
        mock_embedding_client.generate_embedding.return_value = query_vector
        
        mock_similarity_result = AsyncMock()
        mock_similarity_result.plan_id = "01HX0123456789ABCDEFGHIJK"
        mock_similarity_result.intent_type = "schedule_meeting"
        mock_similarity_result.similarity_score = 0.82
        mock_similarity_result.success_rate = 0.9
        mock_similarity_result.total_executions = 15
        mock_similarity_result.plan_pattern = PlanPattern(
            plan_id="01HX0123456789ABCDEFGHIJK",
            intent_type="schedule_meeting",
            success_rate=0.9,
            avg_execution_time_ms=1000.0,
            steps_count=3,
            pattern_summary="Test pattern",
            total_executions=15,
            last_execution=datetime.now(timezone.utc),
            confidence=0.85
        )
        
        mock_vector_adapter.similarity_search.return_value = [mock_similarity_result]
        
        # Execute
        results = await vector_service.similarity_search(
            query_text="book a meeting with the team",
            similarity_threshold=0.7,
            limit=5
        )
        
        # Verify
        assert len(results) == 1
        assert isinstance(results[0], SimilarityMatch)
        assert results[0].plan_id == "01HX0123456789ABCDEFGHIJK"
        assert results[0].similarity_score == 0.82

    async def test_similarity_search_invalid_parameters(self, vector_service):
        """Test similarity search with invalid parameters."""
        # Empty query text
        with pytest.raises(ValueError, match="query_text cannot be empty"):
            await vector_service.similarity_search(query_text="")
        
        # Invalid similarity threshold
        with pytest.raises(ValueError, match="similarity_threshold must be between"):
            await vector_service.similarity_search(
                query_text="test",
                similarity_threshold=1.5
            )
        
        # Invalid limit
        with pytest.raises(ValueError, match="limit must be between"):
            await vector_service.similarity_search(
                query_text="test",
                limit=0
            )

    async def test_queue_embedding_generation(self, vector_service):
        """Test embedding generation queueing."""
        # Execute
        success = await vector_service.queue_embedding_generation(
            plan_id="01HX0123456789ABCDEFGHIJK",
            plan_text="test plan text"
        )
        
        # Verify
        assert success is True
        assert vector_service.embedding_queue.qsize() == 1

    def test_calculate_relevance_score(self, vector_service):
        """Test relevance score calculation."""
        score = vector_service._calculate_relevance_score(
            similarity_score=0.8,
            success_rate=0.9,
            total_executions=10
        )
        
        # Verify score is reasonable
        assert 0.0 <= score <= 1.0
        assert score > 0.7  # Should be high given good inputs


class TestAnalyticsService:
    """Test AnalyticsService functionality."""
    
    @pytest.fixture
    def mock_db_adapter(self):
        """Mock database adapter."""
        return AsyncMock()
    
    @pytest.fixture
    def analytics_service(self, mock_db_adapter):
        """Create AnalyticsService with mocked dependencies."""
        return AnalyticsService(db_adapter=mock_db_adapter)

    async def test_calculate_success_rates(
        self,
        analytics_service,
        mock_db_adapter
    ):
        """Test success rate calculation."""
        # Setup mock data
        mock_data = [
            {
                "intent_type": "schedule_meeting",
                "total_executions": 20,
                "successful_executions": 17,
                "avg_execution_time_ms": 1200.0
            },
            {
                "intent_type": "book_restaurant",
                "total_executions": 15,
                "successful_executions": 12,
                "avg_execution_time_ms": 800.0
            }
        ]
        
        mock_db_adapter.get_success_rate_data.return_value = mock_data
        
        # Execute
        analytics = await analytics_service.calculate_success_rates(timeframe_days=30)
        
        # Verify
        assert len(analytics) == 2
        assert "schedule_meeting" in analytics
        assert analytics["schedule_meeting"].success_rate == 0.85  # 17/20
        assert analytics["schedule_meeting"].confidence_level == "high"  # >20 executions
        
        assert "book_restaurant" in analytics
        assert analytics["book_restaurant"].success_rate == 0.8  # 12/15
        assert analytics["book_restaurant"].confidence_level == "medium"  # 10-20 executions

    async def test_get_performance_trends(
        self,
        analytics_service,
        mock_db_adapter
    ):
        """Test performance trend analysis."""
        # Setup mock data for current and previous periods
        current_metrics = {
            "total_executions": 50,
            "success_rate": 0.9,
            "avg_execution_time_ms": 1000.0,
            "p95_execution_time_ms": 2000.0
        }
        
        previous_metrics = {
            "total_executions": 40,
            "success_rate": 0.8,
            "avg_execution_time_ms": 1200.0,
            "p95_execution_time_ms": 2500.0
        }
        
        mock_db_adapter.get_performance_metrics.side_effect = [current_metrics, previous_metrics]
        
        # Execute
        trends = await analytics_service.get_performance_trends(
            intent_type="schedule_meeting",
            trend_days=30
        )
        
        # Verify
        assert len(trends.trends) == 3  # Three metrics tracked
        
        # Check success rate trend (should be improving)
        success_trend = trends.get_trend_by_metric("success_rate")
        assert success_trend is not None
        assert success_trend.trend_direction == "improving"
        assert success_trend.change_percent > 0

    async def test_identify_high_performing_patterns(
        self,
        analytics_service,
        mock_db_adapter
    ):
        """Test high-performing pattern identification."""
        # Setup mock pattern data
        mock_patterns = [
            {
                "plan_id": "01HX0123456789ABCDEFGHIJK",
                "intent_type": "schedule_meeting",
                "total_executions": 25,
                "success_rate": 0.92,
                "avg_execution_time_ms": 800.0,
                "step_count": 3,
                "pattern_summary": "Fast meeting scheduler"
            },
            {
                "plan_id": "01HX9876543210ZYXWVUTSRQP",
                "intent_type": "book_restaurant",
                "total_executions": 18,
                "success_rate": 0.89,
                "avg_execution_time_ms": 1200.0,
                "step_count": 4,
                "pattern_summary": "Restaurant booking flow"
            }
        ]
        
        mock_db_adapter.get_high_performing_patterns.return_value = mock_patterns
        
        # Execute
        patterns = await analytics_service.identify_high_performing_patterns(
            min_executions=10,
            min_success_rate=0.85
        )
        
        # Verify
        assert len(patterns) == 2
        assert all("performance_score" in pattern for pattern in patterns)
        
        # Patterns should be sorted by performance score
        scores = [pattern["performance_score"] for pattern in patterns]
        assert scores == sorted(scores, reverse=True)

    async def test_health_check(self, analytics_service, mock_db_adapter):
        """Test analytics service health check."""
        mock_db_adapter.health_check.return_value = True
        
        health = await analytics_service.health_check()
        
        assert health is True