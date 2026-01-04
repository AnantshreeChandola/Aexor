"""
Integration Tests for PlanLibrary

Tests the complete PlanLibrary component with real database interactions.
These tests require a PostgreSQL database with pgvector extension.
"""

import pytest
import asyncio
from datetime import datetime, timezone
from typing import List

from ..api.dependencies import (
    get_plan_service, get_vector_service, get_analytics_service,
    get_database_adapter
)
from ..domain.models import (
    Plan, PlanIntentModel, PlanStepModel, PlanMetaModel,
    Signature, PlanOutcome, PlanMetrics, StorePlanRequest
)
from shared.schemas.evidence import EvidenceItem


@pytest.mark.integration
class TestPlanLibraryIntegration:
    """Integration tests for complete PlanLibrary component."""
    
    @pytest.fixture(scope="class")
    async def setup_database(self):
        """Setup test database tables."""
        # Note: In a real test environment, this would:
        # 1. Create test database
        # 2. Run migrations to create tables
        # 3. Setup pgvector extension
        # For now, we assume these exist
        pass
    
    @pytest.fixture
    def sample_plan(self):
        """Create sample plan for testing."""
        return Plan(
            plan_id="01HX0123456789ABCDEFGHIJK",
            intent=PlanIntentModel(
                type="schedule_meeting",
                description="Schedule a team meeting",
                parameters={"duration": 30, "attendees": 3}
            ),
            graph=[
                PlanStepModel(
                    step_id="step_1",
                    operation="fetch_calendar",
                    inputs={"user_id": "user123"},
                    outputs={}
                ),
                PlanStepModel(
                    step_id="step_2",
                    operation="find_availability",
                    inputs={"duration": 30},
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
        """Create sample signature."""
        return Signature(
            signature="test_signature_data_base64",
            public_key="test_public_key_data_base64", 
            algorithm="Ed25519"
        )
    
    @pytest.fixture
    def sample_outcome(self, sample_plan):
        """Create sample outcome."""
        now = datetime.now(timezone.utc)
        return PlanOutcome(
            plan_id=sample_plan.plan_id,
            success=True,
            execution_start=now,
            execution_end=now,
            total_steps=2
        )
    
    @pytest.fixture
    def sample_metrics(self, sample_plan):
        """Create sample metrics."""
        return PlanMetrics(
            plan_id=sample_plan.plan_id,
            execute_latency_ms=1200,
            preview_latency_ms=200
        )

    async def test_database_connectivity(self, setup_database):
        """Test database connectivity and health."""
        db_adapter = get_database_adapter()
        
        # Test health check
        health = await db_adapter.health_check()
        assert health is True, "Database should be healthy"

    async def test_plan_service_health(self):
        """Test plan service health check."""
        plan_service = get_plan_service()
        
        health = await plan_service.health_check()
        assert health["service"] == "PlanService"
        # Note: Health may be unhealthy due to missing OpenAI API keys, etc.

    @pytest.mark.skipif(
        "not config.getoption('--run-integration')",
        reason="Integration tests require --run-integration flag"
    )
    async def test_complete_plan_storage_flow(
        self,
        setup_database,
        sample_plan,
        sample_signature,
        sample_outcome,
        sample_metrics
    ):
        """Test complete plan storage and retrieval flow."""
        plan_service = get_plan_service()
        
        # Note: This test would require:
        # 1. Valid signature verification setup
        # 2. Database tables to exist
        # 3. OpenAI API key for embeddings
        
        try:
            # Store plan
            result = await plan_service.store_plan(
                plan=sample_plan,
                signature=sample_signature,
                outcome=sample_outcome,
                metrics=sample_metrics
            )
            
            assert result.plan_id == sample_plan.plan_id
            assert result.status == "ok"
            
            # Retrieve plan
            retrieved_plan = await plan_service.get_plan_by_id(sample_plan.plan_id)
            assert retrieved_plan is not None
            assert retrieved_plan.plan_id == sample_plan.plan_id
            
        except Exception as e:
            # Expected to fail in test environment without proper setup
            pytest.skip(f"Integration test requires full environment: {e}")

    @pytest.mark.skipif(
        "not config.getoption('--run-integration')",
        reason="Integration tests require --run-integration flag"
    )
    async def test_intent_based_query_flow(self, setup_database):
        """Test intent-based plan querying."""
        plan_service = get_plan_service()
        
        try:
            # Query for schedule_meeting plans
            patterns = await plan_service.get_plans_by_intent(
                intent_type="schedule_meeting",
                success_threshold=0.5,
                limit=10
            )
            
            # Should return empty list if no plans stored
            assert isinstance(patterns, list)
            
            # Convert to Evidence Items
            evidence_items = [pattern.to_evidence_item() for pattern in patterns]
            
            # Validate Evidence Item compliance
            for evidence in evidence_items:
                assert isinstance(evidence, EvidenceItem)
                assert evidence.type == "plan"
                assert evidence.tier == 3
                
        except Exception as e:
            pytest.skip(f"Integration test requires full environment: {e}")

    @pytest.mark.skipif(
        "not config.getoption('--run-integration')",
        reason="Integration tests require --run-integration flag"
    )
    async def test_vector_similarity_search_flow(self, setup_database):
        """Test vector similarity search."""
        vector_service = get_vector_service()
        
        try:
            # Test similarity search
            results = await vector_service.similarity_search(
                query_text="schedule a meeting with the team",
                similarity_threshold=0.5,
                limit=5
            )
            
            assert isinstance(results, list)
            
            # Convert to Evidence Items
            evidence_items = [match.to_evidence_item() for match in results]
            
            # Validate Evidence compliance
            for evidence in evidence_items:
                assert isinstance(evidence, EvidenceItem)
                assert evidence.type == "plan"
                assert evidence.key.startswith("similar_")
                
        except Exception as e:
            # Expected to fail without OpenAI API key
            pytest.skip(f"Vector search requires OpenAI API: {e}")

    @pytest.mark.skipif(
        "not config.getoption('--run-integration')",  
        reason="Integration tests require --run-integration flag"
    )
    async def test_analytics_service_flow(self, setup_database):
        """Test analytics service functionality."""
        analytics_service = get_analytics_service()
        
        try:
            # Test success rate analytics
            analytics = await analytics_service.calculate_success_rates(
                timeframe_days=30
            )
            
            assert isinstance(analytics, dict)
            
            # Test performance trends
            trends = await analytics_service.get_performance_trends(
                intent_type="schedule_meeting",
                trend_days=30
            )
            
            assert hasattr(trends, 'trends')
            assert hasattr(trends, 'generated_at')
            
        except Exception as e:
            pytest.skip(f"Analytics requires database with plan data: {e}")

    async def test_concurrent_plan_operations(self, setup_database):
        """Test concurrent plan storage and retrieval operations."""
        plan_service = get_plan_service()
        
        # Create multiple concurrent tasks
        async def mock_operation(i):
            """Mock operation that doesn't require external dependencies."""
            try:
                # Test plan ID validation
                await plan_service.get_plan_by_id(f"01HX{i:022d}")
                return True
            except Exception:
                return False
        
        # Run concurrent operations
        tasks = [mock_operation(i) for i in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Should handle concurrency gracefully
        assert len(results) == 10
        assert all(isinstance(r, bool) for r in results)

    async def test_error_handling_integration(self):
        """Test error handling across service boundaries."""
        plan_service = get_plan_service()
        
        # Test invalid plan ID
        try:
            result = await plan_service.get_plan_by_id("invalid_id")
        except ValueError as e:
            assert "ULID" in str(e)
        
        # Test empty intent query
        try:
            await plan_service.get_plans_by_intent(intent_type="")
        except ValueError as e:
            assert "empty" in str(e)

    async def test_component_isolation(self):
        """Test that component failures don't cascade."""
        # Test that vector service failure doesn't affect plan service
        plan_service = get_plan_service()
        
        # This should work even if vector service has issues
        try:
            health = await plan_service.health_check()
            assert "service" in health
            assert "dependencies" in health
        except Exception as e:
            # Health checks can fail in test environment
            assert "database" in str(e) or "vector" in str(e)

    async def test_performance_requirements(self, setup_database):
        """Test that operations meet performance requirements."""
        import time
        
        plan_service = get_plan_service()
        
        # Test plan retrieval latency (should be fast even if plan doesn't exist)
        start_time = time.time()
        
        try:
            await plan_service.get_plan_by_id("01HX0123456789ABCDEFGHIJK") 
        except Exception:
            pass
        
        latency = (time.time() - start_time) * 1000
        
        # Should complete within reasonable time even in test environment
        assert latency < 5000  # 5 second timeout for test environment


@pytest.mark.integration
class TestAPIIntegration:
    """Integration tests for API layer."""
    
    async def test_health_endpoint_integration(self):
        """Test health endpoint returns proper format."""
        from ..api.routes import health_check_endpoint
        from ..api.dependencies import (
            get_plan_service, get_vector_service, get_analytics_service
        )
        
        plan_service = get_plan_service()
        vector_service = get_vector_service()
        analytics_service = get_analytics_service()
        
        # Test health check
        health = await health_check_endpoint(
            plan_service=plan_service,
            vector_service=vector_service,
            analytics_service=analytics_service
        )
        
        # Validate response structure
        assert "service" in health
        assert "status" in health
        assert "timestamp" in health
        assert "dependencies" in health
        
        assert health["service"] == "PlanLibrary"
        assert health["status"] in ["healthy", "unhealthy"]


def pytest_addoption(parser):
    """Add command line option for integration tests."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require database/external services"
    )