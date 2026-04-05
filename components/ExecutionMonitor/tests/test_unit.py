"""
ExecutionMonitor unit tests -- domain model validation.

Tests Pydantic models, exception hierarchy, field constraints,
and model serialization.
~25 tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

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
# TrackerRecord validation
# ---------------------------------------------------------------------------


class TestTrackerRecord:
    """TrackerRecord Pydantic model validation."""

    def test_accepts_valid_record(self):
        record = TrackerRecord(
            tracker_id="550e8400-e29b-41d4-a716-446655440000",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status="running",
            total_steps=5,
            completed_steps=2,
        )
        assert record.plan_id == SAMPLE_PLAN_ID
        assert record.status == "running"
        assert record.total_steps == 5
        assert record.completed_steps == 2

    def test_defaults(self):
        record = TrackerRecord(
            tracker_id="550e8400-e29b-41d4-a716-446655440000",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
        )
        assert record.status == "running"
        assert record.total_steps == 0
        assert record.completed_steps == 0
        assert record.notification_sent is False
        assert record.error_type is None
        assert record.error_details is None
        assert record.completed_at is None

    def test_rejects_plan_id_too_short(self):
        with pytest.raises(ValidationError):
            TrackerRecord(
                tracker_id="some-id",
                plan_id="short",
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )

    def test_rejects_plan_id_too_long(self):
        with pytest.raises(ValidationError):
            TrackerRecord(
                tracker_id="some-id",
                plan_id="A" * 27,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
            )

    def test_rejects_empty_user_id(self):
        with pytest.raises(ValidationError):
            TrackerRecord(
                tracker_id="some-id",
                plan_id=SAMPLE_PLAN_ID,
                user_id="",
                trace_id=SAMPLE_TRACE_ID,
            )

    def test_rejects_empty_trace_id(self):
        with pytest.raises(ValidationError):
            TrackerRecord(
                tracker_id="some-id",
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                trace_id="",
            )

    def test_rejects_negative_total_steps(self):
        with pytest.raises(ValidationError):
            TrackerRecord(
                tracker_id="some-id",
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
                total_steps=-1,
            )

    def test_rejects_negative_completed_steps(self):
        with pytest.raises(ValidationError):
            TrackerRecord(
                tracker_id="some-id",
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
                completed_steps=-1,
            )

    def test_accepts_error_details(self):
        record = TrackerRecord(
            tracker_id="some-id",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            error_type="infrastructure_stuck",
            error_details={"reason": "No progress"},
        )
        assert record.error_type == "infrastructure_stuck"
        assert record.error_details == {"reason": "No progress"}

    def test_accepts_timestamps(self):
        now = datetime.now(UTC)
        record = TrackerRecord(
            tracker_id="some-id",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            started_at=now,
            last_progress_at=now,
            completed_at=now,
        )
        assert record.started_at == now
        assert record.completed_at == now


# ---------------------------------------------------------------------------
# RegisterExecutionRequest validation
# ---------------------------------------------------------------------------


class TestRegisterExecutionRequest:
    """RegisterExecutionRequest validation."""

    def test_accepts_valid_request(self):
        req = RegisterExecutionRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            total_steps=5,
        )
        assert req.total_steps == 5

    def test_rejects_plan_id_too_short(self):
        with pytest.raises(ValidationError):
            RegisterExecutionRequest(
                plan_id="short",
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
                total_steps=5,
            )

    def test_rejects_negative_steps(self):
        with pytest.raises(ValidationError):
            RegisterExecutionRequest(
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
                total_steps=-1,
            )

    def test_accepts_zero_steps(self):
        req = RegisterExecutionRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            total_steps=0,
        )
        assert req.total_steps == 0


# ---------------------------------------------------------------------------
# ProgressUpdate validation
# ---------------------------------------------------------------------------


class TestProgressUpdate:
    """ProgressUpdate validation."""

    def test_accepts_valid_update(self):
        upd = ProgressUpdate(
            plan_id=SAMPLE_PLAN_ID,
            completed_steps=3,
        )
        assert upd.completed_steps == 3

    def test_rejects_negative_steps(self):
        with pytest.raises(ValidationError):
            ProgressUpdate(plan_id=SAMPLE_PLAN_ID, completed_steps=-1)


# ---------------------------------------------------------------------------
# CompleteExecutionRequest validation
# ---------------------------------------------------------------------------


class TestCompleteExecutionRequest:
    """CompleteExecutionRequest validation."""

    def test_accepts_success(self):
        req = CompleteExecutionRequest(
            plan_id=SAMPLE_PLAN_ID,
            success=True,
        )
        assert req.success is True
        assert req.error_type is None

    def test_accepts_failure_with_error(self):
        req = CompleteExecutionRequest(
            plan_id=SAMPLE_PLAN_ID,
            success=False,
            error_type="step_failure",
            error_details={"step": 3, "message": "timeout"},
        )
        assert req.success is False
        assert req.error_type == "step_failure"


# ---------------------------------------------------------------------------
# UserNotification validation
# ---------------------------------------------------------------------------


class TestUserNotification:
    """UserNotification validation."""

    def test_accepts_stuck_notification(self):
        notif = UserNotification(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            failure_type="stuck",
            total_steps=5,
            completed_steps=2,
            message="Execution stuck",
        )
        assert notif.failure_type == "stuck"

    def test_accepts_timeout_notification(self):
        notif = UserNotification(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            failure_type="timeout",
        )
        assert notif.failure_type == "timeout"

    def test_rejects_invalid_failure_type(self):
        with pytest.raises(ValidationError):
            UserNotification(
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                trace_id=SAMPLE_TRACE_ID,
                failure_type="unknown",
            )

    def test_default_message(self):
        notif = UserNotification(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            failure_type="stuck",
        )
        assert notif.message == ""


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptions:
    """Exception hierarchy and attributes."""

    def test_monitor_error_is_base(self):
        assert issubclass(TrackerNotFoundError, MonitorError)
        assert issubclass(TrackerDatabaseError, MonitorError)

    def test_tracker_not_found_error(self):
        err = TrackerNotFoundError(SAMPLE_PLAN_ID)
        assert err.plan_id == SAMPLE_PLAN_ID
        assert SAMPLE_PLAN_ID in str(err)

    def test_tracker_database_error(self):
        err = TrackerDatabaseError("connection refused")
        assert err.detail == "connection refused"
        assert "connection refused" in str(err)

    def test_monitor_error_catchable(self):
        with pytest.raises(MonitorError):
            raise TrackerNotFoundError(SAMPLE_PLAN_ID)

    def test_monitor_error_base_instantiation(self):
        err = MonitorError("generic error")
        assert str(err) == "generic error"
