"""
ApprovalGate Service

Core HITL approval logic: token issuance, validation, multi-gate
coordination, preview state binding, and learn-from-approval.

Reference: LLD.md Sections 9.1-9.4
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import ulid

from ..adapters.gate_store import GateStore
from ..adapters.token_issuer import TokenIssuer
from ..domain.models import (
    ApprovalConfigError,
    ApprovalError,
    ApprovalRequest,
    ApprovalState,
    ApprovalToken,
    TokenConsumedError,
    TokenValidationError,
)

logger = logging.getLogger(__name__)


class ApprovalService:
    """HITL approval token management and multi-gate coordination."""

    def __init__(
        self,
        token_issuer: TokenIssuer,
        gate_store: GateStore,
        preview_service: Any | None = None,
        policy_service: Any | None = None,
        token_ttl_s: int = 900,
    ) -> None:
        self._token_issuer = token_issuer
        self._gate_store = gate_store
        self._preview_service = preview_service
        self._policy_service = policy_service
        self._token_ttl_s = token_ttl_s

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def approve(self, request: ApprovalRequest) -> ApprovalToken:
        """Issue an approval token for a plan gate.

        Flow per LLD Section 9.1:
            1. Validate request (scopes non-empty)
            2. Check idempotency: if gate already approved, return existing token
            3. Retrieve preview state from PreviewOrchestrator (best-effort)
            4. Sign JWT with claims
            5. Store gate state in Redis (best-effort)
            6. If policy_matched=False: call PolicyEngine.learn_from_approval()
            7. Return ApprovalToken

        Raises:
            ApprovalError: If scopes are empty.
        """
        logger.info(
            "approval_started",
            extra={
                "plan_id": request.plan_id,
                "gate_id": request.gate_id,
                "user_id": request.user_id,
                "trace_id": request.trace_id,
                "scope_count": len(request.scopes),
            },
        )

        # 1. Validate request
        if not request.scopes:
            raise ApprovalError("Scopes cannot be empty")

        # 2. Check idempotency: if gate already approved, return existing token
        existing = await self._gate_store.get_gate(request.plan_id, request.gate_id)
        if existing and existing.get("status") == "approved":
            logger.info(
                "approval_idempotent",
                extra={
                    "plan_id": request.plan_id,
                    "gate_id": request.gate_id,
                },
            )
            return self._build_token_from_stored(existing, request)

        # 3. Retrieve preview state (best-effort)
        preview_state = None
        if self._preview_service is not None:
            try:
                raw_state = await self._preview_service.get_preview_state(
                    request.plan_id, request.user_id
                )
                if raw_state is not None:
                    # Serialize PreviewStepResult models to dicts for storage
                    preview_state = {}
                    for k, v in raw_state.items():
                        if hasattr(v, "model_dump"):
                            preview_state[k] = v.model_dump()
                        else:
                            preview_state[k] = v
                    logger.debug(
                        "preview_state_bound",
                        extra={
                            "plan_id": request.plan_id,
                            "gate_id": request.gate_id,
                            "step_count": len(preview_state),
                        },
                    )
            except Exception:
                logger.warning(
                    "preview_state_retrieval_failed",
                    extra={
                        "plan_id": request.plan_id,
                        "gate_id": request.gate_id,
                    },
                )

        # 4. Generate token_id and timestamps
        token_id = ulid.new().str
        now = datetime.now(UTC)
        exp = now + timedelta(seconds=self._token_ttl_s)

        # 5. Build JWT claims and sign
        claims = {
            "plan_id": request.plan_id,
            "user_id": request.user_id,
            "gate_id": request.gate_id,
            "scopes": request.scopes,
            "exp": int(exp.timestamp()),
            "iat": int(now.timestamp()),
            "token_id": token_id,
        }
        jwt_string = self._token_issuer.sign(claims)

        # 6. Store gate state in Redis (best-effort)
        stored = await self._gate_store.store_gate(
            plan_id=request.plan_id,
            gate_id=request.gate_id,
            token_id=token_id,
            preview_state=preview_state,
            selected_option=request.selected_option,
            token_claims=claims,
            jwt_token=jwt_string,
            ttl_s=self._token_ttl_s,
        )
        if not stored:
            logger.warning(
                "gate_store_failed",
                extra={
                    "plan_id": request.plan_id,
                    "gate_id": request.gate_id,
                    "operation": "store_gate",
                },
            )

        # 7. If policy_matched=False, learn from approval (best-effort)
        if (
            not request.policy_matched
            and request.role
            and request.tool
            and self._policy_service is not None
        ):
            try:
                logger.info(
                    "learn_from_approval_called",
                    extra={
                        "plan_id": request.plan_id,
                        "role": request.role,
                        "tool": request.tool,
                    },
                )
                await self._policy_service.learn_from_approval(request.role, request.tool)
            except Exception:
                logger.warning(
                    "learn_from_approval_failed",
                    extra={
                        "plan_id": request.plan_id,
                        "role": request.role,
                        "tool": request.tool,
                    },
                )

        # 8. Return ApprovalToken
        approval_token = ApprovalToken(
            token=jwt_string,
            plan_id=request.plan_id,
            user_id=request.user_id,
            gate_id=request.gate_id,
            scopes=request.scopes,
            exp=exp.isoformat(),
            iat=now.isoformat(),
            token_id=token_id,
        )

        logger.info(
            "approval_issued",
            extra={
                "plan_id": request.plan_id,
                "gate_id": request.gate_id,
                "token_id": token_id,
                "exp": exp.isoformat(),
                "scope_count": len(request.scopes),
            },
        )

        # Audit: approval_granted (fire-and-forget)
        await self._emit_audit(
            "approval_granted",
            plan_id=request.plan_id,
            user_id=request.user_id,
            gate_id=request.gate_id,
            scopes=request.scopes,
            token_id=token_id,
        )

        return approval_token

    async def validate_token(self, token: str, plan_id: str, gate_id: str | None = None) -> dict:
        """Validate an approval token and mark it as consumed.

        Returns decoded claims if valid.

        Raises:
            TokenExpiredError: If token has expired.
            TokenValidationError: If signature, plan_id, or gate_id invalid.
            TokenConsumedError: If token was already used (single-use).
        """
        # 1. Verify JWT signature and expiry
        claims = self._token_issuer.verify(token)
        # verify() raises TokenExpiredError or TokenValidationError

        # 2. Validate plan_id match
        if claims["plan_id"] != plan_id:
            logger.warning(
                "token_invalid",
                extra={
                    "plan_id": plan_id,
                    "reason": "plan_id_mismatch",
                },
            )
            raise TokenValidationError("plan_id_mismatch")

        # 3. Validate gate_id match (if provided)
        if gate_id is not None and claims.get("gate_id") != gate_id:
            logger.warning(
                "token_invalid",
                extra={
                    "plan_id": plan_id,
                    "reason": "gate_id_mismatch",
                },
            )
            raise TokenValidationError("gate_id_mismatch")

        # 4. Check single-use: is token already consumed?
        token_id = claims["token_id"]
        if await self._gate_store.is_consumed(token_id):
            logger.warning(
                "token_consumed",
                extra={
                    "plan_id": plan_id,
                    "token_id": token_id,
                },
            )
            raise TokenConsumedError()

        # 5. Mark as consumed (atomic SET NX)
        consumed = await self._gate_store.mark_consumed(token_id, ttl_s=self._token_ttl_s)
        if not consumed:
            # Another concurrent call consumed it first
            logger.warning(
                "token_consumed",
                extra={
                    "plan_id": plan_id,
                    "token_id": token_id,
                },
            )
            raise TokenConsumedError()

        logger.info(
            "token_validated",
            extra={
                "plan_id": plan_id,
                "gate_id": claims.get("gate_id"),
                "token_id": token_id,
            },
        )

        # 6. Return decoded claims
        return claims

    async def get_gate_status(self, plan_id: str) -> dict[str, str]:
        """Get approval status for all gates of a plan.

        Returns dict of gate_id -> status (pending/approved/expired).
        Returns empty dict if Redis unavailable or no gates found.
        """
        result = await self._gate_store.get_all_gates_by_prefix(plan_id)
        logger.debug(
            "gate_status_queried",
            extra={
                "plan_id": plan_id,
                "gate_count": len(result),
            },
        )
        return result

    async def get_approval_state(self, plan_id: str, gate_id: str) -> ApprovalState | None:
        """Get full approval state including preview results and user selection.

        Returns None if gate not found, expired, or Redis unavailable.
        """
        gate_data = await self._gate_store.get_gate(plan_id, gate_id)
        if gate_data is None:
            return None

        return ApprovalState(
            plan_id=plan_id,
            gate_id=gate_id,
            status=gate_data.get("status", "pending"),
            token_claims=gate_data.get("token_claims", {}),
            preview_state=gate_data.get("preview_state"),
            selected_option=gate_data.get("selected_option"),
            approved_at=gate_data.get("approved_at", ""),
        )

    # ------------------------------------------------------------------
    # Audit integration (fire-and-forget)
    # ------------------------------------------------------------------

    async def _emit_audit(
        self,
        event_type: str,
        plan_id: str,
        user_id: str | None = None,
        **extra: Any,
    ) -> None:
        """Fire-and-forget audit event. Never raises."""
        audit = getattr(self, "_audit", None)
        if audit is None:
            return
        try:
            import ulid as _ulid

            from components.Audit.domain.models import AuditEvent, AuditEventType

            event = AuditEvent(
                event_id=_ulid.new().str,
                event_type=AuditEventType(event_type),
                plan_id=plan_id,
                user_id=user_id,
                event_data=extra,
            )
            await audit.record(event)
        except Exception:
            pass  # fire-and-forget

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_token_from_stored(
        existing_gate_data: dict[str, Any],
        request: ApprovalRequest,
    ) -> ApprovalToken:
        """Reconstruct an ApprovalToken from stored gate data for idempotent re-approval."""
        token_claims = existing_gate_data.get("token_claims", {})
        return ApprovalToken(
            token=existing_gate_data.get("jwt_token", ""),
            plan_id=request.plan_id,
            user_id=request.user_id,
            gate_id=request.gate_id,
            scopes=request.scopes,
            exp=datetime.fromtimestamp(token_claims.get("exp", 0), tz=UTC).isoformat(),
            iat=datetime.fromtimestamp(token_claims.get("iat", 0), tz=UTC).isoformat(),
            token_id=existing_gate_data.get("token_id", ""),
        )


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def create_approval_service(
    preview_service: Any | None = None,
    policy_service: Any | None = None,
    redis_client: Any | None = None,
    jwt_secret: str = "",
    token_ttl_s: int = 900,
) -> ApprovalService:
    """Create ApprovalService with all dependencies.

    Called once during app lifespan startup in shared/app.py.

    Args:
        preview_service: PreviewOrchestrator for cached state retrieval.
        policy_service: PolicyEngine for learn_from_approval (optional).
        redis_client: Redis client for gate state and consumed-token tracking.
        jwt_secret: Secret key for JWT signing (required; fallback from env).
        token_ttl_s: Token time-to-live in seconds (default 900 / 15min).

    Raises:
        ApprovalConfigError: If jwt_secret is empty or too short.
    """
    # Read from environment with parameter fallback
    secret = os.environ.get("APPROVAL_TOKEN_SECRET", "") or jwt_secret
    if not secret:
        raise ApprovalConfigError("JWT secret not configured")
    if len(secret) < 16:
        raise ApprovalConfigError(f"JWT secret too short ({len(secret)} chars, minimum 16)")

    ttl = int(os.environ.get("APPROVAL_TOKEN_TTL_S", str(token_ttl_s)))

    token_issuer = TokenIssuer(secret, algorithm="HS256")
    gate_store = GateStore(redis_client, default_ttl_s=ttl)

    return ApprovalService(
        token_issuer=token_issuer,
        gate_store=gate_store,
        preview_service=preview_service,
        policy_service=policy_service,
        token_ttl_s=ttl,
    )
