"""
Audit Observability Tests

Structured logging, component field, no PII in logs, metrics stubs.
Uses FakeAuditDB -- no real database.

Reference: Constitution VI (structured logging, no PII in logs)
"""

from __future__ import annotations

import logging
import re

import pytest

from components.Audit.service.audit_service import AuditService
from components.Audit.tests.conftest import FakeAuditDB, make_event


def _make_service(
    fake_db: FakeAuditDB | None = None,
    **kwargs,
) -> tuple[AuditService, FakeAuditDB]:
    db = fake_db or FakeAuditDB()
    defaults = {
        "flush_threshold": 100,
        "max_buffer_size": 5,
    }
    defaults.update(kwargs)
    svc = AuditService(db_adapter=db, **defaults)
    return svc, db


# ---------------------------------------------------------------------------
# Structured logging tests (~8 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_logs_audit_event_recorded(caplog):
    """DEBUG log emitted on record."""
    svc, _ = _make_service()
    with caplog.at_level(logging.DEBUG):
        await svc.record(make_event())
    assert any("audit_event_recorded" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_flush_logs_audit_buffer_flushed(caplog):
    """INFO log with batch size emitted on flush."""
    svc, _ = _make_service()
    await svc.record(make_event())
    with caplog.at_level(logging.INFO):
        await svc.flush()
    assert any("audit_buffer_flushed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_overflow_logs_audit_buffer_overflow(caplog):
    """WARNING log on buffer overflow."""
    db = FakeAuditDB()
    svc, _ = _make_service(
        fake_db=db,
        max_buffer_size=3,
        flush_threshold=100,
    )
    for _ in range(3):
        await svc.record(make_event())
    db.set_should_fail(True)
    await svc.flush()
    for _ in range(2):
        await svc.record(make_event())
    with caplog.at_level(logging.WARNING):
        await svc.flush()
    assert any("audit_buffer_overflow" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_query_logs_audit_query_executed(caplog):
    """INFO log emitted on query."""
    svc, db = _make_service()
    db._events.append(make_event())
    with caplog.at_level(logging.INFO):
        await svc.query()
    assert any("audit_query_executed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_cleanup_logs_audit_retention_cleanup(caplog):
    """INFO log with deleted count emitted on cleanup."""
    svc, _ = _make_service()
    with caplog.at_level(logging.INFO):
        await svc.cleanup_expired()
    assert any("audit_retention_cleanup" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_db_error_logs_audit_db_error(caplog):
    """ERROR log on DB failure during flush."""
    db = FakeAuditDB()
    svc, _ = _make_service(fake_db=db)
    await svc.record(make_event())
    db.set_should_fail(True)
    with caplog.at_level(logging.ERROR):
        await svc.flush()
    assert any("audit_db_error" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_all_logs_include_component_field(caplog):
    """extra={'component': 'Audit'} on every log."""
    svc, _db = _make_service()
    with caplog.at_level(logging.DEBUG):
        await svc.record(make_event())
        await svc.flush()
    audit_records = [
        r for r in caplog.records if hasattr(r, "component") and r.component == "Audit"
    ]
    assert len(audit_records) >= 2  # record + flush


@pytest.mark.asyncio
async def test_log_levels_appropriate(caplog):
    """DEBUG for record, INFO for flush/query, WARNING for overflow, ERROR for DB error."""
    db = FakeAuditDB()
    svc = AuditService(
        db_adapter=db,
        flush_threshold=100,
        max_buffer_size=2,
    )

    # Record -> DEBUG
    with caplog.at_level(logging.DEBUG):
        await svc.record(make_event())
    debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("audit_event_recorded" in r.message for r in debug_msgs)
    caplog.clear()

    # Flush -> INFO
    with caplog.at_level(logging.INFO):
        await svc.flush()
    info_msgs = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("audit_buffer_flushed" in r.message for r in info_msgs)
    caplog.clear()

    # DB error -> ERROR
    await svc.record(make_event())
    db.set_should_fail(True)
    with caplog.at_level(logging.ERROR):
        await svc.flush()
    error_msgs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("audit_db_error" in r.message for r in error_msgs)


# ---------------------------------------------------------------------------
# No PII in logs tests (~4 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_password_in_log_messages(caplog):
    """No password values appear in log output."""
    svc, _ = _make_service()
    event = make_event(
        event_data={"password": "supersecret123", "role": "Fetcher"},
    )
    with caplog.at_level(logging.DEBUG):
        await svc.record(event)
        await svc.flush()
    for record in caplog.records:
        assert "supersecret123" not in record.getMessage()


@pytest.mark.asyncio
async def test_no_jwt_token_in_log_messages(caplog):
    """No JWT prefix 'eyJ' appears in log output."""
    svc, _ = _make_service()
    event = make_event(
        event_data={"token": "eyJhbGciOiJIUzI1NiJ9.payload.sig"},
    )
    with caplog.at_level(logging.DEBUG):
        await svc.record(event)
        await svc.flush()
    for record in caplog.records:
        assert "eyJhbGciOiJIUzI1NiJ9" not in record.getMessage()


@pytest.mark.asyncio
async def test_no_email_in_log_messages(caplog):
    """No email patterns appear in log output."""
    svc, _ = _make_service()
    event = make_event(
        event_data={"user_email": "test@example.com"},
    )
    with caplog.at_level(logging.DEBUG):
        await svc.record(event)
        await svc.flush()
    email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    for record in caplog.records:
        assert not email_pattern.search(record.getMessage())


@pytest.mark.asyncio
async def test_user_id_logged_as_opaque_only(caplog):
    """user_id appears as opaque string, not joined with PII."""
    svc, _ = _make_service()
    event = make_event(user_id="user-12345")
    with caplog.at_level(logging.DEBUG):
        await svc.record(event)
    # Verify no enriched user info is logged
    for record in caplog.records:
        msg = record.getMessage()
        # Should not contain email patterns or name-like info
        assert "test@" not in msg
        assert "John" not in msg


# ---------------------------------------------------------------------------
# Metrics stubs tests (~3 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_counter_audit_events_recorded():
    """Counter incremented on record."""
    svc, _ = _make_service()
    assert svc._events_recorded == 0
    await svc.record(make_event())
    assert svc._events_recorded == 1
    await svc.record(make_event())
    assert svc._events_recorded == 2


@pytest.mark.asyncio
async def test_metrics_gauge_audit_buffer_size():
    """Gauge reflects buffer length."""
    svc, _ = _make_service()
    assert svc.buffer_size == 0
    await svc.record(make_event())
    assert svc.buffer_size == 1
    await svc.flush()
    assert svc.buffer_size == 0


@pytest.mark.asyncio
async def test_metrics_counter_audit_buffer_overflow():
    """Counter incremented on buffer overflow."""
    db = FakeAuditDB()
    svc = AuditService(
        db_adapter=db,
        max_buffer_size=2,
        flush_threshold=100,
    )
    assert svc._buffer_overflows == 0
    for _ in range(2):
        await svc.record(make_event())
    db.set_should_fail(True)
    await svc.flush()
    # Add more to trigger overflow on next failed flush
    await svc.record(make_event())
    await svc.flush()
    assert svc._buffer_overflows == 1
