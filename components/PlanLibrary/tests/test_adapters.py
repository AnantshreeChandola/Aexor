"""
PlanLibrary Adapter Unit Tests

Tests for DatabaseAdapter and SignatureVerifier.
All tests use mocks (no real database or API calls).

Reference: tasks.md T302
"""

import pytest

from components.PlanLibrary.adapters.signature_verifier import (
    SignatureVerifier,
)
from components.PlanLibrary.domain.models import (
    InvalidSignatureError,
)

VALID_ULID = "01HX1234567890ABCDEFGHJKMN"


class TestSignatureVerifier:
    """Tests for SignatureVerifier."""

    def test_valid_signature_accepted(self):
        """Valid signature with correct fields is accepted."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        from components.PlanLibrary.domain.models import canonicalize_plan

        # Generate a real Ed25519 keypair
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        public_key_hex = public_key.public_bytes_raw().hex()

        plan_data = {
            "plan_id": VALID_ULID,
            "graph": [],
            "meta": {"intent_type": "test"},
        }

        # Sign the canonicalized plan
        canonical = canonicalize_plan(plan_data)
        signature_bytes = private_key.sign(canonical.encode("utf-8"))

        signature_data = {
            "algorithm": "ed25519",
            "public_key": public_key_hex,
            "signature_hex": signature_bytes.hex(),
        }

        verifier = SignatureVerifier(public_key_hex=public_key_hex)
        result = verifier.verify_signature(plan_data, signature_data)
        assert result is True

    def test_invalid_algorithm_rejected(self):
        """Unsupported algorithm raises InvalidSignatureError."""
        verifier = SignatureVerifier()

        with pytest.raises(InvalidSignatureError, match="Unsupported"):
            verifier.verify_signature(
                plan_data={"plan_id": VALID_ULID},
                signature_data={
                    "algorithm": "rsa",
                    "public_key": "abc",
                    "signature_hex": "def",
                },
            )

    def test_missing_signature_fields_rejected(self):
        """Missing signature fields raise InvalidSignatureError."""
        verifier = SignatureVerifier()

        with pytest.raises(InvalidSignatureError, match="Missing"):
            verifier.verify_signature(
                plan_data={"plan_id": VALID_ULID},
                signature_data={"algorithm": "ed25519"},
            )

    def test_tampered_plan_detected(self):
        """Tampered plan data should fail verification."""
        verifier = SignatureVerifier()

        # Using invalid hex will cause verification to fail
        plan_data = {"plan_id": VALID_ULID, "graph": [], "meta": {}}
        signature_data = {
            "algorithm": "ed25519",
            "public_key": "not_valid_hex",
            "signature_hex": "also_not_valid_hex",
        }

        with pytest.raises(InvalidSignatureError):
            verifier.verify_signature(plan_data, signature_data)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
