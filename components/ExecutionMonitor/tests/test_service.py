"""
ExecutionMonitor service tests -- TrackerService and MonitorService.

Tests async service logic: register/progress/complete flows,
error suppression, stuck detection, timeout detection, notification flow,
poll loop lifecycle.
~40 tests.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from components.ExecutionMonitor.domain.models import TrackerRecord
from components.ExecutionMonitor.service.monitor_service import MonitorService
from components.ExecutionMonitor.service.tracker_service import TrackerService
from components.ExecutionMonitor.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_PLAN_ID_2,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
    FakeNotifier,
    FakeTrackerDB,
)

# ---------------------------------------------------------------------------
# TrackerService tests
# ---------------------------------------------------------------------------


class TestTrackerServiceRegister:
    """TrackerService.register() tests."""

    @pytest.mark.asyncio
    async def test_register_creates_record(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record is not None
        assert record.status == "running"
        assert record.total_steps == 5
        assert record.completed_steps == 0

    @pytest.mark.asyncio
    async def test_register_sets_timestamps(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 3)
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.started_at is not None
        assert record.last_progress_at is not None

    @pytest.mark.asyncio
    async def test_register_nonfatal_on_db_failure(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        fake_db.set_should_fail(True)
        # Must not raise
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)

    @pytest.mark.asyncio
    async def test_register_zero_steps(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 0)
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.total_steps == 0


class TestTrackerServiceProgress:
    """TrackerService.report_progress() tests."""

    @pytest.mark.asyncio
    async def test_progress_updates_steps(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        await tracker_service.report_progress(SAMPLE_PLAN_ID, 3)
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.completed_steps == 3

    @pytest.mark.asyncio
    async def test_progress_updates_last_progress_at(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        before = fake_db.get_record(SAMPLE_PLAN_ID).last_progress_at
        await asyncio.sleep(0.01)
        await tracker_service.report_progress(SAMPLE_PLAN_ID, 1)
        after = fake_db.get_record(SAMPLE_PLAN_ID).last_progress_at
        assert after >= before

    @pytest.mark.asyncio
    async def test_progress_nonfatal_on_db_failure(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        fake_db.set_should_fail(True)
        await tracker_service.report_progress(SAMPLE_PLAN_ID, 3)

    @pytest.mark.asyncio
    async def test_progress_no_record_nonfatal(self, tracker_service: TrackerService):
        # No record exists — should not raise
        await tracker_service.report_progress("01NONEXISTENT000000000000AB", 1)

    @pytest.mark.asyncio
    async def test_progress_multiple_updates(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 10)
        await tracker_service.report_progress(SAMPLE_PLAN_ID, 3)
        await tracker_service.report_progress(SAMPLE_PLAN_ID, 7)
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.completed_steps == 7


class TestTrackerServiceComplete:
    """TrackerService.complete() tests."""

    @pytest.mark.asyncio
    async def test_complete_success(self, tracker_service: TrackerService, fake_db: FakeTrackerDB):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        await tracker_service.complete(SAMPLE_PLAN_ID, success=True)
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.status == "completed"
        assert record.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_failure(self, tracker_service: TrackerService, fake_db: FakeTrackerDB):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        await tracker_service.complete(
            SAMPLE_PLAN_ID,
            success=False,
            error_type="step_failure",
            error_details={"step": 3},
        )
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.status == "failed"
        assert record.error_type == "step_failure"
        assert record.error_details == {"step": 3}

    @pytest.mark.asyncio
    async def test_complete_nonfatal_on_db_failure(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        fake_db.set_should_fail(True)
        await tracker_service.complete(SAMPLE_PLAN_ID, success=True)

    @pytest.mark.asyncio
    async def test_complete_no_record_nonfatal(self, tracker_service: TrackerService):
        await tracker_service.complete("01NONEXISTENT000000000000AB", success=True)

    @pytest.mark.asyncio
    async def test_complete_already_completed_noop(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        await tracker_service.complete(SAMPLE_PLAN_ID, success=True)
        # Second complete is a no-op (status is no longer 'running')
        await tracker_service.complete(SAMPLE_PLAN_ID, success=False, error_type="late_failure")
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.status == "completed"  # Still completed, not overwritten


# ---------------------------------------------------------------------------
# MonitorService stuck detection tests
# ---------------------------------------------------------------------------


class TestMonitorServiceStuck:
    """MonitorService stuck execution detection."""

    @pytest.mark.asyncio
    async def test_detects_stuck_execution(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
        stuck_record: TrackerRecord,
    ):
        fake_db.inject_record(stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.status == "stuck"
        assert record.error_type == "infrastructure_stuck"

    @pytest.mark.asyncio
    async def test_stuck_sends_notification(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
        stuck_record: TrackerRecord,
    ):
        fake_db.inject_record(stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        assert len(fake_notifier.notifications) == 1
        assert fake_notifier.notifications[0].failure_type == "stuck"

    @pytest.mark.asyncio
    async def test_stuck_marks_notified(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
        stuck_record: TrackerRecord,
    ):
        fake_db.inject_record(stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.notification_sent is True

    @pytest.mark.asyncio
    async def test_healthy_execution_not_stuck(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
        healthy_record: TrackerRecord,
    ):
        fake_db.inject_record(healthy_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.status == "running"
        assert len(fake_notifier.notifications) == 0

    @pytest.mark.asyncio
    async def test_already_notified_no_duplicate(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
        already_notified_stuck_record: TrackerRecord,
    ):
        fake_db.inject_record(already_notified_stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        assert len(fake_notifier.notifications) == 0


# ---------------------------------------------------------------------------
# MonitorService timeout detection tests
# ---------------------------------------------------------------------------


class TestMonitorServiceTimeout:
    """MonitorService time budget enforcement."""

    @pytest.mark.asyncio
    async def test_detects_timeout(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
        timeout_record: TrackerRecord,
    ):
        fake_db.inject_record(timeout_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.status == "timeout"
        assert record.error_type == "time_budget_exceeded"

    @pytest.mark.asyncio
    async def test_timeout_sends_notification(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
        timeout_record: TrackerRecord,
    ):
        fake_db.inject_record(timeout_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        assert len(fake_notifier.notifications) == 1
        assert fake_notifier.notifications[0].failure_type == "timeout"

    @pytest.mark.asyncio
    async def test_within_budget_not_timed_out(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
    ):
        now = datetime.now(UTC)
        record = TrackerRecord(
            tracker_id=str(uuid4()),
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status="running",
            total_steps=10,
            completed_steps=5,
            started_at=now - timedelta(minutes=45),
            last_progress_at=now - timedelta(seconds=10),
        )
        fake_db.inject_record(record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        result = fake_db.get_record(SAMPLE_PLAN_ID)
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_timeout_takes_priority_over_stuck(
        self,
        fake_db: FakeTrackerDB,
        fake_notifier: FakeNotifier,
    ):
        """Execution that is both stuck AND over budget → timeout wins."""
        now = datetime.now(UTC)
        record = TrackerRecord(
            tracker_id=str(uuid4()),
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status="running",
            total_steps=10,
            completed_steps=3,
            started_at=now - timedelta(minutes=65),
            last_progress_at=now - timedelta(minutes=10),
        )
        fake_db.inject_record(record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        result = fake_db.get_record(SAMPLE_PLAN_ID)
        assert result.status == "timeout"
        assert len(fake_notifier.notifications) == 1
        assert fake_notifier.notifications[0].failure_type == "timeout"


# ---------------------------------------------------------------------------
# MonitorService lifecycle tests
# ---------------------------------------------------------------------------


class TestMonitorServiceLifecycle:
    """MonitorService run/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_flag(self):
        db = FakeTrackerDB()
        monitor = MonitorService(tracker_db=db, poll_interval_s=1)
        assert monitor._running is False
        monitor._running = True
        monitor.stop()
        assert monitor._running is False

    @pytest.mark.asyncio
    async def test_run_and_stop(self):
        db = FakeTrackerDB()
        monitor = MonitorService(tracker_db=db, poll_interval_s=0.05)
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.1)
        assert monitor._running is True
        monitor.stop()
        await asyncio.sleep(0.1)
        assert task.done() or not monitor._running
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_poll_error_does_not_crash(self):
        db = FakeTrackerDB()
        db.set_should_fail(True)
        monitor = MonitorService(tracker_db=db, poll_interval_s=0.05)
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.15)
        assert monitor._running is True  # Still running despite errors
        monitor.stop()
        await asyncio.sleep(0.1)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


