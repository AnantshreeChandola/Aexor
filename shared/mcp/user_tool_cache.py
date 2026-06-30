"""
Per-User Tool Cache — Redis-backed cache of MCP tools available to each user.

Populated at session start via ``ToolCatalog.refresh_user()``.
Downstream readiness checks and the Planner can query this to know
which tools the user actually has access to (vs. the global catalog
which lists everything the system knows about).

Key format: ``user_tools:{user_id}``
Value: JSON array of serialised ToolDefinition dicts
TTL: matches session TTL (default 3600s)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio
import redis.exceptions

logger = logging.getLogger(__name__)

_KEY_PREFIX = "user_tools"
_DEFAULT_TTL = 3600  # 1 hour, same as session TTL


class UserToolCache:
    """Redis cache for per-user tool definitions."""

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

    async def get(self, user_id: str) -> list[dict[str, Any]] | None:
        """Return cached tool definitions, or None on cache miss."""
        try:
            raw = await self._redis.get(self._key(user_id))
        except redis.exceptions.RedisError:
            logger.warning("user_tool_cache_get_failed", extra={"user_id": user_id})
            return None
        if raw is None:
            return None
        return json.loads(raw)

    async def get_tool_names(self, user_id: str) -> set[str] | None:
        """Return just the tool names from cache, or None on miss."""
        tools = await self.get(user_id)
        if tools is None:
            return None
        return {t["name"] for t in tools if "name" in t}

    async def set(self, user_id: str, tools: list[dict[str, Any]]) -> None:
        """Cache tool definitions with TTL."""
        try:
            await self._redis.setex(
                self._key(user_id),
                self._ttl,
                json.dumps(tools),
            )
        except redis.exceptions.RedisError:
            logger.warning("user_tool_cache_set_failed", extra={"user_id": user_id})

    async def invalidate(self, user_id: str) -> None:
        """Remove cached entry."""
        try:
            await self._redis.delete(self._key(user_id))
        except redis.exceptions.RedisError:
            logger.warning("user_tool_cache_invalidate_failed", extra={"user_id": user_id})
