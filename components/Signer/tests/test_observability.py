"""
Observability Tests for Signer

Verifies that structured logging does not leak private keys,
full plan content, or other sensitive information.
"""

import logging

import pytest
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from components.Signer.domain.models import InvalidSignatureError
from components.Signer.service.signer_service import SignerService


def _all_log_text(records: list[logging.LogRecord]) -> str:
    """Combine message and extra fields into searchable text.

    Args:
        records: List of captured log records.

    Returns:
        Combined string of all log messages and extra values.
    """
    parts: list[str] = []
    for r in records:
        parts.append(r.getMessage())
        for key in (
            "plan_hash",
            "pubkey_id",
            "signer",
            "nonce",
            "reason",
            "plan_hash_expected",
            "plan_hash_computed",
        ):
            val = getattr(r, key, None)
            if val is not None:
                parts.append(str(val))
    return " ".join(parts)


class TestObservability:
    """Tests that no PII or secrets appear in log output."""

    async def test_sign_does_not_log_private_key(
        self,
        signer_service: SignerService,
        test_key_pair: tuple,
        sample_plan: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Sign logs do not contain hex-encoded private key."""
        priv, _ = test_key_pair
        priv_hex = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()

        with caplog.at_level(logging.DEBUG, logger="signer"):
            await signer_service.sign_plan(sample_plan)

        full_log = _all_log_text(caplog.records)
        assert priv_hex not in full_log

    async def test_sign_does_not_log_full_plan(
        self,
        signer_service: SignerService,
        sample_plan: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Sign logs do not contain the full plan JSON."""
        with caplog.at_level(logging.DEBUG, logger="signer"):
            await signer_service.sign_plan(sample_plan)

        full_log = _all_log_text(caplog.records)
        assert "book_flight" not in full_log
        assert "search_flights" not in full_log

    async def test_verify_failure_does_not_log_plan(
        self,
        signer_service: SignerService,
        sample_plan: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verification failure logs reason, not full plan."""
        sig = await signer_service.sign_plan(sample_plan)
        tampered = {**sample_plan, "plan_id": "TAMPERED"}

        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="signer"), pytest.raises(InvalidSignatureError):
            await signer_service.verify_signature(tampered, sig.model_dump())

        full_log = _all_log_text(caplog.records)
        assert "hash_mismatch" in full_log
        assert "book_flight" not in full_log

    async def test_sign_produces_info_log(
        self,
        signer_service: SignerService,
        sample_plan: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Signing produces INFO log with plan_hash in extra."""
        with caplog.at_level(logging.INFO, logger="signer"):
            sig = await signer_service.sign_plan(sample_plan)

        assert "plan_signed" in caplog.text
        full_log = _all_log_text(caplog.records)
        assert sig.plan_hash in full_log

    async def test_verify_success_produces_info_log(
        self,
        signer_service: SignerService,
        sample_plan: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Successful verification produces an INFO log."""
        sig = await signer_service.sign_plan(sample_plan)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="signer"):
            await signer_service.verify_signature(sample_plan, sig.model_dump())

        assert "signature_verified" in caplog.text
