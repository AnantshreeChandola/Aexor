"""
TrackerService — Non-fatal Write API for ExecuteOrchestrator

Every method is wrapped in try/except so tracker failure never breaks
plan execution. Called by ExecuteOrchestrator to register executions,
report progress, and mark completion.

Reference: Project_HLD.md §13, SPEC 031 FR-001/FR-003
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TrackerService:
    """Non-fatal execution tracker write API.

    ExecuteOrchestrator calls these methods at key execution milestones.
    All methods catch exceptions internally — tracker failure must never
    break plan execution.
    """

    def __init__(self, tracker_db: Any) -> None:
        self._db = tracker_db

    async def register(
        self,
        plan_id: str,
        user_id: str,
        trace_id: str,
        total_steps: int,
    ) -> None:
        """Register a new execution. Non-fatal."""
        try:
            await self._db.create_tracker(
                plan_id=plan_id,
                user_id=user_id,
                trace_id=trace_id,
                total_steps=total_steps,
            )
            logger.info(
                "tracker_registered",
                extra={
                    "component": "ExecutionMonitor",
                    "plan_id": plan_id,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "total_steps": total_steps,
                },
            )
        except Exception as exc:
            logger.warning(
                "tracker_register_failed",
                extra={
                    "component": "ExecutionMonitor",
                    "plan_id": plan_id,
                    "error": str(exc),
                },
            )

    async def report_progress(
        self,
        plan_id: str,
        completed_steps: int,
    ) -> None:
        """Update completed step count and last_progress_at. Non-fatal."""
        try:
            await self._db.update_progress(
                plan_id=plan_id,
                completed_steps=completed_steps,
            )
            logger.info(
                "tracker_progress",
                extra={
                    "component": "ExecutionMonitor",
                    "plan_id": plan_id,
                    "completed_steps": completed_steps,
                },
            )
        except Exception as exc:
            logger.warning(
                "tracker_progress_failed",
                extra={
                    "component": "ExecutionMonitor",
                    "plan_id": plan_id,
                    "error": str(exc),
                },
            )

    async def complete(
        self,
        plan_id: str,
        success: bool,
        error_type: str | None = None,
        error_details: dict | None = None,
    ) -> None:
        """Mark execution as completed or failed. Non-fatal."""
        try:
            status = "completed" if success else "failed"
            await self._db.mark_terminal(
                plan_id=plan_id,
                status=status,
                error_type=error_type,
                error_details=error_details,
            )
            logger.info(
                "tracker_completed",
                extra={
                    "component": "ExecutionMonitor",
                    "plan_id": plan_id,
                    "status": status,
                    "success": success,
                    "error_type": error_type,
                },
            )
        except Exception as exc:
            logger.warning(
                "tracker_complete_failed",
                extra={
                    "component": "ExecutionMonitor",
                    "plan_id": plan_id,
                    "error": str(exc),
                },
            )
