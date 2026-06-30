"""
PlanLibrary Domain Model Tests

Unit tests for Pydantic models, validation, error classes,
and utility functions.

Reference: LLD.md, tasks.md T102
"""

import json
from datetime import datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from components.PlanLibrary.domain.models import (
    DuplicatePlanError,
    ErrorResponse,
    InvalidQueryError,
    PlanDB,
    PlanLibraryError,
    PlanMetricsDB,
    PlanNotFoundError,
    PlanOutcomeDB,
    PlanPattern,
    PlanTooLargeError,
    QueryPlansRequest,
    StorePlanRequest,
    StorePlanResponse,
    SuccessResponse,
    canonicalize_plan,
    compute_plan_hash,
)

# Valid ULID for testing (Crockford base32, 26 chars)
VALID_ULID = "01HX1234567890ABCDEFGHJKMN"
VALID_ULID_2 = "01HX9876543210ZYXWVTSRQPNM"


class TestPlanDB:
    """Test PlanDB model validation."""

    def test_valid_plan_db(self):
        """Test creating PlanDB with valid data."""
        plan = PlanDB(
            plan_id=VALID_ULID,
            canonical_json={"graph": [], "meta": {"intent_type": "test"}},
            signature_data={"algorithm": "ed25519", "signature_hex": "abc123"},
            intent_type="schedule_meeting",
            step_count=5,
            plan_hash="a" * 64,
            size_bytes=1024,
            created_at=datetime.utcnow(),
        )
        assert plan.plan_id == VALID_ULID
        assert plan.intent_type == "schedule_meeting"
        assert plan.step_count == 5

    def test_invalid_plan_id_rejected(self):
        """Test non-ULID plan_id is rejected."""
        with pytest.raises(PydanticValidationError):
            PlanDB(
                plan_id="not-a-valid-ulid",
                canonical_json={},
                signature_data={},
                intent_type="test",
                step_count=1,
                plan_hash="a" * 64,
                size_bytes=100,
                created_at=datetime.utcnow(),
            )

    def test_lowercase_plan_id_rejected(self):
        """Test lowercase ULID characters are rejected."""
        with pytest.raises(PydanticValidationError):
            PlanDB(
                plan_id="01hx1234567890abcdefghjkmn",
                canonical_json={},
                signature_data={},
                intent_type="test",
                step_count=1,
                plan_hash="a" * 64,
                size_bytes=100,
                created_at=datetime.utcnow(),
            )

    def test_plan_too_many_steps_rejected(self):
        """Test plan with >100 steps is rejected."""
        with pytest.raises(PydanticValidationError):
            PlanDB(
                plan_id=VALID_ULID,
                canonical_json={},
                signature_data={},
                intent_type="test",
                step_count=101,
                plan_hash="a" * 64,
                size_bytes=100,
                created_at=datetime.utcnow(),
            )

    def test_plan_too_large_rejected(self):
        """Test plan >1MB is rejected."""
        with pytest.raises(PydanticValidationError):
            PlanDB(
                plan_id=VALID_ULID,
                canonical_json={},
                signature_data={},
                intent_type="test",
                step_count=1,
                plan_hash="a" * 64,
                size_bytes=1_048_577,  # 1MB + 1
                created_at=datetime.utcnow(),
            )

    def test_max_steps_accepted(self):
        """Test plan with exactly 100 steps is accepted."""
        plan = PlanDB(
            plan_id=VALID_ULID,
            canonical_json={},
            signature_data={},
            intent_type="test",
            step_count=100,
            plan_hash="a" * 64,
            size_bytes=100,
            created_at=datetime.utcnow(),
        )
        assert plan.step_count == 100


class TestPlanOutcomeDB:
    """Test PlanOutcomeDB model validation."""

    def test_success_outcome(self):
        """Test creating a successful outcome."""
        outcome = PlanOutcomeDB(
            plan_id=VALID_ULID,
            success=True,
            execution_start=datetime.utcnow(),
            execution_end=datetime.utcnow(),
            total_steps=5,
        )
        assert outcome.success is True
        assert outcome.error_type is None
        assert outcome.failed_step is None

    def test_failure_outcome(self):
        """Test creating a failure outcome with error details."""
        outcome = PlanOutcomeDB(
            plan_id=VALID_ULID,
            success=False,
            error_type="PROVIDER_ERROR",
            error_details={"message": "API timeout"},
            execution_start=datetime.utcnow(),
            execution_end=datetime.utcnow(),
            total_steps=5,
            failed_step=3,
            context_data={"retry_count": 2},
        )
        assert outcome.success is False
        assert outcome.error_type == "PROVIDER_ERROR"
        assert outcome.failed_step == 3


