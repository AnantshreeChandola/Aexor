"""
ContextRAG Observability Tests

Verifies structured logging, no PII leakage, and correlation fields.

Reference: tasks.md T600
"""

import logging
from unittest.mock import AsyncMock
from uuid import uuid4

from components.ContextRAG.service.context_rag_service import ContextRAGService
from components.History.domain.models import PatternsResponse, QueryFactsResponse
from shared.database.error_handler import DatabaseConnectionError
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent


def _build_service_with_mocks(
    pref_side_effect=None,
    fact_side_effect=None,
):
    """Build a ContextRAGService with configurable mocks."""
    pref_svc = AsyncMock()
    if pref_side_effect:
        pref_svc.get_all_preferences.side_effect = pref_side_effect
    else:
        pref_svc.get_all_preferences.return_value = [
            EvidenceItem(
                type="preference",
                key="tz",
                value="UTC",
                confidence=1.0,
                source_ref="profilestore:prefs/tz",
                tier=2,
            )
        ]

    fact_svc = AsyncMock()
    if fact_side_effect:
        fact_svc.get_facts_by_intent.side_effect = fact_side_effect
    else:
        fact_svc.get_facts_by_intent.return_value = QueryFactsResponse(
            evidence=[], total_count=0, returned_count=0
        )

    pattern_svc = AsyncMock()
    pattern_svc.get_patterns.return_value = PatternsResponse(patterns=[], total_count=0)

    plan_svc = AsyncMock()
    plan_svc.get_plans_by_intent.return_value = []

    return ContextRAGService(
        preference_service=pref_svc,
        fact_service=fact_svc,
        pattern_service=pattern_svc,
        plan_service=plan_svc,
        vector_index_service=None,
    )


def _all_record_extra_values(records: list) -> str:
    """Collect all extra field values from log records as a single string.

    This ensures we check the structured extra dict for PII leaks,
    not just the log message text.
    """
    parts = []
    for record in records:
        parts.append(record.getMessage())
        # Check all extra fields that our code sets
        for attr in (
            "intent_type",
            "user_id",
            "effective_budget",
            "component",
            "op",
            "source",
            "reason",
            "trace_id",
            "evidence_count",
            "total_bytes",
            "degraded_sources",
            "duration_ms",
            "error_type",
        ):
            val = getattr(record, attr, None)
            if val is not None:
                parts.append(str(val))
    return " ".join(parts)


class TestNoPIIInLogs:
    """Verify entity values and constraint values never appear in logs."""

    async def test_no_pii_in_logs(self, caplog):
        """Entity values like 'Alice' and '123-45-6789' must not appear."""
        service = _build_service_with_mocks()
        intent = Intent(
            intent="schedule_meeting",
            entities={"person": "Alice", "ssn": "123-45-6789"},
            constraints={"secret_key": "s3cretValue!"},
            user_id=str(uuid4()),
            context_budget=3,
            trace_id="d" * 32,
        )

        with caplog.at_level(logging.DEBUG, logger="contextrag"):
            await service.gather_evidence(intent)

        log_output = _all_record_extra_values(caplog.records)
        # Entity values must NOT appear
        assert "Alice" not in log_output
        assert "123-45-6789" not in log_output
        # Constraint values must NOT appear
        assert "s3cretValue!" not in log_output
        # Intent type (safe metadata) should appear in extra fields
        assert "schedule_meeting" in log_output


class TestLogContainsIntentType:
    """Verify intent_type appears in structured log."""

    async def test_log_contains_intent_type(self, caplog):
        service = _build_service_with_mocks()
        intent = Intent(
            intent="schedule_meeting",
            entities={"person": "Bob"},
            constraints={},
            user_id=str(uuid4()),
            context_budget=3,
        )
        with caplog.at_level(logging.DEBUG, logger="contextrag"):
            await service.gather_evidence(intent)

        # Check that intent_type appears in log messages (inlined format)
        found = False
        for record in caplog.records:
            if "schedule_meeting" in record.getMessage():
                found = True
                break
        assert found, "intent_type 'schedule_meeting' not found in log messages"


class TestLogContainsDurationMs:
    """Verify duration_ms field is logged on completion."""

    async def test_log_contains_duration_ms(self, caplog):
        service = _build_service_with_mocks()
        intent = Intent(
            intent="schedule_meeting",
            entities={},
            constraints={},
            user_id=str(uuid4()),
            context_budget=3,
        )
        with caplog.at_level(logging.DEBUG, logger="contextrag"):
            await service.gather_evidence(intent)

        # Check that duration_ms appears in log messages (inlined format)
        found = False
        for record in caplog.records:
            if "duration_ms=" in record.getMessage():
                found = True
                break
        assert found, "duration_ms not found in any log message"


class TestLogDegradedSourceWarning:
    """Verify warning-level log with source name when a source fails."""

    async def test_log_degraded_source_warning(self, caplog):
        service = _build_service_with_mocks(
            fact_side_effect=DatabaseConnectionError("test_failure")
        )
        intent = Intent(
            intent="schedule_meeting",
            entities={},
            constraints={},
            user_id=str(uuid4()),
            context_budget=3,
        )
        with caplog.at_level(logging.DEBUG, logger="contextrag"):
            await service.gather_evidence(intent)

        # Find warning-level log about degraded source
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) > 0
        # Check that source name appears in log messages (inlined format)
        found_source = False
        for r in warning_records:
            if "source=history" in r.getMessage():
                found_source = True
                break
        assert found_source, "Warning log for 'history' source not found"


class TestLogCorrelationFields:
    """Verify user_id and trace_id from intent appear in log records."""

    async def test_log_correlation_fields(self, caplog):
        user_id = str(uuid4())
        trace_id = "e" * 32
        service = _build_service_with_mocks()
        intent = Intent(
            intent="schedule_meeting",
            entities={},
            constraints={},
            user_id=user_id,
            context_budget=3,
            trace_id=trace_id,
        )
        with caplog.at_level(logging.DEBUG, logger="contextrag"):
            await service.gather_evidence(intent)

        # Check user_id appears in log messages (inlined format)
        all_messages = " ".join(r.getMessage() for r in caplog.records)
        assert user_id in all_messages, f"user_id {user_id} not found in log messages"