# ---------------------------------------------------------------------------
# MonitorService notification failure handling
# ---------------------------------------------------------------------------


class TestMonitorServiceNotificationFailure:
    """MonitorService handles notifier exceptions gracefully."""

    @pytest.mark.asyncio
    async def test_notification_failure_still_marks_terminal(
        self,
        fake_db: FakeTrackerDB,
        stuck_record: TrackerRecord,
    ):
        notifier = FakeNotifier()
        notifier.set_should_fail(True)
        fake_db.inject_record(stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        # Execution is still marked terminal even though notification failed
        assert record.status == "stuck"

    @pytest.mark.asyncio
    async def test_notification_failure_does_not_mark_notified(
        self,
        fake_db: FakeTrackerDB,
        stuck_record: TrackerRecord,
    ):
        notifier = FakeNotifier()
        notifier.set_should_fail(True)
        fake_db.inject_record(stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()
        record = fake_db.get_record(SAMPLE_PLAN_ID)
        # notification_sent stays False since notifier failed
        assert record.notification_sent is False


# ---------------------------------------------------------------------------
# MonitorService multiple executions
# ---------------------------------------------------------------------------


class TestMonitorServiceMultipleExecutions:
    """MonitorService handles multiple concurrent executions."""

    @pytest.mark.asyncio
    async def test_processes_multiple_executions(
        self, fake_db: FakeTrackerDB, fake_notifier: FakeNotifier
    ):
        now = datetime.now(UTC)
        stuck = TrackerRecord(
            tracker_id=str(uuid4()),
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status="running",
            total_steps=5,
            completed_steps=2,
            started_at=now - timedelta(minutes=10),
            last_progress_at=now - timedelta(minutes=6),
        )
        healthy = TrackerRecord(
            tracker_id=str(uuid4()),
            plan_id=SAMPLE_PLAN_ID_2,
            user_id=SAMPLE_USER_ID,
            trace_id="trace-456",
            status="running",
            total_steps=3,
            completed_steps=1,
            started_at=now - timedelta(minutes=2),
            last_progress_at=now - timedelta(seconds=10),
        )
        fake_db.inject_record(stuck)
        fake_db.inject_record(healthy)

        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()

        # Stuck execution is marked terminal
        r1 = fake_db.get_record(SAMPLE_PLAN_ID)
        assert r1.status == "stuck"

        # Healthy execution is untouched
        r2 = fake_db.get_record(SAMPLE_PLAN_ID_2)
        assert r2.status == "running"

        # Only one notification sent
        assert len(fake_notifier.notifications) == 1

    @pytest.mark.asyncio
    async def test_completed_executions_not_polled(
        self, fake_db: FakeTrackerDB, fake_notifier: FakeNotifier
    ):
        """Completed records are not in active executions query."""
        now = datetime.now(UTC)
        completed = TrackerRecord(
            tracker_id=str(uuid4()),
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            status="completed",
            total_steps=5,
            completed_steps=5,
            started_at=now - timedelta(minutes=10),
            last_progress_at=now - timedelta(minutes=5),
            completed_at=now,
        )
        fake_db.inject_record(completed)

        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=fake_notifier,
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        await monitor._check_active_executions()

        record = fake_db.get_record(SAMPLE_PLAN_ID)
        assert record.status == "completed"
        assert len(fake_notifier.notifications) == 0
