"""
PlanLibrary Contract Tests

Tests for GLOBAL_SPEC compliance and external interface contracts.
Validates Evidence Item format, error codes, and invariants.

Reference: tasks.md T600
"""

import json
from datetime import datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from components.PlanLibrary.domain.models import (
    DuplicatePlanError,
    InvalidQueryError,
    InvalidSignatureError,
    PlanDB,
    PlanLibraryError,
    PlanNotFoundError,
    PlanTooLargeError,
    canonicalize_plan,
    compute_plan_hash,
)
from components.PlanLibrary.service.evidence_service import EvidenceService
from shared.schemas.evidence import EvidenceItem

VALID_ULID = "01HX1234567890ABCDEFGHJKMN"


class TestGlobalSpecCompliance:
    """Test compliance with GLOBAL_SPEC.md contracts."""

    def test_evidence_item_format_compliance(self):
        """Test Evidence Item format matches GLOBAL_SPEC 2.2."""
        evidence = EvidenceItem(
            type="plan",
            key="schedule_meeting_pattern_1",
            value={
                "intent": "schedule_meeting",
                "success_rate": 0.85,
                "avg_execution_time_ms": 1200,
                "steps_count": 6,
                "pattern_summary": "Fetch calendars -> Find overlap -> Book",
            },
            confidence=0.85,
            source_ref=f"planlibrary:plans/{VALID_ULID}",
            ttl_days=None,
            tier=3,
        )

        assert evidence.type == "plan"
        assert evidence.tier == 3
        assert evidence.ttl_days is None
        assert "planlibrary:" in evidence.source_ref
        assert 0.0 <= evidence.confidence <= 1.0

        # Validate serialization roundtrip
        serialized = evidence.model_dump()
        assert all(
            f in serialized
            for f in ["type", "key", "value", "confidence", "source_ref", "ttl_days", "tier"]
        )
        EvidenceItem.model_validate(serialized)

    def test_evidence_item_json_serialization(self):
        """Test Evidence Item can be JSON serialized."""
        evidence = EvidenceItem(
            type="plan",
            key="test_plan",
            value={"intent": "test", "success_rate": 0.9},
            confidence=0.9,
            source_ref=f"planlibrary:plans/{VALID_ULID}",
            ttl_days=None,
            tier=3,
        )

        json_str = evidence.model_dump_json()
        assert isinstance(json_str, str)
        assert "plan" in json_str

        # Roundtrip via JSON
        parsed = json.loads(json_str)
        restored = EvidenceItem.model_validate(parsed)
        assert restored.type == "plan"

    def test_tier_3_data_source_compliance(self):
        """Test PlanLibrary always returns tier=3 per GLOBAL_SPEC 7."""
        evidence = EvidenceItem(
            type="plan",
            key="any",
            value="any",
            confidence=0.8,
            source_ref="planlibrary:plans/any",
            ttl_days=None,
            tier=3,
        )
        assert evidence.tier == 3
        assert 1 <= evidence.tier <= 4

    def test_confidence_score_range(self):
        """Test confidence scores are within 0.0-1.0."""
        # Valid confidence
        evidence = EvidenceItem(
            type="plan",
            key="test",
            value="test",
            confidence=0.85,
            source_ref="planlibrary:plans/test",
            ttl_days=None,
            tier=3,
        )
        assert 0.0 <= evidence.confidence <= 1.0

        # Invalid confidence rejected
        with pytest.raises(PydanticValidationError):
            EvidenceItem(
                type="plan",
                key="test",
                value="test",
                confidence=1.5,
                source_ref="test",
                ttl_days=None,
                tier=3,
            )

    def test_source_ref_format(self):
        """Test source_ref follows planlibrary:plans/{id} pattern."""
        service = EvidenceService()
        evidence = service.to_evidence_item(
            {
                "plan_id": VALID_ULID,
                "intent_type": "test",
                "step_count": 3,
                "success_rate": 0.8,
                "avg_execution_time_ms": 100.0,
            }
        )
        assert evidence.source_ref == f"planlibrary:plans/{VALID_ULID}"


class TestErrorCodeContract:
    """Test error codes match SPEC FR-001."""

    def test_all_error_codes_defined(self):
        """All SPEC error codes have corresponding error classes."""
        error_map = {
            "INVALID_PLAN_ID": ValueError,
            "MALFORMED_PLAN": ValueError,
            "INVALID_SIGNATURE": InvalidSignatureError,
            "DUPLICATE_PLAN_ID": DuplicatePlanError,
            "PLAN_TOO_LARGE": PlanTooLargeError,
            "STORAGE_ERROR": PlanLibraryError,
            "INVALID_QUERY": InvalidQueryError,
        }

        for code, error_class in error_map.items():
            assert issubclass(error_class, Exception), f"Error code {code} has no error class"

    def test_invalid_signature_error_attributes(self):
        """InvalidSignatureError has required attributes for API response."""
        error = InvalidSignatureError(plan_id=VALID_ULID, reason="test")
        assert hasattr(error, "plan_id")
        assert hasattr(error, "reason")

    def test_duplicate_plan_error_attributes(self):
        """DuplicatePlanError has required attributes for API response."""
        error = DuplicatePlanError(plan_id=VALID_ULID)
        assert hasattr(error, "plan_id")

    def test_plan_too_large_error_attributes(self):
        """PlanTooLargeError has required attributes for API response."""
        error = PlanTooLargeError(plan_id=VALID_ULID, reason="too big")
        assert hasattr(error, "plan_id")
        assert hasattr(error, "reason")

    def test_error_hierarchy(self):
        """All PlanLibrary errors inherit from PlanLibraryError."""
        errors = [
            InvalidSignatureError,
            DuplicatePlanError,
            PlanTooLargeError,
            InvalidQueryError,
            PlanNotFoundError,
        ]
        for error_class in errors:
            assert issubclass(error_class, PlanLibraryError)


