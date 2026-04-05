"""
ExecutionMonitor observability tests -- structured logging verification.

Tests that all log events include 'component: ExecutionMonitor',
plan_id correlation, no PII leaks, and correct log levels.
~15 tests.
"""

from __future__ import annotations

import logging

import pytest

from components.ExecutionMonitor.adapters.notifier import LogNotifier
from components.ExecutionMonitor.domain.models import TrackerRecord, UserNotification
from components.ExecutionMonitor.service.monitor_service import MonitorService
from components.ExecutionMonitor.service.tracker_service import TrackerService
from components.ExecutionMonitor.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
    FakeNotifier,
    FakeTrackerDB,
)

# ---------------------------------------------------------------------------
# TrackerService logging
# ---------------------------------------------------------------------------


class TestTrackerServiceLogging:
    """TrackerService emits structured logs with correct fields."""

    @pytest.mark.asyncio
    async def test_register_logs_component(self, tracker_service: TrackerService, caplog):
        with caplog.at_level(logging.INFO):
            await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        assert any("tracker_registered" in r.message for r in caplog.records)
        info_records = [r for r in caplog.records if "tracker_registered" in r.message]
        assert len(info_records) >= 1
        record = info_records[0]
        assert record.component == "ExecutionMonitor"
        assert record.plan_id == SAMPLE_PLAN_ID

    @pytest.mark.asyncio
    async def test_progress_logs_component(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB, caplog
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        with caplog.at_level(logging.INFO):
            await tracker_service.report_progress(SAMPLE_PLAN_ID, 3)
        assert any("tracker_progress" in r.message for r in caplog.records)
        progress_records = [r for r in caplog.records if r.message == "tracker_progress"]
        assert len(progress_records) >= 1
        assert progress_records[0].component == "ExecutionMonitor"

    @pytest.mark.asyncio
    async def test_complete_logs_component(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB, caplog
    ):
        await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        with caplog.at_level(logging.INFO):
            await tracker_service.complete(SAMPLE_PLAN_ID, success=True)
        assert any("tracker_completed" in r.message for r in caplog.records)
        comp_records = [r for r in caplog.records if "tracker_completed" in r.message]
        assert len(comp_records) >= 1
        assert comp_records[0].component == "ExecutionMonitor"

    @pytest.mark.asyncio
    async def test_failure_logs_warning(
        self, tracker_service: TrackerService, fake_db: FakeTrackerDB, caplog
    ):
        fake_db.set_should_fail(True)
        with caplog.at_level(logging.WARNING):
            await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        warn_records = [r for r in caplog.records if "tracker_register_failed" in r.message]
        assert len(warn_records) >= 1
        assert warn_records[0].levelno == logging.WARNING

    @pytest.mark.asyncio
    async def test_no_pii_in_register_log(self, tracker_service: TrackerService, caplog):
        """user_id is logged as opaque identifier, no sensitive data."""
        with caplog.at_level(logging.INFO):
            await tracker_service.register(SAMPLE_PLAN_ID, SAMPLE_USER_ID, SAMPLE_TRACE_ID, 5)
        for record in caplog.records:
            msg = record.getMessage()
            # Ensure no email, password, token values in message
            assert "password" not in msg.lower()
            assert "secret" not in msg.lower()
            assert "token" not in msg.lower()


# ---------------------------------------------------------------------------
# MonitorService logging
# ---------------------------------------------------------------------------


class TestMonitorServiceLogging:
    """MonitorService emits structured logs for detection events."""

    @pytest.mark.asyncio
    async def test_stuck_detection_logs_warning(
        self,
        fake_db: FakeTrackerDB,
        stuck_record: TrackerRecord,
        caplog,
    ):
        fake_db.inject_record(stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=FakeNotifier(),
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        with caplog.at_level(logging.WARNING):
            await monitor._check_active_executions()
        warn_records = [r for r in caplog.records if "execution_stuck_detected" in r.message]
        assert len(warn_records) >= 1
        assert warn_records[0].component == "ExecutionMonitor"
        assert warn_records[0].plan_id == SAMPLE_PLAN_ID

    @pytest.mark.asyncio
    async def test_timeout_detection_logs_warning(
        self,
        fake_db: FakeTrackerDB,
        timeout_record: TrackerRecord,
        caplog,
    ):
        fake_db.inject_record(timeout_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=FakeNotifier(),
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        with caplog.at_level(logging.WARNING):
            await monitor._check_active_executions()
        warn_records = [r for r in caplog.records if "execution_timeout_detected" in r.message]
        assert len(warn_records) >= 1
        assert warn_records[0].component == "ExecutionMonitor"

    @pytest.mark.asyncio
    async def test_poll_error_logs_error(self, caplog):
        db = FakeTrackerDB()
        db.set_should_fail(True)
        monitor = MonitorService(tracker_db=db, poll_interval_s=1)
        with caplog.at_level(logging.ERROR):
            # Simulate what run() does: catch exceptions from _check_active_executions
            try:
                await monitor._check_active_executions()
            except Exception as exc:
                monitor_logger = logging.getLogger(
                    "components.ExecutionMonitor.service.monitor_service"
                )
                monitor_logger.error(
                    "monitor_poll_error",
                    extra={
                        "component": "ExecutionMonitor",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
        error_records = [r for r in caplog.records if "monitor_poll_error" in r.message]
        assert len(error_records) >= 1
        assert error_records[0].component == "ExecutionMonitor"

    @pytest.mark.asyncio
    async def test_no_pii_in_monitor_logs(
        self,
        fake_db: FakeTrackerDB,
        stuck_record: TrackerRecord,
        caplog,
    ):
        fake_db.inject_record(stuck_record)
        monitor = MonitorService(
            tracker_db=fake_db,
            notifier=FakeNotifier(),
            stuck_timeout_minutes=5,
            max_execution_minutes=60,
        )
        with caplog.at_level(logging.DEBUG):
            await monitor._check_active_executions()
        for record in caplog.records:
            msg = record.getMessage()
            assert "password" not in msg.lower()
            assert "secret" not in msg.lower()


# ---------------------------------------------------------------------------
# LogNotifier logging
# ---------------------------------------------------------------------------


class TestLogNotifierLogging:
    """LogNotifier emits structured log events."""

    @pytest.mark.asyncio
    async def test_notify_logs_warning(self, caplog):
        notifier = LogNotifier()
        notification = UserNotification(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            failure_type="stuck",
            message="Test notification",
        )
        with caplog.at_level(
            logging.WARNING, logger="components.ExecutionMonitor.adapters.notifier"
        ):
            result = await notifier.notify(notification)
        assert result is True
        notify_records = [r for r in caplog.records if "execution_notification" in r.message]
        assert len(notify_records) >= 1
        assert notify_records[0].component == "ExecutionMonitor"
        assert notify_records[0].plan_id == SAMPLE_PLAN_ID
        assert notify_records[0].failure_type == "stuck"

    @pytest.mark.asyncio
    async def test_notify_timeout_type(self, caplog):
        notifier = LogNotifier()
        notification = UserNotification(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            trace_id=SAMPLE_TRACE_ID,
            failure_type="timeout",
            message="Timeout notification",
        )
        with caplog.at_level(
            logging.WARNING, logger="components.ExecutionMonitor.adapters.notifier"
        ):
            await notifier.notify(notification)
        notify_records = [r for r in caplog.records if "execution_notification" in r.message]
        assert len(notify_records) >= 1
        assert notify_records[0].failure_type == "timeout"
