"""
Tests for Auth Routes — /auth/token and /auth/register

Tests JWT login and registration endpoints with mocked database.
"""

import os

os.environ["JWT_SECRET"] = "test-secret-key-for-unit-tests-only"

from datetime import UTC, datetime
from uuid import uuid4

from jose import jwt

from shared.api.auth_routes import JWTService

TEST_SECRET = "test-secret-key-for-unit-tests-only"
TEST_ALGORITHM = "HS256"


# --- JWTService Unit Tests ---


class TestJWTService:
    """Tests for JWTService password and token operations."""

    def test_hash_and_verify_password(self):
        """Password hash roundtrip works correctly."""
        plain = "my-secure-password-123"
        hashed = JWTService.hash_password(plain)

        assert hashed != plain
        assert JWTService.verify_password(plain, hashed) is True

    def test_wrong_password_fails_verify(self):
        """Wrong password fails verification."""
        hashed = JWTService.hash_password("correct-password")
        assert JWTService.verify_password("wrong-password", hashed) is False

    def test_create_access_token_has_correct_claims(self):
        """Token contains all required claims with correct values."""
        user_id = uuid4()
        email = "test@example.com"
        context_tier = 3

        token = JWTService.create_access_token(
            user_id=user_id,
            email=email,
            context_tier=context_tier,
        )

        payload = jwt.decode(token, TEST_SECRET, algorithms=[TEST_ALGORITHM])

        assert payload["sub"] == str(user_id)
        assert payload["email"] == email
        assert payload["context_tier"] == context_tier
        assert "iat" in payload
        assert "exp" in payload

    def test_token_expiry_is_in_future(self):
        """Token exp claim is in the future."""
        token = JWTService.create_access_token(
            user_id=uuid4(),
            email="test@example.com",
            context_tier=1,
        )

        payload = jwt.decode(token, TEST_SECRET, algorithms=[TEST_ALGORITHM])
        now = int(datetime.now(tz=UTC).timestamp())

        assert payload["exp"] > now

    def test_token_iat_is_current(self):
        """Token iat claim is approximately now."""
        before = int(datetime.now(tz=UTC).timestamp())
        token = JWTService.create_access_token(
            user_id=uuid4(),
            email="test@example.com",
            context_tier=1,
        )
        after = int(datetime.now(tz=UTC).timestamp())

        payload = jwt.decode(token, TEST_SECRET, algorithms=[TEST_ALGORITHM])

        assert before <= payload["iat"] <= after

    def test_different_passwords_produce_different_hashes(self):
        """Different passwords produce different bcrypt hashes."""
        hash1 = JWTService.hash_password("password-one")
        hash2 = JWTService.hash_password("password-two")

        assert hash1 != hash2

    def test_same_password_produces_different_hashes(self):
        """Same password produces different hashes due to salt."""
        hash1 = JWTService.hash_password("same-password")
        hash2 = JWTService.hash_password("same-password")

        # bcrypt uses random salt, so hashes differ
        assert hash1 != hash2
        # But both verify correctly
        assert JWTService.verify_password("same-password", hash1) is True
        assert JWTService.verify_password("same-password", hash2) is True
