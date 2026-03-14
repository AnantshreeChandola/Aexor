"""
Tests for Encryption Service (AES-256-GCM)

Tests encryption/decryption roundtrip, error handling, and edge cases.
"""

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from shared.security.encryption import EncryptionService, get_encryption_service


@pytest.fixture
def encryption_service():
    """Create encryption service with test key."""
    # Generate test key (32 bytes for AES-256)
    test_key = os.urandom(32)
    return EncryptionService(key=test_key)


def test_encrypt_decrypt_roundtrip(encryption_service):
    """Test basic encrypt/decrypt roundtrip."""
    plaintext = "This is sensitive data"
    encrypted = encryption_service.encrypt(plaintext)
    decrypted = encryption_service.decrypt(encrypted)

    assert decrypted == plaintext


def test_encrypt_decrypt_unicode(encryption_service):
    """Test encryption with Unicode characters."""
    plaintext = "Hello 世界 🌍 Encryption!"
    encrypted = encryption_service.encrypt(plaintext)
    decrypted = encryption_service.decrypt(encrypted)

    assert decrypted == plaintext


def test_encrypt_decrypt_empty_string(encryption_service):
    """Test encryption of empty string."""
    plaintext = ""
    encrypted = encryption_service.encrypt(plaintext)
    decrypted = encryption_service.decrypt(encrypted)

    assert decrypted == plaintext


def test_encrypt_produces_different_ciphertexts(encryption_service):
    """Test that encrypting same plaintext twice produces different ciphertexts (due to random IV)."""
    plaintext = "Same data"
    encrypted1 = encryption_service.encrypt(plaintext)
    encrypted2 = encryption_service.encrypt(plaintext)

    # Ciphertexts should be different (different IVs)
    assert encrypted1 != encrypted2

    # But both should decrypt to same plaintext
    assert encryption_service.decrypt(encrypted1) == plaintext
    assert encryption_service.decrypt(encrypted2) == plaintext


def test_encrypted_format(encryption_service):
    """Test that encrypted data has correct format {iv}:{ciphertext}."""
    plaintext = "Test data"
    encrypted = encryption_service.encrypt(plaintext)

    # Should contain exactly one colon separator
    assert encrypted.count(":") == 1

    # Both parts should be valid base64
    iv_b64, ciphertext_b64 = encrypted.split(":")
    try:
        base64.b64decode(iv_b64)
        base64.b64decode(ciphertext_b64)
    except Exception:
        pytest.fail("Encrypted data is not valid base64")


def test_decrypt_invalid_format():
    """Test that decrypting invalid format raises ValueError."""
    service = EncryptionService(key=os.urandom(32))

    with pytest.raises(ValueError, match="Invalid ciphertext format"):
        service.decrypt("no-colon-separator")


def test_decrypt_tampered_ciphertext(encryption_service):
    """Test that decrypting tampered ciphertext raises InvalidTag."""
    plaintext = "Original data"
    encrypted = encryption_service.encrypt(plaintext)

    # Tamper with ciphertext (change last character)
    iv, ciphertext = encrypted.split(":")
    tampered = f"{iv}:{ciphertext[:-1]}X"

    with pytest.raises(InvalidTag):
        encryption_service.decrypt(tampered)


def test_encrypt_long_text(encryption_service):
    """Test encryption of long text."""
    plaintext = "A" * 10000  # 10KB of data
    encrypted = encryption_service.encrypt(plaintext)
    decrypted = encryption_service.decrypt(encrypted)

    assert decrypted == plaintext


def test_encryption_service_init_with_env_var(monkeypatch):
    """Test initialization from ENCRYPTION_KEY environment variable."""
    test_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("ENCRYPTION_KEY", test_key)

    service = EncryptionService()
    plaintext = "Test with env key"
    encrypted = service.encrypt(plaintext)
    decrypted = service.decrypt(encrypted)

    assert decrypted == plaintext


def test_encryption_service_init_missing_env_var(monkeypatch):
    """Test that initialization fails without ENCRYPTION_KEY env var."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValueError, match="ENCRYPTION_KEY environment variable not set"):
        EncryptionService()


def test_encryption_service_init_wrong_key_length():
    """Test that initialization fails with wrong key length."""
    wrong_key = os.urandom(16)  # 16 bytes instead of 32

    with pytest.raises(ValueError, match="must be 32 bytes"):
        EncryptionService(key=wrong_key)


def test_get_encryption_service_singleton(monkeypatch):
    """Test that get_encryption_service returns singleton instance."""
    test_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("ENCRYPTION_KEY", test_key)

    service1 = get_encryption_service()
    service2 = get_encryption_service()

    assert service1 is service2  # Same instance


def test_different_keys_produce_different_results():
    """Test that different keys produce different ciphertexts."""
    plaintext = "Same data"
    service1 = EncryptionService(key=os.urandom(32))
    service2 = EncryptionService(key=os.urandom(32))

    encrypted1 = service1.encrypt(plaintext)
    encrypted2 = service2.encrypt(plaintext)

    # Should not be able to decrypt with wrong key
    assert encrypted1 != encrypted2
    with pytest.raises(InvalidTag):
        service1.decrypt(encrypted2)
