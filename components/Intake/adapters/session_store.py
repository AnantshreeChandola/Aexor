"""
Session Store Protocol and Redis Implementation

SessionStore protocol for session CRUD. RedisSessionStore uses
redis.asyncio for key-value session persistence with TTL.

Reference: LLD Section 7.1
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import redis.asyncio
import redis.exceptions

from components.Intake.domain.models import (
    Session,
    SessionStoreUnavailableError,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session persistence."""

    async def get(self, user_id: str, session_id: str) -> Session | None: ...

    async def save(self, session: Session) -> None: ...

    async def delete(self, user_id: str, session_id: str) -> bool: ...


class RedisSessionStore:
    """Redis-backed session store with TTL refresh on save."""

    def __init__(
        self,
        redis_client: redis.asyncio.Redis,
        ttl_seconds: int = 86400,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    @staticmethod
    def _key(user_id: str, session_id: str) -> str:
        return f"session:{user_id}:{session_id}"

    async def get(self, user_id: str, session_id: str) -> Session | None:
        """Retrieve session from Redis. Returns None if missing."""
        key = self._key(user_id, session_id)
        try:
            raw = await self._redis.get(key)
        except redis.exceptions.RedisError as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

        if raw is None:
            return None

        return Session.model_validate_json(raw)

    async def save(self, session: Session) -> None:
        """Persist session to Redis with TTL refresh."""
        key = self._key(session.user_id, session.session_id)
        data = session.model_dump_json()
        try:
            await self._redis.setex(key, self._ttl, data)
        except redis.exceptions.RedisError as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

    async def delete(self, user_id: str, session_id: str) -> bool:
        """Delete session. Returns True if key existed, False otherwise."""
        key = self._key(user_id, session_id)
        try:
            removed = await self._redis.delete(key)
        except redis.exceptions.RedisError as exc:
            raise SessionStoreUnavailableError(str(exc)) from exc

        return removed > 0
