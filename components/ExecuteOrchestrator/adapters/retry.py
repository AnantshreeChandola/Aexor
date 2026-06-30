"""
Retry Adapter

Exponential backoff retry for transient failures.

Reference: LLD.md Section 6.8
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from shared.schemas.plan import PlanStep

from ..domain.models import MCPInvocationError

logger = logging.getLogger(__name__)

_RETRYABLE_ERRORS = {"503", "504", "timeout", "connection_reset"}


class RetryPolicy:
    """Exponential backoff retry for transient failures."""

    def __init__(
        self,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
        retry_on: set[str] | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.retry_on = retry_on or _RETRYABLE_ERRORS

    async def execute_with_retry(
        self,
        operation: Callable[[], Any],
        step: PlanStep,
        plan_id: str = "",
    ) -> dict[str, Any]:
        """Execute operation with retry on transient failures.

        Args:
            operation: Async callable to execute.
            step: PlanStep for logging context.
            plan_id: Plan ID for log correlation.

        Returns:
            Result dict from the operation.

        Raises:
            Original error after all retries exhausted.
        """
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                return await operation()
            except MCPInvocationError as exc:
                last_error = exc
                if not self._is_retryable(exc) or attempt == self.max_retries:
                    raise

                backoff = self.backoff_base_s * (2**attempt)
                logger.info(
                    "step_retried",
                    extra={
                        "plan_id": plan_id,
                        "step": step.step,
                        "attempt": attempt + 1,
                        "backoff_s": backoff,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(backoff)
            except Exception:
                raise

        # Should not reach here, but just in case
        if last_error:
            raise last_error
        raise RuntimeError("Retry exhausted unexpectedly")

    def _is_retryable(self, error: MCPInvocationError) -> bool:
        """Check if the error message indicates a retryable condition."""
        msg = str(error).lower()
        return any(code in msg for code in self.retry_on)
