"""
Database Adapter for ExecutionMonitor

Async SQLAlchemy 2.0 operations for the execution_tracker table.
Uses shared database utilities.

Reference: Project_HLD.md §2.14
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update

from shared.database.adapter import get_database_adapter
from shared.database.models import ExecutionTrackerTable

from ..domain.models import TrackerRecord

logger = logging.getLogger(__name__)


def _row_to_record(row: ExecutionTrackerTable) -> TrackerRecord:
    """Convert an ExecutionTrackerTable row to a TrackerRecord model."""
    return TrackerRecord(
        tracker_id=str(row.tracker_id),
        plan_id=row.plan_id,
        user_id=row.user_id,
        trace_id=row.trace_id,
        status=row.status,
        total_steps=row.total_steps,
        completed_steps=row.completed_steps,
        error_type=row.error_type,
        error_details=row.error_details,
        notification_sent=row.notification_sent,
        started_at=row.started_at,
        last_progress_at=row.last_progress_at,
        completed_at=row.completed_at,
    )


class TrackerDatabaseAdapter:
    """ExecutionMonitor database adapter using shared infrastructure."""

    def __init__(self) -> None:
        self.shared_db = get_database_adapter()
        logger.info(
            "tracker_db_initialized",
            extra={"component": "ExecutionMonitor"},
        )

    async def create_tracker(
        self,
        plan_id: str,
        user_id: str,
        trace_id: str,
        total_steps: int,
    ) -> TrackerRecord:
        """Insert a new tracker row. Returns the created TrackerRecord."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            row = ExecutionTrackerTable(
                plan_id=plan_id,
                user_id=user_id,
                trace_id=trace_id,
                total_steps=total_steps,
                status="running",
                completed_steps=0,
                notification_sent=False,
                started_at=now,
                last_progress_at=now,
            )
            session.add(row)
            await session.flush()
            record = _row_to_record(row)
        return record

    async def update_progress(self, plan_id: str, completed_steps: int) -> bool:
        """Update completed_steps and last_progress_at. Returns True if row updated."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            stmt = (
                update(ExecutionTrackerTable)
                .where(
                    ExecutionTrackerTable.plan_id == plan_id,
                    ExecutionTrackerTable.status == "running",
                )
                .values(completed_steps=completed_steps, last_progress_at=now)
            )
            result = await session.execute(stmt)
            return result.rowcount > 0

    async def mark_terminal(
        self,
        plan_id: str,
        status: str,
        error_type: str | None = None,
        error_details: dict | None = None,
    ) -> bool:
        """Mark execution as terminal (completed/stuck/timeout). Returns True if updated."""
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            stmt = (
                update(ExecutionTrackerTable)
                .where(
                    ExecutionTrackerTable.plan_id == plan_id,
                    ExecutionTrackerTable.status == "running",
                )
                .values(
                    status=status,
                    error_type=error_type,
                    error_details=error_details,
                    completed_at=now,
                )
            )
            result = await session.execute(stmt)
            return result.rowcount > 0

    async def mark_notified(self, plan_id: str) -> bool:
        """Mark notification_sent=True for a tracker. Returns True if updated."""
        async with self.shared_db.get_session() as session, session.begin():
            stmt = (
                update(ExecutionTrackerTable)
                .where(ExecutionTrackerTable.plan_id == plan_id)
                .values(notification_sent=True)
            )
            result = await session.execute(stmt)
            return result.rowcount > 0

    async def get_active_executions(self, limit: int = 100) -> list[TrackerRecord]:
        """Get all running executions, oldest first."""
        async with self.shared_db.get_session() as session:
            stmt = (
                select(ExecutionTrackerTable)
                .where(ExecutionTrackerTable.status == "running")
                .order_by(ExecutionTrackerTable.started_at)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [_row_to_record(row) for row in result.scalars().all()]

    async def get_tracker_by_plan(self, plan_id: str) -> TrackerRecord | None:
        """Get the most recent tracker for a plan_id."""
        async with self.shared_db.get_session() as session:
            stmt = (
                select(ExecutionTrackerTable)
                .where(ExecutionTrackerTable.plan_id == plan_id)
                .order_by(ExecutionTrackerTable.started_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _row_to_record(row) if row else None

    async def health_check(self) -> bool:
        """Check database connectivity."""
        return await self.shared_db.health_check()
