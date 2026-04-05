"""
ExecutionMonitor contract tests -- schema conformance.

Tests TrackerRecord serialization, table-model alignment,
status enum coverage, and model round-trips.
~15 tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from components.ExecutionMonitor.adapters.notifier import LogNotifier, Notifier
from components.ExecutionMonitor.domain.models import (
    CompleteExecutionRequest,
    MonitorError,
    ProgressUpdate,
    RegisterExecutionRequest,
    TrackerDatabaseError,
    TrackerNotFoundError,
    TrackerRecord,
    UserNotification,
)
from components.ExecutionMonitor.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
)

# ---------------------------------------------------------------------------
# TrackerRecord serialization
# ---------------------------------------------------------------------------


class TestTrackerRecordSerialization:
    """TrackerRecord model_dump / model_validate round-trips."""

    def test_round_trip(self):
        now = datetime.now(UTC)
        record = TrackerRecord(
            tracker_id="550e8400-e29b-41d4-a716-446655440000",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status="running",
            total_steps=5,
            completed_steps=2,
            started_at=now,
            last_progress_at=now,
        )
        data = record.model_dump()
        restored = TrackerRecord.model_validate(data)
        assert restored.plan_id == record.plan_id
        assert restored.status == record.status
        assert restored.total_steps == record.total_steps

    def test_json_round_trip(self):
        now = datetime.now(UTC)
        record = TrackerRecord(
            tracker_id="550e8400-e29b-41d4-a716-446655440000",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status="completed",
            total_steps=3,
            completed_steps=3,
            started_at=now,
            last_progress_at=now,
            completed_at=now,
        )
        json_str = record.model_dump_json()
        restored = TrackerRecord.model_validate_json(json_str)
        assert restored.status == "completed"
        assert restored.completed_at is not None

    def test_dump_includes_all_fields(self):
        record = TrackerRecord(
            tracker_id="some-id",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        data = record.model_dump()
        expected_keys = {
            "tracker_id", "plan_id", "user_id", "trace_id", "status",
            "total_steps", "completed_steps", "error_type", "error_details",
            "notification_sent", "started_at", "last_progress_at", "completed_at",
        }
        assert set(data.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Status enum coverage
# ---------------------------------------------------------------------------


class TestStatusValues:
    """TrackerRecord supports all expected status values."""

    @pytest.mark.parametrize("status", ["running", "completed", "failed", "stuck", "timeout"])
    def test_valid_statuses(self, status: str):
        record = TrackerRecord(
            tracker_id="some-id",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status=status,
        )
        assert record.status == status


# ---------------------------------------------------------------------------
# Table-model alignment
# ---------------------------------------------------------------------------


class TestTableModelAlignment:
    """Verify TrackerRecord fields align with ExecutionTrackerTable columns."""

    def test_tracker_record_has_all_table_columns(self):
        """TrackerRecord must have fields for every execution_tracker column."""
        expected_columns = [
            "tracker_id", "plan_id", "user_id", "trace_id", "status",
            "total_steps", "completed_steps", "error_type", "error_details",
            "notification_sent", "started_at", "last_progress_at", "completed_at",
        ]
        model_fields = set(TrackerRecord.model_fields.keys())
        for col in expected_columns:
            assert col in model_fields, f"Missing field: {col}"

    def test_execution_tracker_table_exists(self):
        """ExecutionTrackerTable should be importable from shared models."""
        from shared.database.models import ExecutionTrackerTable
        assert ExecutionTrackerTable.__tablename__ == "execution_tracker"

    def test_table_has_expected_columns(self):
        from shared.database.models import ExecutionTrackerTable
        table_columns = {c.name for c in ExecutionTrackerTable.__table__.columns}
        expected = {
            "tracker_id", "plan_id", "user_id", "trace_id", "status",
            "total_steps", "completed_steps", "error_type", "error_details",
            "notification_sent", "started_at", "last_progress_at", "completed_at",
        }
        assert expected.issubset(table_columns)


# ---------------------------------------------------------------------------
# Request model serialization
# ---------------------------------------------------------------------------


class TestRequestModelSerialization:
    """Request/response models serialize correctly."""

    def test_register_request_round_trip(self):
        req = RegisterExecutionRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            total_steps=5,
        )
        data = req.model_dump()
        restored = RegisterExecutionRequest.model_validate(data)
        assert restored.plan_id == req.plan_id

    def test_progress_update_round_trip(self):
        upd = ProgressUpdate(plan_id=SAMPLE_PLAN_ID, completed_steps=3)
        data = upd.model_dump()
        restored = ProgressUpdate.model_validate(data)
        assert restored.completed_steps == 3

    def test_complete_request_round_trip(self):
        req = CompleteExecutionRequest(
            plan_id=SAMPLE_PLAN_ID,
            success=False,
            error_type="step_failure",
            error_details={"step": 3},
        )
        data = req.model_dump()
        restored = CompleteExecutionRequest.model_validate(data)
        assert restored.error_type == "step_failure"

    def test_notification_round_trip(self):
        notif = UserNotification(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            failure_type="stuck",
            message="test",
        )
        data = notif.model_dump()
        restored = UserNotification.model_validate(data)
        assert restored.failure_type == "stuck"


# ---------------------------------------------------------------------------
# Notifier protocol conformance
# ---------------------------------------------------------------------------


class TestNotifierProtocol:
    """LogNotifier conforms to Notifier protocol."""

    def test_log_notifier_is_notifier(self):
        assert isinstance(LogNotifier(), Notifier)

    def test_exception_hierarchy(self):
        assert issubclass(TrackerNotFoundError, MonitorError)
        assert issubclass(TrackerDatabaseError, MonitorError)
        assert issubclass(MonitorError, Exception)
