"""
Per-model circuit breaker for LLM calls.

State machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED
Parameters: failure_threshold=5, timeout_s=60, success_threshold=2

Reference: LLD SS6.3
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

from components.Planner.domain.models import CircuitOpenError


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """In-memory circuit breaker for a single LLM model."""

    def __init__(
        self,
        model_name: str = "unknown",
        failure_threshold: int = 5,
        timeout_s: int = 60,
        success_threshold: int = 2,
    ) -> None:
        self.model_name = model_name
        self.failure_threshold = failure_threshold
        self.timeout_s = timeout_s
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

    def get_state(self) -> CircuitState:
        """Return current state (for metrics)."""
        if self._state == CircuitState.OPEN and self._timeout_elapsed():
            return CircuitState.HALF_OPEN
        return self._state

    def _timeout_elapsed(self) -> bool:
        if self._last_failure_time is None:
            return False
        return (time.monotonic() - self._last_failure_time) >= self.timeout_s

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute func with circuit breaker protection."""
        async with self._lock:
            # Check if OPEN -> maybe transition to HALF_OPEN
            if self._state == CircuitState.OPEN:
                if self._timeout_elapsed():
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                else:
                    raise CircuitOpenError(self.model_name)

            current_state = self._state

        try:
            result = await func(*args, **kwargs)
        except Exception:
            async with self._lock:
                self._on_failure()
            raise

        async with self._lock:
            self._on_success(current_state)

        return result

    def _on_failure(self) -> None:
        self._last_failure_time = time.monotonic()
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._failure_count = self.failure_threshold
        else:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def _on_success(self, state_at_call: CircuitState) -> None:
        if state_at_call == CircuitState.HALF_OPEN or self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
        else:
            # CLOSED: reset failure count on success
            self._failure_count = 0