class TestPlanMetricsDB:
    """Test PlanMetricsDB model validation."""

    def test_valid_metrics(self):
        """Test creating valid metrics."""
        metrics = PlanMetricsDB(
            plan_id=VALID_ULID,
            preview_latency_ms=150,
            execute_latency_ms=800,
            step_timings={"step_1": 100, "step_2": 200},
            resource_usage={"memory_mb": 64},
        )
        assert metrics.execute_latency_ms == 800

    def test_metrics_without_preview(self):
        """Test metrics with null preview latency."""
        metrics = PlanMetricsDB(
            plan_id=VALID_ULID,
            execute_latency_ms=500,
        )
        assert metrics.preview_latency_ms is None


class TestStorePlanRequest:
    """Test StorePlanRequest validation."""

    def test_valid_request(self):
        """Test valid store plan request."""
        request = StorePlanRequest(
            plan={"plan_id": VALID_ULID, "graph": [], "meta": {"intent_type": "test"}},
            signature={"algorithm": "ed25519", "public_key": "abc", "signature_hex": "def"},
            outcome={
                "success": True,
                "execution_start": "2025-01-01T00:00:00Z",
                "execution_end": "2025-01-01T00:01:00Z",
                "total_steps": 5,
            },
            metrics={"execute_latency_ms": 500},
        )
        assert request.plan["plan_id"] == VALID_ULID

    def test_missing_plan_fields_rejected(self):
        """Test request with missing required plan fields."""
        with pytest.raises(PydanticValidationError):
            StorePlanRequest(
                plan={"plan_id": VALID_ULID},  # Missing graph and meta
                signature={"algorithm": "ed25519"},
                outcome={"success": True},
                metrics={"execute_latency_ms": 500},
            )

    def test_serialization_roundtrip(self):
        """Test request serialization roundtrip."""
        request = StorePlanRequest(
            plan={"plan_id": VALID_ULID, "graph": [{"step": 1}], "meta": {"intent_type": "test"}},
            signature={"algorithm": "ed25519", "public_key": "abc", "signature_hex": "def"},
            outcome={
                "success": True,
                "execution_start": "2025-01-01T00:00:00Z",
                "execution_end": "2025-01-01T00:01:00Z",
                "total_steps": 1,
            },
            metrics={"execute_latency_ms": 100},
        )
        serialized = request.model_dump()
        deserialized = StorePlanRequest.model_validate(serialized)
        assert deserialized.plan["plan_id"] == VALID_ULID


class TestStorePlanResponse:
    """Test StorePlanResponse model."""

    def test_response_serialization(self):
        """Test response serialization roundtrip."""
        now = datetime.utcnow()
        response = StorePlanResponse(
            plan_id=VALID_ULID,
            stored_at=now,
        )
        serialized = response.model_dump()
        assert serialized["status"] == "ok"
        assert serialized["plan_id"] == VALID_ULID


class TestQueryPlansRequest:
    """Test QueryPlansRequest validation."""

    def test_valid_query(self):
        """Test valid query request."""
        query = QueryPlansRequest(
            intent_type="schedule_meeting",
            success_threshold=0.8,
            limit=20,
            recency_days=30,
        )
        assert query.intent_type == "schedule_meeting"

    def test_negative_limit_rejected(self):
        """Test negative limit is rejected."""
        with pytest.raises(PydanticValidationError):
            QueryPlansRequest(
                intent_type="test",
                limit=-1,
            )

    def test_zero_limit_rejected(self):
        """Test zero limit is rejected."""
        with pytest.raises(PydanticValidationError):
            QueryPlansRequest(
                intent_type="test",
                limit=0,
            )

    def test_excessive_limit_rejected(self):
        """Test limit >1000 is rejected."""
        with pytest.raises(PydanticValidationError):
            QueryPlansRequest(
                intent_type="test",
                limit=1001,
            )

    def test_invalid_intent_type_rejected(self):
        """Test intent type with special characters is rejected."""
        with pytest.raises(PydanticValidationError):
            QueryPlansRequest(
                intent_type="invalid type!",
            )

    def test_empty_intent_type_rejected(self):
        """Test empty intent type is rejected."""
        with pytest.raises(PydanticValidationError):
            QueryPlansRequest(
                intent_type="",
            )

    def test_defaults_applied(self):
        """Test default values are applied."""
        query = QueryPlansRequest(intent_type="test")
        assert query.success_threshold == 0.7
        assert query.limit == 50
        assert query.recency_days is None


