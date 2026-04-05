"""
ApprovalGate unit tests -- core approval logic.

Tests domain models, exceptions, TokenIssuer adapter,
idempotent re-approval, and multi-gate isolation.
~33 tests covering US1-US5, FR-001 through FR-010.
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from components.ApprovalGate.domain.models import (
    ApprovalConfigError,
    ApprovalError,
    ApprovalRequest,
    ApprovalState,
    ApprovalToken,
    InvalidGateError,
    TokenConsumedError,
    TokenExpiredError,
    TokenValidationError,
)
from components.ApprovalGate.tests.conftest import (
    SAMPLE_GATE_IDS,
    SAMPLE_PLAN_ID,
    SAMPLE_SCOPES,
    SAMPLE_USER_ID,
)

# ---------------------------------------------------------------------------
# ApprovalRequest validation tests
# ---------------------------------------------------------------------------


class TestApprovalRequestValidation:
    """ApprovalRequest model validation."""

    def test_accepts_valid_request(self):
        """Accepts valid request with all fields."""
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            gate_id="gate-A",
            scopes=["calendar.write"],
            selected_option={"slot": "Tuesday 10:00"},
            trace_id="trace-123",
            policy_matched=True,
            role="Fetcher",
            tool="google.calendar",
        )
        assert req.plan_id == SAMPLE_PLAN_ID
        assert req.user_id == SAMPLE_USER_ID

    def test_rejects_plan_id_too_short(self):
        """Rejects plan_id shorter than 26 chars."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id="short",
                user_id=SAMPLE_USER_ID,
                scopes=SAMPLE_SCOPES,
            )

    def test_rejects_plan_id_too_long(self):
        """Rejects plan_id longer than 26 chars."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id="A" * 27,
                user_id=SAMPLE_USER_ID,
                scopes=SAMPLE_SCOPES,
            )

    def test_rejects_empty_user_id(self):
        """Rejects empty user_id."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id=SAMPLE_PLAN_ID,
                user_id="",
                scopes=SAMPLE_SCOPES,
            )

    def test_rejects_invalid_gate_id_no_prefix(self):
        """Rejects gate_id not matching pattern (no prefix)."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                gate_id="invalid",
                scopes=SAMPLE_SCOPES,
            )

    def test_rejects_invalid_gate_id_empty_suffix(self):
        """Rejects gate_id 'gate-' (empty suffix)."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                gate_id="gate-",
                scopes=SAMPLE_SCOPES,
            )

    def test_rejects_invalid_gate_id_wrong_case(self):
        """Rejects gate_id 'GATE-A' (uppercase prefix)."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                gate_id="GATE-A",
                scopes=SAMPLE_SCOPES,
            )

    def test_rejects_empty_scopes(self):
        """Rejects empty scopes list."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                scopes=[],
            )

    def test_rejects_scopes_too_many(self):
        """Rejects scopes list with more than 10 items."""
        with pytest.raises(ValidationError):
            ApprovalRequest(
                plan_id=SAMPLE_PLAN_ID,
                user_id=SAMPLE_USER_ID,
                scopes=[f"scope.{i}" for i in range(11)],
            )

    def test_accepts_selected_option_none(self):
        """Accepts selected_option=None (default)."""
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            scopes=SAMPLE_SCOPES,
        )
        assert req.selected_option is None

    def test_accepts_policy_matched_true_default(self):
        """Accepts policy_matched=True (default)."""
        req = ApprovalRequest(
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            scopes=SAMPLE_SCOPES,
        )
        assert req.policy_matched is True


# ---------------------------------------------------------------------------
# ApprovalToken validation tests
# ---------------------------------------------------------------------------


