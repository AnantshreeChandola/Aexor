"""
Signer Service

Ed25519 plan signing and verification service. Signs deterministic
plans and verifies signatures for integrity and audit purposes.
"""

import base64
import logging
import os
from datetime import UTC, datetime
from typing import Any

import ulid
from cryptography.exceptions import InvalidSignature as CryptoInvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from components.Signer.adapters.canonicalizer import (
    canonicalize_plan,
    compute_plan_hash,
)
from components.Signer.domain.models import (
    InvalidSignatureError,
    PlanSignature,
    SigningKeyNotConfiguredError,
    UnsupportedAlgorithmError,
)

logger = logging.getLogger("signer")


class SignerService:
    """Ed25519 plan signing and verification service."""

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        public_key: Ed25519PublicKey,
        pubkey_id: str = "k1",
    ) -> None:
        """Initialize with a single key pair.

        Args:
            private_key: Ed25519 private key for signing.
            public_key: Ed25519 public key for verification.
            pubkey_id: Key identifier (default: "k1").
        """
        self._private_key = private_key
        self._public_key = public_key
        self._pubkey_id = pubkey_id

    async def sign_plan(
        self,
        plan_data: dict[str, Any],
        signer_identity: str = "planner@system",
    ) -> PlanSignature:
        """Sign a plan and return a PlanSignature.

        Args:
            plan_data: Plan dictionary to sign.
            signer_identity: Identity of the signer.

        Returns:
            PlanSignature conforming to signature.schema.json.

        Raises:
            ValueError: If plan_data is empty or None.
        """
        if not plan_data:
            raise ValueError("plan_data must be a non-empty dict")

        canonical_json = canonicalize_plan(plan_data)
        plan_hash = compute_plan_hash(plan_data)
        canonical_bytes = canonical_json.encode("utf-8")

        sig_bytes = self._private_key.sign(canonical_bytes)
        sig_b64 = base64.b64encode(sig_bytes).decode("ascii")

        nonce = str(ulid.new())
        ts = datetime.now(UTC).isoformat()

        signature = PlanSignature(
            algo="Ed25519",
            signer=signer_identity,
            ts=ts,
            nonce=nonce,
            signature=sig_b64,
            pubkey_id=self._pubkey_id,
            plan_hash=plan_hash,
        )

        logger.info(
            "plan_signed",
            extra={
                "plan_hash": plan_hash,
                "pubkey_id": self._pubkey_id,
                "signer": signer_identity,
                "nonce": nonce,
            },
        )

        return signature

    async def verify_signature(
        self,
        plan_data: dict[str, Any],
        signature_data: dict[str, Any],
    ) -> bool:
        """Verify a plan signature. Raises on failure.

        Args:
            plan_data: Plan dictionary to verify.
            signature_data: Signature dict with PlanSignature fields.

        Returns:
            True if signature is valid.

        Raises:
            UnsupportedAlgorithmError: If algo is not Ed25519.
            InvalidSignatureError: If signature verification fails.
        """
        algo = signature_data.get("algo", "")
        if algo != "Ed25519":
            raise UnsupportedAlgorithmError(algo)

        computed_hash = compute_plan_hash(plan_data)
        expected_hash = signature_data.get("plan_hash", "")

        if computed_hash != expected_hash:
            logger.warning(
                "signature_verification_failed",
                extra={
                    "reason": "hash_mismatch",
                    "plan_hash_expected": expected_hash,
                    "plan_hash_computed": computed_hash,
                },
            )
            raise InvalidSignatureError(reason="hash_mismatch")

        sig_b64 = signature_data.get("signature", "")
        try:
            sig_bytes = base64.b64decode(sig_b64, validate=True)
        except Exception:
            logger.warning(
                "signature_verification_failed",
                extra={
                    "reason": "malformed_signature",
                    "plan_hash_expected": expected_hash,
                    "plan_hash_computed": computed_hash,
                },
            )
            raise InvalidSignatureError(reason="malformed_signature")

        canonical_json = canonicalize_plan(plan_data)
        canonical_bytes = canonical_json.encode("utf-8")

        try:
            self._public_key.verify(sig_bytes, canonical_bytes)
        except CryptoInvalidSignature:
            logger.warning(
                "signature_verification_failed",
                extra={
                    "reason": "signature_verification_failed",
                    "plan_hash_expected": expected_hash,
                    "plan_hash_computed": computed_hash,
                },
            )
            raise InvalidSignatureError(
                reason="signature_verification_failed"
            )

        logger.info(
            "signature_verified",
            extra={
                "plan_hash": signature_data.get("plan_hash"),
                "pubkey_id": signature_data.get("pubkey_id"),
            },
        )

        return True


def create_signer_service(pubkey_id: str = "k1") -> SignerService:
    """Create SignerService from environment variables.

    Reads:
        PLAN_SIGNING_PRIVATE_KEY: Hex-encoded Ed25519 private key.
        PLAN_SIGNING_PUBLIC_KEY: Hex-encoded Ed25519 public key.

    Args:
        pubkey_id: Key identifier (default: "k1").

    Returns:
        Configured SignerService.

    Raises:
        SigningKeyNotConfiguredError: If env vars missing or invalid.
    """
    private_hex = os.environ.get("PLAN_SIGNING_PRIVATE_KEY")
    if not private_hex:
        raise SigningKeyNotConfiguredError(
            "PLAN_SIGNING_PRIVATE_KEY not set"
        )

    public_hex = os.environ.get("PLAN_SIGNING_PUBLIC_KEY")
    if not public_hex:
        raise SigningKeyNotConfiguredError(
            "PLAN_SIGNING_PUBLIC_KEY not set"
        )

    try:
        private_key = Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(private_hex)
        )
    except (ValueError, Exception) as exc:
        raise SigningKeyNotConfiguredError(
            f"Invalid private key hex: {exc}"
        )

    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(public_hex)
        )
    except (ValueError, Exception) as exc:
        raise SigningKeyNotConfiguredError(
            f"Invalid public key hex: {exc}"
        )

    logger.info(
        "signer_service_created",
        extra={"pubkey_id": pubkey_id},
    )

    return SignerService(
        private_key=private_key,
        public_key=public_key,
        pubkey_id=pubkey_id,
    )
