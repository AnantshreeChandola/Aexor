"""
Tests for Scheduler service.

Unit tests for SchedulerService using mocked dependencies.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from components.Scheduler.domain.models import (
    CreateScheduledPlanRequest,
    RecurrenceConfig,
    ScheduledPlan,
    ScheduledPlanNotFoundError,
    ScheduleValidationError,
    UpdateScheduledPlanRequest,
)
from components.Scheduler.service.scheduler_service import SchedulerService


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_active_schedules = AsyncMock(return_value=[])
    return db


@pytest.fixture
def mock_services():
    return {
        "planner_service": MagicMock(),
        "execute_service": MagicMock(),
        "approval_service": MagicMock(),
        "plan_service": MagicMock(),
    }


@pytest.fixture
def service(mock_db, mock_services):
    return SchedulerService(
        db=mock_db,
        planner_service=mock_services["planner_service"],
        execute_service=mock_services["execute_service"],
        approval_service=mock_services["approval_service"],
        plan_service=mock_services["plan_service"],
    )


@pytest.fixture
def sample_schedule():
    return ScheduledPlan(
        id=uuid4(),
        user_id=uuid4(),
        name="Test Schedule",
        intent_type="schedule_meeting",
        skeleton_json={"intent": "schedule_meeting", "steps": []},
        entities_json={"title": "Standup"},
        schedule_type="once",
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        status="active",
        approval_mode="auto_approve",
        timezone="UTC",
        run_count=0,
    )


class TestSchedulerServiceCreate:
    """Test schedule creation."""

    async def test_create_once_success(self, service, mock_db):
        user_id = uuid4()
        future = datetime.now(UTC) + timedelta(hours=1)
        request = CreateScheduledPlanRequest(
            name="Test",
            intent_type="schedule_meeting",
            skeleton_json={"intent": "schedule_meeting"},
            schedule_type="once",
            scheduled_at=future,
            timezone="UTC",
        )

        expected = ScheduledPlan(
            id=uuid4(), user_id=user_id, name="Test",
            intent_type="schedule_meeting",
            skeleton_json={"intent": "schedule_meeting"},
            schedule_type="once", scheduled_at=future,
            status="active", timezone="UTC", approval_mode="notify_and_wait",
        )
        mock_db.create_scheduled_plan = AsyncMock(return_value=expected)

        with patch.object(service, '_register_job'):
            result = await service.create(user_id, request)

        assert result.name == "Test"
        assert result.schedule_type == "once"
        # Write intent with no explicit approval_mode → notify_and_wait
        call_kwargs = mock_db.create_scheduled_plan.call_args.kwargs
        assert call_kwargs["approval_mode"] == "notify_and_wait"

    async def test_create_once_past_date_raises(self, service):
        user_id = uuid4()
        past = datetime.now(UTC) - timedelta(hours=1)
        request = CreateScheduledPlanRequest(
            name="Test",
            intent_type="test",
            skeleton_json={},
            schedule_type="once",
            scheduled_at=past,
            timezone="UTC",
        )

        with pytest.raises(ScheduleValidationError, match="future"):
            await service.create(user_id, request)

    async def test_create_once_missing_scheduled_at_raises(self, service):
        user_id = uuid4()
        request = CreateScheduledPlanRequest(
            name="Test",
            intent_type="test",
            skeleton_json={},
            schedule_type="once",
            timezone="UTC",
        )

        with pytest.raises(ScheduleValidationError, match="scheduled_at"):
            await service.create(user_id, request)

    async def test_create_recurring_success(self, service, mock_db):
        user_id = uuid4()
        request = CreateScheduledPlanRequest(
            name="Daily Standup",
            intent_type="schedule_meeting",
            skeleton_json={"intent": "schedule_meeting"},
            schedule_type="recurring",
            recurrence_config=RecurrenceConfig(
                frequency="daily", interval=1, time_of_day="09:00",
            ),
            timezone="America/Chicago",
        )

        expected = ScheduledPlan(
            id=uuid4(), user_id=user_id, name="Daily Standup",
            intent_type="schedule_meeting",
            skeleton_json={"intent": "schedule_meeting"},
            schedule_type="recurring",
            cron_expression="Every day at 09:00",
            recurrence_config={"frequency": "daily", "interval": 1, "time_of_day": "09:00"},
            status="active", timezone="America/Chicago",
            approval_mode="notify_and_wait",
        )
        mock_db.create_scheduled_plan = AsyncMock(return_value=expected)

        with patch.object(service, '_register_job'):
            result = await service.create(user_id, request)

        assert result.schedule_type == "recurring"
        assert result.cron_expression == "Every day at 09:00"
        # Write intent with no explicit approval_mode → notify_and_wait
        call_kwargs = mock_db.create_scheduled_plan.call_args.kwargs
        assert call_kwargs["approval_mode"] == "notify_and_wait"

    async def test_create_recurring_missing_config_raises(self, service):
        user_id = uuid4()
        request = CreateScheduledPlanRequest(
            name="Test",
            intent_type="test",
            skeleton_json={},
            schedule_type="recurring",
            timezone="UTC",
        )

        with pytest.raises(ScheduleValidationError, match="recurrence_config"):
            await service.create(user_id, request)


class TestSchedulerServiceCRUD:
    """Test get/list/update/delete operations."""

    async def test_get_success(self, service, mock_db, sample_schedule):
        mock_db.get_scheduled_plan = AsyncMock(return_value=sample_schedule)
        result = await service.get(sample_schedule.id, sample_schedule.user_id)
        assert result.id == sample_schedule.id

    async def test_get_not_found_raises(self, service, mock_db):
        mock_db.get_scheduled_plan = AsyncMock(return_value=None)
        with pytest.raises(ScheduledPlanNotFoundError):
            await service.get(uuid4(), uuid4())

    async def test_list_returns_all(self, service, mock_db, sample_schedule):
        mock_db.list_scheduled_plans = AsyncMock(return_value=[sample_schedule])
        result = await service.list(sample_schedule.user_id)
        assert len(result) == 1

    async def test_delete_success(self, service, mock_db, sample_schedule):
        mock_db.delete_scheduled_plan = AsyncMock(return_value=True)
        with patch.object(service, '_remove_job'):
            await service.delete(sample_schedule.id, sample_schedule.user_id)
        mock_db.delete_scheduled_plan.assert_called_once()

    async def test_delete_not_found_raises(self, service, mock_db):
        mock_db.delete_scheduled_plan = AsyncMock(return_value=False)
        with patch.object(service, '_remove_job'), pytest.raises(ScheduledPlanNotFoundError):
            await service.delete(uuid4(), uuid4())

    async def test_update_pause(self, service, mock_db, sample_schedule):
        mock_db.get_scheduled_plan = AsyncMock(return_value=sample_schedule)
        mock_db.update_scheduled_plan = AsyncMock(return_value=True)

        paused = sample_schedule.model_copy(update={"status": "paused"})
        # Second call returns paused version
        mock_db.get_scheduled_plan = AsyncMock(side_effect=[sample_schedule, paused])

        request = UpdateScheduledPlanRequest(status="paused")
        with patch.object(service, '_remove_job') as remove_mock:
            await service.update(sample_schedule.id, sample_schedule.user_id, request)
            remove_mock.assert_called()

    async def test_update_resume(self, service, mock_db, sample_schedule):
        paused = sample_schedule.model_copy(update={"status": "paused"})
        resumed = sample_schedule.model_copy(update={"status": "active"})

        mock_db.get_scheduled_plan = AsyncMock(side_effect=[paused, resumed])
        mock_db.update_scheduled_plan = AsyncMock(return_value=True)

        request = UpdateScheduledPlanRequest(status="active")
        with patch.object(service, '_remove_job'), \
             patch.object(service, '_register_job') as reg_mock:
            await service.update(sample_schedule.id, sample_schedule.user_id, request)
            reg_mock.assert_called()


class TestSchedulerServiceApprovalModes:
    """Test approval mode configuration."""

    async def test_create_with_auto_approve(self, service, mock_db):
        user_id = uuid4()
        future = datetime.now(UTC) + timedelta(hours=1)
        request = CreateScheduledPlanRequest(
            name="Test",
            intent_type="test",
            skeleton_json={},
            schedule_type="once",
            scheduled_at=future,
            timezone="UTC",
            approval_mode="auto_approve",
        )

        expected = ScheduledPlan(
            id=uuid4(), user_id=user_id, name="Test",
            intent_type="test", skeleton_json={},
            schedule_type="once", scheduled_at=future,
            status="active", timezone="UTC",
            approval_mode="auto_approve",
        )
        mock_db.create_scheduled_plan = AsyncMock(return_value=expected)

        with patch.object(service, '_register_job'):
            result = await service.create(user_id, request)

        assert result.approval_mode == "auto_approve"

    async def test_create_with_notify_and_wait(self, service, mock_db):
        user_id = uuid4()
        future = datetime.now(UTC) + timedelta(hours=1)
        request = CreateScheduledPlanRequest(
            name="Test",
            intent_type="test",
            skeleton_json={},
            schedule_type="once",
            scheduled_at=future,
            timezone="UTC",
            approval_mode="notify_and_wait",
        )

        expected = ScheduledPlan(
            id=uuid4(), user_id=user_id, name="Test",
            intent_type="test", skeleton_json={},
            schedule_type="once", scheduled_at=future,
            status="active", timezone="UTC",
            approval_mode="notify_and_wait",
        )
        mock_db.create_scheduled_plan = AsyncMock(return_value=expected)

        with patch.object(service, '_register_job'):
            result = await service.create(user_id, request)

        assert result.approval_mode == "notify_and_wait"


class TestApprovalModeInference:
    """Test intent-aware default approval mode inference."""

    async def test_create_read_only_defaults_to_auto_approve(self, service, mock_db):
        """Read-only intent with no explicit approval_mode → auto_approve."""
        user_id = uuid4()
        future = datetime.now(UTC) + timedelta(hours=1)
        request = CreateScheduledPlanRequest(
            name="List Emails",
            intent_type="list_email",
            skeleton_json={"intent": "list_email"},
            schedule_type="once",
            scheduled_at=future,
            timezone="UTC",
        )

        expected = ScheduledPlan(
            id=uuid4(), user_id=user_id, name="List Emails",
            intent_type="list_email",
            skeleton_json={"intent": "list_email"},
            schedule_type="once", scheduled_at=future,
            status="active", timezone="UTC", approval_mode="auto_approve",
        )
        mock_db.create_scheduled_plan = AsyncMock(return_value=expected)

        with patch.object(service, '_register_job'):
            await service.create(user_id, request)

        call_kwargs = mock_db.create_scheduled_plan.call_args.kwargs
        assert call_kwargs["approval_mode"] == "auto_approve"

    async def test_create_write_defaults_to_notify_and_wait(self, service, mock_db):
        """Write intent with no explicit approval_mode → notify_and_wait."""
        user_id = uuid4()
        future = datetime.now(UTC) + timedelta(hours=1)
        request = CreateScheduledPlanRequest(
            name="Send Email",
            intent_type="send_email",
            skeleton_json={"intent": "send_email"},
            schedule_type="once",
            scheduled_at=future,
            timezone="UTC",
        )

        expected = ScheduledPlan(
            id=uuid4(), user_id=user_id, name="Send Email",
            intent_type="send_email",
            skeleton_json={"intent": "send_email"},
            schedule_type="once", scheduled_at=future,
            status="active", timezone="UTC", approval_mode="notify_and_wait",
        )
        mock_db.create_scheduled_plan = AsyncMock(return_value=expected)

        with patch.object(service, '_register_job'):
            await service.create(user_id, request)

        call_kwargs = mock_db.create_scheduled_plan.call_args.kwargs
        assert call_kwargs["approval_mode"] == "notify_and_wait"

    async def test_explicit_approval_mode_overrides_default(self, service, mock_db):
        """Explicit approval_mode overrides intent-based inference."""
        user_id = uuid4()
        future = datetime.now(UTC) + timedelta(hours=1)
        request = CreateScheduledPlanRequest(
            name="List Emails",
            intent_type="list_email",
            skeleton_json={"intent": "list_email"},
            schedule_type="once",
            scheduled_at=future,
            timezone="UTC",
            approval_mode="notify_and_wait",
        )

        expected = ScheduledPlan(
            id=uuid4(), user_id=user_id, name="List Emails",
            intent_type="list_email",
            skeleton_json={"intent": "list_email"},
            schedule_type="once", scheduled_at=future,
            status="active", timezone="UTC", approval_mode="notify_and_wait",
        )
        mock_db.create_scheduled_plan = AsyncMock(return_value=expected)

        with patch.object(service, '_register_job'):
            await service.create(user_id, request)

        # User explicitly set notify_and_wait even though it's a read-only intent
        call_kwargs = mock_db.create_scheduled_plan.call_args.kwargs
        assert call_kwargs["approval_mode"] == "notify_and_wait"

    def test_infer_approval_mode_read_intents(self):
        """Various read-only intent prefixes should yield auto_approve."""
        read_intents = [
            "list_email", "check_calendar", "show_contacts",
            "get_user_profile", "view_dashboard", "find_documents",
            "search_tickets", "query_database", "lookup_address",
            "fetch_report", "read_messages", "display_metrics",
        ]
        for intent in read_intents:
            assert SchedulerService._infer_approval_mode(intent) == "auto_approve", (
                f"Expected auto_approve for {intent}"
            )

    def test_infer_approval_mode_write_intents(self):
        """Write intent prefixes should yield notify_and_wait."""
        write_intents = [
            "send_email", "create_event", "delete_file",
            "schedule_meeting", "update_record", "move_document",
        ]
        for intent in write_intents:
            assert SchedulerService._infer_approval_mode(intent) == "notify_and_wait", (
                f"Expected notify_and_wait for {intent}"
            )
