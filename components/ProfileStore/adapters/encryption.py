"""
Encryption Adapter for ProfileStore

Wrapper around shared encryption service for sensitive preferences.
Provides ProfileStore-specific encryption functionality.

Reference: LLD.md §6.2
"""

import logging
from typing import Any

from shared.security.encryption import EncryptionService, get_encryption_service

logger = logging.getLogger(__name__)


class EncryptionAdapter:
    """
    ProfileStore-specific encryption adapter.

    Wraps the shared encryption service with ProfileStore-specific functionality.
    Handles encryption/decryption of sensitive preference values.
    """

    def __init__(self, encryption_service: EncryptionService = None):
        """
        Initialize encryption adapter.

        Args:
            encryption_service: Encryption service instance
                               If None, uses shared singleton instance
        """
        if encryption_service is None:
            encryption_service = get_encryption_service()

        self.encryption_service = encryption_service
        logger.debug("Encryption adapter initialized")

    def encrypt_value(self, value: Any) -> str:
        """
        Encrypt a preference value.

        Args:
            value: Preference value to encrypt (will be JSON-serialized)

        Returns:
            Base64-encoded ciphertext
        """
        # Convert value to JSON string for encryption
        import json

        value_str = json.dumps(value, separators=(",", ":"))

        # Encrypt using shared service
        ciphertext = self.encryption_service.encrypt(value_str)

        logger.debug("Preference value encrypted")
        return ciphertext

    def decrypt_value(self, ciphertext: str) -> Any:
        """
        Decrypt a preference value.

        Args:
            ciphertext: Base64-encoded encrypted value

        Returns:
            Decrypted preference value (JSON-deserialized)
        """
        # Decrypt using shared service
        value_str = self.encryption_service.decrypt(ciphertext)

        # Parse JSON back to original value
        import json

        value = json.loads(value_str)

        logger.debug("Preference value decrypted")
        return value

    def is_encrypted(self, value_str: str) -> bool:
        """
        Check if a value appears to be encrypted.

        Heuristic check based on ciphertext format: {iv}:{ciphertext}

        Args:
            value_str: String to check

        Returns:
            True if value appears to be encrypted
        """
        try:
            # Encrypted values have format: base64:base64
            parts = value_str.split(":")
            if len(parts) != 2:
                return False

            # Check if both parts look like base64
            import base64

            for part in parts:
                base64.b64decode(part)

            return True

        except Exception:
            return False


# Singleton instance
_encryption_adapter = None


def get_encryption_adapter() -> EncryptionAdapter:
    """
    Get singleton encryption adapter instance.

    Returns:
        EncryptionAdapter: Shared instance
    """
    global _encryption_adapter
    if _encryption_adapter is None:
        _encryption_adapter = EncryptionAdapter()
    return _encryption_adapter
