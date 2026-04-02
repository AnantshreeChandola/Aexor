"""
Idempotency Adapter

Redis 3-state idempotency for Booker steps.
States: IN_FLIGHT -> SUCCEEDED | FAILED.

Reference: LLD.md Section 6.4
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from ..domain.models import IdempotencyConflict, StepResult

logger = logging.getLogger(__name__)

_TTL_SECONDS = 86400  # 24 hours
_DEFAULT_TIMEOUT_MINUTES = 5


class IdempotencyAdapter:
    """Redis 3-state idempotency for Booker steps."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def build_key(
        self,
        user_id: str,
        integration_id: str,
        plan_id: str,
        step: int,
        call: str,
        args: dict[str, Any],
    ) -> str:
        """Build deterministic idempotency key with args hash."""
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()[:16]
        return f"idem:{user_id}:{integration_id}:{plan_id}:{step}:{call}:{args_hash}"

    async def check_and_claim(
        self,
        key: str,
        execution_id: str,
        timeout_minutes: int = _DEFAULT_TIMEOUT_MINUTES,
    ) -> StepResult | None:
        """Check idempotency state and optionally claim the slot.

        Returns:
            StepResult with cached result if SUCCEEDED.
            None if caller should proceed with execution.

        Raises:
            IdempotencyConflict: If another execution owns IN_FLIGHT slot.
        """
        record = await self._redis.hgetall(key)

        if not record:
            # No prior record -- claim as IN_FLIGHT
            await self._set_in_flight(key, execution_id)
            return None

        state = record.get("state", "")

        if state == "SUCCEEDED":
            result_json = record.get("result_json", "{}")
            result = json.loads(result_json)
            return StepResult(
                step=int(record.get("step", 0)),
                status="completed",
                result=result,
            )

        if state == "IN_FLIGHT":
            claimed_at = float(record.get("claimed_at", 0))
            elapsed_min = (time.time() - claimed_at) / 60
            if elapsed_min < timeout_minutes:
                raise IdempotencyConflict(key)
            # Stale -- take over
            await self._set_in_flight(key, execution_id)
            return None

        if state == "FAILED":
            # Delete and allow retry
            await self._redis.delete(key)
            await self._set_in_flight(key, execution_id)
            return None

        # Unknown state -- treat as clean
        await self._set_in_flight(key, execution_id)
        return None

    async def mark_succeeded(self, key: str, result: dict[str, Any]) -> None:
        """Mark as SUCCEEDED with cached result."""
        result_json = json.dumps(result, default=str)
        await self._redis.hset(
            key,
            mapping={
                "state": "SUCCEEDED",
                "result_json": result_json,
                "completed_at": str(time.time()),
            },
        )
        await self._redis.expire(key, _TTL_SECONDS)

    async def mark_failed(self, key: str, error: str) -> None:
        """Mark as FAILED (available for retry)."""
        await self._redis.hset(
            key,
            mapping={
                "state": "FAILED",
                "error": error,
                "failed_at": str(time.time()),
            },
        )
        await self._redis.expire(key, _TTL_SECONDS)

    async def _set_in_flight(self, key: str, execution_id: str) -> None:
        """Set IN_FLIGHT state with ownership."""
        await self._redis.hset(
            key,
            mapping={
                "state": "IN_FLIGHT",
                "execution_id": execution_id,
                "claimed_at": str(time.time()),
            },
        )
        await self._redis.expire(key, _TTL_SECONDS)
