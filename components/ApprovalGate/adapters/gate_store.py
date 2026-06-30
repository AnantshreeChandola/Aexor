"""
Gate Store Adapter

Redis-backed gate state storage, consumed-token tracking, and approval state.
All operations use graceful degradation: failures logged, never propagated.

Reference: LLD.md Section 6.2
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class GateStore:
    """Redis-backed gate state and token consumption tracking."""

    def __init__(self, redis_client: Any | None, default_ttl_s: int = 900) -> None:
        self._redis = redis_client
        self._default_ttl_s = default_ttl_s

    async def store_gate(
        self,
        plan_id: str,
        gate_id: str,
        token_id: str,
        preview_state: dict | None,
        selected_option: dict | None,
        token_claims: dict,
        jwt_token: str = "",
        ttl_s: int | None = None,
    ) -> bool:
        """Store gate approval state in Redis.

        Key: gate:{plan_id}:{gate_id}
        Returns True on success, False on Redis failure (graceful degradation).
        """
        if self._redis is None:
            logger.warning(
                "gate_store_failed",
                extra={
                    "plan_id": plan_id,
                    "gate_id": gate_id,
                    "operation": "store_gate",
                    "error": "no redis client",
                },
            )
            return False

        key = f"gate:{plan_id}:{gate_id}"
        ttl = ttl_s if ttl_s is not None else self._default_ttl_s
        value = {
            "status": "approved",
            "token_id": token_id,
            "preview_state": preview_state,
            "selected_option": selected_option,
            "token_claims": token_claims,
            "jwt_token": jwt_token,
            "approved_at": datetime.now(UTC).isoformat(),
        }
        try:
            payload = json.dumps(value)
            await self._redis.set(key, payload, ex=ttl)
            return True
        except Exception as exc:
            logger.warning(
                "gate_store_failed",
                extra={
                    "plan_id": plan_id,
                    "gate_id": gate_id,
                    "operation": "store_gate",
                    "error": str(exc),
                },
            )
            return False

    async def get_gate(self, plan_id: str, gate_id: str) -> dict | None:
        """Retrieve gate state. Returns None if missing/expired/Redis down."""
        if self._redis is None:
            return None

        key = f"gate:{plan_id}:{gate_id}"
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning(
                "gate_store_failed",
                extra={
                    "plan_id": plan_id,
                    "gate_id": gate_id,
                    "operation": "get_gate",
                    "error": str(exc),
                },
            )
            return None

    async def get_all_gates(self, plan_id: str, gate_ids: list[str]) -> dict[str, str]:
        """Get status for multiple gates by specific gate_ids.

        Returns gate_id -> status mapping.
        """
        if self._redis is None:
            return {}

        result: dict[str, str] = {}
        for gate_id in gate_ids:
            gate_data = await self.get_gate(plan_id, gate_id)
            if gate_data is not None:
                result[gate_id] = gate_data.get("status", "pending")
        return result

    async def get_all_gates_by_prefix(self, plan_id: str) -> dict[str, str]:
        """Scan Redis for gate:{plan_id}:* keys.

        Returns gate_id -> status mapping. Used by get_gate_status().
        """
        if self._redis is None:
            return {}

        prefix = f"gate:{plan_id}:"
        try:
            keys = await self._redis.keys(f"{prefix}*")
            result: dict[str, str] = {}
            for key in keys:
                # Extract gate_id from key pattern gate:{plan_id}:{gate_id}
                gate_id = key[len(prefix) :]
                raw = await self._redis.get(key)
                if raw is not None:
                    data = json.loads(raw)
                    result[gate_id] = data.get("status", "pending")
            return result
        except Exception as exc:
            logger.warning(
                "gate_store_failed",
                extra={
                    "plan_id": plan_id,
                    "gate_id": "*",
                    "operation": "get_all_gates_by_prefix",
                    "error": str(exc),
                },
            )
            return {}

    async def mark_consumed(self, token_id: str, ttl_s: int) -> bool:
        """Mark a token as consumed (SET NX with TTL).

        Key: consumed:{token_id}
        Returns True if successfully marked (first use), False if already consumed.
        Returns True if Redis unavailable (fail-open -- JWT expiry still enforced).
        """
        if self._redis is None:
            return True

        key = f"consumed:{token_id}"
        try:
            result = await self._redis.set(key, "1", ex=ttl_s, nx=True)
            # Redis SET NX returns True if key was set, None/False if already exists
            return result is True or (result is not None and result)
        except Exception as exc:
            logger.warning(
                "consumed_check_failed",
                extra={
                    "token_id": token_id,
                    "operation": "mark_consumed",
                    "error": str(exc),
                },
            )
            return True  # Fail-open

    async def is_consumed(self, token_id: str) -> bool:
        """Check if token was already consumed.

        Returns False if Redis unavailable (fail-open -- JWT expiry still enforced).
        """
        if self._redis is None:
            return False

        key = f"consumed:{token_id}"
        try:
            result = await self._redis.get(key)
            return result is not None
        except Exception as exc:
            logger.warning(
                "consumed_check_failed",
                extra={
                    "token_id": token_id,
                    "operation": "is_consumed",
                    "error": str(exc),
                },
            )
            return False  # Fail-open
