"""
ApprovalGate service tests -- token issuance, validation, gate state,
preview binding, learn-from-approval, GateStore adapter.

~30 tests covering US1-US5, FR-001 through FR-012.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from components.ApprovalGate.adapters.gate_store import GateStore
from components.ApprovalGate.domain.models import (
    ApprovalRequest,
    ApprovalState,
    ApprovalToken,
    TokenConsumedError,
    TokenExpiredError,
    TokenValidationError,
)
from components.ApprovalGate.service.approval_service import (
    ApprovalService,
    create_approval_service,
)
from components.ApprovalGate.tests.conftest import (
    SAMPLE_PLAN_ID,
    SAMPLE_SCOPES,
    SAMPLE_USER_ID,
)

# ---------------------------------------------------------------------------
# Approve flow tests (T600)
# ---------------------------------------------------------------------------


class TestApproveFlow:
    """Service-level tests for approve flow."""

    async def test_approve_returns_approval_token(self, approval_service, sample_approval_request):
        """US1 / FR-001: approve() returns an ApprovalToken."""
        token = await approval_service.approve(sample_approval_request)
        assert isinstance(token, ApprovalToken)
        assert token.plan_id == SAMPLE_PLAN_ID
        assert token.user_id == SAMPLE_USER_ID
        assert token.gate_id == "gate-A"
        assert token.scopes == SAMPLE_SCOPES

    async def test_token_exp_is_15_min_after_iat(self, approval_service, sample_approval_request):
        """US1 / FR-001: Token exp is ~15 minutes after iat (within 2s tolerance)."""
        token = await approval_service.approve(sample_approval_request)
        exp = datetime.fromisoformat(token.exp)
        iat = datetime.fromisoformat(token.iat)
        diff = (exp - iat).total_seconds()
        assert abs(diff - 900) < 2

    async def test_token_jwt_contains_all_claims(
        self, approval_service, sample_approval_request, token_issuer
    ):
        """US1 / FR-002: Token JWT contains required claims."""
        token = await approval_service.approve(sample_approval_request)
        claims = token_issuer.verify(token.token)
        for key in ("plan_id", "user_id", "gate_id", "scopes", "exp", "iat", "token_id"):
            assert key in claims

    async def test_approve_stores_gate_in_redis(
        self, approval_service, sample_approval_request, gate_store
    ):
        """US1: approve() stores gate state in Redis."""
        await approval_service.approve(sample_approval_request)
        data = await gate_store.get_gate(SAMPLE_PLAN_ID, "gate-A")
        assert data is not None
        assert data["status"] == "approved"

    async def test_approve_calls_preview_service(
        self, approval_service, sample_approval_request, mock_preview_service
    ):
        """US3 / FR-006: approve() calls preview_service.get_preview_state()."""
        await approval_service.approve(sample_approval_request)
        mock_preview_service.get_preview_state.assert_called_once_with(
            SAMPLE_PLAN_ID, SAMPLE_USER_ID
        )

    async def test_approve_succeeds_when_preview_service_none(
        self, approval_service_minimal, sample_approval_request
    ):
        """US3 / FR-006: When preview_service is None, approval succeeds."""
        token = await approval_service_minimal.approve(sample_approval_request)
        assert isinstance(token, ApprovalToken)

    async def test_approve_succeeds_when_preview_service_raises(
        self, approval_service, sample_approval_request, mock_preview_service
    ):
        """US3 / FR-006: When preview_service raises, approval succeeds with preview_state=None."""
        mock_preview_service.get_preview_state = AsyncMock(side_effect=RuntimeError("preview down"))
        token = await approval_service.approve(sample_approval_request)
        assert isinstance(token, ApprovalToken)

    async def test_get_approval_state_returns_state(
        self, approval_service, sample_approval_request
    ):
        """US3 / FR-010: get_approval_state() returns ApprovalState."""
        await approval_service.approve(sample_approval_request)
        state = await approval_service.get_approval_state(SAMPLE_PLAN_ID, "gate-A")
        assert isinstance(state, ApprovalState)
        assert state.status == "approved"
        assert state.token_claims is not None

    async def test_get_approval_state_returns_none_when_not_found(self, approval_service):
        """US3 / FR-010: get_approval_state() returns None when gate not found."""
        state = await approval_service.get_approval_state(SAMPLE_PLAN_ID, "gate-Z")
        assert state is None

    async def test_learn_from_approval_called_when_policy_unmatched(
        self, approval_service, sample_approval_request_spawned, mock_policy_service
    ):
        """US4 / FR-008: learn_from_approval() called when policy_matched=False."""
        await approval_service.approve(sample_approval_request_spawned)
        mock_policy_service.learn_from_approval.assert_called_once_with(
            "Fetcher", "google.calendar"
        )

    async def test_learn_not_called_when_policy_matched(
        self, approval_service, sample_approval_request, mock_policy_service
    ):
        """US4 / FR-008: learn_from_approval() NOT called when policy_matched=True."""
        await approval_service.approve(sample_approval_request)
        mock_policy_service.learn_from_approval.assert_not_called()

    async def test_approve_succeeds_when_policy_service_none(self, approval_service_minimal):
        """US4 / FR-008: When PolicyEngine is None, approval succeeds."""
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            scopes=SAMPLE_SCOPES,
            policy_matched=False,
            role="Fetcher",
            tool="google.calendar",
        )
        token = await approval_service_minimal.approve(req)
        assert isinstance(token, ApprovalToken)

    async def test_approve_succeeds_when_policy_service_raises(
        self, approval_service, mock_policy_service
    ):
        """US4 / FR-008: When PolicyEngine raises, approval succeeds."""
        mock_policy_service.learn_from_approval = AsyncMock(side_effect=RuntimeError("policy down"))
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            gate_id="gate-spawn8",
            scopes=SAMPLE_SCOPES,
            policy_matched=False,
            role="Fetcher",
            tool="google.calendar",
        )
        token = await approval_service.approve(req)
        assert isinstance(token, ApprovalToken)


# ---------------------------------------------------------------------------
# Token validation tests
# ---------------------------------------------------------------------------


class TestTokenValidation:
    """Token validation flow tests."""

    async def test_validate_returns_claims(self, approval_service, sample_approval_request):
        """US5: validate_token() returns decoded claims for valid token."""
        token = await approval_service.approve(sample_approval_request)
        claims = await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)
        assert claims["plan_id"] == SAMPLE_PLAN_ID
        assert claims["user_id"] == SAMPLE_USER_ID

    async def test_single_use_enforcement(self, approval_service, sample_approval_request):
        """US5 / FR-003: Second validate_token() raises TokenConsumedError."""
        token = await approval_service.approve(sample_approval_request)
        await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)
        with pytest.raises(TokenConsumedError):
            await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)

    async def test_validate_raises_expired(self, approval_service, token_issuer):
        """US5: validate_token() raises TokenExpiredError for expired token."""
        import time

        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "user_id": SAMPLE_USER_ID,
            "gate_id": "gate-A",
            "scopes": SAMPLE_SCOPES,
            "exp": int(time.time()) - 100,
            "iat": int(time.time()) - 200,
            "token_id": "01JXYZ9876543210KLMNOPQRST",
        }
        jwt_str = token_issuer.sign(claims)
        with pytest.raises(TokenExpiredError):
            await approval_service.validate_token(jwt_str, SAMPLE_PLAN_ID)

    async def test_validate_raises_plan_id_mismatch(
        self, approval_service, sample_approval_request
    ):
        """US5: validate_token() raises TokenValidationError for plan_id mismatch."""
        token = await approval_service.approve(sample_approval_request)
        with pytest.raises(TokenValidationError, match="plan_id_mismatch"):
            await approval_service.validate_token(token.token, "01ZZZZ1234567890ABCDEFGHIJ")

    async def test_validate_raises_gate_id_mismatch(
        self, approval_service, sample_approval_request
    ):
        """US5: validate_token() raises TokenValidationError for gate_id mismatch."""
        token = await approval_service.approve(sample_approval_request)
        with pytest.raises(TokenValidationError, match="gate_id_mismatch"):
            await approval_service.validate_token(token.token, SAMPLE_PLAN_ID, gate_id="gate-Z")

    async def test_get_gate_status_after_multi_gate(
        self, approval_service, sample_approval_request_multi_gate
    ):
        """FR-009: get_gate_status() returns correct mapping after approvals."""
        await approval_service.approve(sample_approval_request_multi_gate("gate-A"))
        await approval_service.approve(sample_approval_request_multi_gate("gate-B"))
        status = await approval_service.get_gate_status(SAMPLE_PLAN_ID)
        assert status["gate-A"] == "approved"
        assert status["gate-B"] == "approved"


# ---------------------------------------------------------------------------
# Redis degradation tests
# ---------------------------------------------------------------------------


class TestRedisDegradation:
    """FR-012: Graceful degradation when Redis unavailable."""

    async def test_approve_works_without_redis(self, approval_service_minimal):
        """approve() still returns valid JWT without Redis."""
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            scopes=SAMPLE_SCOPES,
        )
        token = await approval_service_minimal.approve(req)
        assert isinstance(token, ApprovalToken)
        assert token.token.startswith("eyJ")

    async def test_validate_works_without_redis(self, approval_service_minimal):
        """validate_token() still works without Redis (fail-open)."""
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            scopes=SAMPLE_SCOPES,
        )
        token = await approval_service_minimal.approve(req)
        claims = await approval_service_minimal.validate_token(token.token, SAMPLE_PLAN_ID)
        assert claims["plan_id"] == SAMPLE_PLAN_ID

    async def test_get_gate_status_empty_without_redis(self, approval_service_minimal):
        """get_gate_status() returns empty dict without Redis."""
        status = await approval_service_minimal.get_gate_status(SAMPLE_PLAN_ID)
        assert status == {}

    async def test_get_approval_state_none_without_redis(self, approval_service_minimal):
        """get_approval_state() returns None without Redis."""
        state = await approval_service_minimal.get_approval_state(SAMPLE_PLAN_ID, "gate-A")
        assert state is None


# ---------------------------------------------------------------------------
# GateStore adapter tests (T601)
# ---------------------------------------------------------------------------


class TestGateStoreAdapter:
    """Tests for GateStore Redis operations."""

    async def test_store_gate_returns_true(self, gate_store):
        """store_gate() returns True when Redis available."""
        result = await gate_store.store_gate(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            token_id="tok-123",
            preview_state=None,
            selected_option=None,
            token_claims={"plan_id": SAMPLE_PLAN_ID},
        )
        assert result is True

    async def test_store_gate_returns_false_no_redis(self, gate_store_no_redis):
        """store_gate() returns False when Redis is None."""
        result = await gate_store_no_redis.store_gate(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            token_id="tok-123",
            preview_state=None,
            selected_option=None,
            token_claims={},
        )
        assert result is False

    async def test_store_gate_returns_false_on_error(self):
        """store_gate() returns False and logs warning on Redis error."""
        broken_redis = AsyncMock()
        broken_redis.set = AsyncMock(side_effect=ConnectionError("down"))
        store = GateStore(broken_redis, default_ttl_s=900)
        result = await store.store_gate(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            token_id="tok-123",
            preview_state=None,
            selected_option=None,
            token_claims={},
        )
        assert result is False

    async def test_get_gate_returns_data(self, gate_store):
        """get_gate() returns stored gate data on hit."""
        await gate_store.store_gate(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            token_id="tok-123",
            preview_state=None,
            selected_option=None,
            token_claims={"plan_id": SAMPLE_PLAN_ID},
        )
        data = await gate_store.get_gate(SAMPLE_PLAN_ID, "gate-A")
        assert data is not None
        assert data["status"] == "approved"
        assert data["token_id"] == "tok-123"

    async def test_get_gate_returns_none_on_miss(self, gate_store):
        """get_gate() returns None on cache miss."""
        data = await gate_store.get_gate(SAMPLE_PLAN_ID, "gate-nonexistent")
        assert data is None

    async def test_get_gate_returns_none_no_redis(self, gate_store_no_redis):
        """get_gate() returns None when Redis is None."""
        data = await gate_store_no_redis.get_gate(SAMPLE_PLAN_ID, "gate-A")
        assert data is None

    async def test_mark_consumed_first_call_true(self, gate_store):
        """mark_consumed() returns True on first call (SET NX succeeds)."""
        result = await gate_store.mark_consumed("tok-456", ttl_s=900)
        assert result is True

    async def test_mark_consumed_second_call_false(self, gate_store):
        """mark_consumed() returns False on second call (SET NX fails)."""
        await gate_store.mark_consumed("tok-456", ttl_s=900)
        result = await gate_store.mark_consumed("tok-456", ttl_s=900)
        assert result is False

    async def test_mark_consumed_no_redis_returns_true(self, gate_store_no_redis):
        """mark_consumed() returns True when Redis is None (fail-open)."""
        result = await gate_store_no_redis.mark_consumed("tok-789", ttl_s=900)
        assert result is True

    async def test_is_consumed_no_redis_returns_false(self, gate_store_no_redis):
        """is_consumed() returns False when Redis is None (fail-open)."""
        result = await gate_store_no_redis.is_consumed("tok-789")
        assert result is False

    async def test_get_all_gates_by_prefix(self, gate_store):
        """get_all_gates_by_prefix() returns gate_id -> status mapping."""
        await gate_store.store_gate(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            token_id="tok-1",
            preview_state=None,
            selected_option=None,
            token_claims={},
        )
        await gate_store.store_gate(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-B",
            token_id="tok-2",
            preview_state=None,
            selected_option=None,
            token_claims={},
        )
        result = await gate_store.get_all_gates_by_prefix(SAMPLE_PLAN_ID)
        assert result["gate-A"] == "approved"
        assert result["gate-B"] == "approved"

    async def test_get_all_gates_by_prefix_no_redis(self, gate_store_no_redis):
        """get_all_gates_by_prefix() returns empty dict when Redis is None."""
        result = await gate_store_no_redis.get_all_gates_by_prefix(SAMPLE_PLAN_ID)
        assert result == {}


# ---------------------------------------------------------------------------
# Factory function tests
# ---------------------------------------------------------------------------


class TestFactory:
    """Tests for create_approval_service() factory."""

    def test_factory_creates_valid_service(self, monkeypatch):
        """create_approval_service() creates valid ApprovalService."""
        monkeypatch.setenv("APPROVAL_TOKEN_SECRET", "test-secret-minimum-16-chars-ok")
        service = create_approval_service()
        assert isinstance(service, ApprovalService)

    def test_factory_raises_on_missing_secret(self, monkeypatch):
        """create_approval_service() raises ApprovalConfigError if no secret."""
        monkeypatch.delenv("APPROVAL_TOKEN_SECRET", raising=False)
        from components.ApprovalGate.domain.models import ApprovalConfigError

        with pytest.raises(ApprovalConfigError, match="JWT secret not configured"):
            create_approval_service(jwt_secret="")

    def test_factory_raises_on_short_secret(self, monkeypatch):
        """create_approval_service() raises ApprovalConfigError if secret too short."""
        monkeypatch.delenv("APPROVAL_TOKEN_SECRET", raising=False)
        from components.ApprovalGate.domain.models import ApprovalConfigError

        with pytest.raises(ApprovalConfigError, match="too short"):
            create_approval_service(jwt_secret="short")

    def test_factory_reads_ttl_from_env(self, monkeypatch):
        """create_approval_service() reads APPROVAL_TOKEN_TTL_S from env."""
        monkeypatch.setenv("APPROVAL_TOKEN_SECRET", "test-secret-minimum-16-chars-ok")
        monkeypatch.setenv("APPROVAL_TOKEN_TTL_S", "300")
        service = create_approval_service()
        assert service._token_ttl_s == 300