class TestErrorClasses:
    """Test error class hierarchy and attributes."""

    def test_all_errors_inherit_from_base(self):
        """Test all error classes inherit from PlanLibraryError."""
        assert issubclass(DuplicatePlanError, PlanLibraryError)
        assert issubclass(PlanTooLargeError, PlanLibraryError)
        assert issubclass(InvalidQueryError, PlanLibraryError)
        assert issubclass(PlanNotFoundError, PlanLibraryError)

    def test_duplicate_plan_error_attributes(self):
        """Test DuplicatePlanError has required attributes."""
        error = DuplicatePlanError(plan_id=VALID_ULID)
        assert error.plan_id == VALID_ULID
        assert VALID_ULID in str(error)

    def test_plan_too_large_error_attributes(self):
        """Test PlanTooLargeError has required attributes."""
        error = PlanTooLargeError(plan_id=VALID_ULID, reason="exceeds 100 steps")
        assert error.plan_id == VALID_ULID
        assert error.reason == "exceeds 100 steps"

    def test_invalid_query_error_attributes(self):
        """Test InvalidQueryError has required attributes."""
        error = InvalidQueryError(reason="negative limit")
        assert error.reason == "negative limit"

    def test_plan_not_found_error_attributes(self):
        """Test PlanNotFoundError has required attributes."""
        error = PlanNotFoundError(plan_id=VALID_ULID)
        assert error.plan_id == VALID_ULID


class TestResponseModels:
    """Test response wrapper models."""

    def test_error_response(self):
        """Test ErrorResponse serialization."""
        response = ErrorResponse(
            error_code="DUPLICATE_PLAN_ID",
            message="Plan with this ID already exists",
            details={"plan_id": VALID_ULID},
        )
        serialized = response.model_dump()
        assert serialized["status"] == "error"
        assert serialized["error_code"] == "DUPLICATE_PLAN_ID"

    def test_success_response(self):
        """Test SuccessResponse with tier 3."""
        response = SuccessResponse(
            data={"plan_id": VALID_ULID},
            tier=3,
        )
        assert response.tier == 3
        assert response.status == "ok"


class TestCanonicalizeAndHash:
    """Test plan canonicalization and hashing utilities."""

    def test_canonicalize_sorts_keys(self):
        """Test canonicalize produces sorted keys."""
        plan = {"zebra": 1, "alpha": 2, "middle": 3}
        canonical = canonicalize_plan(plan)
        parsed = json.loads(canonical)
        keys = list(parsed.keys())
        assert keys == sorted(keys)

    def test_canonicalize_no_whitespace(self):
        """Test canonicalize produces no extra whitespace."""
        plan = {"key": "value", "nested": {"a": 1}}
        canonical = canonicalize_plan(plan)
        assert " " not in canonical
        assert "\n" not in canonical

    def test_canonicalize_deterministic(self):
        """Test same input always produces same output."""
        plan = {"b": 2, "a": 1, "c": {"z": 26, "y": 25}}
        result1 = canonicalize_plan(plan)
        result2 = canonicalize_plan(plan)
        assert result1 == result2

    def test_compute_hash_deterministic(self):
        """Test same canonical JSON always produces same hash."""
        canonical = '{"a":1,"b":2}'
        hash1 = compute_plan_hash(canonical)
        hash2 = compute_plan_hash(canonical)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_different_input_different_hash(self):
        """Test different inputs produce different hashes."""
        hash1 = compute_plan_hash('{"a":1}')
        hash2 = compute_plan_hash('{"a":2}')
        assert hash1 != hash2

    def test_canonicalize_then_hash_deterministic(self):
        """Test full pipeline is deterministic."""
        plan = {"z": [3, 2, 1], "a": {"nested": True}}
        hash1 = compute_plan_hash(canonicalize_plan(plan))
        hash2 = compute_plan_hash(canonicalize_plan(plan))
        assert hash1 == hash2


class TestPlanPattern:
    """Test PlanPattern model."""

    def test_valid_pattern(self):
        """Test creating valid plan pattern."""
        pattern = PlanPattern(
            plan_id=VALID_ULID,
            intent_type="schedule_meeting",
            success_rate=0.85,
            avg_execution_time_ms=1200.0,
            steps_count=6,
            pattern_summary="Fetch calendars -> Find overlap -> Book",
        )
        assert pattern.success_rate == 0.85

    def test_confidence_range_validation(self):
        """Test success_rate must be between 0.0 and 1.0."""
        with pytest.raises(PydanticValidationError):
            PlanPattern(
                plan_id=VALID_ULID,
                intent_type="test",
                success_rate=1.5,
                avg_execution_time_ms=100.0,
                steps_count=1,
                pattern_summary="test",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
