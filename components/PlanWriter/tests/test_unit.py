"""
PlanWriter Unit Tests -- Domain Models and Fact Deriver

Covers PersistResult, BulkPersistResult, error classes, and derive_fact().

Reference: tasks.md T102, T201
"""

import json
from uuid import uuid4

import pytest

from components.PlanWriter.adapters.fact_deriver import (
    _build_action_summary,
    _build_entity_summary,
    _build_error_summary,
    derive_fact,
)
from components.PlanWriter.domain.models import (
    BulkPersistResult,
    FactDerivationError,
    PersistResult,
    PlanLibraryWriteError,
    PlanWriterError,
)
from components.PlanWriter.tests.conftest import SAMPLE_PLAN_HASH, SAMPLE_PLAN_ID
from shared.schemas.outcome import PlanOutcome
from shared.schemas.plan import Plan

# ── Phase 1: Domain Model Tests ──────────────────────────────────


# Helper to build a minimal Plan for tests
def _make_plan(
    plan_id: str = SAMPLE_PLAN_ID,
    intent_type: str = "book_flight",
    entities: dict | None = None,
) -> Plan:
    """Build a minimal Plan model for testing."""
    return Plan(
        plan_id=plan_id,
        intent={
            "intent": intent_type,
            "entities": entities or {},
            "constraints": {},
            "tz": "America/Chicago",
            "user_id": "00000000-0000-0000-0000-000000000001",
        },
        graph=[
            {
                "step": 1,
                "mode": "interactive",
                "role": "Fetcher",
                "uses": "test.api",
                "call": "test_call",
                "args": {},
            },
        ],
        constraints={},
        plugins=[],
        meta={
            "created_at": "2026-03-19T10:00:00Z",
            "author": "planner@system",
            "canonical_hash": f"sha256:{SAMPLE_PLAN_HASH}",
        },
    )


def _make_outcome(
    success: bool = True,
    error_type: str | None = None,
    failed_step: int | None = None,
) -> PlanOutcome:
    """Build a minimal PlanOutcome for testing."""
    return PlanOutcome(
        success=success,
        error_type=error_type,
        error_details={"reason": "test"} if error_type else None,
        execution_start="2026-03-19T10:00:00Z",
        execution_end="2026-03-19T10:00:01Z",
        total_steps=5,
        failed_step=failed_step,
    )


class TestDomainModels:
    """Test PersistResult, BulkPersistResult, and error classes."""

    def test_persist_result_valid(self):
        """PersistResult accepts valid 26-char plan_id."""
        result = PersistResult(
            plan_id=SAMPLE_PLAN_ID,
            status="ok",
        )
        assert result.plan_id == SAMPLE_PLAN_ID
        assert result.fact_id is None
        assert result.embedding_stored is False
        assert result.errors == []

    def test_persist_result_invalid_plan_id_too_short(self):
        """PersistResult rejects plan_id shorter than 26 chars."""
        with pytest.raises(Exception):
            PersistResult(plan_id="short", status="ok")

    def test_persist_result_invalid_plan_id_too_long(self):
        """PersistResult rejects plan_id longer than 26 chars."""
        with pytest.raises(Exception):
            PersistResult(plan_id="A" * 27, status="ok")

    def test_persist_result_with_all_fields(self):
        """PersistResult with all fields populated."""
        fact_id = uuid4()
        result = PersistResult(
            plan_id=SAMPLE_PLAN_ID,
            fact_id=fact_id,
            embedding_stored=True,
            status="partial",
            errors=["History failed"],
        )
        assert result.fact_id == fact_id
        assert result.embedding_stored is True
        assert result.status == "partial"
        assert len(result.errors) == 1

    def test_persist_result_serializes_to_dict(self):
        """PersistResult serialization matches SPEC output format."""
        result = PersistResult(
            plan_id=SAMPLE_PLAN_ID,
            status="ok",
            embedding_stored=True,
        )
        data = result.model_dump()
        assert "plan_id" in data
        assert "fact_id" in data
        assert "embedding_stored" in data
        assert "status" in data
        assert "errors" in data

    def test_persist_result_json_roundtrip(self):
        """PersistResult JSON serialization roundtrip."""
        result = PersistResult(
            plan_id=SAMPLE_PLAN_ID,
            status="ok",
        )
        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["plan_id"] == SAMPLE_PLAN_ID
        assert parsed["status"] == "ok"

    def test_bulk_persist_result_aggregation(self):
        """BulkPersistResult correctly tracks summary counts."""
        results = [
            PersistResult(plan_id=SAMPLE_PLAN_ID, status="ok"),
            PersistResult(plan_id=SAMPLE_PLAN_ID, status="partial"),
        ]
        bulk = BulkPersistResult(
            results=results,
            total=2,
            succeeded=1,
            partial=1,
            failed=0,
        )
        assert bulk.total == 2
        assert bulk.succeeded == 1
        assert bulk.partial == 1
        assert bulk.failed == 0
        assert len(bulk.results) == 2

    def test_plan_writer_error_base(self):
        """PlanWriterError is base exception."""
        err = PlanWriterError("test")
        assert isinstance(err, Exception)

    def test_plan_library_write_error(self):
        """PlanLibraryWriteError stores plan_id and reason."""
        err = PlanLibraryWriteError("PLANID" + "0" * 20, "db down")
        assert err.plan_id == "PLANID" + "0" * 20
        assert err.reason == "db down"
        assert "PLANID" in str(err)
        assert "db down" in str(err)
        assert isinstance(err, PlanWriterError)

    def test_fact_derivation_error(self):
        """FactDerivationError stores plan_id and reason."""
        err = FactDerivationError("PLANID" + "0" * 20, "missing field")
        assert err.plan_id == "PLANID" + "0" * 20
        assert err.reason == "missing field"
        assert "PLANID" in str(err)
        assert "missing field" in str(err)
        assert isinstance(err, PlanWriterError)


