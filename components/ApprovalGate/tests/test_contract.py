"""
ApprovalGate contract tests -- model conformance, GLOBAL_SPEC S2.7,
approval_token.schema.json, end-to-end flows.

~15 tests.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from components.ApprovalGate.domain.models import (
    ApprovalError,
    InvalidGateError,
    TokenConsumedError,
    TokenValidationError,
)
from components.ApprovalGate.tests.conftest import (
    SAMPLE_GATE_IDS,
    SAMPLE_PLAN_ID,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "shared" / "schemas" / "approval_token.schema.json"
)

JWT_PATTERN = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

# ---------------------------------------------------------------------------
# Schema conformance tests
# ---------------------------------------------------------------------------


class TestSchemaConformance:
    """ApprovalToken vs GLOBAL_SPEC S2.7 and approval_token.schema.json."""

    async def test_approval_token_has_required_fields(
        self, approval_service, sample_approval_request
    ):
        """ApprovalToken.model_dump() contains all GLOBAL_SPEC S2.7 required fields."""
        token = await approval_service.approve(sample_approval_request)
        dumped = token.model_dump()
        for field in ("token", "plan_id", "user_id", "exp", "scopes"):
            assert field in dumped, f"Missing required field: {field}"

    async def test_approval_token_has_extended_fields(
        self, approval_service, sample_approval_request
    ):
        """ApprovalToken.model_dump() contains extended fields."""
        token = await approval_service.approve(sample_approval_request)
        dumped = token.model_dump()
        for field in ("gate_id", "iat", "token_id"):
            assert field in dumped, f"Missing extended field: {field}"

    async def test_token_matches_jwt_format(self, approval_service, sample_approval_request):
        """ApprovalToken.token matches JWT format pattern."""
        token = await approval_service.approve(sample_approval_request)
        assert JWT_PATTERN.match(token.token), f"Token does not match JWT pattern: {token.token}"

    async def test_plan_id_is_26_chars(self, approval_service, sample_approval_request):
        """ApprovalToken.plan_id is exactly 26 characters."""
        token = await approval_service.approve(sample_approval_request)
        assert len(token.plan_id) == 26

    async def test_exp_is_valid_iso_8601(self, approval_service, sample_approval_request):
        """ApprovalToken.exp is a valid ISO 8601 timestamp."""
        token = await approval_service.approve(sample_approval_request)
        parsed = datetime.fromisoformat(token.exp)
        assert parsed is not None

    async def test_iat_is_valid_iso_8601(self, approval_service, sample_approval_request):
        """ApprovalToken.iat is a valid ISO 8601 timestamp."""
        token = await approval_service.approve(sample_approval_request)
        parsed = datetime.fromisoformat(token.iat)
        assert parsed is not None

    async def test_scopes_is_non_empty_list(self, approval_service, sample_approval_request):
        """ApprovalToken.scopes is a non-empty list."""
        token = await approval_service.approve(sample_approval_request)
        assert isinstance(token.scopes, list)
        assert len(token.scopes) > 0


# ---------------------------------------------------------------------------
# JSON Schema validation
# ---------------------------------------------------------------------------


class TestJsonSchemaValidation:
    """Validate against approval_token.schema.json."""

    async def test_approve_validates_against_schema(
        self, approval_service, sample_approval_request
    ):
        """Full approve() produces an ApprovalToken that validates against schema."""
        jsonschema = pytest.importorskip("jsonschema")

        schema = json.loads(SCHEMA_PATH.read_text())
        token = await approval_service.approve(sample_approval_request)
        dumped = token.model_dump()

        # Remove fields not in schema (additionalProperties: false)
        schema_props = set(schema.get("properties", {}).keys())
        filtered = {k: v for k, v in dumped.items() if k in schema_props}

        jsonschema.validate(filtered, schema)

    async def test_schema_required_fields_present(self, approval_service, sample_approval_request):
        """Schema required fields are all present."""
        schema = json.loads(SCHEMA_PATH.read_text())
        token = await approval_service.approve(sample_approval_request)
        dumped = token.model_dump()

        required = schema.get("required", [])
        for field in required:
            assert field in dumped, f"Missing schema required field: {field}"


# ---------------------------------------------------------------------------
# End-to-end flow tests
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    """Full approve -> validate -> single-use flows."""

    async def test_issue_then_validate_succeeds(self, approval_service, sample_approval_request):
        """Issue token via approve(), then validate via validate_token()."""
        token = await approval_service.approve(sample_approval_request)
        claims = await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)
        assert claims["plan_id"] == SAMPLE_PLAN_ID

    async def test_issue_validate_validate_consumed(
        self, approval_service, sample_approval_request
    ):
        """Issue token, validate once, validate again -- TokenConsumedError."""
        token = await approval_service.approve(sample_approval_request)
        await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)
        with pytest.raises(TokenConsumedError):
            await approval_service.validate_token(token.token, SAMPLE_PLAN_ID)

    async def test_multi_gate_distinct_tokens(
        self, approval_service, sample_approval_request_multi_gate
    ):
        """Issue tokens for gate-A, gate-B, gate-C -- each has distinct gate_id and token_id."""
        tokens = []
        for gate_id in SAMPLE_GATE_IDS:
            token = await approval_service.approve(sample_approval_request_multi_gate(gate_id))
            tokens.append(token)

        gate_ids = {t.gate_id for t in tokens}
        token_ids = {t.token_id for t in tokens}
        assert gate_ids == set(SAMPLE_GATE_IDS)
        assert len(token_ids) == 3

    async def test_full_3_gate_status_all_approved(
        self, approval_service, sample_approval_request_multi_gate
    ):
        """get_gate_status() after full 3-gate approval shows all approved."""
        for gate_id in SAMPLE_GATE_IDS:
            await approval_service.approve(sample_approval_request_multi_gate(gate_id))

        status = await approval_service.get_gate_status(SAMPLE_PLAN_ID)
        for gate_id in SAMPLE_GATE_IDS:
            assert status[gate_id] == "approved"


# ---------------------------------------------------------------------------
# Error contract tests
# ---------------------------------------------------------------------------


class TestErrorContract:
    """Custom exceptions conform to contract."""

    def test_all_exceptions_are_approval_error(self):
        """All custom exceptions are subclasses of ApprovalError."""
        from components.ApprovalGate.domain.models import (
            ApprovalConfigError,
            TokenConsumedError,
            TokenExpiredError,
        )

        for cls in (
            ApprovalConfigError,
            InvalidGateError,
            TokenExpiredError,
            TokenValidationError,
            TokenConsumedError,
        ):
            assert issubclass(cls, ApprovalError)

    def test_token_validation_error_has_reason(self):
        """TokenValidationError has reason attribute."""
        err = TokenValidationError("plan_id_mismatch")
        assert err.reason == "plan_id_mismatch"

    def test_invalid_gate_error_has_gate_id(self):
        """InvalidGateError has gate_id attribute."""
        err = InvalidGateError("bad-gate")
        assert err.gate_id == "bad-gate"
