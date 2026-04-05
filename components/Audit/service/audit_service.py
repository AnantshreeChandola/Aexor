"""
AuditService -- Fire-and-Forget Audit Event Recording

Core service for the Audit component. Provides:
- Non-blocking event recording with in-memory buffering
- PII/secret sanitization before persistence
- Batch flush to database (every 100ms or 10 events)
- Cursor-based paginated query
- Retention cleanup for expired events

record() NEVER raises to callers (fire-and-forget invariant).

Reference: LLD.md Sections 8, 10, 13
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from ..domain.models import (
    AuditEvent,
    AuditQueryParams,
    AuditQueryResult,
)

logger = logging.getLogger(__name__)

# Keys that are stripped from event_data (case-insensitive)
_SENSITIVE_KEYS = frozenset(
    {"password", "secret", "token", "credential", "api_key"},
)

_MAX_ERROR_DETAILS_LEN = 500


@runtime_checkable
class AuditServiceProtocol(Protocol):
    """Protocol for the audit service."""

    async def record(self, event: AuditEvent) -> None: ...
    async def query(
        self,
        plan_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        event_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> AuditQueryResult: ...
    async def flush(self) -> None: ...


class AuditService:
    """Centralized audit event service with buffered writes.

    All methods that write events catch exceptions internally to
    guarantee that audit failures never propagate to callers.
    """

    def __init__(
        self,
        db_adapter: Any,
        max_buffer_size: int = 1000,
        flush_threshold: int = 10,
        flush_interval_s: float = 0.1,
        retention_days: int = 90,
    ) -> None:
        self._db = db_adapter
        self._max_buffer_size = max_buffer_size
        self._flush_threshold = flush_threshold
        self._flush_interval_s = flush_interval_s
        self._retention_days = retention_days

        # Buffer state
        self._buffer: list[AuditEvent] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None

        # Metrics stubs (simple counters for test assertions)
        self._events_recorded: int = 0
        self._buffer_overflows: int = 0
        self._events_queried: int = 0

    # ---------------------------------------------------------------
    # Properties
    # ---------------------------------------------------------------

    @property
    def buffer_size(self) -> int:
        """Current number of events in the buffer."""
        return len(self._buffer)

    # ---------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------

    async def record(self, event: AuditEvent) -> None:
        """Append an audit event. Non-blocking; buffers if DB unavailable.

        NEVER raises to caller (fire-and-forget invariant).
        """
        try:
            self._sanitize(event.event_data)
            async with self._lock:
                self._buffer.append(event)
                self._events_recorded += 1
            logger.debug(
                "audit_event_recorded",
                extra={
                    "component": "Audit",
                    "event_type": event.event_type.value,
                    "event_id": event.event_id,
                    "plan_id": event.plan_id,
                },
            )
            # Auto-flush if threshold reached
            if len(self._buffer) >= self._flush_threshold:
                await self._flush_buffer()
        except Exception as exc:
            logger.error(
                "audit_record_error",
                extra={
                    "component": "Audit",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

    async def query(
        self,
        plan_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        event_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> AuditQueryResult:
        """Query audit events with filters. Returns paginated results."""
        params = AuditQueryParams(
            plan_id=plan_id,
            user_id=user_id,
            trace_id=trace_id,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            cursor=cursor,
            limit=limit,
        )
        result = await self._db.query_events(params)
        self._events_queried += 1
        logger.info(
            "audit_query_executed",
            extra={
                "component": "Audit",
                "total_count": result.total_count,
                "returned": len(result.events),
            },
        )
        return result

    async def flush(self) -> None:
        """Force-flush the in-memory buffer to the database (public)."""
        await self._flush_buffer()

    async def start(self) -> None:
        """Start background flush loop."""
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(
                self._run_flush_loop(),
                name="audit-flush-loop",
            )

    async def stop(self) -> None:
        """Cancel flush loop and perform final flush."""
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None
        await self._flush_buffer()

    async def cleanup_expired(self) -> int:
        """Delete audit events older than retention_days.

        Returns the number of deleted events.
        """
        cutoff = datetime.now(UTC) - timedelta(
            days=self._retention_days,
        )
        deleted = await self._db.delete_expired(before=cutoff)
        logger.info(
            "audit_retention_cleanup",
            extra={
                "component": "Audit",
                "deleted_count": deleted,
                "retention_days": self._retention_days,
            },
        )
        return deleted

    # ---------------------------------------------------------------
    # Private methods
    # ---------------------------------------------------------------

    def _sanitize(self, event_data: dict[str, Any]) -> dict[str, Any]:
        """Strip PII/secrets from event_data in-place.

        - Removes keys matching sensitive patterns (case-insensitive)
        - Truncates error_details to 500 chars
        - Operates recursively on nested dicts
        """
        keys_to_remove = [k for k in event_data if k.lower() in _SENSITIVE_KEYS]
        for k in keys_to_remove:
            del event_data[k]

        # Truncate error_details
        if "error_details" in event_data:
            val = event_data["error_details"]
            if isinstance(val, str) and len(val) > _MAX_ERROR_DETAILS_LEN:
                event_data["error_details"] = val[:_MAX_ERROR_DETAILS_LEN]

        # Recurse into nested dicts
        for _key, val in list(event_data.items()):
            if isinstance(val, dict):
                self._sanitize(val)

        return event_data

    async def _flush_buffer(self) -> None:
        """Flush buffered events to the database.

        On DB error, events are re-added to buffer.
        On overflow (> max_buffer_size), oldest events are dropped.
        """
        async with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer)
            self._buffer.clear()

        try:
            await self._db.append_events_batch(batch)
            logger.info(
                "audit_buffer_flushed",
                extra={
                    "component": "Audit",
                    "batch_size": len(batch),
                },
            )
        except Exception as exc:
            logger.error(
                "audit_db_error",
                extra={
                    "component": "Audit",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "batch_size": len(batch),
                },
            )
            # Re-add events to buffer
            async with self._lock:
                self._buffer = batch + self._buffer
                # Check overflow
                if len(self._buffer) > self._max_buffer_size:
                    overflow = len(self._buffer) - self._max_buffer_size
                    self._buffer = self._buffer[overflow:]
                    self._buffer_overflows += 1
                    logger.warning(
                        "audit_buffer_overflow",
                        extra={
                            "component": "Audit",
                            "dropped_count": overflow,
                            "buffer_size": len(self._buffer),
                        },
                    )

    async def _run_flush_loop(self) -> None:
        """Background loop that periodically flushes the buffer."""
        while True:
            await asyncio.sleep(self._flush_interval_s)
            try:
                await self._flush_buffer()
            except Exception as exc:
                logger.error(
                    "audit_flush_loop_error",
                    extra={
                        "component": "Audit",
                        "error": str(exc),
                    },
                )
