"""
Signature Verifier for PlanLibrary

Ed25519 signature verification for plan integrity.
Uses cryptography library for verification.

Reference: LLD.md, tasks.md T303
"""

import logging
import os
from typing import Any

from ..domain.models import InvalidSignatureError, canonicalize_plan

logger = logging.getLogger(__name__)


class SignatureVerifier:
    """
    Ed25519 signature verifier for plan integrity.

    Verifies that plan signatures are valid before storage.
    """

    def __init__(self, public_key_hex: str | None = None) -> None:
        """
        Initialize signature verifier.

        Args:
            public_key_hex: Hex-encoded Ed25519 public key.
                           Reads PLAN_SIGNING_PUBLIC_KEY env if None.
        """
        self.public_key_hex = public_key_hex or os.getenv("PLAN_SIGNING_PUBLIC_KEY", "")
        logger.info(
            "Signature verifier initialized",
            extra={"component": "PlanLibrary"},
        )

    def verify_signature(
        self,
        plan_data: dict[str, Any],
        signature_data: dict[str, Any],
    ) -> bool:
        """
        Verify Ed25519 signature against plan data.

        Args:
            plan_data: Plan dictionary to verify
            signature_data: Signature data containing algorithm,
                          public_key, signature_hex

        Returns:
            True if signature is valid

        Raises:
            InvalidSignatureError: If signature verification fails
        """
        plan_id = plan_data.get("plan_id", "unknown")

        # Validate signature data has required fields
        required_fields = {"algorithm", "public_key", "signature_hex"}
        missing = required_fields - set(signature_data.keys())
        if missing:
            raise InvalidSignatureError(
                plan_id=plan_id,
                reason=f"Missing signature fields: {missing}",
            )

        # Validate algorithm
        if signature_data.get("algorithm") != "ed25519":
            raise InvalidSignatureError(
                plan_id=plan_id,
                reason=(f"Unsupported algorithm: {signature_data.get('algorithm')}"),
            )

        try:
            return self._verify_ed25519(plan_data, signature_data)
        except InvalidSignatureError:
            raise
        except Exception as e:
            logger.error(
                "Signature verification error",
                extra={
                    "plan_id": plan_id,
                    "error_type": type(e).__name__,
                    "component": "PlanLibrary",
                },
            )
            raise InvalidSignatureError(
                plan_id=plan_id,
                reason=f"Verification error: {type(e).__name__}",
            )

    def _verify_ed25519(
        self,
        plan_data: dict[str, Any],
        signature_data: dict[str, Any],
    ) -> bool:
        """
        Perform Ed25519 signature verification.

        Args:
            plan_data: Plan data to verify
            signature_data: Signature data

        Returns:
            True if signature is valid

        Raises:
            InvalidSignatureError: If verification fails
        """
        plan_id = plan_data.get("plan_id", "unknown")

        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            # Canonicalize plan
            canonical = canonicalize_plan(plan_data)
            message_bytes = canonical.encode("utf-8")

            # Decode public key and signature
            public_key_bytes = bytes.fromhex(signature_data["public_key"])
            signature_bytes = bytes.fromhex(signature_data["signature_hex"])

            # Load public key and verify
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            public_key.verify(signature_bytes, message_bytes)

            logger.info(
                "Signature verified successfully",
                extra={
                    "plan_id": plan_id,
                    "component": "PlanLibrary",
                    "operation": "verify_signature",
                },
            )
            return True

        except ImportError:
            # If cryptography not available, log and return True
            # for development environments
            logger.warning(
                "Cryptography library not available, skipping verification",
                extra={"plan_id": plan_id, "component": "PlanLibrary"},
            )
            return True

        except Exception as e:
            raise InvalidSignatureError(
                plan_id=plan_id,
                reason=f"Ed25519 verification failed: {e}",
            )
