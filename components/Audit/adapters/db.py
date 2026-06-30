"""
Database Adapter for Audit

Async SQLAlchemy 2.0 operations for the audit_events table.
Append-only writes, filtered queries with cursor pagination,
and retention cleanup.

Reference: LLD.md Section 7
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import delete, func, select

from shared.database.adapter import get_database_adapter
from shared.database.models import AuditEventTable

from ..domain.models import AuditEvent, AuditQueryParams, AuditQueryResult

logger = logging.getLogger(__name__)


@runtime_checkable
class AuditDatabaseAdapterProtocol(Protocol):
    """Protocol for audit database operations."""

    async def append_event(self, event: AuditEvent) -> None: ...

    async def append_events_batch(
        self,
        events: list[AuditEvent],
    ) -> None: ...

    async def query_events(
        self,
        params: AuditQueryParams,
    ) -> AuditQueryResult: ...

    async def delete_expired(self, before: datetime) -> int: ...


def _event_to_row(event: AuditEvent) -> dict[str, Any]:
    """Convert an AuditEvent model to a dict for INSERT."""
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "plan_id": event.plan_id,
        "user_id": event.user_id,
        "trace_id": event.trace_id,
        "step_number": event.step_number,
        "event_data": event.event_data,
        "created_at": event.created_at,
    }


def _row_to_event(row: AuditEventTable) -> AuditEvent:
    """Convert an AuditEventTable row to an AuditEvent model."""
    return AuditEvent(
        event_id=row.event_id,
        event_type=row.event_type,
        plan_id=row.plan_id,
        user_id=row.user_id,
        trace_id=row.trace_id,
        step_number=row.step_number,
        event_data=row.event_data or {},
        created_at=row.created_at,
    )


class AuditDatabaseAdapter:
    """Audit database adapter using shared infrastructure.

    Provides append-only INSERT, filtered SELECT with cursor pagination,
    and retention cleanup DELETE. No UPDATE method (append-only invariant).
    """

    def __init__(self) -> None:
        self.shared_db = get_database_adapter()
        logger.info(
            "audit_db_initialized",
            extra={"component": "Audit"},
        )

    async def append_event(self, event: AuditEvent) -> None:
        """Insert a single audit event."""
        async with self.shared_db.get_session() as session, session.begin():
            row = AuditEventTable(**_event_to_row(event))
            session.add(row)

    async def append_events_batch(
        self,
        events: list[AuditEvent],
    ) -> None:
        """Bulk insert multiple audit events."""
        if not events:
            return
        async with self.shared_db.get_session() as session, session.begin():
            rows = [AuditEventTable(**_event_to_row(e)) for e in events]
            session.add_all(rows)

    async def query_events(
        self,
        params: AuditQueryParams,
    ) -> AuditQueryResult:
        """Query audit events with dynamic filters and cursor pagination.

        Cursor pagination uses event_id > cursor ORDER BY event_id ASC.
        Total count uses the same WHERE filters but no cursor/limit.
        """
        async with self.shared_db.get_session() as session:
            # Build base WHERE conditions
            conditions = self._build_conditions(params)

            # Total count (same filters, no cursor, no limit)
            count_conditions = self._build_conditions(
                params,
                include_cursor=False,
            )
            count_stmt = select(
                func.count(),
            ).select_from(AuditEventTable)
            for cond in count_conditions:
                count_stmt = count_stmt.where(cond)
            count_result = await session.execute(count_stmt)
            total_count = count_result.scalar() or 0

            # Paginated query
            stmt = select(AuditEventTable)
            for cond in conditions:
                stmt = stmt.where(cond)
            stmt = stmt.order_by(
                AuditEventTable.event_id,
            ).limit(params.limit)

            result = await session.execute(stmt)
            rows = result.scalars().all()
            events = [_row_to_event(row) for row in rows]

            # Determine next_cursor
            next_cursor = None
            if events and len(events) == params.limit:
                next_cursor = events[-1].event_id

            return AuditQueryResult(
                events=events,
                next_cursor=next_cursor,
                total_count=total_count,
            )

    async def delete_expired(self, before: datetime) -> int:
        """Delete audit events older than the given datetime.

        Returns the number of deleted rows.
        """
        async with self.shared_db.get_session() as session, session.begin():
            stmt = delete(AuditEventTable).where(
                AuditEventTable.created_at < before,
            )
            result = await session.execute(stmt)
            return result.rowcount

    @staticmethod
    def _build_conditions(
        params: AuditQueryParams,
        include_cursor: bool = True,
    ) -> list:
        """Build SQLAlchemy WHERE conditions from query params."""
        conditions: list = []
        if params.plan_id is not None:
            conditions.append(
                AuditEventTable.plan_id == params.plan_id,
            )
        if params.user_id is not None:
            conditions.append(
                AuditEventTable.user_id == params.user_id,
            )
        if params.trace_id is not None:
            conditions.append(
                AuditEventTable.trace_id == params.trace_id,
            )
        if params.event_type is not None:
            conditions.append(
                AuditEventTable.event_type == params.event_type,
            )
        if params.start_time is not None:
            conditions.append(
                AuditEventTable.created_at >= params.start_time,
            )
        if params.end_time is not None:
            conditions.append(
                AuditEventTable.created_at <= params.end_time,
            )
        if include_cursor and params.cursor is not None:
            conditions.append(
                AuditEventTable.event_id > params.cursor,
            )
        return conditions
