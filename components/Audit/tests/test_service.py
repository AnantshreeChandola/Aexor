"""
AuditService Unit Tests

Tests for record, query, buffering, flush, sanitization, retention,
and error handling. Uses FakeAuditDB -- no real database.

Reference: SPEC SC-001, SC-004, SC-005; User Stories 1-5
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from components.Audit.domain.models import AuditEventType
from components.Audit.service.audit_service import AuditService
from components.Audit.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_PLAN_ID_2,
    SAMPLE_TRACE_ID,
    SAMPLE_USER_ID,
    SAMPLE_USER_ID_2,
    FakeAuditDB,
    make_event,
)

# ---------------------------------------------------------------------------
# Record method tests (~12 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_appends_event_to_buffer(
    audit_service: AuditService,
):
    """Event goes to buffer, not immediately to DB."""
    event = make_event()
    await audit_service.record(event)
    assert audit_service.buffer_size == 1


@pytest.mark.asyncio
async def test_record_auto_flushes_at_threshold(
    fake_db: FakeAuditDB,
):
    """Flush triggers when buffer reaches flush_threshold."""
    svc = AuditService(
        db_adapter=fake_db,
        flush_threshold=3,
    )
    for _ in range(3):
        await svc.record(make_event())
    # Buffer should have been flushed
    assert svc.buffer_size == 0
    assert len(fake_db.stored_events) == 3


@pytest.mark.asyncio
async def test_record_never_raises_on_db_error(
    fake_db: FakeAuditDB,
):
    """Fire-and-forget invariant: record never raises."""
    svc = AuditService(
        db_adapter=fake_db,
        flush_threshold=1,
    )
    fake_db.set_should_fail(True)
    # Should not raise even though DB fails on flush
    await svc.record(make_event())


@pytest.mark.asyncio
async def test_record_sanitizes_password_from_event_data(
    audit_service: AuditService,
):
    """Password key stripped from event_data."""
    event = make_event(
        event_data={"password": "s3cret", "role": "Fetcher"},
    )
    await audit_service.record(event)
    await audit_service.flush()
    assert "password" not in event.event_data
    assert event.event_data["role"] == "Fetcher"


@pytest.mark.asyncio
async def test_record_sanitizes_secret_from_event_data(
    audit_service: AuditService,
):
    """Secret key stripped from event_data."""
    event = make_event(event_data={"secret": "xyz"})
    await audit_service.record(event)
    assert "secret" not in event.event_data


@pytest.mark.asyncio
async def test_record_sanitizes_token_from_event_data(
    audit_service: AuditService,
):
    """Token key stripped from event_data."""
    event = make_event(event_data={"token": "eyJabc..."})
    await audit_service.record(event)
    assert "token" not in event.event_data


@pytest.mark.asyncio
async def test_record_sanitizes_credential_from_event_data(
    audit_service: AuditService,
):
    """Credential key stripped from event_data."""
    event = make_event(event_data={"credential": "cred123"})
    await audit_service.record(event)
    assert "credential" not in event.event_data


@pytest.mark.asyncio
async def test_record_sanitizes_api_key_from_event_data(
    audit_service: AuditService,
):
    """api_key key stripped from event_data."""
    event = make_event(event_data={"api_key": "key-xyz"})
    await audit_service.record(event)
    assert "api_key" not in event.event_data


@pytest.mark.asyncio
async def test_record_truncates_error_details(
    audit_service: AuditService,
):
    """error_details truncated to 500 chars."""
    long_details = "x" * 1000
    event = make_event(
        event_data={"error_details": long_details},
    )
    await audit_service.record(event)
    assert len(event.event_data["error_details"]) == 500


@pytest.mark.asyncio
async def test_record_execution_started_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Correct event_type for execution_started."""
    event = make_event(
        event_type=AuditEventType.EXECUTION_STARTED,
        event_data={"total_steps": 5},
    )
    await audit_service.record(event)
    await audit_service.flush()
    assert len(fake_db.stored_events) == 1
    assert fake_db.stored_events[0].event_type == AuditEventType.EXECUTION_STARTED


