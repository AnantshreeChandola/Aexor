"""
MonitorService — Background Polling Watchdog

Polls the execution_tracker table for running executions and detects
infrastructure-level failures: stuck executions (no progress for 5+ min)
and time budget violations (60+ min total). Infrastructure failures are
terminal — no replay, user must start a new plan.

Reference: Project_HLD.md §13, SPEC 031 FR-004 through FR-009
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from ..adapters.notifier import LogNotifier, Notifier
from ..domain.models import TrackerRecord, UserNotification

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_POLL_INTERVAL_S = 30
DEFAULT_STUCK_TIMEOUT_MINUTES = 5
DEFAULT_MAX_EXECUTION_MINUTES = 60


class MonitorService:
    """Background watchdog that detects stuck/timed-out executions.

    Runs as an asyncio background task. Polls execution_tracker for
    running executions and evaluates each against stuck and time-budget
    thresholds.
    """

    def __init__(
        self,
        tracker_db: Any,
        notifier: Notifier | None = None,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
        stuck_timeout_minutes: int = DEFAULT_STUCK_TIMEOUT_MINUTES,
        max_execution_minutes: int = DEFAULT_MAX_EXECUTION_MINUTES,
    ) -> None:
        self._db = tracker_db
        self._notifier = notifier or LogNotifier()
        self._poll_interval_s = poll_interval_s
        self._stuck_timeout_minutes = stuck_timeout_minutes
        self._max_execution_minutes = max_execution_minutes
        self._running = False

    async def run(self) -> None:
        """Background polling loop. Runs until stop() is called."""
        self._running = True
        logger.info(
            "monitor_started",
            extra={
                "component": "ExecutionMonitor",
                "poll_interval_s": self._poll_interval_s,
                "stuck_timeout_minutes": self._stuck_timeout_minutes,
                "max_execution_minutes": self._max_execution_minutes,
            },
        )
        while self._running:
            try:
                await self._check_active_executions()
            except Exception as exc:
                logger.error(
                    "monitor_poll_error",
                    extra={
                        "component": "ExecutionMonitor",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
            await asyncio.sleep(self._poll_interval_s)

        logger.info(
            "monitor_stopped",
            extra={"component": "ExecutionMonitor"},
        )

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False

    async def _check_active_executions(self) -> None:
        """Poll for active executions and evaluate each."""
        records = await self._db.get_active_executions(limit=100)
        now = datetime.now(UTC)

        for record in records:
            # Time budget check first (takes priority over stuck)
            if self._is_over_time_budget(record, now):
                await self._handle_timeout(record)
            elif self._is_stuck(record, now):
                await self._handle_stuck(record)

    def _is_stuck(self, record: TrackerRecord, now: datetime) -> bool:
        """Check if execution has no progress for stuck_timeout_minutes."""
        if record.last_progress_at is None:
            return False
        last = record.last_progress_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        elapsed_minutes = (now - last).total_seconds() / 60
        return elapsed_minutes > self._stuck_timeout_minutes

    def _is_over_time_budget(self, record: TrackerRecord, now: datetime) -> bool:
        """Check if execution has exceeded max_execution_minutes."""
        if record.started_at is None:
            return False
        started = record.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        elapsed_minutes = (now - started).total_seconds() / 60
        return elapsed_minutes > self._max_execution_minutes

    async def _handle_stuck(self, record: TrackerRecord) -> None:
        """Mark execution as stuck and notify user."""
        logger.warning(
            "execution_stuck_detected",
            extra={
                "component": "ExecutionMonitor",
                "plan_id": record.plan_id,
                "user_id": record.user_id,
                "trace_id": record.trace_id,
                "completed_steps": record.completed_steps,
                "total_steps": record.total_steps,
                "last_progress_at": str(record.last_progress_at),
            },
        )

        await self._db.mark_terminal(
            plan_id=record.plan_id,
            status="stuck",
            error_type="infrastructure_stuck",
            error_details={"reason": "No progress for 5+ minutes"},
        )

        if not record.notification_sent:
            notification = UserNotification(
                plan_id=record.plan_id,
                user_id=record.user_id,
                trace_id=record.trace_id,
                failure_type="stuck",
                total_steps=record.total_steps,
                completed_steps=record.completed_steps,
                started_at=record.started_at,
                last_progress_at=record.last_progress_at,
                message="Execution stuck -- no progress for 5+ minutes. Please start a new plan.",
            )
            try:
                await self._notifier.notify(notification)
                await self._db.mark_notified(record.plan_id)
            except Exception as exc:
                logger.error(
                    "notification_failed",
                    extra={
                        "component": "ExecutionMonitor",
                        "plan_id": record.plan_id,
                        "error": str(exc),
                    },
                )

    async def _handle_timeout(self, record: TrackerRecord) -> None:
        """Mark execution as timed-out and notify user."""
        logger.warning(
            "execution_timeout_detected",
            extra={
                "component": "ExecutionMonitor",
                "plan_id": record.plan_id,
                "user_id": record.user_id,
                "trace_id": record.trace_id,
                "completed_steps": record.completed_steps,
                "total_steps": record.total_steps,
                "started_at": str(record.started_at),
            },
        )

        await self._db.mark_terminal(
            plan_id=record.plan_id,
            status="timeout",
            error_type="time_budget_exceeded",
            error_details={"reason": "Exceeded 60-minute time budget"},
        )

        if not record.notification_sent:
            notification = UserNotification(
                plan_id=record.plan_id,
                user_id=record.user_id,
                trace_id=record.trace_id,
                failure_type="timeout",
                total_steps=record.total_steps,
                completed_steps=record.completed_steps,
                started_at=record.started_at,
                last_progress_at=record.last_progress_at,
                message="Execution timed out -- exceeded 60-minute budget. Please start a new plan.",
            )
            try:
                await self._notifier.notify(notification)
                await self._db.mark_notified(record.plan_id)
            except Exception as exc:
                logger.error(
                    "notification_failed",
                    extra={
                        "component": "ExecutionMonitor",
                        "plan_id": record.plan_id,
                        "error": str(exc),
                    },
                )


def create_tracker_service(tracker_db: Any = None) -> Any:
    """Create TrackerService with database adapter.

    Called once during app lifespan startup in shared/app.py.
    """
    from ..adapters.tracker_db import TrackerDatabaseAdapter
    from .tracker_service import TrackerService

    db = tracker_db or TrackerDatabaseAdapter()
    return TrackerService(tracker_db=db)


def create_monitor_service(
    tracker_db: Any = None,
    notifier: Notifier | None = None,
    poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
    stuck_timeout_minutes: int = DEFAULT_STUCK_TIMEOUT_MINUTES,
    max_execution_minutes: int = DEFAULT_MAX_EXECUTION_MINUTES,
) -> MonitorService:
    """Create MonitorService with database adapter and notifier.

    Called once during app lifespan startup in shared/app.py.
    """
    from ..adapters.tracker_db import TrackerDatabaseAdapter

    db = tracker_db or TrackerDatabaseAdapter()
    return MonitorService(
        tracker_db=db,
        notifier=notifier,
        poll_interval_s=poll_interval_s,
        stuck_timeout_minutes=stuck_timeout_minutes,
        max_execution_minutes=max_execution_minutes,
    )