# ── Phase 2: Fact Deriver Tests ──────────────────────────────────


class TestFactDeriver:
    """Test derive_fact() and helper functions."""

    def test_derive_fact_success_with_entities(self):
        """Successful plan with entities produces success template."""
        plan = _make_plan(
            intent_type="book_flight",
            entities={"destination": "NYC", "airline": "Delta"},
        )
        outcome = _make_outcome(success=True)
        result = derive_fact(plan, outcome)

        assert result.outcome is True
        assert result.source_plan_id == SAMPLE_PLAN_ID
        assert result.intent_type == "book_flight"
        assert result.entities == {"destination": "NYC", "airline": "Delta"}
        assert "NYC" in result.fact_text
        assert "Delta" in result.fact_text
        assert result.ttl_days == 30

    def test_derive_fact_failure_with_error(self):
        """Failed plan produces failure template with error details."""
        plan = _make_plan(intent_type="book_flight")
        outcome = _make_outcome(
            success=False,
            error_type="timeout",
            failed_step=3,
        )
        result = derive_fact(plan, outcome)

        assert result.outcome is False
        assert "Failed" in result.fact_text
        assert "timeout" in result.fact_text
        assert "step 3" in result.fact_text

    def test_derive_fact_no_entities(self):
        """Plan with no entities uses fallback template."""
        plan = _make_plan(intent_type="check_status", entities={})
        outcome = _make_outcome(success=True)
        result = derive_fact(plan, outcome)

        assert result.entities == {}
        assert "check_status" in result.fact_text

    def test_derive_fact_intent_from_plan_intent(self):
        """Intent type extracted from plan.intent.intent."""
        plan = _make_plan(intent_type="schedule_meeting")
        outcome = _make_outcome(success=True)
        result = derive_fact(plan, outcome)
        assert result.intent_type == "schedule_meeting"

    def test_derive_fact_send_email(self):
        """Intent type extracted correctly for send_email."""
        plan = _make_plan(intent_type="send_email")
        outcome = _make_outcome(success=True)
        result = derive_fact(plan, outcome)
        assert result.intent_type == "send_email"

    def test_derive_fact_deterministic(self):
        """Same inputs always produce identical StoreFactRequest."""
        plan = _make_plan(
            intent_type="book_flight",
            entities={"destination": "NYC"},
        )
        outcome = _make_outcome(success=True)
        r1 = derive_fact(plan, outcome)
        r2 = derive_fact(plan, outcome)
        assert r1.fact_text == r2.fact_text
        assert r1.intent_type == r2.intent_type
        assert r1.entities == r2.entities
        assert r1.outcome == r2.outcome
        assert r1.source_plan_id == r2.source_plan_id

    def test_derive_fact_no_pii_in_fact_text(self):
        """fact_text does not contain raw plan JSON."""
        plan = _make_plan(intent_type="book_flight")
        outcome = _make_outcome(success=True)
        result = derive_fact(plan, outcome)
        raw_json = json.dumps(plan.model_dump())
        assert raw_json not in result.fact_text

    def test_derive_fact_empty_entities(self):
        """Empty entities dict produces fallback template."""
        plan = _make_plan(intent_type="check_status", entities={})
        outcome = _make_outcome(success=True)
        result = derive_fact(plan, outcome)
        assert result.entities == {}

    def test_derive_fact_missing_outcome_fields_defaults(self):
        """Outcome with success=False defaults."""
        plan = _make_plan(intent_type="test")
        outcome = _make_outcome(success=False)
        result = derive_fact(plan, outcome)
        assert result.outcome is False

    def test_build_entity_summary_multiple(self):
        """Entity summary with multiple entities."""
        entities = {"destination": "NYC", "airline": "Delta"}
        summary = _build_entity_summary(entities)
        assert "NYC" in summary
        assert "Delta" in summary

    def test_build_entity_summary_empty(self):
        """Empty entities produce empty summary."""
        assert _build_entity_summary({}) == ""

    def test_build_action_summary_known_verb(self):
        """Known verb prefix is converted to past tense."""
        assert "Booked" in _build_action_summary("book_flight")

    def test_build_action_summary_unknown_verb(self):
        """Unknown verb gets generic past tense."""
        summary = _build_action_summary("zap_things")
        assert "Zaped" in summary or "zap" in summary.lower()

    def test_build_error_summary_with_step(self):
        """Error summary includes step number."""
        outcome = _make_outcome(success=False, error_type="timeout", failed_step=3)
        assert _build_error_summary(outcome) == "timeout at step 3"

    def test_build_error_summary_without_step(self):
        """Error summary without step number."""
        outcome = _make_outcome(success=False, error_type="api_error")
        assert _build_error_summary(outcome) == "api_error"
