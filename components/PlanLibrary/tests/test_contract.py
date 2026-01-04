"""
Contract Tests for PlanLibrary

Tests Evidence Item format compliance with GLOBAL_SPEC.
Validates plan storage → query → Evidence conversion flow.
Tests all error codes and response formats from SPEC.
"""

import pytest
from datetime import datetime, timezone
from typing import List, Dict, Any

from shared.schemas.evidence import EvidenceItem
from ..domain.models import (
    Plan, PlanIntentModel, PlanStepModel, PlanMetaModel,
    PlanPattern, SimilarityMatch, ErrorResponse,
    StorePlanRequest, StorePlanResponse
)


class TestEvidenceItemCompliance:
    """Test Evidence Item format compliance with GLOBAL_SPEC §2.2."""
    
    @pytest.fixture
    def sample_plan_pattern(self):
        """Create sample PlanPattern for testing."""
        return PlanPattern(
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
    
    @pytest.fixture
    def sample_similarity_match(self, sample_plan_pattern):
        """Create sample SimilarityMatch for testing."""
        return SimilarityMatch(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent_type="book_restaurant",
            similarity_score=0.82,
            success_rate=0.75,
            relevance_score=0.78,
            plan_pattern=sample_plan_pattern
        )

    def test_plan_pattern_evidence_item_compliance(self, sample_plan_pattern):
        """Test PlanPattern to Evidence Item conversion compliance."""
        evidence = sample_plan_pattern.to_evidence_item()
        
        # Validate Evidence Item type compliance
        assert isinstance(evidence, EvidenceItem)
        
        # Validate required fields (GLOBAL_SPEC §2.2)
        assert evidence.type == "plan"
        assert evidence.key is not None
        assert len(evidence.key) > 0
        assert evidence.value is not None
        assert evidence.confidence is not None
        assert evidence.source_ref is not None
        assert evidence.tier is not None
        
        # Validate field constraints
        assert 0.0 <= evidence.confidence <= 1.0
        assert 1 <= evidence.tier <= 4
        assert len(evidence.key) <= 128
        assert len(evidence.source_ref) >= 1
        
        # Validate specific Plan Evidence requirements
        assert evidence.type == "plan"
        assert evidence.key.startswith("schedule_meeting_pattern_")
        assert evidence.confidence == 0.9  # Uses pattern confidence
        assert evidence.source_ref == "planlibrary:plans/01GRSKBTCE3XTVX61BQ4EWJRCS"
        assert evidence.ttl_days is None  # Permanent storage
        assert evidence.tier == 3  # Historical data tier (GLOBAL_SPEC §7)
        
        # Validate value structure for plan Evidence
        value = evidence.value
        assert isinstance(value, dict)
        assert "intent" in value
        assert "success_rate" in value
        assert "avg_execution_time_ms" in value
        assert "steps_count" in value
        assert "pattern_summary" in value
        assert "total_executions" in value
        assert "last_execution" in value
        
        # Validate value content
        assert value["intent"] == "schedule_meeting"
        assert value["success_rate"] == 0.85
        assert value["avg_execution_time_ms"] == 1200.0
        assert value["steps_count"] == 6
        assert value["total_executions"] == 20
        assert "Fetch calendars" in value["pattern_summary"]

    def test_similarity_match_evidence_item_compliance(self, sample_similarity_match):
        """Test SimilarityMatch to Evidence Item conversion compliance."""
        evidence = sample_similarity_match.to_evidence_item()
        
        # Validate Evidence Item type compliance
        assert isinstance(evidence, EvidenceItem)
        
        # Validate required fields
        assert evidence.type == "plan"
        assert evidence.key.startswith("similar_book_restaurant_")
        assert evidence.confidence == 0.78  # Uses relevance_score
        assert evidence.source_ref == "planlibrary:plans/01GRSKBTCE3XTVX61BQ4EWJRCS"
        assert evidence.tier == 3
        
        # Validate similarity-specific characteristics
        assert "similar_" in evidence.key
        assert evidence.confidence != sample_similarity_match.plan_pattern.confidence
        assert evidence.confidence == sample_similarity_match.relevance_score

    def test_evidence_item_json_serialization(self, sample_plan_pattern):
        """Test Evidence Item JSON serialization compliance."""
        evidence = sample_plan_pattern.to_evidence_item()
        
        # Serialize to JSON
        json_str = evidence.model_dump_json()
        assert isinstance(json_str, str)
        
        # Deserialize back
        evidence_dict = evidence.model_dump()
        reconstructed = EvidenceItem.model_validate(evidence_dict)
        
        # Validate round-trip consistency
        assert reconstructed.type == evidence.type
        assert reconstructed.key == evidence.key
        assert reconstructed.confidence == evidence.confidence
        assert reconstructed.source_ref == evidence.source_ref
        assert reconstructed.tier == evidence.tier

    def test_evidence_item_field_validation(self):
        """Test Evidence Item field validation according to GLOBAL_SPEC."""
        
        # Test invalid type
        with pytest.raises(ValueError):
            EvidenceItem(
                type="invalid_type",  # Not in allowed types
                key="test_key",
                value={"data": "test"},
                confidence=0.8,
                source_ref="test:ref",
                tier=3
            )
        
        # Test invalid confidence range
        with pytest.raises(ValueError):
            EvidenceItem(
                type="plan",
                key="test_key", 
                value={"data": "test"},
                confidence=1.5,  # Above 1.0
                source_ref="test:ref",
                tier=3
            )
        
        # Test invalid tier
        with pytest.raises(ValueError):
            EvidenceItem(
                type="plan",
                key="test_key",
                value={"data": "test"},
                confidence=0.8,
                source_ref="test:ref",
                tier=5  # Above valid range
            )
        
        # Test key length limits
        with pytest.raises(ValueError):
            EvidenceItem(
                type="plan",
                key="a" * 129,  # Exceeds 128 char limit
                value={"data": "test"},
                confidence=0.8,
                source_ref="test:ref",
                tier=3
            )

    def test_multiple_evidence_items_consistency(self):
        """Test consistency across multiple Evidence Items."""
        patterns = [
            PlanPattern(
                plan_id=f"01HX{i:022d}",
                intent_type="schedule_meeting",
                success_rate=0.8 + (i * 0.05),
                avg_execution_time_ms=1000.0 + (i * 100),
                steps_count=3 + i,
                pattern_summary=f"Pattern {i}",
                total_executions=10 + i,
                last_execution=datetime.now(timezone.utc),
                confidence=0.8 + (i * 0.05)
            )
            for i in range(5)
        ]
        
        evidence_items = [pattern.to_evidence_item() for pattern in patterns]
        
        # Validate all items are properly formatted
        assert len(evidence_items) == 5
        
        # Validate consistent structure
        for evidence in evidence_items:
            assert evidence.type == "plan"
            assert evidence.tier == 3
            assert evidence.ttl_days is None
            assert evidence.key.startswith("schedule_meeting_pattern_")
            assert evidence.source_ref.startswith("planlibrary:plans/")
            
            # Validate value structure consistency
            value = evidence.value
            required_fields = [
                "intent", "success_rate", "avg_execution_time_ms",
                "steps_count", "pattern_summary", "total_executions", "last_execution"
            ]
            for field in required_fields:
                assert field in value

    def test_evidence_item_contextrag_integration(self, sample_plan_pattern):
        """Test Evidence Item compatibility with ContextRAG expectations."""
        evidence = sample_plan_pattern.to_evidence_item()
        
        # Simulate ContextRAG processing
        context_items = [evidence]
        
        # Validate ContextRAG expected fields
        for item in context_items:
            # Type filtering
            assert item.type in ["preference", "history", "contact", "plan", "exemplar"]
            
            # Tier-based filtering (GLOBAL_SPEC §7)
            assert 1 <= item.tier <= 4
            
            # Confidence-based ranking
            assert 0.0 <= item.confidence <= 1.0
            
            # Source traceability
            assert item.source_ref.startswith("planlibrary:")
            
            # Value structure for plan type
            if item.type == "plan":
                assert isinstance(item.value, dict)
                assert "intent" in item.value
                assert "success_rate" in item.value


class TestErrorResponseCompliance:
    """Test error response format compliance with SPEC requirements."""
    
    def test_error_response_structure(self):
        """Test ErrorResponse structure compliance."""
        error = ErrorResponse(
            error_code="INVALID_SIGNATURE",
            message="Plan signature verification failed",
            details={"plan_id": "01GRSKBTCE3XTVX61BQ4EWJRCS"}
        )
        
        # Validate required fields
        assert error.status == "error"
        assert error.error_code == "INVALID_SIGNATURE"
        assert error.message == "Plan signature verification failed"
        assert error.details is not None
        
        # Validate JSON serialization
        error_dict = error.model_dump()
        assert "status" in error_dict
        assert "error_code" in error_dict
        assert "message" in error_dict
        assert "details" in error_dict

    def test_all_spec_error_codes(self):
        """Test all error codes defined in SPEC are valid."""
        spec_error_codes = [
            "INVALID_PLAN_ID",
            "MALFORMED_PLAN",
            "INVALID_SIGNATURE", 
            "DUPLICATE_PLAN_ID",
            "PLAN_TOO_LARGE",
            "STORAGE_ERROR",
            "INVALID_QUERY",
            "VECTOR_SEARCH_UNAVAILABLE"
        ]
        
        for error_code in spec_error_codes:
            error = ErrorResponse(
                error_code=error_code,
                message=f"Test error for {error_code}",
                details={}
            )
            
            assert error.error_code == error_code
            assert error.status == "error"

    def test_error_response_json_schema_compliance(self):
        """Test ErrorResponse JSON schema compliance."""
        error = ErrorResponse(
            error_code="PLAN_TOO_LARGE",
            message="Plan exceeds size limit",
            details={
                "size_bytes": 1048576,
                "max_bytes": 1048576,
                "plan_id": "01GRSKBTCE3XTVX61BQ4EWJRCS"
            }
        )
        
        # Serialize to dict for schema validation
        error_dict = error.model_dump()
        
        # Validate schema structure matches SPEC
        assert error_dict["status"] == "error"
        assert error_dict["error_code"] in [
            "INVALID_PLAN_ID", "MALFORMED_PLAN", "INVALID_SIGNATURE",
            "DUPLICATE_PLAN_ID", "PLAN_TOO_LARGE", "STORAGE_ERROR", 
            "INVALID_QUERY", "VECTOR_SEARCH_UNAVAILABLE"
        ]
        assert isinstance(error_dict["message"], str)
        assert isinstance(error_dict["details"], dict)


class TestStorePlanResponseCompliance:
    """Test StorePlanResponse compliance with SPEC."""
    
    def test_store_plan_response_structure(self):
        """Test StorePlanResponse structure compliance."""
        response = StorePlanResponse(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            stored_at=datetime.now(timezone.utc),
            embedding_queued=True
        )
        
        # Validate required fields
        assert response.status == "ok"
        assert response.plan_id == "01GRSKBTCE3XTVX61BQ4EWJRCS"
        assert isinstance(response.stored_at, datetime)
        assert isinstance(response.embedding_queued, bool)
        
        # Validate JSON serialization
        response_dict = response.model_dump()
        assert "status" in response_dict
        assert "plan_id" in response_dict
        assert "stored_at" in response_dict
        assert "embedding_queued" in response_dict

    def test_plan_id_ulid_format_compliance(self):
        """Test plan_id ULID format compliance."""
        valid_ulid = "01GRSKBTCE3XTVX61BQ4EWJRCS"
        
        response = StorePlanResponse(
            plan_id=valid_ulid,
            stored_at=datetime.now(timezone.utc),
            embedding_queued=False
        )
        
        # Validate ULID format (26 characters, base32)
        assert len(response.plan_id) == 26
        assert response.plan_id.isalnum()
        assert response.plan_id.isupper() or response.plan_id.isdigit()


class TestPlanStorageFlowCompliance:
    """Test complete plan storage → query → Evidence Item flow."""
    
    @pytest.fixture
    def complete_plan(self):
        """Create complete plan for flow testing."""
        return Plan(
            plan_id="01GRSKBTCE3XTVX61BQ4EWJRCS",
            intent=PlanIntentModel(
                type="schedule_meeting",
                description="Schedule team meeting with calendar integration",
                parameters={"duration": 60, "participants": 5}
            ),
            graph=[
                PlanStepModel(
                    step_id="step_1",
                    operation="fetch_user_calendar",
                    inputs={"user_id": "user123"},
                    outputs={"calendar_events": []}
                ),
                PlanStepModel(
                    step_id="step_2", 
                    operation="find_time_slots",
                    inputs={"duration": 60, "events": []},
                    outputs={"available_slots": []}
                ),
                PlanStepModel(
                    step_id="step_3",
                    operation="present_options",
                    inputs={"slots": []},
                    outputs={"selected_slot": {}}
                ),
                PlanStepModel(
                    step_id="step_4",
                    operation="book_meeting",
                    inputs={"slot": {}, "participants": []},
                    outputs={"meeting_id": "meeting123"}
                )
            ],
            constraints={"max_duration": 120, "min_participants": 2},
            meta=PlanMetaModel(
                created_at=datetime.now(timezone.utc),
                version="2.1",
                creator="planner_v2"
            )
        )

    def test_plan_to_pattern_to_evidence_flow(self, complete_plan):
        """Test complete flow from Plan to Evidence Item."""
        # Step 1: Plan storage (would result in PlanPattern creation)
        pattern = PlanPattern(
            plan_id=complete_plan.plan_id,
            intent_type=complete_plan.intent.type,
            success_rate=0.92,
            avg_execution_time_ms=1800.0,
            steps_count=len(complete_plan.graph),
            pattern_summary="Fetch calendar → Find slots → Present options → Book meeting",
            total_executions=35,
            last_execution=datetime.now(timezone.utc),
            confidence=0.95
        )
        
        # Step 2: Pattern to Evidence Item conversion
        evidence = pattern.to_evidence_item()
        
        # Step 3: Validate end-to-end compliance
        assert evidence.type == "plan"
        assert evidence.tier == 3  # Historical data
        assert evidence.confidence == 0.95
        assert evidence.value["intent"] == complete_plan.intent.type
        assert evidence.value["steps_count"] == len(complete_plan.graph)
        
        # Step 4: Validate ContextRAG compatibility
        assert evidence.source_ref.startswith("planlibrary:")
        assert 0.0 <= evidence.confidence <= 1.0
        assert evidence.key.startswith(f"{complete_plan.intent.type}_pattern_")

    def test_similarity_search_evidence_flow(self, complete_plan):
        """Test similarity search to Evidence Item flow."""
        # Simulate similarity search result
        pattern = PlanPattern(
            plan_id=complete_plan.plan_id,
            intent_type=complete_plan.intent.type,
            success_rate=0.88,
            avg_execution_time_ms=1600.0,
            steps_count=len(complete_plan.graph),
            pattern_summary="Meeting scheduling pattern",
            total_executions=25,
            last_execution=datetime.now(timezone.utc),
            confidence=0.85
        )
        
        similarity_match = SimilarityMatch(
            plan_id=complete_plan.plan_id,
            intent_type=complete_plan.intent.type,
            similarity_score=0.87,
            success_rate=0.88,
            relevance_score=0.82,  # Combined score
            plan_pattern=pattern
        )
        
        # Convert to Evidence Item
        evidence = similarity_match.to_evidence_item()
        
        # Validate similarity-specific Evidence characteristics
        assert evidence.key.startswith("similar_schedule_meeting_")
        assert evidence.confidence == 0.82  # Uses relevance_score, not pattern confidence
        assert evidence.type == "plan"
        assert evidence.tier == 3

    def test_bulk_evidence_processing(self):
        """Test bulk Evidence Item processing for ContextRAG."""
        # Simulate multiple plan patterns from different queries
        patterns = [
            PlanPattern(
                plan_id=f"0{i}GRSKBTCE3XTVX61BQ4EWJRCS{i}",
                intent_type=f"intent_type_{i % 3}",
                success_rate=min(0.7 + (i * 0.05), 1.0),
                avg_execution_time_ms=1000.0 + (i * 200),
                steps_count=3 + (i % 5),
                pattern_summary=f"Pattern {i} workflow",
                total_executions=10 + i,
                last_execution=datetime.now(timezone.utc),
                confidence=min(0.7 + (i * 0.02), 0.95)  # Max 0.95 to stay under 1.0
            )
            for i in range(10)
        ]
        
        # Convert all to Evidence Items
        evidence_items = [pattern.to_evidence_item() for pattern in patterns]
        
        # Validate bulk processing compliance
        assert len(evidence_items) == 10
        
        # Validate all items meet Evidence Item standards
        for evidence in evidence_items:
            assert isinstance(evidence, EvidenceItem)
            assert evidence.type == "plan"
            assert evidence.tier == 3
            assert 0.0 <= evidence.confidence <= 1.0
            assert evidence.source_ref.startswith("planlibrary:plans/")
        
        # Validate sorting capability (ContextRAG requirement)
        sorted_evidence = sorted(evidence_items, key=lambda x: x.confidence, reverse=True)
        confidences = [e.confidence for e in sorted_evidence]
        assert confidences == sorted(confidences, reverse=True)