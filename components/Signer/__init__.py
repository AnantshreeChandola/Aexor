"""
Signer Component

Ed25519 plan signing and verification for plan integrity,
enterprise audit, and replay protection.
"""

from components.Signer.domain.models import (
    InvalidSignatureError,
    PlanSignature,
    SignerError,
    SigningKeyNotConfiguredError,
    UnsupportedAlgorithmError,
)
from components.Signer.service.signer_service import (
    SignerService,
    create_signer_service,
)

__all__ = [
    "InvalidSignatureError",
    "PlanSignature",
    "SignerError",
    "SignerService",
    "SigningKeyNotConfiguredError",
    "UnsupportedAlgorithmError",
    "create_signer_service",
]
