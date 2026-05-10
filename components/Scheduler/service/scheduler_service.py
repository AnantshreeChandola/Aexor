"""
Scheduler Service — APScheduler Lifecycle & Job Execution

Manages the AsyncIOScheduler with in-memory job store, registers jobs from
the database on startup, and executes scheduled plans through the same
pipeline as the frontend's executeFromBuilder() flow.
"""

from __future__ import annotations

import contextlib
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from ..adapters.cron_builder import recurrence_to_display, recurrence_to_trigger_kwargs
from ..adapters.db import SchedulerDatabaseAdapter
from ..domain.models import (
    CreateScheduledPlanRequest,
    RecurrenceConfig,
    ScheduledPlan,
    ScheduledPlanNotFoundError,
    ScheduleValidationError,
    UpdateScheduledPlanRequest,
)

logger = logging.getLogger(__name__)

_READ_ONLY_INTENT_RE = re.compile(
    r"^(list|check|show|get|view|find|search|query|look_?up|fetch|read|display)",
    re.IGNORECASE,
)


class SchedulerService:
    """
    Manages scheduled plan lifecycle using APScheduler.

    In-memory job store + PostgreSQL as source of truth.
    On startup, all active schedules are loaded from DB and re-registered.
    """

    def __init__(
        self,
        db: SchedulerDatabaseAdapter,
        planner_service: Any,
        execute_service: Any,
        approval_service: Any,
        plan_service: Any,
    ) -> None:
        self._db = db
        self._planner = planner_service
        self._execute = execute_service
        self._approval = approval_service
        self._plan_service = plan_service
        self._scheduler: AsyncIOScheduler | None = None

    @staticmethod
    def _infer_approval_mode(intent_type: str) -> str:
        """Infer approval mode from intent type.

        Read-only intents → auto_approve (no side-effects, safe to auto-resolve gates).
        Write intents → notify_and_wait (requires human confirmation).
        """
        if _READ_ONLY_INTENT_RE.match(intent_type):
            return "auto_approve"
        return "notify_and_wait"

    async def start(self) -> None:
        """Create and start the APScheduler, reload active jobs from DB."""
        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
        )
        self._scheduler.start()
        logger.info("APScheduler started")

        # Reload active schedules from DB
        try:
            active = await self._db.get_active_schedules()
            for schedule in active:
                try:
                    self._register_job(schedule)
                except Exception as exc:
                    logger.warning(
                        "Failed to re-register schedule %s: %s",
                        schedule.id, exc,
                    )
            logger.info("Loaded %d active schedules from DB", len(active))
        except Exception as exc:
            logger.warning("Failed to load active schedules: %s", exc)

    async def stop(self) -> None:
        """Shutdown the APScheduler."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(
        self, user_id: uuid.UUID, request: CreateScheduledPlanRequest,
    ) -> ScheduledPlan:
        """Create a new scheduled plan and register the APScheduler job."""
        # Validate
        if request.schedule_type == "once":
            if request.scheduled_at is None:
                raise ScheduleValidationError(
                    "scheduled_at is required for one-time schedules"
                )
            if request.scheduled_at.replace(tzinfo=None) < datetime.now(UTC).replace(tzinfo=None):
                raise ScheduleValidationError("scheduled_at must be in the future")
            next_run_at = request.scheduled_at
            cron_expression = None
            recurrence_dict = None
        elif request.schedule_type == "recurring":
            if request.recurrence_config is None:
                raise ScheduleValidationError(
                    "recurrence_config is required for recurring schedules"
                )
            cron_expression = recurrence_to_display(request.recurrence_config)
            recurrence_dict = request.recurrence_config.model_dump(mode="json")
            # Compute next_run_at (APScheduler will handle actual timing)
            next_run_at = self._compute_next_run(request.recurrence_config, request.timezone)
        else:
            raise ScheduleValidationError(f"Invalid schedule_type: {request.schedule_type}")

        # Resolve approval mode: explicit user choice wins, else infer from intent
        effective_approval_mode = request.approval_mode or self._infer_approval_mode(
            request.intent_type,
        )

        # Persist to DB
        schedule = await self._db.create_scheduled_plan(
            user_id=user_id,
            name=request.name,
            intent_type=request.intent_type,
            skeleton_json=request.skeleton_json,
            entities_json=request.entities_json,
            constraints_json=request.constraints_json,
            schedule_type=request.schedule_type,
            scheduled_at=request.scheduled_at,
            cron_expression=cron_expression,
            recurrence_config=recurrence_dict,
            timezone=request.timezone,
            approval_mode=effective_approval_mode,
            next_run_at=next_run_at,
            max_runs=request.max_runs,
            source_plan_id=request.source_plan_id,
        )

        # Register APScheduler job
        self._register_job(schedule)
        logger.info("Created schedule %s (%s)", schedule.id, schedule.schedule_type)
        return schedule

    async def get(
        self, schedule_id: uuid.UUID, user_id: uuid.UUID,
    ) -> ScheduledPlan:
        """Get a single scheduled plan."""
        schedule = await self._db.get_scheduled_plan(schedule_id, user_id)
        if schedule is None:
            raise ScheduledPlanNotFoundError(f"Schedule {schedule_id} not found")
        return schedule

    async def list(
        self, user_id: uuid.UUID, status_filter: str | None = None,
    ) -> list[ScheduledPlan]:
        """List all scheduled plans for a user."""
        return await self._db.list_scheduled_plans(user_id, status_filter)

    async def update(
        self, schedule_id: uuid.UUID, user_id: uuid.UUID,
        request: UpdateScheduledPlanRequest,
    ) -> ScheduledPlan:
        """Update a scheduled plan: pause/resume/edit."""
        schedule = await self._db.get_scheduled_plan(schedule_id, user_id)
        if schedule is None:
            raise ScheduledPlanNotFoundError(f"Schedule {schedule_id} not found")

        fields: dict[str, Any] = {}
        if request.name is not None:
            fields["name"] = request.name
        if request.entities_json is not None:
            fields["entities_json"] = request.entities_json
        if request.timezone is not None:
            fields["timezone"] = request.timezone
        if request.approval_mode is not None:
            fields["approval_mode"] = request.approval_mode
        if request.max_runs is not None:
            fields["max_runs"] = request.max_runs

        # Handle status transitions
        if request.status is not None:
            old_status = schedule.status
            new_status = request.status

            if new_status == "paused" and old_status == "active":
                self._remove_job(schedule_id)
                fields["status"] = "paused"

            elif new_status == "active" and old_status in ("paused", "failed"):
                fields["status"] = "active"
                # Will re-register after update

            elif new_status == "cancelled":
                self._remove_job(schedule_id)
                fields["status"] = "cancelled"

            else:
                fields["status"] = new_status

        # Handle schedule edits
        if request.scheduled_at is not None and schedule.schedule_type == "once":
            fields["scheduled_at"] = request.scheduled_at
            fields["next_run_at"] = request.scheduled_at

        if request.recurrence_config is not None and schedule.schedule_type == "recurring":
            fields["recurrence_config"] = request.recurrence_config.model_dump()
            fields["cron_expression"] = recurrence_to_display(request.recurrence_config)
            tz = request.timezone or schedule.timezone
            fields["next_run_at"] = self._compute_next_run(request.recurrence_config, tz)

        await self._db.update_scheduled_plan(schedule_id, user_id, **fields)

        # Re-register job if now active
        updated = await self._db.get_scheduled_plan(schedule_id, user_id)
        if updated and updated.status == "active":
            self._remove_job(schedule_id)
            self._register_job(updated)

        return updated

    async def delete(
        self, schedule_id: uuid.UUID, user_id: uuid.UUID,
    ) -> None:
        """Delete a scheduled plan and remove the APScheduler job."""
        self._remove_job(schedule_id)
        deleted = await self._db.delete_scheduled_plan(schedule_id, user_id)
        if not deleted:
            raise ScheduledPlanNotFoundError(f"Schedule {schedule_id} not found")
        logger.info("Deleted schedule %s", schedule_id)

    # ------------------------------------------------------------------
    # Job Registration
    # ------------------------------------------------------------------

    def _register_job(self, schedule: ScheduledPlan) -> None:
        """Register an APScheduler job for a schedule."""
        if self._scheduler is None:
            return

        job_id = f"scheduled_plan_{schedule.id}"

        if schedule.schedule_type == "once" and schedule.scheduled_at:
            trigger = DateTrigger(
                run_date=schedule.scheduled_at,
                timezone=schedule.timezone,
            )
        elif schedule.schedule_type == "recurring" and schedule.recurrence_config:
            config = RecurrenceConfig.model_validate(schedule.recurrence_config)
            kwargs = recurrence_to_trigger_kwargs(config)
            trigger = CronTrigger(timezone=schedule.timezone, **kwargs)

            # Set end_date if configured
            if config.end_date:
                trigger.end_date = config.end_date
        else:
            logger.warning("Cannot register job for schedule %s: missing trigger data", schedule.id)
            return

        self._scheduler.add_job(
            self._execute_scheduled_plan,
            trigger=trigger,
            id=job_id,
            args=[str(schedule.id)],
            replace_existing=True,
            name=f"schedule:{schedule.name}",
        )
        logger.info("Registered job %s (type=%s)", job_id, schedule.schedule_type)

    def _remove_job(self, schedule_id: uuid.UUID) -> None:
        """Remove an APScheduler job if it exists."""
        if self._scheduler is None:
            return
        job_id = f"scheduled_plan_{schedule_id}"
        with contextlib.suppress(Exception):
            self._scheduler.remove_job(job_id)

    # ------------------------------------------------------------------
    # Job Execution
    # ------------------------------------------------------------------

    async def _execute_scheduled_plan(self, schedule_id: str) -> None:
        """
        Execute a scheduled plan — called by APScheduler at trigger time.

        Replicates the pipeline from orchestrate_routes:
        1. Load schedule from DB
        2. Build Intent from stored skeleton + entities
        3. Generate plan via PlannerService
        4. Issue approval token via ApprovalGate
        5. Execute via ExecuteOrchestrator
        6. Handle GateApprovalRequired based on approval_mode
        7. Record outcome
        """
        from uuid import UUID as UUID_

        sid = UUID_(schedule_id)
        logger.info("Executing scheduled plan %s", schedule_id)

        try:
            # 1. Load schedule from DB (no user scoping — internal call)
            schedules = await self._db.get_active_schedules()
            schedule = next(
                (s for s in schedules if str(s.id) == schedule_id),
                None,
            )
            if schedule is None:
                logger.warning("Schedule %s not found or not active, skipping", schedule_id)
                return

            if schedule.status != "active":
                logger.info("Schedule %s is %s, skipping execution", schedule_id, schedule.status)
                return

            # Guard: required services
            if self._planner is None or self._execute is None or self._approval is None:
                logger.error("Required services not available for schedule %s", schedule_id)
                await self._db.record_execution(
                    sid, success=False,
                    error_type="SERVICE_UNAVAILABLE",
                    error_details={"message": "Required services not initialized"},
                )
                return

            # 2. Build Intent
            from shared.schemas.intent import Intent

            intent = Intent(
                intent=schedule.intent_type,
                entities=schedule.entities_json,
                constraints=schedule.constraints_json,
                user_id=str(schedule.user_id),
                tz=schedule.timezone,
            )

            # 3. Generate plan
            planner_result = await self._planner.generate_plan(intent)
            plan = planner_result.plan

            # 4. Issue approval token
            from components.ApprovalGate.domain.models import ApprovalRequest

            approval_request = ApprovalRequest(
                plan_id=plan.plan_id,
                user_id=str(schedule.user_id),
                gate_id="gate-A",
                scopes=["default"],
                trace_id=plan.trace_id or uuid.uuid4().hex,
            )
            approval_token = await self._approval.approve(approval_request)

            # 5. Execute plan
            from components.ExecuteOrchestrator.domain.models import (
                ExecuteRequest,
                GateApprovalRequired,
            )

            execute_request = ExecuteRequest(
                plan=plan,
                approval_token=approval_token.token,
                user_id=str(schedule.user_id),
                trace_id=plan.trace_id or uuid.uuid4().hex,
            )

            try:
                await self._execute.execute_plan(execute_request)
            except GateApprovalRequired as gate_exc:
                # 6. Handle gate based on approval_mode
                if schedule.approval_mode == "auto_approve":
                    # Auto-approve and retry with gate context
                    logger.info(
                        "Auto-approving gate %s for schedule %s",
                        gate_exc.gate_id, schedule_id,
                    )
                    preview_state = gate_exc.partial_results or {}
                    preview_state[f"gate_{gate_exc.gate_id}_approved"] = True

                    retry_request = ExecuteRequest(
                        plan=plan,
                        approval_token=approval_token.token,
                        user_id=str(schedule.user_id),
                        trace_id=plan.trace_id or uuid.uuid4().hex,
                        preview_state=preview_state,
                    )
                    await self._execute.execute_plan(retry_request)
                else:
                    # notify_and_wait: record as needing manual approval
                    logger.info(
                        "Schedule %s hit gate %s, approval_mode=%s — pausing",
                        schedule_id, gate_exc.gate_id, schedule.approval_mode,
                    )
                    await self._db.record_execution(
                        sid, success=False,
                        error_type="GATE_APPROVAL_REQUIRED",
                        error_details={
                            "gate_id": gate_exc.gate_id,
                            "step": gate_exc.step,
                            "message": str(gate_exc),
                        },
                    )
                    # Pause the schedule so user can intervene
                    await self._db.update_scheduled_plan(
                        sid, schedule.user_id, status="paused",
                    )
                    self._remove_job(sid)
                    return

            # 7. Record success
            await self._db.record_execution(sid, success=True)

            # Update next_run_at for recurring schedules
            if schedule.schedule_type == "recurring" and schedule.recurrence_config:
                config = RecurrenceConfig.model_validate(schedule.recurrence_config)
                next_run = self._compute_next_run(config, schedule.timezone)
                await self._db.update_scheduled_plan(
                    sid, schedule.user_id, next_run_at=next_run,
                )

            logger.info(
                "Schedule %s executed successfully (run #%d)",
                schedule_id, (schedule.run_count or 0) + 1,
            )

        except Exception as exc:
            logger.error("Schedule %s execution failed: %s", schedule_id, exc)
            try:
                await self._db.record_execution(
                    sid, success=False,
                    error_type=type(exc).__name__,
                    error_details={"message": str(exc)},
                )
            except Exception as db_exc:
                logger.error("Failed to record execution error: %s", db_exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_next_run(
        self, config: RecurrenceConfig, timezone: str,
    ) -> datetime:
        """Compute the next run time from a RecurrenceConfig."""
        try:
            kwargs = recurrence_to_trigger_kwargs(config)
            trigger = CronTrigger(timezone=timezone, **kwargs)
            next_fire = trigger.get_next_fire_time(None, datetime.now(UTC))
            return next_fire if next_fire else datetime.now(UTC)
        except Exception:
            return datetime.now(UTC)
