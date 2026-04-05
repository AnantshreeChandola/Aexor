"""
ApprovalGate test fixtures -- mock adapters, sample data, configured services.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from components.ApprovalGate.adapters.gate_store import GateStore
from components.ApprovalGate.adapters.token_issuer import TokenIssuer
from components.ApprovalGate.domain.models import ApprovalRequest
from components.ApprovalGate.service.approval_service import ApprovalService

SAMPLE_PLAN_ID = "01JXYZ1234567890ABCDEFGHIJ"
SAMPLE_USER_ID = "user-uuid-12345678-abcd-efgh"
SAMPLE_GATE_IDS = ["gate-A", "gate-B", "gate-C"]
SAMPLE_SCOPES = ["calendar.write"]
JWT_SECRET = "test-approval-gate-secret-key-minimum-32-chars"
TOKEN_TTL_S = 900


# ---------------------------------------------------------------------------
# Basic fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def jwt_secret() -> str:
    """Test JWT secret string."""
    return JWT_SECRET


@pytest.fixture()
def token_ttl_s() -> int:
    """Default token TTL: 900 (15 minutes)."""
    return TOKEN_TTL_S


@pytest.fixture()
def sample_plan_id() -> str:
    """Hardcoded 26-char ULID string."""
    return SAMPLE_PLAN_ID


@pytest.fixture()
def sample_user_id() -> str:
    """Test UUID string."""
    return SAMPLE_USER_ID


@pytest.fixture()
def sample_gate_ids() -> list[str]:
    """Multi-gate testing gate IDs."""
    return list(SAMPLE_GATE_IDS)


@pytest.fixture()
def sample_scopes() -> list[str]:
    """Sample scopes list."""
    return list(SAMPLE_SCOPES)


# ---------------------------------------------------------------------------
# ApprovalRequest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_approval_request(
    sample_plan_id: str,
    sample_user_id: str,
    sample_scopes: list[str],
) -> ApprovalRequest:
    """ApprovalRequest with default values."""
    return ApprovalRequest(
        plan_id=sample_plan_id,
        user_id=sample_user_id,
        scopes=sample_scopes,
    )


@pytest.fixture()
def sample_approval_request_multi_gate(
    sample_plan_id: str,
    sample_user_id: str,
    sample_scopes: list[str],
):
    """Factory function that creates ApprovalRequest for a given gate_id."""

    def _make(gate_id: str) -> ApprovalRequest:
        return ApprovalRequest(
            plan_id=sample_plan_id,
            user_id=sample_user_id,
            gate_id=gate_id,
            scopes=sample_scopes,
        )

    return _make


@pytest.fixture()
def sample_approval_request_spawned(
    sample_plan_id: str,
    sample_user_id: str,
) -> ApprovalRequest:
    """ApprovalRequest with policy_matched=False for spawned step gate."""
    return ApprovalRequest(
        plan_id=sample_plan_id,
        user_id=sample_user_id,
        gate_id="gate-spawn8",
        scopes=["calendar.write"],
        policy_matched=False,
        role="Fetcher",
        tool="google.calendar",
    )


# ---------------------------------------------------------------------------
# Adapter fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def token_issuer(jwt_secret: str) -> TokenIssuer:
    """TokenIssuer instance with test secret."""
    return TokenIssuer(jwt_secret, algorithm="HS256")


class FakeRedisClient:
    """In-memory fake async Redis client for testing.

    Supports set(), get(), delete(), keys() with TTL tracking.
    Supports set(..., nx=True) for SET NX semantics.
    Supports set(..., ex=...) for TTL.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self._store:
            return None  # Key already exists, SET NX fails
        self._store[key] = value
        if ex is not None:
            self._ttls[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttls.pop(key, None)

    async def keys(self, pattern: str) -> list[str]:
        """Simple prefix-based key matching (supports trailing *)."""
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in self._store if k.startswith(prefix)]
        return [k for k in self._store if k == pattern]

    def get_ttl(self, key: str) -> int | None:
        """Test helper: retrieve the TTL set for a key."""
        return self._ttls.get(key)


@pytest.fixture()
def mock_redis_client() -> FakeRedisClient:
    """Fake async Redis client with in-memory dict storage."""
    return FakeRedisClient()


@pytest.fixture()
def gate_store(mock_redis_client: FakeRedisClient, token_ttl_s: int) -> GateStore:
    """GateStore instance with mock Redis."""
    return GateStore(mock_redis_client, default_ttl_s=token_ttl_s)


@pytest.fixture()
def gate_store_no_redis(token_ttl_s: int) -> GateStore:
    """GateStore instance with redis_client=None."""
    return GateStore(None, default_ttl_s=token_ttl_s)


# ---------------------------------------------------------------------------
# Mock service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_preview_service() -> AsyncMock:
    """Mock for PreviewService.get_preview_state().

    Returns configurable dict of step results. Supports raising exceptions.
    """
    service = AsyncMock()
    service.get_preview_state = AsyncMock(
        return_value={
            1: {"step": 1, "status": "completed", "result": {"events": []}},
            2: {"step": 2, "status": "deferred", "reason": "gated"},
        }
    )
    return service


@pytest.fixture()
def mock_policy_service() -> AsyncMock:
    """Mock for PolicyService.learn_from_approval().

    Returns a PolicyRule-like dict. Supports raising exceptions.
    """
    service = AsyncMock()
    service.learn_from_approval = AsyncMock(
        return_value={"policy_id": "learned:Fetcher:google.calendar"}
    )
    return service


# ---------------------------------------------------------------------------
# Service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def approval_service(
    token_issuer: TokenIssuer,
    gate_store: GateStore,
    mock_preview_service: AsyncMock,
    mock_policy_service: AsyncMock,
    token_ttl_s: int,
) -> ApprovalService:
    """Fully wired ApprovalService with all mock dependencies."""
    return ApprovalService(
        token_issuer=token_issuer,
        gate_store=gate_store,
        preview_service=mock_preview_service,
        policy_service=mock_policy_service,
        token_ttl_s=token_ttl_s,
    )


@pytest.fixture()
def approval_service_minimal(jwt_secret: str, token_ttl_s: int) -> ApprovalService:
    """ApprovalService with preview_service=None, policy_service=None, redis_client=None.

    Tests graceful degradation.
    """
    token_issuer = TokenIssuer(jwt_secret, algorithm="HS256")
    gate_store = GateStore(None, default_ttl_s=token_ttl_s)
    return ApprovalService(
        token_issuer=token_issuer,
        gate_store=gate_store,
        preview_service=None,
        policy_service=None,
        token_ttl_s=token_ttl_s,
    )
