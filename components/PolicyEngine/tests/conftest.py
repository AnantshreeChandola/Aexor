"""
PolicyEngine test fixtures — mock adapters, sample data, configured services.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from components.PolicyEngine.adapters.cache import PolicyCacheAdapter
from components.PolicyEngine.adapters.db import PolicyDatabaseAdapter
from components.PolicyEngine.domain.models import PolicyDB, SpawnRequest
from components.PolicyEngine.service.policy_service import PolicyService
from shared.schemas.policy import PolicyRule

# ---------------------------------------------------------------------------
# Sample policies
# ---------------------------------------------------------------------------

DEFAULT_POLICY = PolicyRule(
    policy_id="default-reasoning",
    name="Default LLM Reasoning Policy",
    version=1,
    scope="role",
    allowed_tools=["*"],
    allowed_roles=["Fetcher", "Analyzer", "Reasoner"],
    max_spawned_steps=3,
    require_approval=False,
    data_access=["tier1", "tier2"],
    forbidden_actions=[],
    token_budget=8192,
)

RESTRICTIVE_POLICY = PolicyRule(
    policy_id="restrictive-booking",
    name="Restrictive Booking Policy",
    version=1,
    scope="step",
    allowed_tools=["google.calendar"],
    allowed_roles=["Booker"],
    max_spawned_steps=1,
    require_approval=True,
    data_access=["tier1"],
    forbidden_actions=["delete_all_events"],
    token_budget=4096,
)

SYSTEM_POLICY = PolicyRule(
    policy_id="system-default",
    name="System Default Policy",
    version=1,
    scope="system",
    allowed_tools=["*"],
    allowed_roles=[],
    max_spawned_steps=3,
    require_approval=False,
    data_access=["tier1"],
    forbidden_actions=[],
    token_budget=8192,
)


DEFAULT_POLICY_DB = PolicyDB(
    policy_id="default-reasoning",
    name="Default LLM Reasoning Policy",
    version=1,
    scope="role",
    allowed_tools=["*"],
    allowed_roles=["Fetcher", "Analyzer", "Reasoner"],
    max_spawned_steps=3,
    require_approval=False,
    data_access=["tier1", "tier2"],
    forbidden_actions=[],
    token_budget=8192,
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
)

# ---------------------------------------------------------------------------
# Sample spawn requests
# ---------------------------------------------------------------------------

SAMPLE_PLAN_ID = "01JBXYZ1234567890ABCDEFGHI"


def make_spawn_request(
    *,
    policy_ref: str | None = "default-reasoning",
    proposed_steps: list[dict] | None = None,
    current_step_count: int = 5,
    plan_plugins: list[str] | None = None,
) -> SpawnRequest:
    """Create a sample spawn request with sensible defaults."""
    if proposed_steps is None:
        proposed_steps = [
            {
                "step": 6,
                "mode": "interactive",
                "role": "Fetcher",
                "uses": "google.calendar",
                "call": "list_events",
                "args": {},
                "after": [5],
                "can_spawn": False,
            },
        ]
    return SpawnRequest(
        plan_id=SAMPLE_PLAN_ID,
        plan_revision=1,
        spawning_step=5,
        proposed_steps=proposed_steps,
        current_step_count=current_step_count,
        plan_plugins=plan_plugins or ["google.calendar", "system.echo"],
        policy_ref=policy_ref,
    )


# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_db_adapter():
    """Mock PolicyDatabaseAdapter."""
    adapter = AsyncMock(spec=PolicyDatabaseAdapter)
    adapter.store_policy = AsyncMock(return_value=True)
    adapter.get_policy = AsyncMock(return_value=DEFAULT_POLICY_DB)
    adapter.list_policies = AsyncMock(return_value=[DEFAULT_POLICY_DB])
    adapter.store_attestation = AsyncMock(return_value=True)
    adapter.get_attestations_for_plan = AsyncMock(return_value=[])
    adapter.health_check = AsyncMock(return_value=True)
    return adapter


@pytest.fixture()
def mock_cache_adapter():
    """Mock PolicyCacheAdapter that always misses."""
    adapter = AsyncMock(spec=PolicyCacheAdapter)
    adapter.get_policy = AsyncMock(return_value=None)
    adapter.set_policy = AsyncMock(return_value=None)
    adapter.invalidate = AsyncMock(return_value=None)
    return adapter


@pytest.fixture()
def mock_cache_hit_adapter():
    """Mock PolicyCacheAdapter with cache hit for default policy."""
    adapter = AsyncMock(spec=PolicyCacheAdapter)
    adapter.get_policy = AsyncMock(return_value=DEFAULT_POLICY)
    adapter.set_policy = AsyncMock(return_value=None)
    adapter.invalidate = AsyncMock(return_value=None)
    return adapter


@pytest.fixture()
def policy_service(mock_db_adapter, mock_cache_adapter):
    """Fully wired PolicyService with all mocks."""
    return PolicyService(
        db_adapter=mock_db_adapter,
        cache_adapter=mock_cache_adapter,
    )


@pytest.fixture()
def policy_service_with_cache(mock_db_adapter, mock_cache_hit_adapter):
    """PolicyService with cache hits enabled."""
    return PolicyService(
        db_adapter=mock_db_adapter,
        cache_adapter=mock_cache_hit_adapter,
    )
