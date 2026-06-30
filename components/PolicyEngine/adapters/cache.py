"""
Redis Cache Adapter for PolicyEngine

Caches policy rules to avoid DB lookups on hot paths.
Key pattern: policy_cache:{policy_id}:{version} (5m TTL).
Degrades gracefully if Redis is unavailable.

Reference: GLOBAL_SPEC §2.9
"""

from __future__ import annotations

import logging

from shared.schemas.policy import PolicyRule

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # 5 minutes
CACHE_KEY_PREFIX = "policy_cache"


class PolicyCacheAdapter:
    """Redis cache for policy rules with graceful degradation."""

    def __init__(self, redis_client: object | None) -> None:
        self._redis = redis_client

    async def get_policy(self, policy_id: str, version: int) -> PolicyRule | None:
        """Retrieve a cached policy. Returns None on miss or Redis error."""
        if self._redis is None:
            return None
        try:
            key = f"{CACHE_KEY_PREFIX}:{policy_id}:{version}"
            data = await self._redis.get(key)
            if data is None:
                return None
            return PolicyRule.model_validate_json(data)
        except Exception:
            logger.warning(
                "Redis cache read failed for policy %s:%d, falling through to DB",
                policy_id,
                version,
            )
            return None

    async def set_policy(self, policy_id: str, version: int, rule: PolicyRule) -> None:
        """Cache a policy rule. Silently fails if Redis is unavailable."""
        if self._redis is None:
            return
        try:
            key = f"{CACHE_KEY_PREFIX}:{policy_id}:{version}"
            data = rule.model_dump_json()
            await self._redis.set(key, data, ex=CACHE_TTL_SECONDS)
        except Exception:
            logger.warning(
                "Redis cache write failed for policy %s:%d",
                policy_id,
                version,
            )

    async def invalidate(self, policy_id: str, version: int) -> None:
        """Remove a cached policy entry. Silently fails if Redis is unavailable."""
        if self._redis is None:
            return
        try:
            key = f"{CACHE_KEY_PREFIX}:{policy_id}:{version}"
            await self._redis.delete(key)
        except Exception:
            logger.warning(
                "Redis cache invalidate failed for policy %s:%d",
                policy_id,
                version,
            )