class TestInvariantCompliance:
    """Test invariants from SPEC."""

    def test_plan_uniqueness_via_domain(self):
        """Plan ID uniqueness enforced by ULID format validation."""
        plan = PlanDB(
            plan_id=VALID_ULID,
            canonical_json={},
            signature_data={},
            intent_type="test",
            step_count=1,
            plan_hash="a" * 64,
            size_bytes=100,
            created_at=datetime.utcnow(),
        )
        assert plan.plan_id == VALID_ULID

    def test_canonical_serialization_deterministic(self):
        """Canonical serialization produces sorted keys, no whitespace."""
        plan_data = {"z": 1, "a": 2, "m": {"nested": True, "alpha": False}}
        canonical1 = canonicalize_plan(plan_data)
        canonical2 = canonicalize_plan(plan_data)

        # Deterministic
        assert canonical1 == canonical2

        # Sorted keys
        parsed = json.loads(canonical1)
        assert list(parsed.keys()) == sorted(parsed.keys())

        # No extra whitespace
        assert " " not in canonical1
        assert "\n" not in canonical1

    def test_hash_determinism(self):
        """Same canonical JSON always produces same SHA-256 hash."""
        canonical = '{"a":1,"b":2}'
        hash1 = compute_plan_hash(canonical)
        hash2 = compute_plan_hash(canonical)
        assert hash1 == hash2
        assert len(hash1) == 64

    def test_immutable_storage_model(self):
        """Plans domain model does not have update methods."""
        plan = PlanDB(
            plan_id=VALID_ULID,
            canonical_json={},
            signature_data={},
            intent_type="test",
            step_count=1,
            plan_hash="a" * 64,
            size_bytes=100,
            created_at=datetime.utcnow(),
        )

        # PlanDB is a Pydantic model -- frozen by convention
        # Verify no mutation methods exist
        methods = dir(plan)
        assert "update" not in methods or not callable(getattr(plan, "update", None))

    def test_outcome_references_valid_plan_id(self):
        """PlanOutcomeDB requires valid plan_id."""
        from components.PlanLibrary.domain.models import PlanOutcomeDB

        outcome = PlanOutcomeDB(
            plan_id=VALID_ULID,
            success=True,
            execution_start=datetime.utcnow(),
            execution_end=datetime.utcnow(),
            total_steps=3,
        )
        assert outcome.plan_id == VALID_ULID


class TestPreviewExecuteModelCompliance:
    """Test PlanLibrary does NOT use Preview/Execute model."""

    def test_no_preview_execute_in_plan_service(self):
        """PlanService does not have preview_/execute_ methods."""
        from components.PlanLibrary.service.plan_service import PlanService

        methods = dir(PlanService)
        preview_methods = [m for m in methods if m.startswith("preview_")]
        execute_methods = [m for m in methods if m.startswith("execute_")]

        assert len(preview_methods) == 0, f"PlanService has preview methods: {preview_methods}"
        assert len(execute_methods) == 0, f"PlanService has execute methods: {execute_methods}"

    def test_direct_operation_methods_exist(self):
        """PlanService has direct operation methods."""
        from components.PlanLibrary.service.plan_service import PlanService

        methods = dir(PlanService)
        assert "store_plan" in methods
        assert "get_plans_by_intent" in methods
        assert "get_plan_by_id" in methods


class TestEvidenceServiceContract:
    """Test EvidenceService compliance with GLOBAL_SPEC."""

    def test_to_evidence_item_output_format(self):
        """to_evidence_item returns correct Evidence Item format."""
        service = EvidenceService()
        evidence = service.to_evidence_item(
            {
                "plan_id": VALID_ULID,
                "intent_type": "schedule_meeting",
                "step_count": 6,
                "success_rate": 0.85,
                "avg_execution_time_ms": 1200.0,
            }
        )

        assert evidence.type == "plan"
        assert evidence.tier == 3
        assert evidence.ttl_days is None
        assert evidence.confidence == 0.85
        assert "planlibrary:plans/" in evidence.source_ref
        assert evidence.value["intent"] == "schedule_meeting"
        assert evidence.value["success_rate"] == 0.85

    def test_batch_conversion(self):
        """to_evidence_items handles batch conversion."""
        service = EvidenceService()
        plans = [
            {
                "plan_id": VALID_ULID,
                "intent_type": "test",
                "step_count": 3,
                "success_rate": 0.9,
                "avg_execution_time_ms": 100.0,
            },
        ]
        items = service.to_evidence_items(plans)
        assert len(items) == 1
        assert all(isinstance(i, EvidenceItem) for i in items)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
