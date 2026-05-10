"""
Tests for Scheduler cron_builder adapter.

Validates RecurrenceConfig → APScheduler trigger kwargs conversion
and human-readable display string generation.
"""

import pytest

from components.Scheduler.adapters.cron_builder import (
    recurrence_to_display,
    recurrence_to_trigger_kwargs,
)
from components.Scheduler.domain.models import RecurrenceConfig, ScheduleValidationError


class TestRecurrenceToTriggerKwargs:
    """Test trigger kwargs generation from RecurrenceConfig."""

    def test_hourly_default(self):
        config = RecurrenceConfig(frequency="hourly", interval=1, time_of_day="00:30")
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"hour": "*", "minute": "30"}

    def test_hourly_every_2(self):
        config = RecurrenceConfig(frequency="hourly", interval=2, time_of_day="00:15")
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"hour": "*/2", "minute": "15"}

    def test_daily_default(self):
        config = RecurrenceConfig(frequency="daily", interval=1, time_of_day="09:00")
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"hour": "9", "minute": "0"}

    def test_daily_every_3(self):
        config = RecurrenceConfig(frequency="daily", interval=3, time_of_day="14:30")
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"day": "*/3", "hour": "14", "minute": "30"}

    def test_weekly_weekdays(self):
        config = RecurrenceConfig(
            frequency="weekly", interval=1,
            days_of_week=[0, 1, 2, 3, 4], time_of_day="09:00",
        )
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"day_of_week": "0,1,2,3,4", "hour": "9", "minute": "0"}

    def test_weekly_selected_days(self):
        config = RecurrenceConfig(
            frequency="weekly", interval=1,
            days_of_week=[0, 2, 4], time_of_day="10:00",
        )
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"day_of_week": "0,2,4", "hour": "10", "minute": "0"}

    def test_weekly_missing_days_raises(self):
        config = RecurrenceConfig(frequency="weekly", interval=1, time_of_day="09:00")
        with pytest.raises(ScheduleValidationError, match="days_of_week"):
            recurrence_to_trigger_kwargs(config)

    def test_monthly(self):
        config = RecurrenceConfig(
            frequency="monthly", interval=1,
            day_of_month=15, time_of_day="09:00",
        )
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"day": "15", "hour": "9", "minute": "0"}

    def test_monthly_missing_day_raises(self):
        config = RecurrenceConfig(frequency="monthly", interval=1, time_of_day="09:00")
        with pytest.raises(ScheduleValidationError, match="day_of_month"):
            recurrence_to_trigger_kwargs(config)

    def test_no_time_defaults_to_midnight(self):
        config = RecurrenceConfig(frequency="daily", interval=1)
        result = recurrence_to_trigger_kwargs(config)
        assert result == {"hour": "0", "minute": "0"}


class TestRecurrenceToDisplay:
    """Test human-readable cron expression generation."""

    def test_hourly(self):
        config = RecurrenceConfig(frequency="hourly", interval=1, time_of_day="00:30")
        assert recurrence_to_display(config) == "Every hour at :30"

    def test_hourly_every_2(self):
        config = RecurrenceConfig(frequency="hourly", interval=2, time_of_day="00:00")
        assert recurrence_to_display(config) == "Every 2 hours at :00"

    def test_daily(self):
        config = RecurrenceConfig(frequency="daily", interval=1, time_of_day="09:00")
        assert recurrence_to_display(config) == "Every day at 09:00"

    def test_daily_every_3(self):
        config = RecurrenceConfig(frequency="daily", interval=3, time_of_day="14:30")
        assert recurrence_to_display(config) == "Every 3 days at 14:30"

    def test_weekly_weekdays(self):
        config = RecurrenceConfig(
            frequency="weekly", interval=1,
            days_of_week=[0, 1, 2, 3, 4], time_of_day="09:00",
        )
        assert recurrence_to_display(config) == "Every weekday at 09:00"

    def test_weekly_weekend(self):
        config = RecurrenceConfig(
            frequency="weekly", interval=1,
            days_of_week=[5, 6], time_of_day="10:00",
        )
        assert recurrence_to_display(config) == "Every weekend at 10:00"

    def test_weekly_selected(self):
        config = RecurrenceConfig(
            frequency="weekly", interval=1,
            days_of_week=[0, 2, 4], time_of_day="14:30",
        )
        assert recurrence_to_display(config) == "Every Mon, Wed, Fri at 14:30"

    def test_weekly_biweekly(self):
        config = RecurrenceConfig(
            frequency="weekly", interval=2,
            days_of_week=[0], time_of_day="10:00",
        )
        assert recurrence_to_display(config) == "Every 2 weeks on Mon at 10:00"

    def test_monthly(self):
        config = RecurrenceConfig(
            frequency="monthly", interval=1,
            day_of_month=15, time_of_day="09:00",
        )
        assert recurrence_to_display(config) == "Monthly on the 15th at 09:00"

    def test_monthly_1st(self):
        config = RecurrenceConfig(
            frequency="monthly", interval=1,
            day_of_month=1, time_of_day="09:00",
        )
        assert recurrence_to_display(config) == "Monthly on the 1st at 09:00"

    def test_monthly_2nd(self):
        config = RecurrenceConfig(
            frequency="monthly", interval=1,
            day_of_month=2, time_of_day="09:00",
        )
        assert recurrence_to_display(config) == "Monthly on the 2nd at 09:00"

    def test_monthly_3rd(self):
        config = RecurrenceConfig(
            frequency="monthly", interval=1,
            day_of_month=3, time_of_day="09:00",
        )
        assert recurrence_to_display(config) == "Monthly on the 3rd at 09:00"

    def test_monthly_every_2(self):
        config = RecurrenceConfig(
            frequency="monthly", interval=2,
            day_of_month=15, time_of_day="09:00",
        )
        assert recurrence_to_display(config) == "Every 2 months on the 15th at 09:00"
