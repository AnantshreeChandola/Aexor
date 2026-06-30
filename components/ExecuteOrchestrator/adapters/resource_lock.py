"""
Resource Lock Adapter

Redis-based resource locks for Booker steps.

Reference: LLD.md Section 6.5
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from ..domain.models import ResourceLockTimeout

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 30
_POLL_INTERVAL_SECONDS = 0.5


class ResourceLockAdapter:
    """Redis-based resource locks for write operations."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._owner_id = str(uuid.uuid4())
        self._held_locks: dict[str, str] = {}

    async def acquire(self, lock_key: str, timeout_s: int = _LOCK_TTL_SECONDS) -> bool:
        """Acquire lock with polling.

        Args:
            lock_key: Lock key to acquire.
            timeout_s: Max seconds to wait before giving up.

        Returns:
            True if lock acquired.

        Raises:
            ResourceLockTimeout: If lock not acquired within timeout.
        """
        owner_value = f"{self._owner_id}:{uuid.uuid4()}"
        elapsed = 0.0

        while elapsed < timeout_s:
            acquired = await self._redis.set(
                lock_key,
                owner_value,
                nx=True,
                ex=_LOCK_TTL_SECONDS,
            )
            if acquired:
                self._held_locks[lock_key] = owner_value
                return True

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS

        raise ResourceLockTimeout(lock_key, timeout_s)

    async def release(self, lock_key: str) -> None:
        """Release lock (only if we own it)."""
        owner_value = self._held_locks.pop(lock_key, None)
        if owner_value is None:
            return

        current = await self._redis.get(lock_key)
        if current == owner_value:
            await self._redis.delete(lock_key)
