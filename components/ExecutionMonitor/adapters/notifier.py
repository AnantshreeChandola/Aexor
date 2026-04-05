"""
Notifier Adapter for ExecutionMonitor

Protocol for sending user notifications when executions are stuck or
timed out. LogNotifier is the default implementation that emits
structured log events. Real push notifications (webhook/SSE/email)
are deferred to future implementations.

Reference: Project_HLD.md §2.14
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from ..domain.models import UserNotification

logger = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """Protocol for sending execution failure notifications."""

    async def notify(self, notification: UserNotification) -> bool:
        """Send a notification. Returns True on success."""
        ...


class LogNotifier:
    """Default notifier — emits structured log events.

    Real push notifications (webhook, SSE, email) will implement the
    Notifier protocol in future iterations.
    """

    async def notify(self, notification: UserNotification) -> bool:
        """Log the notification as a structured event."""
        logger.warning(
            "execution_notification",
            extra={
                "component": "ExecutionMonitor",
                "plan_id": notification.plan_id,
                "user_id": notification.user_id,
                "trace_id": notification.trace_id,
                "failure_type": notification.failure_type,
                "total_steps": notification.total_steps,
                "completed_steps": notification.completed_steps,
                "notification_message": notification.message,
            },
        )
        return True
