"""
Signer Domain Models

PlanSignature Pydantic model and error classes for the Signer component.
Field names conform to GLOBAL_SPEC v2.2 Section 2.4 and
shared/schemas/signature.schema.json.
"""

from pydantic import BaseModel, Field


class PlanSignature(BaseModel):
    """Cryptographic signature for a plan.

    Field names match GLOBAL_SPEC v2.2 Section 2.4 and
    shared/schemas/signature.schema.json.
    """

    algo: str = Field(
        default="Ed25519",
        description="Signature algorithm",
    )
    signer: str = Field(
        description="Identity of the signer (e.g., 'planner@system')",
    )
    signature: str = Field(
        description="Base64-encoded Ed25519 signature",
        min_length=64,
    )
    pubkey_id: str = Field(
        description="Public key identifier (e.g., 'k1')",
    )
    plan_hash: str = Field(
        description="SHA-256 hex digest of canonical plan bytes",
        min_length=64,
        max_length=64,
    )
    ts: str = Field(
        description="ISO 8601 timestamp of when the signature was created",
    )
    nonce: str = Field(
        description="ULID nonce to prevent replay attacks",
        min_length=26,
        max_length=26,
    )


# --- Error Classes ---


class SignerError(Exception):
    """Base error for Signer component."""


class SigningKeyNotConfiguredError(SignerError):
    """Raised when private/public key env vars are missing or invalid."""

    def __init__(self, reason: str = "Key not configured") -> None:
        self.reason = reason
        super().__init__(f"Signing key not configured: {reason}")


class InvalidSignatureError(SignerError):
    """Raised when signature verification fails."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid signature: {reason}")


class UnsupportedAlgorithmError(SignerError):
    """Raised when signature uses unsupported algorithm."""

    def __init__(self, algo: str) -> None:
        self.algo = algo
        super().__init__(f"Unsupported algorithm: {algo}")