@pytest.mark.asyncio
async def test_record_step_completed_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """step_completed includes step_number, role, latency."""
    event = make_event(
        event_type=AuditEventType.STEP_COMPLETED,
        step_number=2,
        event_data={
            "role": "Fetcher",
            "status": "success",
            "latency_ms": 120,
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert stored.step_number == 2
    assert stored.event_data["role"] == "Fetcher"
    assert stored.event_data["latency_ms"] == 120


@pytest.mark.asyncio
async def test_record_step_failed_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """step_failed sanitizes error_details."""
    event = make_event(
        event_type=AuditEventType.STEP_FAILED,
        step_number=3,
        event_data={
            "error_type": "ToolExecutionError",
            "error_details": "a" * 600,
            "password": "leaked",
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert "password" not in stored.event_data
    assert len(stored.event_data["error_details"]) == 500


# ---------------------------------------------------------------------------
# Query method tests (~8 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_by_plan_id(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Filter by plan_id returns only matching events."""
    e1 = make_event(plan_id=SAMPLE_PLAN_ID)
    e2 = make_event(plan_id=SAMPLE_PLAN_ID_2)
    fake_db._events.extend([e1, e2])
    result = await audit_service.query(plan_id=SAMPLE_PLAN_ID)
    assert all(e.plan_id == SAMPLE_PLAN_ID for e in result.events)
    assert len(result.events) == 1


@pytest.mark.asyncio
async def test_query_by_user_id(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Filter by user_id returns only matching events."""
    e1 = make_event(user_id=SAMPLE_USER_ID)
    e2 = make_event(user_id=SAMPLE_USER_ID_2)
    fake_db._events.extend([e1, e2])
    result = await audit_service.query(user_id=SAMPLE_USER_ID)
    assert all(e.user_id == SAMPLE_USER_ID for e in result.events)
    assert len(result.events) == 1


@pytest.mark.asyncio
async def test_query_by_trace_id(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Filter by trace_id returns only matching events."""
    e1 = make_event(trace_id=SAMPLE_TRACE_ID)
    e2 = make_event(trace_id="other-trace")
    fake_db._events.extend([e1, e2])
    result = await audit_service.query(trace_id=SAMPLE_TRACE_ID)
    assert len(result.events) == 1


@pytest.mark.asyncio
async def test_query_by_event_type(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Filter by event_type returns only matching events."""
    e1 = make_event(event_type=AuditEventType.EXECUTION_STARTED)
    e2 = make_event(event_type=AuditEventType.STEP_COMPLETED)
    fake_db._events.extend([e1, e2])
    result = await audit_service.query(
        event_type="execution_started",
    )
    assert len(result.events) == 1
    assert result.events[0].event_type == AuditEventType.EXECUTION_STARTED


@pytest.mark.asyncio
async def test_query_with_time_range(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """start_time/end_time filtering works."""
    now = datetime.now(UTC)
    old = make_event(
        created_at=now - timedelta(hours=2),
    )
    recent = make_event(created_at=now)
    fake_db._events.extend([old, recent])
    result = await audit_service.query(
        start_time=now - timedelta(hours=1),
    )
    assert len(result.events) == 1


@pytest.mark.asyncio
async def test_query_with_cursor_pagination(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Cursor-based forward paging works."""
    events = [make_event() for _ in range(5)]
    events.sort(key=lambda e: e.event_id)
    fake_db._events.extend(events)

    # First page
    r1 = await audit_service.query(limit=2)
    assert len(r1.events) == 2
    assert r1.next_cursor is not None

    # Second page
    r2 = await audit_service.query(
        cursor=r1.next_cursor,
        limit=2,
    )
    assert len(r2.events) == 2
    assert r2.events[0].event_id > r1.events[-1].event_id


@pytest.mark.asyncio
async def test_query_default_limit_50(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Default page size is 50."""
    events = [make_event() for _ in range(60)]
    fake_db._events.extend(events)
    result = await audit_service.query()
    assert len(result.events) == 50


@pytest.mark.asyncio
async def test_query_max_limit_200(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Limit is capped at 200 by AuditQueryParams validation."""
    from pydantic import ValidationError

    from components.Audit.domain.models import AuditQueryParams

    with pytest.raises(ValidationError):
        AuditQueryParams(limit=201)


# ---------------------------------------------------------------------------
# Buffer management tests (~10 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_sends_buffer_to_db(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Manual flush empties buffer and writes to DB."""
    await audit_service.record(make_event())
    await audit_service.record(make_event())
    assert audit_service.buffer_size == 2
    await audit_service.flush()
    assert audit_service.buffer_size == 0
    assert len(fake_db.stored_events) == 2


@pytest.mark.asyncio
async def test_flush_clears_buffer_after_success(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """Buffer is empty after successful flush."""
    await audit_service.record(make_event())
    await audit_service.flush()
    assert audit_service.buffer_size == 0


@pytest.mark.asyncio
async def test_flush_retains_events_on_db_failure(
    fake_db: FakeAuditDB,
):
    """Events stay in buffer when DB fails."""
    svc = AuditService(
        db_adapter=fake_db,
        flush_threshold=100,
    )
    await svc.record(make_event())
    fake_db.set_should_fail(True)
    await svc.flush()
    # Events should be back in buffer
    assert svc.buffer_size == 1


@pytest.mark.asyncio
async def test_buffer_overflow_drops_oldest(
    fake_db: FakeAuditDB,
):
    """Oldest events dropped when buffer exceeds max_buffer_size."""
    svc = AuditService(
        db_adapter=fake_db,
        max_buffer_size=5,
        flush_threshold=100,
    )
    # Add 5 events to buffer
    for _ in range(5):
        await svc.record(make_event())
    assert svc.buffer_size == 5

    # Fail flush, then add more to trigger overflow
    fake_db.set_should_fail(True)
    await svc.flush()  # events back in buffer = 5

    # Add 3 more without flushing
    for _ in range(3):
        await svc.record(make_event())
    # Buffer is now 8; trigger a failed flush to cause overflow
    await svc.flush()
    # Buffer should be capped at max_buffer_size
    assert svc.buffer_size <= 5


@pytest.mark.asyncio
async def test_buffer_overflow_logs_warning(
    fake_db: FakeAuditDB,
    caplog,
):
    """WARNING logged on buffer overflow."""
    svc = AuditService(
        db_adapter=fake_db,
        max_buffer_size=3,
        flush_threshold=100,
    )
    for _ in range(3):
        await svc.record(make_event())
    fake_db.set_should_fail(True)
    await svc.flush()
    # Add more events to trigger overflow on next failed flush
    for _ in range(2):
        await svc.record(make_event())
    with caplog.at_level(logging.WARNING):
        await svc.flush()
    assert any("audit_buffer_overflow" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_concurrent_record_is_thread_safe(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """asyncio.Lock protects buffer during concurrent access."""
    tasks = [audit_service.record(make_event()) for _ in range(20)]
    await asyncio.gather(*tasks)
    total = audit_service.buffer_size + len(fake_db.stored_events)
    assert total == 20


@pytest.mark.asyncio
async def test_flush_loop_runs_periodically(
    fake_db: FakeAuditDB,
):
    """Background flush task triggers periodic flushes."""
    svc = AuditService(
        db_adapter=fake_db,
        flush_threshold=100,
        flush_interval_s=0.05,
    )
    await svc.record(make_event())
    await svc.start()
    await asyncio.sleep(0.15)
    await svc.stop()
    assert len(fake_db.stored_events) == 1


@pytest.mark.asyncio
async def test_stop_flushes_remaining_buffer(
    fake_db: FakeAuditDB,
):
    """Shutdown drains remaining buffer."""
    svc = AuditService(
        db_adapter=fake_db,
        flush_threshold=100,
    )
    await svc.record(make_event())
    await svc.record(make_event())
    await svc.start()
    await svc.stop()
    assert len(fake_db.stored_events) == 2
    assert svc.buffer_size == 0


@pytest.mark.asyncio
async def test_empty_buffer_flush_is_noop(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """No DB call when buffer is empty."""
    await audit_service.flush()
    assert len(fake_db.stored_events) == 0


@pytest.mark.asyncio
async def test_batch_insert_multiple_events(
    fake_db: FakeAuditDB,
):
    """Batch of N events sent in single flush."""
    svc = AuditService(
        db_adapter=fake_db,
        flush_threshold=5,
    )
    for _ in range(5):
        await svc.record(make_event())
    assert len(fake_db.stored_events) == 5


# ---------------------------------------------------------------------------
# Approval and policy event tests (~5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_approval_granted_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """gate_id, user_id, scopes, token_id present."""
    event = make_event(
        event_type=AuditEventType.APPROVAL_GRANTED,
        event_data={
            "gate_id": "gate-001",
            "token_id": "tok-abc",
            "scopes": ["write:calendar"],
            "approved_at": "2026-04-05T12:00:00Z",
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert stored.event_data["gate_id"] == "gate-001"
    assert stored.event_data["token_id"] == "tok-abc"
    assert stored.event_data["scopes"] == ["write:calendar"]


@pytest.mark.asyncio
async def test_record_approval_expired_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """gate_id and plan_id present on expired event."""
    event = make_event(
        event_type=AuditEventType.APPROVAL_EXPIRED,
        event_data={"gate_id": "gate-002"},
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert stored.event_type == AuditEventType.APPROVAL_EXPIRED
    assert stored.plan_id == SAMPLE_PLAN_ID


@pytest.mark.asyncio
async def test_record_approval_does_not_store_jwt(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """JWT value absent after sanitization, only token_id kept."""
    event = make_event(
        event_type=AuditEventType.APPROVAL_GRANTED,
        event_data={
            "token_id": "tok-xyz",
            "token": "eyJhbGciOiJIUzI1NiJ9.payload.sig",
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert "token" not in stored.event_data
    assert stored.event_data["token_id"] == "tok-xyz"


@pytest.mark.asyncio
async def test_record_policy_attestation_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """attestation_id, policy_id, decision present."""
    event = make_event(
        event_type=AuditEventType.POLICY_ATTESTATION,
        event_data={
            "attestation_id": "att-001",
            "policy_id": "pol-budget",
            "decision": "allowed",
            "spawned_by_step": 2,
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert stored.event_data["attestation_id"] == "att-001"
    assert stored.event_data["policy_id"] == "pol-budget"


@pytest.mark.asyncio
async def test_record_policy_denial_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """violations, reason present."""
    event = make_event(
        event_type=AuditEventType.POLICY_DENIAL,
        event_data={
            "reason": "budget_exceeded",
            "violations": ["max_spend"],
            "parent_step": 1,
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert stored.event_data["reason"] == "budget_exceeded"
    assert stored.event_data["violations"] == ["max_spend"]


# ---------------------------------------------------------------------------
# Retention and infrastructure event tests (~5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_expired_deletes_old_events(
    fake_db: FakeAuditDB,
):
    """Events older than retention_days removed."""
    svc = AuditService(
        db_adapter=fake_db,
        retention_days=30,
    )
    now = datetime.now(UTC)
    old = make_event(created_at=now - timedelta(days=31))
    recent = make_event(created_at=now)
    fake_db._events.extend([old, recent])
    await svc.cleanup_expired()
    assert len(fake_db.stored_events) == 1
    assert fake_db.stored_events[0].created_at == recent.created_at


@pytest.mark.asyncio
async def test_cleanup_expired_returns_deleted_count(
    fake_db: FakeAuditDB,
):
    """Cleanup returns count of deleted events."""
    svc = AuditService(
        db_adapter=fake_db,
        retention_days=30,
    )
    now = datetime.now(UTC)
    for _ in range(3):
        fake_db._events.append(
            make_event(created_at=now - timedelta(days=31)),
        )
    deleted = await svc.cleanup_expired()
    assert deleted == 3


@pytest.mark.asyncio
async def test_cleanup_expired_keeps_recent_events(
    fake_db: FakeAuditDB,
):
    """Recent events untouched by cleanup."""
    svc = AuditService(
        db_adapter=fake_db,
        retention_days=90,
    )
    now = datetime.now(UTC)
    recent = make_event(created_at=now - timedelta(days=10))
    fake_db._events.append(recent)
    deleted = await svc.cleanup_expired()
    assert deleted == 0
    assert len(fake_db.stored_events) == 1


@pytest.mark.asyncio
async def test_record_execution_stuck_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """plan_id, detection_reason, elapsed_time on stuck event."""
    event = make_event(
        event_type=AuditEventType.EXECUTION_STUCK,
        event_data={
            "detection_reason": "no_progress",
            "elapsed_time_s": 360,
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert stored.event_type == AuditEventType.EXECUTION_STUCK
    assert stored.event_data["detection_reason"] == "no_progress"


@pytest.mark.asyncio
async def test_record_execution_timeout_event(
    audit_service: AuditService,
    fake_db: FakeAuditDB,
):
    """plan_id, timeout details on timeout event."""
    event = make_event(
        event_type=AuditEventType.EXECUTION_TIMEOUT,
        event_data={
            "timeout_minutes": 60,
            "elapsed_minutes": 65,
        },
    )
    await audit_service.record(event)
    await audit_service.flush()
    stored = fake_db.stored_events[0]
    assert stored.event_type == AuditEventType.EXECUTION_TIMEOUT
    assert stored.event_data["timeout_minutes"] == 60
