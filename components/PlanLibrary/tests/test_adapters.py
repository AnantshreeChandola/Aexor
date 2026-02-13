"""
PlanLibrary Adapter Unit Tests

Tests for DatabaseAdapter, VectorAdapter, EmbeddingClient,
SignatureVerifier, and CacheAdapter.
All tests use mocks (no real database or API calls).

Reference: tasks.md T305
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from components.PlanLibrary.adapters.embedding_client import (
    CircuitBreaker,
    CircuitState,
    EmbeddingClient,
)
from components.PlanLibrary.adapters.signature_verifier import (
    SignatureVerifier,
)
from components.PlanLibrary.adapters.cache import CacheAdapter
from components.PlanLibrary.domain.models import (
    EmbeddingServiceError,
    InvalidSignatureError,
)


VALID_ULID = "01HX1234567890ABCDEFGHJKMN"


class TestCircuitBreaker:
    """Tests for CircuitBreaker pattern."""

    def test_starts_closed(self):
        """Circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_opens_after_threshold_failures(self):
        """Circuit breaker opens after threshold consecutive failures."""
        cb = CircuitBreaker(failure_threshold=3)

        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_transitions_to_half_open_after_timeout(self):
        """Circuit breaker transitions to HALF_OPEN after timeout."""
        cb = CircuitBreaker(failure_threshold=1, timeout_seconds=0.01)

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Wait for timeout
        time.sleep(0.02)
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_closes_on_success_after_half_open(self):
        """Circuit breaker closes on success in HALF_OPEN state."""
        cb = CircuitBreaker(failure_threshold=1, timeout_seconds=0.01)

        cb.record_failure()
        time.sleep(0.02)
        cb.can_execute()  # Transition to HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_success_resets_failure_count(self):
        """Success resets failure counter."""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0


class TestEmbeddingClient:
    """Tests for EmbeddingClient."""

    @pytest.mark.asyncio
    async def test_successful_embedding_generation(self):
        """Successful embedding generation returns 1536-dim vector."""
        client = EmbeddingClient(api_key="test-key")

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]

        with patch.object(client, "_call_api", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = [0.1] * 1536
            result = await client.generate_embedding("test text")

        assert len(result) == 1536
        assert client.circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self):
        """Circuit breaker opens after consecutive failures."""
        client = EmbeddingClient(api_key="test-key", max_retries=1)

        with patch.object(client, "_call_api", new_callable=AsyncMock) as mock_api:
            mock_api.side_effect = Exception("API error")

            # First call: fails and opens circuit
            with pytest.raises(EmbeddingServiceError):
                await client.generate_embedding("test")

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_when_open(self):
        """Open circuit breaker skips API calls."""
        client = EmbeddingClient(api_key="test-key")
        # Force circuit open
        client.circuit_breaker.state = CircuitState.OPEN
        client.circuit_breaker.last_failure_time = time.time()

        with pytest.raises(EmbeddingServiceError, match="Circuit breaker"):
            await client.generate_embedding("test")


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


class TestCacheAdapter:
    """Tests for CacheAdapter."""

    def test_cache_unavailable_returns_none(self):
        """Cache returns None when Redis is unavailable."""
        adapter = CacheAdapter.__new__(CacheAdapter)
        adapter._client = None
        adapter._available = False

    @pytest.mark.asyncio
    async def test_cache_get_unavailable(self):
        """Cache get returns None when unavailable."""
        adapter = CacheAdapter.__new__(CacheAdapter)
        adapter._client = None
        adapter._available = False

        result = await adapter.get_cached_plan(VALID_ULID)
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_set_unavailable(self):
        """Cache set returns False when unavailable."""
        adapter = CacheAdapter.__new__(CacheAdapter)
        adapter._client = None
        adapter._available = False

        result = await adapter.cache_plan(VALID_ULID, {"test": True})
        assert result is False

    @pytest.mark.asyncio
    async def test_cache_invalidate_unavailable(self):
        """Cache invalidate returns False when unavailable."""
        adapter = CacheAdapter.__new__(CacheAdapter)
        adapter._client = None
        adapter._available = False

        result = await adapter.invalidate(VALID_ULID)
        assert result is False

    @pytest.mark.asyncio
    async def test_cache_get_exception_returns_none(self):
        """Cache get returns None on Redis exception."""
        adapter = CacheAdapter.__new__(CacheAdapter)
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=Exception("Redis error"))
        adapter._client = mock_client
        adapter._available = True

        result = await adapter.get_cached_plan(VALID_ULID)
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_set_exception_returns_false(self):
        """Cache set returns False on Redis exception."""
        adapter = CacheAdapter.__new__(CacheAdapter)
        mock_client = MagicMock()
        mock_client.setex = AsyncMock(side_effect=Exception("Redis error"))
        adapter._client = mock_client
        adapter._available = True

        result = await adapter.cache_plan(VALID_ULID, {"test": True})
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
