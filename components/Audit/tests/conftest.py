"""
Audit test fixtures -- FakeAuditDB, sample constants, event factories.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import ulid

from components.Audit.domain.models import (
    AuditEvent,
    AuditEventType,
    AuditQueryParams,
    AuditQueryResult,
)
from components.Audit.service.audit_service import AuditService

SAMPLE_PLAN_ID = "01JXYZ1234567890ABCDEFGHIJ"
SAMPLE_USER_ID = "user-uuid-12345678-abcd-efgh"
SAMPLE_TRACE_ID = "trace-abc-123"
SAMPLE_PLAN_ID_2 = "01JABC9876543210ZYXWVUTSRQ"
SAMPLE_USER_ID_2 = "user-uuid-87654321-dcba-hgfe"


# ---------------------------------------------------------------------------
# FakeAuditDB (in-memory)
# ---------------------------------------------------------------------------


class FakeAuditDB:
    """In-memory fake database adapter for testing.

    Stores AuditEvent instances in a list. Supports all
    AuditDatabaseAdapterProtocol methods.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._should_fail = False

    def set_should_fail(self, fail: bool) -> None:
        """Toggle failure mode for testing error handling."""
        self._should_fail = fail

    async def append_event(self, event: AuditEvent) -> None:
        if self._should_fail:
            raise RuntimeError("FakeAuditDB: simulated failure")
        self._events.append(event)

    async def append_events_batch(
        self,
        events: list[AuditEvent],
    ) -> None:
        if self._should_fail:
            raise RuntimeError("FakeAuditDB: simulated failure")
        self._events.extend(events)

    async def query_events(
        self,
        params: AuditQueryParams,
    ) -> AuditQueryResult:
        if self._should_fail:
            raise RuntimeError("FakeAuditDB: simulated failure")

        filtered = list(self._events)

        if params.plan_id is not None:
            filtered = [e for e in filtered if e.plan_id == params.plan_id]
        if params.user_id is not None:
            filtered = [e for e in filtered if e.user_id == params.user_id]
        if params.trace_id is not None:
            filtered = [e for e in filtered if e.trace_id == params.trace_id]
        if params.event_type is not None:
            filtered = [e for e in filtered if e.event_type.value == params.event_type]
        if params.start_time is not None:
            filtered = [e for e in filtered if e.created_at >= params.start_time]
        if params.end_time is not None:
            filtered = [e for e in filtered if e.created_at <= params.end_time]

        # Sort by event_id (ULID = chronological)
        filtered.sort(key=lambda e: e.event_id)

        total_count = len(filtered)

        # Cursor pagination
        if params.cursor is not None:
            filtered = [e for e in filtered if e.event_id > params.cursor]

        page = filtered[: params.limit]
        next_cursor = page[-1].event_id if page and len(page) == params.limit else None

        return AuditQueryResult(
            events=page,
            next_cursor=next_cursor,
            total_count=total_count,
        )

    async def delete_expired(self, before: datetime) -> int:
        if self._should_fail:
            raise RuntimeError("FakeAuditDB: simulated failure")
        original = len(self._events)
        self._events = [e for e in self._events if e.created_at >= before]
        return original - len(self._events)

    # Test helpers
    @property
    def stored_events(self) -> list[AuditEvent]:
        """Direct access to stored events for assertions."""
        return list(self._events)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_event(
    event_type: AuditEventType = AuditEventType.EXECUTION_STARTED,
    plan_id: str | None = SAMPLE_PLAN_ID,
    user_id: str | None = SAMPLE_USER_ID,
    trace_id: str | None = SAMPLE_TRACE_ID,
    step_number: int | None = None,
    event_data: dict | None = None,
    created_at: datetime | None = None,
    event_id: str | None = None,
) -> AuditEvent:
    """Create an AuditEvent with sensible defaults."""
    return AuditEvent(
        event_id=event_id or ulid.new().str,
        event_type=event_type,
        plan_id=plan_id,
        user_id=user_id,
        trace_id=trace_id,
        step_number=step_number,
        event_data=event_data or {},
        created_at=created_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Fixtures
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


@pytest.fixture()
def fake_db() -> FakeAuditDB:
    return FakeAuditDB()


@pytest.fixture()
def audit_service(fake_db: FakeAuditDB) -> AuditService:
    """AuditService wired to FakeAuditDB with low thresholds for testing."""
    return AuditService(
        db_adapter=fake_db,
        max_buffer_size=1000,
        flush_threshold=10,
        flush_interval_s=0.1,
        retention_days=90,
    )


@pytest.fixture()
def sample_event() -> AuditEvent:
    """A basic execution_started event."""
    return make_event()


@pytest.fixture()
def step_completed_event() -> AuditEvent:
    """A step_completed event with typical data."""
    return make_event(
        event_type=AuditEventType.STEP_COMPLETED,
        step_number=1,
        event_data={
            "role": "Fetcher",
            "status": "success",
            "latency_ms": 150,
        },
    )


@pytest.fixture()
def approval_event() -> AuditEvent:
    """An approval_granted event."""
    return make_event(
        event_type=AuditEventType.APPROVAL_GRANTED,
        event_data={
            "gate_id": "gate-001",
            "token_id": "tok-abc123",
            "scopes": ["write:calendar"],
            "approved_at": "2026-04-05T12:00:00Z",
        },
    )