class TestApprovalTokenValidation:
    """ApprovalToken model validation."""

    def test_round_trip_serialization(self):
        """model_dump() then model_validate() produces identical model."""
        original = ApprovalToken(
            token="eyJhbGciOiJIUzI1NiJ9.test.sig",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            gate_id="gate-A",
            scopes=["calendar.write"],
            exp="2026-04-05T10:15:00+00:00",
            iat="2026-04-05T10:00:00+00:00",
            token_id="01JXYZ9876543210KLMNOPQRST",
        )
        dumped = original.model_dump()
        restored = ApprovalToken.model_validate(dumped)
        assert restored == original

    def test_all_required_fields_present(self):
        """All required fields present."""
        token = ApprovalToken(
            token="eyJhbGciOiJIUzI1NiJ9.test.sig",
            plan_id=SAMPLE_PLAN_ID,
            user_id=SAMPLE_USER_ID,
            gate_id="gate-A",
            scopes=["calendar.write"],
            exp="2026-04-05T10:15:00+00:00",
            iat="2026-04-05T10:00:00+00:00",
            token_id="01JXYZ9876543210KLMNOPQRST",
        )
        dumped = token.model_dump()
        for field in ("token", "plan_id", "user_id", "gate_id", "scopes", "exp", "iat", "token_id"):
            assert field in dumped

    def test_plan_id_enforces_26_char(self):
        """plan_id enforces 26-char ULID constraint."""
        with pytest.raises(ValidationError):
            ApprovalToken(
                token="eyJ.test.sig",
                plan_id="short",
                user_id=SAMPLE_USER_ID,
                gate_id="gate-A",
                scopes=["calendar.write"],
                exp="2026-04-05T10:15:00+00:00",
                iat="2026-04-05T10:00:00+00:00",
                token_id="01JXYZ9876543210KLMNOPQRST",
            )


# ---------------------------------------------------------------------------
# ApprovalState validation tests
# ---------------------------------------------------------------------------


class TestApprovalStateValidation:
    """ApprovalState model validation."""

    def test_status_accepts_approved(self):
        """Status accepts 'approved'."""
        state = ApprovalState(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            status="approved",
            approved_at="2026-04-05T10:00:00+00:00",
        )
        assert state.status == "approved"

    def test_status_accepts_pending(self):
        """Status accepts 'pending'."""
        state = ApprovalState(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            status="pending",
            approved_at="2026-04-05T10:00:00+00:00",
        )
        assert state.status == "pending"

    def test_status_accepts_expired(self):
        """Status accepts 'expired'."""
        state = ApprovalState(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            status="expired",
            approved_at="2026-04-05T10:00:00+00:00",
        )
        assert state.status == "expired"

    def test_status_rejects_invalid(self):
        """Status rejects invalid value."""
        with pytest.raises(ValidationError):
            ApprovalState(
                plan_id=SAMPLE_PLAN_ID,
                gate_id="gate-A",
                status="unknown",
                approved_at="2026-04-05T10:00:00+00:00",
            )

    def test_preview_state_defaults_to_none(self):
        """preview_state defaults to None."""
        state = ApprovalState(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            status="approved",
            approved_at="2026-04-05T10:00:00+00:00",
        )
        assert state.preview_state is None

    def test_selected_option_defaults_to_none(self):
        """selected_option defaults to None."""
        state = ApprovalState(
            plan_id=SAMPLE_PLAN_ID,
            gate_id="gate-A",
            status="approved",
            approved_at="2026-04-05T10:00:00+00:00",
        )
        assert state.selected_option is None


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """Custom exception hierarchy."""

    def test_approval_error_is_base(self):
        """ApprovalError is base class."""
        err = ApprovalError("test")
        assert isinstance(err, Exception)

    def test_config_error_is_subclass(self):
        """ApprovalConfigError is subclass of ApprovalError."""
        err = ApprovalConfigError("missing secret")
        assert isinstance(err, ApprovalError)

    def test_invalid_gate_error_stores_gate_id(self):
        """InvalidGateError stores gate_id attribute."""
        err = InvalidGateError("bad-gate")
        assert isinstance(err, ApprovalError)
        assert err.gate_id == "bad-gate"
        assert "bad-gate" in str(err)

    def test_token_expired_error_is_subclass(self):
        """TokenExpiredError is subclass of ApprovalError."""
        err = TokenExpiredError("expired")
        assert isinstance(err, ApprovalError)

    def test_token_validation_error_stores_reason(self):
        """TokenValidationError stores reason attribute."""
        err = TokenValidationError("plan_id_mismatch")
        assert isinstance(err, ApprovalError)
        assert err.reason == "plan_id_mismatch"

    def test_token_consumed_error_is_subclass(self):
        """TokenConsumedError is subclass of ApprovalError."""
        err = TokenConsumedError()
        assert isinstance(err, ApprovalError)


