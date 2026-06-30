"""
Encryption Service - AES-256-GCM Implementation

Provides encryption/decryption for sensitive data using AES-256-GCM.
Thread-safe and reusable across components.

Reference: SHARED_INFRASTRUCTURE.md §3.1
"""

import base64
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


class EncryptionService:
    """
    Shared encryption service using AES-256-GCM.

    Ciphertext Format: {iv}:{ciphertext} (base64-encoded)
    - IV: 12 bytes (96-bit, recommended for GCM)
    - Ciphertext: Encrypted data + authentication tag (tag included by AESGCM)

    Example:
        >>> service = EncryptionService()
        >>> encrypted = service.encrypt("sensitive data")
        >>> # encrypted: "SGVsbG8=:YWJjZGVm..."
        >>> decrypted = service.decrypt(encrypted)
        >>> # decrypted: "sensitive data"

    Thread-safe: Yes (AESGCM is stateless)
    """

    def __init__(self, key: bytes | None = None):
        """
        Initialize encryption service with AES-256 key.

        Args:
            key: 32-byte encryption key (256 bits)
                 If None, reads from ENCRYPTION_KEY environment variable

        Raises:
            ValueError: If key is missing or not 32 bytes
        """
        if key is None:
            # Load from environment
            key_b64 = os.getenv("ENCRYPTION_KEY")
            if not key_b64:
                raise ValueError(
                    "ENCRYPTION_KEY environment variable not set. "
                    "Generate with: python -c 'import secrets, base64; "
                    "print(base64.b64encode(secrets.token_bytes(32)).decode())'"
                )
            key = base64.b64decode(key_b64)

        if len(key) != 32:
            raise ValueError(f"Encryption key must be 32 bytes (256 bits), got {len(key)} bytes")

        self.cipher = AESGCM(key)
        logger.info("Encryption service initialized with AES-256-GCM")

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext and return base64-encoded ciphertext.

        Args:
            plaintext: String to encrypt

        Returns:
            Base64-encoded ciphertext in format: {iv}:{ciphertext}

        Example:
            >>> encrypted = service.encrypt("my secret")
            >>> print(encrypted)
            "SGVsbG8xMjM=:YWJjZGVmZ2hpams..."
        """
        # Generate random IV (12 bytes for GCM)
        iv = os.urandom(12)

        # Encrypt (GCM includes authentication tag in ciphertext)
        ciphertext = self.cipher.encrypt(iv, plaintext.encode("utf-8"), None)

        # Encode both IV and ciphertext to base64
        iv_b64 = base64.b64encode(iv).decode("utf-8")
        ciphertext_b64 = base64.b64encode(ciphertext).decode("utf-8")

        # Return in format {iv}:{ciphertext}
        return f"{iv_b64}:{ciphertext_b64}"

    def decrypt(self, encrypted: str) -> str:
        """
        Decrypt base64-encoded ciphertext.

        Args:
            encrypted: Ciphertext in format {iv}:{ciphertext} (base64-encoded)

        Returns:
            Decrypted plaintext string

        Raises:
            ValueError: If ciphertext format is invalid
            cryptography.exceptions.InvalidTag: If ciphertext is tampered

        Example:
            >>> decrypted = service.decrypt("SGVsbG8xMjM=:YWJjZGVm...")
            >>> print(decrypted)
            "my secret"
        """
        try:
            # Split IV and ciphertext
            iv_b64, ciphertext_b64 = encrypted.split(":", 1)
        except ValueError:
            raise ValueError(
                f"Invalid ciphertext format. Expected '{{iv}}:{{ciphertext}}', "
                f"got: {encrypted[:50]}..."
            )

        # Decode from base64
        iv = base64.b64decode(iv_b64)
        ciphertext = base64.b64decode(ciphertext_b64)

        # Decrypt (will raise InvalidTag if tampered)
        plaintext_bytes = self.cipher.decrypt(iv, ciphertext, None)
        return plaintext_bytes.decode("utf-8")


# Singleton instance (lazy-loaded)
_encryption_service = None


def get_encryption_service() -> EncryptionService:
    """
    Get singleton encryption service instance.

    Returns:
        EncryptionService: Shared instance

    Example:
        >>> from shared.security.encryption import get_encryption_service
        >>> service = get_encryption_service()
        >>> encrypted = service.encrypt("data")
    """
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service
