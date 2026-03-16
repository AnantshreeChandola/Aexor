"""
Unit Tests for SignerService

Tests sign_plan() and verify_signature() methods with real
Ed25519 keys generated in fixtures.
"""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from components.Signer.adapters.canonicalizer import compute_plan_hash
from components.Signer.domain.models import (
    InvalidSignatureError,
    PlanSignature,
    UnsupportedAlgorithmError,
)
from components.Signer.service.signer_service import SignerService

# ----------------------------------------------------------------
# sign_plan() tests (T013)
# ----------------------------------------------------------------


class TestSignPlan:
    """Unit tests for SignerService.sign_plan()."""

    async def test_sign_returns_plan_signature(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """sign_plan returns a PlanSignature instance."""
        result = await signer_service.sign_plan(sample_plan)
        assert isinstance(result, PlanSignature)

    async def test_sign_algo_is_ed25519(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """algo field is always Ed25519."""
        result = await signer_service.sign_plan(sample_plan)
        assert result.algo == "Ed25519"

    async def test_sign_plan_hash_deterministic(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Same plan dict gives same plan_hash."""
        sig1 = await signer_service.sign_plan(sample_plan)
        sig2 = await signer_service.sign_plan(sample_plan)
        assert sig1.plan_hash == sig2.plan_hash

    async def test_sign_signature_is_base64(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """signature field is valid base64."""
        result = await signer_service.sign_plan(sample_plan)
        decoded = base64.b64decode(result.signature)
        assert len(decoded) > 0

    async def test_sign_pubkey_id_matches_constructor(
        self,
        test_key_pair: tuple,
        sample_plan: dict,
    ) -> None:
        """pubkey_id matches the value from constructor."""
        priv, pub = test_key_pair
        svc = SignerService(priv, pub, pubkey_id="k42")
        result = await svc.sign_plan(sample_plan)
        assert result.pubkey_id == "k42"

    async def test_sign_signer_identity_default(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Default signer identity is planner@system."""
        result = await signer_service.sign_plan(sample_plan)
        assert result.signer == "planner@system"

    async def test_sign_signer_identity_custom(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Custom identity is used when provided."""
        result = await signer_service.sign_plan(sample_plan, signer_identity="admin@ops")
        assert result.signer == "admin@ops"

    async def test_sign_empty_plan_raises_value_error(
        self,
        signer_service: SignerService,
    ) -> None:
        """Empty dict raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            await signer_service.sign_plan({})

    async def test_sign_none_plan_raises_value_error(
        self,
        signer_service: SignerService,
    ) -> None:
        """None raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            await signer_service.sign_plan(None)

    async def test_sign_plan_hash_matches_compute(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """plan_hash matches compute_plan_hash() output."""
        result = await signer_service.sign_plan(sample_plan)
        expected = compute_plan_hash(sample_plan)
        assert result.plan_hash == expected


# ----------------------------------------------------------------
# verify_signature() tests (T014)
# ----------------------------------------------------------------


class TestVerifySignature:
    """Unit tests for SignerService.verify_signature()."""

    async def test_verify_valid_signature_returns_true(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Sign then verify succeeds."""
        sig = await signer_service.sign_plan(sample_plan)
        result = await signer_service.verify_signature(sample_plan, sig.model_dump())
        assert result is True

    async def test_verify_tampered_plan_raises(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Modified plan after signing raises hash_mismatch."""
        sig = await signer_service.sign_plan(sample_plan)
        tampered = {**sample_plan, "plan_id": "TAMPERED"}
        with pytest.raises(InvalidSignatureError) as exc_info:
            await signer_service.verify_signature(tampered, sig.model_dump())
        assert exc_info.value.reason == "hash_mismatch"

    async def test_verify_wrong_algo_raises_unsupported(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """algo=RSA raises UnsupportedAlgorithmError."""
        sig = await signer_service.sign_plan(sample_plan)
        sig_dict = sig.model_dump()
        sig_dict["algo"] = "RSA"
        with pytest.raises(UnsupportedAlgorithmError) as exc_info:
            await signer_service.verify_signature(sample_plan, sig_dict)
        assert exc_info.value.algo == "RSA"

    async def test_verify_malformed_base64_raises(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Corrupted signature base64 raises malformed_signature."""
        sig = await signer_service.sign_plan(sample_plan)
        sig_dict = sig.model_dump()
        sig_dict["signature"] = "!!!not-valid-base64!!!"
        with pytest.raises(InvalidSignatureError) as exc_info:
            await signer_service.verify_signature(sample_plan, sig_dict)
        assert exc_info.value.reason == "malformed_signature"

    async def test_verify_wrong_signature_bytes(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Valid base64 but wrong bytes raises verification_failed."""
        sig = await signer_service.sign_plan(sample_plan)
        sig_dict = sig.model_dump()
        # Create valid base64 of wrong bytes (64 zero bytes)
        wrong_sig = base64.b64encode(b"\x00" * 64).decode()
        sig_dict["signature"] = wrong_sig
        with pytest.raises(InvalidSignatureError) as exc_info:
            await signer_service.verify_signature(sample_plan, sig_dict)
        assert exc_info.value.reason == "signature_verification_failed"

    async def test_verify_plan_hash_mismatch_explicit(
        self,
        signer_service: SignerService,
        sample_plan: dict,
    ) -> None:
        """Manually wrong plan_hash raises hash_mismatch."""
        sig = await signer_service.sign_plan(sample_plan)
        sig_dict = sig.model_dump()
        sig_dict["plan_hash"] = "a" * 64
        with pytest.raises(InvalidSignatureError) as exc_info:
            await signer_service.verify_signature(sample_plan, sig_dict)
        assert exc_info.value.reason == "hash_mismatch"

    async def test_verify_different_key_fails(
        self,
        sample_plan: dict,
        signer_service: SignerService,
    ) -> None:
        """Sign with key A, verify with key B fails."""
        sig = await signer_service.sign_plan(sample_plan)

        other_priv = Ed25519PrivateKey.generate()
        other_pub = other_priv.public_key()
        other_svc = SignerService(other_priv, other_pub)

        with pytest.raises(InvalidSignatureError) as exc_info:
            await other_svc.verify_signature(sample_plan, sig.model_dump())
        assert exc_info.value.reason == "signature_verification_failed"
