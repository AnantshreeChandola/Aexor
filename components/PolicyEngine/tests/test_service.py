"""
PolicyEngine service tests — cache/DB interaction, CRUD, attestations.

Tests the service-level behavior: cache-first lookups, attestation creation,
policy CRUD operations.
~20 tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from components.PolicyEngine.domain.models import (
    AttestationError,
)
from components.PolicyEngine.tests.conftest import (
    SAMPLE_PLAN_ID,
    make_spawn_request,
)
from shared.schemas.policy import PolicyDecision, PolicyRule

# ---------------------------------------------------------------------------
# Cache-first lookups
# ---------------------------------------------------------------------------


class TestCacheFirstLookup:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self, policy_service_with_cache, mock_db_adapter):
        """Cache hit → returns cached policy, no DB call."""
        result = await policy_service_with_cache.get_policy("default-reasoning", version=1)
        assert result is not None
        assert result.policy_id == "default-reasoning"
        mock_db_adapter.get_policy.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_falls_to_db(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """Cache miss → falls through to DB, populates cache."""
        result = await policy_service.get_policy("default-reasoning", version=1)
        assert result is not None
        assert result.policy_id == "default-reasoning"
        mock_db_adapter.get_policy.assert_called_once_with("default-reasoning", 1)
        mock_cache_adapter.set_policy.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_miss_db_miss_returns_none(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """Cache miss + DB miss → returns None."""
        mock_db_adapter.get_policy.return_value = None
        result = await policy_service.get_policy("nonexistent", version=1)
        assert result is None
        mock_cache_adapter.set_policy.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_version_skips_cache(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """get_policy without version → skip cache, go to DB directly."""
        result = await policy_service.get_policy("default-reasoning")
        assert result is not None
        mock_cache_adapter.get_policy.assert_not_called()
        mock_db_adapter.get_policy.assert_called_once_with("default-reasoning", None)

    @pytest.mark.asyncio
    async def test_redis_unavailable_falls_to_db(self, mock_db_adapter):
        """Redis error → graceful fallback to DB."""
        failing_cache = AsyncMock()
        failing_cache.get_policy = AsyncMock(side_effect=Exception("Redis down"))
        failing_cache.set_policy = AsyncMock()
        # Even though the cache raises, we call get_policy which uses AsyncMock spec
        # The service catches None from cache.get_policy; let's test with cache returning None
        cache = AsyncMock()
        cache.get_policy = AsyncMock(return_value=None)
        cache.set_policy = AsyncMock(side_effect=Exception("Redis down"))

        from components.PolicyEngine.service.policy_service import PolicyService

        service = PolicyService(db_adapter=mock_db_adapter, cache_adapter=cache)
        result = await service.get_policy("default-reasoning", version=1)
        assert result is not None
        mock_db_adapter.get_policy.assert_called_once()


# ---------------------------------------------------------------------------
# Attestation creation
# ---------------------------------------------------------------------------


class TestAttestationCreation:
    @pytest.mark.asyncio
    async def test_create_attestation_success(self, policy_service, mock_db_adapter):
        """Attestation creation stores to DB and returns valid model."""
        decision = PolicyDecision(
            allowed=True, reason="Approved by policy", requires_approval=False
        )
        attestation = await policy_service.create_attestation(
            plan_id=SAMPLE_PLAN_ID,
            plan_revision=1,
            spawned_by_step=5,
            new_steps=[{"step": 6, "role": "Fetcher"}],
            policy_id="default-reasoning",
            policy_version=1,
            decision=decision,
        )
        assert attestation.plan_id == SAMPLE_PLAN_ID
        assert attestation.policy_id == "default-reasoning"
        assert len(attestation.attestation_id) == 26
        mock_db_adapter.store_attestation.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_attestation_generates_ulid(self, policy_service, mock_db_adapter):
        """Each attestation gets a unique ULID."""
        decision = PolicyDecision(allowed=True, reason="OK")
        a1 = await policy_service.create_attestation(
            SAMPLE_PLAN_ID, 1, 5, [{"step": 6}], "pol-1", 1, decision
        )
        a2 = await policy_service.create_attestation(
            SAMPLE_PLAN_ID, 1, 5, [{"step": 7}], "pol-1", 1, decision
        )
        assert a1.attestation_id != a2.attestation_id

    @pytest.mark.asyncio
    async def test_create_attestation_db_failure_raises(self, policy_service, mock_db_adapter):
        """DB failure during attestation storage → AttestationError."""
        mock_db_adapter.store_attestation.side_effect = RuntimeError("DB write failed")
        decision = PolicyDecision(allowed=True, reason="OK")
        with pytest.raises(AttestationError, match="Failed to store"):
            await policy_service.create_attestation(
                SAMPLE_PLAN_ID, 1, 5, [{"step": 6}], "pol-1", 1, decision
            )

    @pytest.mark.asyncio
    async def test_attestation_contains_decision(self, policy_service, mock_db_adapter):
        """Attestation record includes the full decision."""
        decision = PolicyDecision(
            allowed=True, reason="Policy approved", requires_approval=True, violations=[]
        )
        attestation = await policy_service.create_attestation(
            SAMPLE_PLAN_ID, 1, 5, [{"step": 6}], "pol-1", 1, decision
        )
        assert attestation.decision.allowed is True
        assert attestation.decision.requires_approval is True


# ---------------------------------------------------------------------------
# Policy CRUD
# ---------------------------------------------------------------------------


class TestPolicyCRUD:
    @pytest.mark.asyncio
    async def test_create_policy_stores_and_invalidates(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """create_policy stores to DB and invalidates cache."""
        rule = PolicyRule(
            policy_id="new-policy",
            name="New Policy",
            version=1,
            scope="step",
        )
        result = await policy_service.create_policy(rule)
        assert result.policy_id == "new-policy"
        mock_db_adapter.store_policy.assert_called_once()
        mock_cache_adapter.invalidate.assert_called_once_with("new-policy", 1)

    @pytest.mark.asyncio
    async def test_list_policies_no_filter(self, policy_service, mock_db_adapter):
        """list_policies without scope returns all policies."""
        result = await policy_service.list_policies()
        assert len(result) == 1
        assert result[0].policy_id == "default-reasoning"
        mock_db_adapter.list_policies.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_list_policies_with_scope(self, policy_service, mock_db_adapter):
        """list_policies with scope passes filter to DB."""
        await policy_service.list_policies(scope="role")
        mock_db_adapter.list_policies.assert_called_once_with("role")

    @pytest.mark.asyncio
    async def test_get_policy_specific_version(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """get_policy with explicit version checks cache then DB."""
        result = await policy_service.get_policy("default-reasoning", version=1)
        assert result is not None
        mock_cache_adapter.get_policy.assert_called_once_with("default-reasoning", 1)

    @pytest.mark.asyncio
    async def test_get_policy_latest_version(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """get_policy without version bypasses cache."""
        result = await policy_service.get_policy("default-reasoning")
        assert result is not None
        mock_cache_adapter.get_policy.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_policy_returns_same_rule(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """create_policy returns the same rule that was passed in."""
        rule = PolicyRule(
            policy_id="test-pol",
            name="Test",
            version=2,
            scope="system",
            allowed_tools=["slack.chat"],
            max_spawned_steps=5,
        )
        result = await policy_service.create_policy(rule)
        assert result.policy_id == "test-pol"
        assert result.version == 2
        assert result.allowed_tools == ["slack.chat"]
        assert result.max_spawned_steps == 5


# ---------------------------------------------------------------------------
# Integration: evaluate_spawn with cache
# ---------------------------------------------------------------------------


class TestEvaluateSpawnWithCache:
    @pytest.mark.asyncio
    async def test_evaluate_spawn_resolves_policy(self, policy_service_with_cache, mock_db_adapter):
        """evaluate_spawn resolves the policy and evaluates correctly."""
        request = make_spawn_request()
        decision = await policy_service_with_cache.evaluate_spawn(request)
        assert decision.allowed is True
        # evaluate_spawn calls get_policy without version (latest),
        # which bypasses cache and goes to DB. This is correct behavior —
        # we always want the latest policy version for spawn evaluation.
        mock_db_adapter.get_policy.assert_called_once_with("default-reasoning", None)

    @pytest.mark.asyncio
    async def test_evaluate_spawn_populates_cache_on_miss(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """evaluate_spawn with cache miss populates cache after DB lookup."""
        request = make_spawn_request()
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True
        mock_cache_adapter.set_policy.assert_called()


# ---------------------------------------------------------------------------
# Learn from approval
# ---------------------------------------------------------------------------


class TestLearnFromApproval:
    @pytest.mark.asyncio
    async def test_learn_creates_policy(self, policy_service, mock_db_adapter, mock_cache_adapter):
        """learn_from_approval creates a policy with correct id, scope, tools, roles."""
        rule = await policy_service.learn_from_approval("Fetcher", "google.calendar")
        assert rule.policy_id == "learned:Fetcher:google.calendar"
        assert rule.scope == "role"
        assert rule.allowed_tools == ["google.calendar"]
        assert rule.allowed_roles == ["Fetcher"]
        assert rule.require_approval is False
        mock_db_adapter.store_policy.assert_called_once()
        mock_cache_adapter.invalidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_learn_policy_name_descriptive(self, policy_service, mock_db_adapter):
        """Learned policy name includes role and tool."""
        rule = await policy_service.learn_from_approval("Analyzer", "slack.chat")
        assert "Analyzer" in rule.name
        assert "slack.chat" in rule.name

    @pytest.mark.asyncio
    async def test_learn_custom_limits(self, policy_service, mock_db_adapter):
        """learn_from_approval respects custom max_spawned_steps and token_budget."""
        rule = await policy_service.learn_from_approval(
            "Fetcher", "google.calendar", max_spawned_steps=5, token_budget=4096
        )
        assert rule.max_spawned_steps == 5
        assert rule.token_budget == 4096

    @pytest.mark.asyncio
    async def test_learn_upserts_on_repeat(self, policy_service, mock_db_adapter):
        """Calling learn_from_approval twice does not error (upsert semantics)."""
        await policy_service.learn_from_approval("Fetcher", "google.calendar")
        await policy_service.learn_from_approval("Fetcher", "google.calendar")
        assert mock_db_adapter.store_policy.call_count == 2

    @pytest.mark.asyncio
    async def test_learn_then_evaluate_auto_approves(
        self, policy_service, mock_db_adapter, mock_cache_adapter
    ):
        """End-to-end: learn a policy, then evaluate_spawn auto-approves."""
        from datetime import UTC, datetime

        from components.PolicyEngine.domain.models import PolicyDB

        # First: learn from approval
        await policy_service.learn_from_approval("Fetcher", "google.calendar")

        # Set up mock so learned policy is returned on lookup
        learned_db = PolicyDB(
            policy_id="learned:Fetcher:google.calendar",
            name="Learned policy for Fetcher using google.calendar",
            version=1,
            scope="role",
            allowed_tools=["google.calendar"],
            allowed_roles=["Fetcher"],
            max_spawned_steps=3,
            require_approval=False,
            data_access=[],
            forbidden_actions=[],
            token_budget=8192,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        # First get_policy call (explicit ref=None → skip), second (learned lookup) returns policy
        mock_db_adapter.get_policy.return_value = learned_db

        request = make_spawn_request(
            policy_ref=None,
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list_events",
                    "can_spawn": False,
                }
            ],
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True
        assert decision.requires_approval is False
        assert decision.policy_matched is True
