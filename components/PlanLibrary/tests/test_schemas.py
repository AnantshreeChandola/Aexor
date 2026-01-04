"""
Schema Validation Tests for PlanLibrary

Tests all domain models with valid and invalid data.
Validates Evidence Item format compliance with GLOBAL_SPEC.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4
from typing import Dict, Any

from pydantic import ValidationError

from ..domain.models import (
    Plan, PlanIntentModel, PlanStepModel, PlanMetaModel,
    Signature, PlanOutcome, PlanMetrics, PlanEmbedding,
    StorePlanRequest, StorePlanResponse, PlanQueryRequest,
    PlanPattern, SimilarityMatch, ErrorResponse,
    StepTiming, ResourceUsage
)
from shared.schemas.evidence import EvidenceItem


class TestPlanModels:
    """Test Plan and related models."""
    
    def test_valid_plan(self):
        """Test Plan model with valid data."""
        intent = PlanIntentModel(
            type="schedule_meeting",
            description="Schedule a meeting with team",
            parameters={"duration": 30}
        )
        
        steps = [
            PlanStepModel(
                step_id="step_1",
                operation="fetch_calendar",
                inputs={"user_id": "123"},
                outputs={}
            )
        ]
        
        meta = PlanMetaModel(
            created_at=datetime.now(timezone.utc),
            version="1.0",
            creator="planner"
        )
        
        plan = Plan(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent=intent,
            graph=steps,
            constraints={"max_duration": 60},
            meta=meta
        )
        
        assert plan.plan_id == "01GRSKBTCE3XTVX61BQ4EWJRCS"
        assert plan.intent.type == "schedule_meeting"
        assert len(plan.graph) == 1
        assert plan.graph[0].step_id == "step_1"
        
        # Test canonical JSON generation
        canonical_json = plan.to_canonical_json()
        assert isinstance(canonical_json, str)
        assert '"plan_id":"01GRSKBTCE3XTVX61BQ4EWJRCS"' in canonical_json
        
        # Test hash generation
        plan_hash = plan.get_plan_hash()
        assert len(plan_hash) == 64  # SHA-256 hex
        
        # Test size calculation
        size_bytes = plan.get_size_bytes()
        assert size_bytes > 0
    
    def test_invalid_plan_id(self):
        """Test Plan with invalid ULID format."""
        with pytest.raises(ValidationError) as exc_info:
            Plan(
                plan_id="invalid_id",  # Not a valid ULID
                intent=PlanIntentModel(type="test"),
                graph=[PlanStepModel(step_id="s1", operation="op1")],
                meta=PlanMetaModel(created_at=datetime.now(timezone.utc))
            )
        
        assert "String should match pattern" in str(exc_info.value)
    
    def test_plan_too_many_steps(self):
        """Test Plan with more than 100 steps."""
        intent = PlanIntentModel(type="test")
        meta = PlanMetaModel(created_at=datetime.now(timezone.utc))
        
        # Create 101 steps
        steps = [
            PlanStepModel(step_id=f"step_{i}", operation=f"op_{i}")
            for i in range(101)
        ]
        
        with pytest.raises(ValidationError) as exc_info:
            Plan(
                plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
                intent=intent,
                graph=steps,
                meta=meta
            )
        
        assert "should have at most 100 items" in str(exc_info.value)


class TestSignatureModel:
    """Test Signature validation."""
    
    def test_valid_signature(self):
        """Test valid Ed25519 signature."""
        signature = Signature(
            signature="base64encodedSignature==",
            public_key="base64encodedPublicKey==",
            algorithm="Ed25519",
            signed_at=datetime.now(timezone.utc)
        )
        
        assert signature.algorithm == "Ed25519"
        assert signature.signature == "base64encodedSignature=="
    
    def test_invalid_algorithm(self):
        """Test signature with unsupported algorithm."""
        with pytest.raises(ValidationError) as exc_info:
            Signature(
                signature="base64encodedSignature==",
                public_key="base64encodedPublicKey==",
                algorithm="RSA2048"  # Not supported
            )
        
        assert "String should match pattern" in str(exc_info.value)


class TestPlanOutcomeModel:
    """Test PlanOutcome validation."""
    
    def test_valid_outcome(self):
        """Test valid plan outcome."""
        outcome = PlanOutcome(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            success=True,
            execution_start=datetime.now(timezone.utc),
            execution_end=datetime.now(timezone.utc),
            total_steps=5
        )
        
        assert outcome.success is True
        assert outcome.total_steps == 5
        
        # Test duration calculation
        duration = outcome.get_duration_seconds()
        assert duration >= 0.0
    
    def test_failed_step_validation(self):
        """Test failed_step cannot exceed total_steps."""
        with pytest.raises(ValidationError) as exc_info:
            PlanOutcome(
                plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
                success=False,
                execution_start=datetime.now(timezone.utc),
                execution_end=datetime.now(timezone.utc),
                total_steps=5,
                failed_step=6  # Exceeds total_steps
            )
        
        assert "failed_step (6) cannot exceed total_steps (5)" in str(exc_info.value)


class TestPlanEmbeddingModel:
    """Test PlanEmbedding validation."""
    
    def test_valid_embedding(self):
        """Test valid embedding with 1536 dimensions."""
        vector = [0.1] * 1536  # OpenAI ada-002 dimensions
        
        embedding = PlanEmbedding(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            vector=vector
        )
        
        assert len(embedding.vector) == 1536
        assert embedding.model_version == "text-embedding-ada-002"
        assert embedding.vector_norm is not None
        assert embedding.vector_norm > 0
    
    def test_invalid_vector_dimensions(self):
        """Test embedding with wrong vector dimensions."""
        vector = [0.1] * 512  # Wrong dimensions
        
        with pytest.raises(ValidationError) as exc_info:
            PlanEmbedding(
                plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
                vector=vector
            )
        
        assert "List should have at least 1536 items" in str(exc_info.value)


class TestStorePlanRequest:
    """Test StorePlanRequest validation."""
    
    def create_valid_plan(self) -> Plan:
        """Create valid plan for testing."""
        return Plan(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent=PlanIntentModel(type="test"),
            graph=[PlanStepModel(step_id="s1", operation="op1")],
            meta=PlanMetaModel(created_at=datetime.now(timezone.utc))
        )
    
    def test_valid_store_request(self):
        """Test valid StorePlanRequest."""
        plan = self.create_valid_plan()
        signature = Signature(
            signature="sig==",
            public_key="key==",
            algorithm="Ed25519"
        )
        outcome = PlanOutcome(
            plan_id=plan.plan_id,
            success=True,
            execution_start=datetime.now(timezone.utc),
            execution_end=datetime.now(timezone.utc),
            total_steps=1
        )
        metrics = PlanMetrics(
            plan_id=plan.plan_id,
            execute_latency_ms=1000
        )
        
        request = StorePlanRequest(
            plan=plan,
            signature=signature,
            outcome=outcome,
            metrics=metrics
        )
        
        assert request.plan.plan_id == plan.plan_id
        assert request.signature.algorithm == "Ed25519"
    
    def test_plan_size_limit(self):
        """Test StorePlanRequest with oversized plan."""
        # Create a large plan that exceeds 1MB
        large_steps = [
            PlanStepModel(
                step_id=f"step_{i}",
                operation=f"op_{i}",
                inputs={"large_data": "x" * 25000}  # Large input data
            )
            for i in range(50)  # Should exceed 1MB total
        ]
        
        plan = Plan(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent=PlanIntentModel(type="test"),
            graph=large_steps,
            meta=PlanMetaModel(created_at=datetime.now(timezone.utc))
        )
        
        signature = Signature(signature="sig==", public_key="key==", algorithm="Ed25519")
        outcome = PlanOutcome(
            plan_id=plan.plan_id,
            success=True,
            execution_start=datetime.now(timezone.utc),
            execution_end=datetime.now(timezone.utc),
            total_steps=50
        )
        metrics = PlanMetrics(plan_id=plan.plan_id, execute_latency_ms=1000)
        
        with pytest.raises(ValidationError) as exc_info:
            StorePlanRequest(
                plan=plan,
                signature=signature,
                outcome=outcome,
                metrics=metrics
            )
        
        assert "exceeds 1MB limit" in str(exc_info.value)


class TestPlanQueryRequest:
    """Test PlanQueryRequest validation."""
    
    def test_valid_intent_query(self):
        """Test valid query by intent type."""
        query = PlanQueryRequest(
            intent_type="schedule_meeting",
            success_threshold=0.8,
            limit=10
        )
        
        assert query.intent_type == "schedule_meeting"
        assert query.success_threshold == 0.8
        assert query.limit == 10
    
    def test_valid_similarity_query(self):
        """Test valid similarity search query."""
        query = PlanQueryRequest(
            query_text="book a restaurant for dinner",
            similarity_threshold=0.6,
            limit=5
        )
        
        assert query.query_text == "book a restaurant for dinner"
        assert query.similarity_threshold == 0.6
    
    def test_missing_query_parameters(self):
        """Test query without intent_type or query_text."""
        with pytest.raises(ValidationError) as exc_info:
            PlanQueryRequest(
                success_threshold=0.7,
                limit=50
            )
        
        assert "Either intent_type or query_text must be provided" in str(exc_info.value)


class TestPlanPatternEvidenceItem:
    """Test Evidence Item conversion compliance."""
    
    def test_plan_pattern_to_evidence_item(self):
        """Test conversion from PlanPattern to Evidence Item."""
        pattern = PlanPattern(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent_type="schedule_meeting",
            success_rate=0.85,
            avg_execution_time_ms=1200.0,
            steps_count=6,
            pattern_summary="Fetch calendars → Find overlap → User choice → Book event",
            total_executions=20,
            last_execution=datetime.now(timezone.utc),
            confidence=0.9
        )
        
        evidence = pattern.to_evidence_item()
        
        # Validate Evidence Item compliance
        assert isinstance(evidence, EvidenceItem)
        assert evidence.type == "plan"
        assert evidence.key.startswith("schedule_meeting_pattern_")
        assert evidence.confidence == 0.9
        assert evidence.source_ref == "planlibrary:plans/01GRSKBTCE3XTVX61BQ4EWJRCS"
        assert evidence.ttl_days is None  # Permanent storage
        assert evidence.tier == 3  # Historical data tier
        
        # Validate value structure
        assert evidence.value["intent"] == "schedule_meeting"
        assert evidence.value["success_rate"] == 0.85
        assert evidence.value["avg_execution_time_ms"] == 1200.0
        assert evidence.value["steps_count"] == 6
        assert "pattern_summary" in evidence.value
    
    def test_similarity_match_to_evidence_item(self):
        """Test conversion from SimilarityMatch to Evidence Item."""
        pattern = PlanPattern(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent_type="book_restaurant",
            success_rate=0.75,
            avg_execution_time_ms=800.0,
            steps_count=4,
            pattern_summary="Search restaurants → Select → Reserve table",
            total_executions=15,
            last_execution=datetime.now(timezone.utc),
            confidence=0.8
        )
        
        match = SimilarityMatch(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent_type="book_restaurant",
            similarity_score=0.82,
            success_rate=0.75,
            relevance_score=0.78,  # Combined score
            plan_pattern=pattern
        )
        
        evidence = match.to_evidence_item()
        
        # Validate similarity-specific Evidence Item
        assert evidence.type == "plan"
        assert evidence.key.startswith("similar_book_restaurant_")
        assert evidence.confidence == 0.78  # Uses relevance_score
        assert evidence.tier == 3


class TestErrorResponse:
    """Test error response format."""
    
    def test_standard_error_response(self):
        """Test standard error response format."""
        error = ErrorResponse(
            error_code="INVALID_SIGNATURE",
            message="Plan signature verification failed",
            details={"plan_id": "01GRSKBTCE3XTVX61BQ4EWJRCS"}
        )
        
        assert error.status == "error"
        assert error.error_code == "INVALID_SIGNATURE"
        assert error.message == "Plan signature verification failed"
        assert error.details["plan_id"] == "01GRSKBTCE3XTVX61BQ4EWJRCS"


class TestResourceUsageModel:
    """Test ResourceUsage validation."""
    
    def test_valid_resource_usage(self):
        """Test valid resource usage metrics."""
        usage = ResourceUsage(
            memory_mb=128.5,
            cpu_percent=45.2
        )
        
        assert usage.memory_mb == 128.5
        assert usage.cpu_percent == 45.2
    
    def test_invalid_cpu_percent(self):
        """Test CPU percentage over 100."""
        with pytest.raises(ValidationError):
            ResourceUsage(
                memory_mb=128.5,
                cpu_percent=150.0  # Over 100%
            )


class TestStepTimingModel:
    """Test StepTiming validation."""
    
    def test_valid_step_timing(self):
        """Test valid step timing data."""
        timing = StepTiming(
            step_id="step_1",
            duration_ms=250
        )
        
        assert timing.step_id == "step_1"
        assert timing.duration_ms == 250
    
    def test_negative_duration(self):
        """Test negative duration is rejected."""
        with pytest.raises(ValidationError):
            StepTiming(
                step_id="step_1",
                duration_ms=-100  # Negative duration
            )