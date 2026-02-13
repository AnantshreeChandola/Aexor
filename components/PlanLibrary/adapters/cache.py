"""
Cache Adapter for PlanLibrary

Redis caching layer with graceful degradation.
Redis failures do not block operations.

Reference: LLD.md, tasks.md T304
"""

import json
import logging
from typing import Any

from ..domain.models import PlanLibraryError

logger = logging.getLogger(__name__)

DEFAULT_TTL = 3600  # 1 hour


class CacheAdapter:
    """
    Redis cache adapter for PlanLibrary.

    Provides optional caching with graceful degradation.
    Redis failures return None and log warnings, never block.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        """
        Initialize cache adapter.

        Args:
            redis_url: Redis connection URL. Reads REDIS_URL env if None.
        """
        self._client: Any = None
        self._redis_url = redis_url
        self._available = False
        self._init_client()

    def _init_client(self) -> None:
        """Initialize Redis client with graceful fallback."""
        try:
            import os

            import redis.asyncio as aioredis

            url = self._redis_url or os.getenv(
                "REDIS_URL", "redis://localhost:6379"
            )
            self._client = aioredis.from_url(
                url, decode_responses=True
            )
            self._available = True
            logger.info(
                "Cache adapter initialized",
                extra={"component": "PlanLibrary"},
            )
        except Exception as e:
            self._available = False
            logger.warning(
                "Cache adapter unavailable, operating without cache",
                extra={
                    "error": str(e),
                    "component": "PlanLibrary",
                },
            )

    async def get_cached_plan(self, plan_id: str) -> dict[str, Any] | None:
        """
        Get cached plan data.

        Args:
            plan_id: ULID plan identifier

        Returns:
            Cached plan dict or None if not found/unavailable
        """
        if not self._available or self._client is None:
            return None

        try:
            key = f"plan_cache:{plan_id}"
            data = await self._client.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning(
                "Cache read failed, returning None",
                extra={
                    "plan_id": plan_id,
                    "error": str(e),
                    "component": "PlanLibrary",
                },
            )
            return None

    async def cache_plan(
        self,
        plan_id: str,
        plan_data: dict[str, Any],
        ttl: int = DEFAULT_TTL,
    ) -> bool:
        """
        Cache plan data.

        Args:
            plan_id: ULID plan identifier
            plan_data: Plan data to cache
            ttl: Time-to-live in seconds (default 1h)

        Returns:
            True if cached successfully, False on failure
        """
        if not self._available or self._client is None:
            return False

        try:
            key = f"plan_cache:{plan_id}"
            await self._client.setex(key, ttl, json.dumps(plan_data))
            return True
        except Exception as e:
            logger.warning(
                "Cache write failed",
                extra={
                    "plan_id": plan_id,
                    "error": str(e),
                    "component": "PlanLibrary",
                },
            )
            return False

    async def invalidate(self, plan_id: str) -> bool:
        """
        Invalidate cached plan data.

        Args:
            plan_id: ULID plan identifier

        Returns:
            True if invalidated, False on failure
        """
        if not self._available or self._client is None:
            return False

        try:
            key = f"plan_cache:{plan_id}"
            await self._client.delete(key)
            return True
        except Exception as e:
            logger.warning(
                "Cache invalidation failed",
                extra={
                    "plan_id": plan_id,
                    "error": str(e),
                    "component": "PlanLibrary",
                },
            )
            return False
