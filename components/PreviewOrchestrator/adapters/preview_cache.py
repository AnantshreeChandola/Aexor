"""
Preview Cache Adapter

Redis-backed cache for preview state with graceful degradation.
All operations are best-effort: failures are logged, never propagated.

Reference: LLD.md Section 6.2
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PreviewCacheAdapter:
    """Redis cache adapter for preview state."""

    def __init__(
        self,
        redis_client: Any | None,
        ttl_s: int = 900,
    ) -> None:
        self._redis = redis_client
        self._ttl_s = ttl_s

    def _cache_key(self, plan_id: str, user_id: str) -> str:
        """Build Redis key: preview:{user_id}:{plan_id}."""
        return f"preview:{user_id}:{plan_id}"

    async def store(
        self,
        plan_id: str,
        user_id: str,
        state: dict[int, dict[str, Any]],
    ) -> str | None:
        """Cache preview state in Redis.

        Returns cache key on success, None on failure (graceful degradation).
        """
        if self._redis is None:
            logger.warning(
                "cache_store_failed",
                extra={
                    "plan_id": plan_id,
                    "error": "no redis client",
                },
            )
            return None

        key = self._cache_key(plan_id, user_id)
        try:
            # JSON keys must be strings; convert int keys
            serializable = {str(k): v for k, v in state.items()}
            payload = json.dumps(serializable)
            await self._redis.set(key, payload, ex=self._ttl_s)
            logger.debug(
                "cache_stored",
                extra={
                    "plan_id": plan_id,
                    "user_id": user_id,
                    "cache_key": key,
                    "ttl_s": self._ttl_s,
                },
            )
            return key
        except Exception as exc:
            logger.warning(
                "cache_store_failed",
                extra={"plan_id": plan_id, "error": str(exc)},
            )
            return None

    async def retrieve(
        self,
        plan_id: str,
        user_id: str,
    ) -> dict[int, dict[str, Any]] | None:
        """Retrieve cached preview state.

        Returns None if expired, missing, or Redis unavailable.
        """
        if self._redis is None:
            return None

        key = self._cache_key(plan_id, user_id)
        try:
            raw = await self._redis.get(key)
            if raw is None:
                logger.debug(
                    "cache_retrieved",
                    extra={
                        "plan_id": plan_id,
                        "user_id": user_id,
                        "hit": False,
                    },
                )
                return None

            data = json.loads(raw)
            # Convert string keys back to int
            result = {int(k): v for k, v in data.items()}
            logger.debug(
                "cache_retrieved",
                extra={
                    "plan_id": plan_id,
                    "user_id": user_id,
                    "hit": True,
                },
            )
            return result
        except Exception as exc:
            logger.warning(
                "cache_retrieve_failed",
                extra={"plan_id": plan_id, "error": str(exc)},
            )
            return None