# ---------------------------------------------------------------------------
# TokenIssuer adapter tests (T501)
# ---------------------------------------------------------------------------


class TestTokenIssuer:
    """TokenIssuer adapter tests."""

    def test_sign_returns_jwt_string(self, token_issuer):
        """sign() returns a JWT string starting with 'eyJ'."""
        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "user_id": SAMPLE_USER_ID,
            "gate_id": "gate-A",
            "scopes": ["calendar.write"],
            "exp": int(time.time()) + 900,
            "iat": int(time.time()),
            "token_id": "01JXYZ9876543210KLMNOPQRST",
        }
        token = token_issuer.sign(claims)
        assert token.startswith("eyJ")

    def test_verify_decodes_valid_token(self, token_issuer):
        """verify() decodes a valid token and returns claims dict."""
        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "user_id": SAMPLE_USER_ID,
            "gate_id": "gate-A",
            "scopes": ["calendar.write"],
            "exp": int(time.time()) + 900,
            "iat": int(time.time()),
            "token_id": "01JXYZ9876543210KLMNOPQRST",
        }
        token = token_issuer.sign(claims)
        decoded = token_issuer.verify(token)
        assert decoded["plan_id"] == SAMPLE_PLAN_ID
        assert decoded["user_id"] == SAMPLE_USER_ID

    def test_verify_raises_expired_for_past_exp(self, token_issuer):
        """verify() raises TokenExpiredError for expired token."""
        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "user_id": SAMPLE_USER_ID,
            "gate_id": "gate-A",
            "scopes": ["calendar.write"],
            "exp": int(time.time()) - 100,  # In the past
            "iat": int(time.time()) - 200,
            "token_id": "01JXYZ9876543210KLMNOPQRST",
        }
        token = token_issuer.sign(claims)
        with pytest.raises(TokenExpiredError):
            token_issuer.verify(token)

    def test_verify_raises_validation_for_wrong_secret(self, jwt_secret):
        """verify() raises TokenValidationError for token signed with wrong secret."""
        from components.ApprovalGate.adapters.token_issuer import TokenIssuer

        issuer_a = TokenIssuer("secret-key-A-minimum-16-chars")
        issuer_b = TokenIssuer("secret-key-B-minimum-16-chars")
        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "exp": int(time.time()) + 900,
            "iat": int(time.time()),
            "token_id": "test123",
        }
        token = issuer_a.sign(claims)
        with pytest.raises(TokenValidationError):
            issuer_b.verify(token)

    def test_verify_raises_validation_for_malformed_token(self, token_issuer):
        """verify() raises TokenValidationError for malformed token string."""
        with pytest.raises(TokenValidationError):
            token_issuer.verify("not-a-jwt-token")

    def test_round_trip_claims(self, token_issuer):
        """sign(claims) then verify(token) returns matching claims."""
        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "user_id": SAMPLE_USER_ID,
            "gate_id": "gate-A",
            "scopes": ["calendar.write"],
            "exp": int(time.time()) + 900,
            "iat": int(time.time()),
            "token_id": "01JXYZ9876543210KLMNOPQRST",
        }
        token = token_issuer.sign(claims)
        decoded = token_issuer.verify(token)
        assert decoded["plan_id"] == claims["plan_id"]
        assert decoded["user_id"] == claims["user_id"]
        assert decoded["gate_id"] == claims["gate_id"]
        assert decoded["scopes"] == claims["scopes"]
        assert decoded["token_id"] == claims["token_id"]

    def test_sign_includes_exp_iat_as_integers(self, token_issuer):
        """sign() includes exp and iat as integer Unix timestamps."""
        now = int(time.time())
        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "user_id": SAMPLE_USER_ID,
            "gate_id": "gate-A",
            "scopes": ["calendar.write"],
            "exp": now + 900,
            "iat": now,
            "token_id": "01JXYZ9876543210KLMNOPQRST",
        }
        token = token_issuer.sign(claims)
        decoded = token_issuer.verify(token)
        assert isinstance(decoded["exp"], int)
        assert isinstance(decoded["iat"], int)

    def test_full_claims_round_trip(self, token_issuer):
        """Claims with all required fields round-trip correctly."""
        claims = {
            "plan_id": SAMPLE_PLAN_ID,
            "user_id": SAMPLE_USER_ID,
            "gate_id": "gate-B",
            "scopes": ["calendar.write", "contacts.read"],
            "exp": int(time.time()) + 900,
            "iat": int(time.time()),
            "token_id": "01JXYZ9876543210KLMNOPQXYZ",
        }
        token = token_issuer.sign(claims)
        decoded = token_issuer.verify(token)
        for key in ("plan_id", "user_id", "gate_id", "scopes", "exp", "iat", "token_id"):
            assert key in decoded
            assert decoded[key] == claims[key]


