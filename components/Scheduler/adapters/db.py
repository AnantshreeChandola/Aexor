"""
Database Adapter for Scheduler

Async SQLAlchemy 2.0 operations for the scheduled_plans table.
Uses shared database utilities.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update

from shared.database.adapter import get_database_adapter
from shared.database.models import ScheduledPlanTable

from ..domain.models import ScheduledPlan

logger = logging.getLogger(__name__)


def _row_to_model(row: ScheduledPlanTable) -> ScheduledPlan:
    """Convert a ScheduledPlanTable row to a ScheduledPlan domain model."""
    return ScheduledPlan(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        intent_type=row.intent_type,
        skeleton_json=row.skeleton_json,
        entities_json=row.entities_json or {},
        constraints_json=row.constraints_json or {},
        schedule_type=row.schedule_type,
        scheduled_at=row.scheduled_at,
        cron_expression=row.cron_expression,
        recurrence_config=row.recurrence_config,
        timezone=row.timezone,
        status=row.status,
        approval_mode=row.approval_mode,
        last_run_at=row.last_run_at,
        next_run_at=row.next_run_at,
        run_count=row.run_count,
        max_runs=row.max_runs,
        last_error=row.last_error,
        source_plan_id=row.source_plan_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SchedulerDatabaseAdapter:
    """Scheduler database adapter using shared infrastructure."""

    def __init__(self) -> None:
        self.shared_db = get_database_adapter()
        logger.info(
            "scheduler_db_initialized",
            extra={"component": "Scheduler"},
        )

    async def create_scheduled_plan(
        self,
        user_id: UUID,
        name: str,
        intent_type: str,
        skeleton_json: dict,
        entities_json: dict,
        constraints_json: dict,
        schedule_type: str,
        scheduled_at: datetime | None,
        cron_expression: str | None,
        recurrence_config: dict | None,
        timezone: str,
        approval_mode: str,
        next_run_at: datetime | None,
        max_runs: int | None,
        source_plan_id: str | None,
    ) -> ScheduledPlan:
        """Insert a new scheduled plan. Returns the created ScheduledPlan."""
        async with self.shared_db.get_session() as session, session.begin():
            row = ScheduledPlanTable(
                user_id=user_id,
                name=name,
                intent_type=intent_type,
                skeleton_json=skeleton_json,
                entities_json=entities_json,
                constraints_json=constraints_json,
                schedule_type=schedule_type,
                scheduled_at=scheduled_at,
                cron_expression=cron_expression,
                recurrence_config=recurrence_config,
                timezone=timezone,
                status="active",
                approval_mode=approval_mode,
                next_run_at=next_run_at,
                max_runs=max_runs,
                source_plan_id=source_plan_id,
            )
            session.add(row)
            await session.flush()
            return _row_to_model(row)

    async def get_scheduled_plan(
        self, schedule_id: UUID, user_id: UUID,
    ) -> ScheduledPlan | None:
        """Get a single scheduled plan, user-scoped."""
        async with self.shared_db.get_session() as session:
            stmt = select(ScheduledPlanTable).where(
                ScheduledPlanTable.id == schedule_id,
                ScheduledPlanTable.user_id == user_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _row_to_model(row) if row else None

    async def list_scheduled_plans(
        self, user_id: UUID, status_filter: str | None = None,
    ) -> list[ScheduledPlan]:
        """List all scheduled plans for a user, optionally filtered by status."""
        async with self.shared_db.get_session() as session:
            stmt = (
                select(ScheduledPlanTable)
                .where(ScheduledPlanTable.user_id == user_id)
                .order_by(ScheduledPlanTable.created_at.desc())
            )
            if status_filter:
                stmt = stmt.where(ScheduledPlanTable.status == status_filter)
            result = await session.execute(stmt)
            return [_row_to_model(row) for row in result.scalars().all()]

    async def update_scheduled_plan(
        self, schedule_id: UUID, user_id: UUID, **fields,
    ) -> bool:
        """Partial update of a scheduled plan. Returns True if row updated."""
        fields["updated_at"] = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            stmt = (
                update(ScheduledPlanTable)
                .where(
                    ScheduledPlanTable.id == schedule_id,
                    ScheduledPlanTable.user_id == user_id,
                )
                .values(**fields)
            )
            result = await session.execute(stmt)
            return result.rowcount > 0

    async def delete_scheduled_plan(
        self, schedule_id: UUID, user_id: UUID,
    ) -> bool:
        """Hard delete a scheduled plan. Returns True if row deleted."""
        async with self.shared_db.get_session() as session, session.begin():
            stmt = delete(ScheduledPlanTable).where(
                ScheduledPlanTable.id == schedule_id,
                ScheduledPlanTable.user_id == user_id,
            )
            result = await session.execute(stmt)
            return result.rowcount > 0

    async def get_active_schedules(self) -> list[ScheduledPlan]:
        """Get all active schedules (for startup recovery)."""
        async with self.shared_db.get_session() as session:
            stmt = (
                select(ScheduledPlanTable)
                .where(ScheduledPlanTable.status == "active")
                .order_by(ScheduledPlanTable.next_run_at)
            )
            result = await session.execute(stmt)
            return [_row_to_model(row) for row in result.scalars().all()]

    async def record_execution(
        self,
        schedule_id: UUID,
        success: bool,
        error_type: str | None = None,
        error_details: dict | None = None,
    ) -> None:
        """
        Record an execution outcome: update run_count, last_run_at, last_error.
        Marks one-time schedules as 'completed'. Marks max_runs-reached as 'completed'.
        """
        now = datetime.now(UTC)
        async with self.shared_db.get_session() as session, session.begin():
            # Fetch current state
            stmt = select(ScheduledPlanTable).where(ScheduledPlanTable.id == schedule_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if not row:
                return

            new_count = (row.run_count or 0) + 1
            values: dict = {
                "run_count": new_count,
                "last_run_at": now,
                "updated_at": now,
            }

            if not success:
                values["last_error"] = {
                    "error_type": error_type,
                    "error_details": error_details,
                    "occurred_at": now.isoformat(),
                }
                values["status"] = "failed"
            else:
                values["last_error"] = None
                # Mark completed if one-time or max_runs reached
                if row.schedule_type == "once" or (row.max_runs and new_count >= row.max_runs):
                    values["status"] = "completed"

            update_stmt = (
                update(ScheduledPlanTable)
                .where(ScheduledPlanTable.id == schedule_id)
                .values(**values)
            )
            await session.execute(update_stmt)
