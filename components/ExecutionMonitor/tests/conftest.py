"""
ExecutionMonitor test fixtures -- FakeTrackerDB, sample records, configured services.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from components.ExecutionMonitor.domain.models import TrackerRecord, UserNotification
from components.ExecutionMonitor.service.monitor_service import MonitorService
from components.ExecutionMonitor.service.tracker_service import TrackerService

SAMPLE_PLAN_ID = "01JXYZ1234567890ABCDEFGHIJ"
SAMPLE_USER_ID = "user-uuid-12345678-abcd-efgh"
SAMPLE_TRACE_ID = "trace-abc-123"
SAMPLE_PLAN_ID_2 = "01JABC9876543210ZYXWVUTSRQ"


# ---------------------------------------------------------------------------
# FakeTrackerDB (in-memory)
# ---------------------------------------------------------------------------


class FakeTrackerDB:
    """In-memory fake database adapter for testing.

    Stores TrackerRecord instances keyed by plan_id.
    Supports all TrackerDatabaseAdapter methods.
    """

    def __init__(self) -> None:
        self._records: dict[str, TrackerRecord] = {}
        self._should_fail = False

    def set_should_fail(self, fail: bool) -> None:
        """Toggle failure mode for testing error handling."""
        self._should_fail = fail

    async def create_tracker(
        self,
        plan_id: str,
        user_id: str,
        trace_id: str,
        total_steps: int,
    ) -> TrackerRecord:
        if self._should_fail:
            raise RuntimeError("FakeTrackerDB: simulated failure")
        now = datetime.now(UTC)
        record = TrackerRecord(
            tracker_id=str(uuid4()),
            plan_id=plan_id,
            user_id=user_id,
            trace_id=trace_id,
            status="running",
            total_steps=total_steps,
            completed_steps=0,
            notification_sent=False,
            started_at=now,
            last_progress_at=now,
        )
        self._records[plan_id] = record
        return record

    async def update_progress(self, plan_id: str, completed_steps: int) -> bool:
        if self._should_fail:
            raise RuntimeError("FakeTrackerDB: simulated failure")
        record = self._records.get(plan_id)
        if record is None or record.status != "running":
            return False
        self._records[plan_id] = record.model_copy(
            update={
                "completed_steps": completed_steps,
                "last_progress_at": datetime.now(UTC),
            }
        )
        return True

    async def mark_terminal(
        self,
        plan_id: str,
        status: str,
        error_type: str | None = None,
        error_details: dict | None = None,
    ) -> bool:
        if self._should_fail:
            raise RuntimeError("FakeTrackerDB: simulated failure")
        record = self._records.get(plan_id)
        if record is None or record.status != "running":
            return False
        self._records[plan_id] = record.model_copy(
            update={
                "status": status,
                "error_type": error_type,
                "error_details": error_details,
                "completed_at": datetime.now(UTC),
            }
        )
        return True

    async def mark_notified(self, plan_id: str) -> bool:
        if self._should_fail:
            raise RuntimeError("FakeTrackerDB: simulated failure")
        record = self._records.get(plan_id)
        if record is None:
            return False
        self._records[plan_id] = record.model_copy(update={"notification_sent": True})
        return True

    async def get_active_executions(self, limit: int = 100) -> list[TrackerRecord]:
        if self._should_fail:
            raise RuntimeError("FakeTrackerDB: simulated failure")
        active = [r for r in self._records.values() if r.status == "running"]
        active.sort(key=lambda r: r.started_at or datetime.min.replace(tzinfo=UTC))
        return active[:limit]

    async def get_tracker_by_plan(self, plan_id: str) -> TrackerRecord | None:
        if self._should_fail:
            raise RuntimeError("FakeTrackerDB: simulated failure")
        return self._records.get(plan_id)

    def get_record(self, plan_id: str) -> TrackerRecord | None:
        """Test helper: direct access to stored record."""
        return self._records.get(plan_id)

    def inject_record(self, record: TrackerRecord) -> None:
        """Test helper: inject a record directly."""
        self._records[record.plan_id] = record


class FakeNotifier:
    """In-memory fake notifier for testing."""

    def __init__(self) -> None:
        self.notifications: list[UserNotification] = []
        self._should_fail = False

    def set_should_fail(self, fail: bool) -> None:
        self._should_fail = fail

    async def notify(self, notification: UserNotification) -> bool:
        if self._should_fail:
            raise RuntimeError("FakeNotifier: simulated failure")
        self.notifications.append(notification)
        return True


# ---------------------------------------------------------------------------
# Basic fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_plan_id() -> str:
    return SAMPLE_PLAN_ID


@pytest.fixture()
def sample_user_id() -> str:
    return SAMPLE_USER_ID


@pytest.fixture()
def sample_trace_id() -> str:
    return SAMPLE_TRACE_ID


# ---------------------------------------------------------------------------
# Fake adapter fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_db() -> FakeTrackerDB:
    return FakeTrackerDB()


@pytest.fixture()
def fake_notifier() -> FakeNotifier:
    return FakeNotifier()


# ---------------------------------------------------------------------------
# Service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tracker_service(fake_db: FakeTrackerDB) -> TrackerService:
    return TrackerService(tracker_db=fake_db)


@pytest.fixture()
def monitor_service(fake_db: FakeTrackerDB, fake_notifier: FakeNotifier) -> MonitorService:
    return MonitorService(
        tracker_db=fake_db,
        notifier=fake_notifier,
        poll_interval_s=1,
        stuck_timeout_minutes=5,
        max_execution_minutes=60,
    )


# ---------------------------------------------------------------------------
# Sample record fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def healthy_record() -> TrackerRecord:
    """Running execution with recent progress."""
    now = datetime.now(UTC)
    return TrackerRecord(
        tracker_id=str(uuid4()),
        plan_id=SAMPLE_PLAN_ID,
        user_id=SAMPLE_USER_ID,
        trace_id=SAMPLE_TRACE_ID,
        status="running",
        total_steps=5,
        completed_steps=2,
        notification_sent=False,
        started_at=now - timedelta(minutes=2),
        last_progress_at=now - timedelta(seconds=30),
    )


@pytest.fixture()
def stuck_record() -> TrackerRecord:
    """Running execution with no progress for 6 minutes."""
    now = datetime.now(UTC)
    return TrackerRecord(
        tracker_id=str(uuid4()),
        plan_id=SAMPLE_PLAN_ID,
        user_id=SAMPLE_USER_ID,
        trace_id=SAMPLE_TRACE_ID,
        status="running",
        total_steps=5,
        completed_steps=2,
        notification_sent=False,
        started_at=now - timedelta(minutes=10),
        last_progress_at=now - timedelta(minutes=6),
    )


@pytest.fixture()
def timeout_record() -> TrackerRecord:
    """Running execution that exceeded 60-minute budget."""
    now = datetime.now(UTC)
    return TrackerRecord(
        tracker_id=str(uuid4()),
        plan_id=SAMPLE_PLAN_ID,
        user_id=SAMPLE_USER_ID,
        trace_id=SAMPLE_TRACE_ID,
        status="running",
        total_steps=10,
        completed_steps=8,
        notification_sent=False,
        started_at=now - timedelta(minutes=65),
        last_progress_at=now - timedelta(seconds=10),
    )


@pytest.fixture()
def already_notified_stuck_record() -> TrackerRecord:
    """Stuck execution that was already notified."""
    now = datetime.now(UTC)
    return TrackerRecord(
        tracker_id=str(uuid4()),
        plan_id=SAMPLE_PLAN_ID,
        user_id=SAMPLE_USER_ID,
        trace_id=SAMPLE_TRACE_ID,
        status="running",
        total_steps=5,
        completed_steps=2,
        notification_sent=True,
        started_at=now - timedelta(minutes=10),
        last_progress_at=now - timedelta(minutes=6),
    )


@pytest.fixture()
def completed_record() -> TrackerRecord:
    """Successfully completed execution."""
    now = datetime.now(UTC)
    return TrackerRecord(
        tracker_id=str(uuid4()),
        plan_id=SAMPLE_PLAN_ID,
        user_id=SAMPLE_USER_ID,
        trace_id=SAMPLE_TRACE_ID,
        status="completed",
        total_steps=5,
        completed_steps=5,
        notification_sent=False,
        started_at=now - timedelta(minutes=3),
        last_progress_at=now - timedelta(minutes=1),
        completed_at=now,
    )
