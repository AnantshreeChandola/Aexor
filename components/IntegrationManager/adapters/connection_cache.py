"""
Connection Cache — Redis-backed provider connection cache.

Caches the set of connected provider names per user in Redis,
co-located with session data. Warmed at session start so that
readiness checks avoid per-tool DB round-trips.

Key format: ``connections:{user_id}``
Value: JSON array of provider name strings
TTL: matches session TTL (default 3600s)
"""

from __future__ import annotations

import json
import logging

import redis.asyncio
import redis.exceptions

logger = logging.getLogger(__name__)

_KEY_PREFIX = "connections"
_DEFAULT_TTL = 3600  # 1 hour, same as session TTL


class ConnectionCache:
    """Thin Redis wrapper for per-user connected-provider sets."""

    def __init__(
        self,
        redis_client: redis.asyncio.Redis,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    @staticmethod
    def _key(user_id: str) -> str:
        return f"{_KEY_PREFIX}:{user_id}"

    async def get(self, user_id: str) -> set[str] | None:
        """Return cached provider set, or None on cache miss."""
        try:
            raw = await self._redis.get(self._key(user_id))
        except redis.exceptions.RedisError:
            logger.warning("connection_cache_get_failed", extra={"user_id": user_id})
            return None
        if raw is None:
            return None
        return set(json.loads(raw))

    async def set(self, user_id: str, providers: set[str]) -> None:
        """Cache the provider set with TTL."""
        try:
            await self._redis.setex(
                self._key(user_id),
                self._ttl,
                json.dumps(sorted(providers)),
            )
        except redis.exceptions.RedisError:
            logger.warning("connection_cache_set_failed", extra={"user_id": user_id})

    async def invalidate(self, user_id: str) -> None:
        """Remove cached entry, forcing re-warm on next session."""
        try:
            await self._redis.delete(self._key(user_id))
        except redis.exceptions.RedisError:
            logger.warning(
                "connection_cache_invalidate_failed", extra={"user_id": user_id}
            )

    async def is_cached(self, user_id: str, provider_name: str) -> bool | None:
        """Check if provider is in cached set.

        Returns:
            True/False if cache hit, None if cache miss.
        """
        providers = await self.get(user_id)
        if providers is None:
            return None
        return provider_name in providers
