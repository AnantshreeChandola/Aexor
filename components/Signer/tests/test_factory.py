"""
Unit Tests for create_signer_service() Factory

Tests key loading from environment variables, including
missing, invalid, and wrong-length key scenarios.
"""

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from components.Signer.domain.models import (
    SigningKeyNotConfiguredError,
)
from components.Signer.service.signer_service import (
    SignerService,
    create_signer_service,
)


def _generate_hex_keys() -> tuple[str, str]:
    """Generate valid hex-encoded Ed25519 key pair.

    Returns:
        Tuple of (private_hex, public_hex).
    """
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    pub_bytes = priv.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    return priv_bytes.hex(), pub_bytes.hex()


class TestCreateSignerService:
    """Tests for create_signer_service() factory."""

    def test_factory_creates_service_with_valid_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Valid hex env vars produce a working SignerService."""
        priv_hex, pub_hex = _generate_hex_keys()
        monkeypatch.setenv("PLAN_SIGNING_PRIVATE_KEY", priv_hex)
        monkeypatch.setenv("PLAN_SIGNING_PUBLIC_KEY", pub_hex)

        service = create_signer_service()
        assert isinstance(service, SignerService)

    def test_factory_missing_private_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing PLAN_SIGNING_PRIVATE_KEY raises error."""
        monkeypatch.delenv(
            "PLAN_SIGNING_PRIVATE_KEY", raising=False
        )
        _, pub_hex = _generate_hex_keys()
        monkeypatch.setenv("PLAN_SIGNING_PUBLIC_KEY", pub_hex)

        with pytest.raises(SigningKeyNotConfiguredError) as exc_info:
            create_signer_service()
        assert "PRIVATE_KEY" in exc_info.value.reason

    def test_factory_missing_public_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing PLAN_SIGNING_PUBLIC_KEY raises error."""
        priv_hex, _ = _generate_hex_keys()
        monkeypatch.setenv("PLAN_SIGNING_PRIVATE_KEY", priv_hex)
        monkeypatch.delenv(
            "PLAN_SIGNING_PUBLIC_KEY", raising=False
        )

        with pytest.raises(SigningKeyNotConfiguredError) as exc_info:
            create_signer_service()
        assert "PUBLIC_KEY" in exc_info.value.reason

    def test_factory_invalid_hex_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-hex string raises error."""
        monkeypatch.setenv(
            "PLAN_SIGNING_PRIVATE_KEY", "not-valid-hex-string"
        )
        monkeypatch.setenv(
            "PLAN_SIGNING_PUBLIC_KEY", "also-not-hex"
        )

        with pytest.raises(SigningKeyNotConfiguredError):
            create_signer_service()

    def test_factory_wrong_key_length_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hex of wrong length (16 bytes) raises error."""
        short_hex = "aa" * 16  # 16 bytes, need 32
        monkeypatch.setenv("PLAN_SIGNING_PRIVATE_KEY", short_hex)
        monkeypatch.setenv("PLAN_SIGNING_PUBLIC_KEY", short_hex)

        with pytest.raises(SigningKeyNotConfiguredError):
            create_signer_service()