# ---------------------------------------------------------------------------
# Idempotent re-approval tests
# ---------------------------------------------------------------------------


class TestIdempotentReApproval:
    """Second approve() for same gate_id returns existing token."""

    async def test_second_approve_returns_existing(self, approval_service, sample_approval_request):
        """Second approve() for same gate_id returns existing token."""
        token1 = await approval_service.approve(sample_approval_request)
        token2 = await approval_service.approve(sample_approval_request)
        assert token2.token_id == token1.token_id

    async def test_re_approval_same_token_id(self, approval_service, sample_approval_request):
        """Re-approval returns same token_id as first approval."""
        token1 = await approval_service.approve(sample_approval_request)
        token2 = await approval_service.approve(sample_approval_request)
        assert token1.token_id == token2.token_id


# ---------------------------------------------------------------------------
# Multi-gate isolation tests
# ---------------------------------------------------------------------------


class TestMultiGateIsolation:
    """Multi-gate approval isolation."""

    async def test_approve_gate_a_does_not_affect_gate_b(
        self, approval_service, sample_approval_request_multi_gate
    ):
        """Approving gate-A does not affect gate-B status (pending)."""
        await approval_service.approve(sample_approval_request_multi_gate("gate-A"))
        status = await approval_service.get_gate_status(SAMPLE_PLAN_ID)
        assert status.get("gate-A") == "approved"
        assert "gate-B" not in status  # gate-B not yet approved

    async def test_three_sequential_approvals_distinct_tokens(
        self, approval_service, sample_approval_request_multi_gate
    ):
        """Three sequential gate approvals produce three distinct tokens."""
        tokens = []
        for gate_id in SAMPLE_GATE_IDS:
            token = await approval_service.approve(sample_approval_request_multi_gate(gate_id))
            tokens.append(token)

        gate_ids = [t.gate_id for t in tokens]
        assert gate_ids == SAMPLE_GATE_IDS

        token_ids = [t.token_id for t in tokens]
        assert len(set(token_ids)) == 3  # All distinct

    async def test_get_gate_status_after_partial_approvals(
        self, approval_service, sample_approval_request_multi_gate
    ):
        """get_gate_status() returns correct mapping after partial approvals."""
        await approval_service.approve(sample_approval_request_multi_gate("gate-A"))
        await approval_service.approve(sample_approval_request_multi_gate("gate-B"))

        status = await approval_service.get_gate_status(SAMPLE_PLAN_ID)
        assert status["gate-A"] == "approved"
        assert status["gate-B"] == "approved"
        assert "gate-C" not in status
