"""
Cron Builder — RecurrenceConfig to APScheduler Trigger Conversion

Converts the UI-friendly RecurrenceConfig into APScheduler CronTrigger kwargs
and generates human-readable cron expression strings for display.
"""

from __future__ import annotations

from ..domain.models import RecurrenceConfig, ScheduleValidationError

# Day-of-week names for display
_DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_time(time_of_day: str | None) -> tuple[int, int]:
    """Parse HH:MM string into (hour, minute). Defaults to 00:00."""
    if not time_of_day:
        return 0, 0
    parts = time_of_day.split(":")
    return int(parts[0]), int(parts[1])


def recurrence_to_trigger_kwargs(config: RecurrenceConfig) -> dict:
    """
    Convert RecurrenceConfig to APScheduler CronTrigger keyword arguments.

    Returns:
        Dict of kwargs suitable for ``CronTrigger(**kwargs)``.

    Raises:
        ScheduleValidationError: If the config is invalid.
    """
    hour, minute = _parse_time(config.time_of_day)
    freq = config.frequency
    interval = config.interval

    if freq == "hourly":
        return {
            "hour": f"*/{interval}" if interval > 1 else "*",
            "minute": str(minute),
        }

    if freq == "daily":
        if interval == 1:
            return {"hour": str(hour), "minute": str(minute)}
        return {
            "day": f"*/{interval}",
            "hour": str(hour),
            "minute": str(minute),
        }

    if freq == "weekly":
        days = config.days_of_week
        if not days:
            raise ScheduleValidationError(
                "days_of_week is required for weekly recurrence"
            )
        # APScheduler uses 0=Mon..6=Sun (same as our model)
        dow_str = ",".join(str(d) for d in sorted(days))
        return {
            "day_of_week": dow_str,
            "hour": str(hour),
            "minute": str(minute),
        }

    if freq == "monthly":
        day = config.day_of_month
        if not day:
            raise ScheduleValidationError(
                "day_of_month is required for monthly recurrence"
            )
        return {
            "day": str(day),
            "hour": str(hour),
            "minute": str(minute),
        }

    raise ScheduleValidationError(f"Unknown frequency: {freq}")


def recurrence_to_display(config: RecurrenceConfig) -> str:
    """
    Generate a human-readable cron expression string for display.

    Examples:
        "Every hour at :30"
        "Every day at 09:00"
        "Every weekday at 09:00"
        "Every Mon, Wed, Fri at 14:30"
        "Every 2 weeks on Mon at 10:00"
        "Monthly on the 15th at 09:00"
    """
    hour, minute = _parse_time(config.time_of_day)
    time_str = f"{hour:02d}:{minute:02d}"
    freq = config.frequency
    interval = config.interval

    if freq == "hourly":
        base = f"Every {interval} hours" if interval > 1 else "Every hour"
        return f"{base} at :{minute:02d}"

    if freq == "daily":
        base = f"Every {interval} days" if interval > 1 else "Every day"
        return f"{base} at {time_str}"

    if freq == "weekly":
        days = sorted(config.days_of_week or [])
        if days == [0, 1, 2, 3, 4]:
            day_label = "weekday"
        elif days == [5, 6]:
            day_label = "weekend"
        else:
            day_label = ", ".join(_DOW_NAMES[d] for d in days)

        if interval > 1:
            return f"Every {interval} weeks on {day_label} at {time_str}"
        return f"Every {day_label} at {time_str}"

    if freq == "monthly":
        day = config.day_of_month or 1
        suffix = "th"
        if day in (1, 21, 31):
            suffix = "st"
        elif day in (2, 22):
            suffix = "nd"
        elif day in (3, 23):
            suffix = "rd"
        base = f"Every {interval} months" if interval > 1 else "Monthly"
        return f"{base} on the {day}{suffix} at {time_str}"

    return f"Custom schedule ({freq})"
