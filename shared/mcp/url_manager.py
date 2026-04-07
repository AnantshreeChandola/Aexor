"""
Composio Per-User MCP URL Manager

Generates and caches per-user MCP URLs from config.
Each user gets a unique URL that scopes their OAuth connections.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .config import ComposioConfig

logger = logging.getLogger(__name__)


@dataclass
class _CachedUrl:
    url: str
    created_at: float


class MCPUrlManager:
    """Generates per-user Composio MCP URLs with TTL caching.

    Each user gets a unique URL scoped to their OAuth connections.
    URLs are cached with a configurable TTL and per-user asyncio locks
    prevent duplicate concurrent generation.
    """

    def __init__(self, composio_config: ComposioConfig) -> None:
        self._config = composio_config
        self._cache: dict[str, _CachedUrl] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    async def get_url(self, user_id: str) -> str:
        """Return a cached per-user MCP URL, generating one if expired or missing."""
        cached = self._cache.get(user_id)
        if cached is not None:
            age = time.monotonic() - cached.created_at
            if age < self._config.user_url_cache_ttl:
                return cached.url

        lock = self._get_lock(user_id)
        async with lock:
            # Double-check after acquiring lock
            cached = self._cache.get(user_id)
            if cached is not None:
                age = time.monotonic() - cached.created_at
                if age < self._config.user_url_cache_ttl:
                    return cached.url

            url = await self._generate_url(user_id)
            self._cache[user_id] = _CachedUrl(url=url, created_at=time.monotonic())
            return url

    async def get_system_url(self) -> str:
        """Return the system-level MCP URL (for tool catalog refresh)."""
        return await self.get_url(self._config.system_user_id)

    def invalidate(self, user_id: str) -> None:
        """Remove cached URL for a user, forcing re-generation."""
        self._cache.pop(user_id, None)

    def invalidate_all(self) -> None:
        """Clear all cached URLs (for shutdown)."""
        self._cache.clear()

    async def _generate_url(self, user_id: str) -> str:
        """Construct per-user Composio MCP URL from config.

        URL format: ``{base_url}/v3/mcp/{mcp_config_id}?user_id={user_id}``
        """
        base = self._config.base_url.rstrip("/")
        url = f"{base}/v3/mcp/{self._config.mcp_config_id}/mcp?user_id={user_id}"

        logger.info(
            "Composio MCP URL generated",
            extra={"user_id": user_id},
        )
        return url
