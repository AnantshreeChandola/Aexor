"""
PolicyEngine unit tests — evaluation logic.

Tests the core evaluate_spawn() method against policy rules.
~25 tests covering deny-by-default, all constraint checks, and edge cases.
"""

from __future__ import annotations

import pytest

from components.PolicyEngine.tests.conftest import (
    DEFAULT_POLICY_DB,
    make_spawn_request,
)

# ---------------------------------------------------------------------------
# Deny-by-default
# ---------------------------------------------------------------------------


class TestDenyByDefault:
    """No matching policy → denied."""

    @pytest.mark.asyncio
    async def test_no_policy_ref_denies(self, policy_service, mock_db_adapter):
        """No policy_ref at all → deny-by-default."""
        request = make_spawn_request(policy_ref=None)
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "deny-by-default" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_policy_ref_not_found_denies(self, policy_service, mock_db_adapter):
        """policy_ref points to non-existent policy → deny-by-default."""
        mock_db_adapter.get_policy.return_value = None
        request = make_spawn_request(policy_ref="nonexistent-policy")
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "no matching policy" in decision.reason.lower()


# ---------------------------------------------------------------------------
# Allowed tools check
# ---------------------------------------------------------------------------


class TestAllowedTools:
    @pytest.mark.asyncio
    async def test_wildcard_allows_any_tool(self, policy_service):
        """Policy with allowed_tools=["*"] allows any tool."""
        request = make_spawn_request(
            plan_plugins=["any.tool"],  # tool must also be in plan plugins
            proposed_steps=[
                {"step": 6, "role": "Fetcher", "uses": "any.tool", "call": "op", "can_spawn": False}
            ],
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_tool_not_in_allowed_list_denies(self, policy_service, mock_db_adapter):
        """Tool not in allowed_tools (non-wildcard) → denied."""
        restrictive_db = DEFAULT_POLICY_DB.model_copy(update={"allowed_tools": ["google.calendar"]})
        mock_db_adapter.get_policy.return_value = restrictive_db
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "slack.chat",
                    "call": "send",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "not in allowed_tools" in decision.reason

    @pytest.mark.asyncio
    async def test_tool_in_allowed_list_permits(self, policy_service, mock_db_adapter):
        """Tool in explicit allowed_tools list → allowed."""
        restrictive_db = DEFAULT_POLICY_DB.model_copy(update={"allowed_tools": ["google.calendar"]})
        mock_db_adapter.get_policy.return_value = restrictive_db
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Allowed roles check
# ---------------------------------------------------------------------------


class TestAllowedRoles:
    @pytest.mark.asyncio
    async def test_role_not_in_allowed_roles_denies(self, policy_service):
        """Role not in allowed_roles → denied."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Notifier",
                    "uses": "google.calendar",
                    "call": "notify",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "not in allowed_roles" in decision.reason

    @pytest.mark.asyncio
    async def test_role_in_allowed_roles_permits(self, policy_service):
        """Role in allowed_roles → allowed."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_empty_allowed_roles_permits_any(self, policy_service, mock_db_adapter):
        """Empty allowed_roles → all roles permitted."""
        db_model = DEFAULT_POLICY_DB.model_copy(update={"allowed_roles": []})
        mock_db_adapter.get_policy.return_value = db_model
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "AnyRole",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Forbidden actions check
# ---------------------------------------------------------------------------


class TestForbiddenActions:
    @pytest.mark.asyncio
    async def test_forbidden_action_denies(self, policy_service, mock_db_adapter):
        """Call in forbidden_actions → denied."""
        db_model = DEFAULT_POLICY_DB.model_copy(update={"forbidden_actions": ["delete_all_events"]})
        mock_db_adapter.get_policy.return_value = db_model
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "delete_all_events",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "forbidden_actions" in decision.reason

    @pytest.mark.asyncio
    async def test_non_forbidden_action_permits(self, policy_service):
        """Call not in forbidden_actions → allowed."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list_events",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Max spawned steps check
# ---------------------------------------------------------------------------


class TestMaxSpawnedSteps:
    @pytest.mark.asyncio
    async def test_exceeds_max_spawned_steps_denies(self, policy_service):
        """Proposed count > max_spawned_steps → denied."""
        steps = [
            {
                "step": i,
                "role": "Fetcher",
                "uses": "google.calendar",
                "call": "list",
                "can_spawn": False,
            }
            for i in range(6, 10)  # 4 steps, policy allows 3
        ]
        request = make_spawn_request(proposed_steps=steps)
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "max_spawned_steps" in decision.reason

    @pytest.mark.asyncio
    async def test_within_max_spawned_steps_permits(self, policy_service):
        """Proposed count ≤ max_spawned_steps → allowed."""
        steps = [
            {
                "step": i,
                "role": "Fetcher",
                "uses": "google.calendar",
                "call": "list",
                "can_spawn": False,
            }
            for i in range(6, 9)  # 3 steps, policy allows 3
        ]
        request = make_spawn_request(proposed_steps=steps)
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Total plan size check
# ---------------------------------------------------------------------------


class TestTotalPlanSize:
    @pytest.mark.asyncio
    async def test_total_exceeds_100_denies(self, policy_service, mock_db_adapter):
        """current_step_count + proposed > 100 → denied."""
        db_model = DEFAULT_POLICY_DB.model_copy(update={"max_spawned_steps": 10})
        mock_db_adapter.get_policy.return_value = db_model
        request = make_spawn_request(
            current_step_count=99,
            proposed_steps=[
                {
                    "step": 100,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                },
                {
                    "step": 101,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                },
            ],
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "100" in decision.reason

    @pytest.mark.asyncio
    async def test_total_at_limit_permits(self, policy_service):
        """current + proposed = 100 → allowed."""
        steps = [
            {
                "step": i,
                "role": "Fetcher",
                "uses": "google.calendar",
                "call": "list",
                "can_spawn": False,
            }
            for i in range(98, 101)  # 3 new steps
        ]
        request = make_spawn_request(current_step_count=97, proposed_steps=steps)
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Recursive spawning check
# ---------------------------------------------------------------------------


class TestRecursiveSpawning:
    @pytest.mark.asyncio
    async def test_spawned_step_with_can_spawn_denies(self, policy_service):
        """Proposed step with can_spawn=True → denied (no recursive spawning)."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Reasoner",
                    "uses": "google.calendar",
                    "call": "analyze",
                    "can_spawn": True,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "recursive" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_spawned_step_without_can_spawn_permits(self, policy_service):
        """Proposed step with can_spawn=False → allowed."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Booker HITL enforcement
# ---------------------------------------------------------------------------


class TestBookerHITL:
    @pytest.mark.asyncio
    async def test_booker_role_forces_approval(self, policy_service, mock_db_adapter):
        """Spawned Booker step → requires_approval forced True."""
        # Use policy with allowed_roles including Booker
        db_model = DEFAULT_POLICY_DB.model_copy(update={"allowed_roles": ["Fetcher", "Booker"]})
        mock_db_adapter.get_policy.return_value = db_model
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Booker",
                    "uses": "google.calendar",
                    "call": "create_event",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True
        assert decision.requires_approval is True

    @pytest.mark.asyncio
    async def test_non_booker_role_no_forced_approval(self, policy_service):
        """Non-Booker role → requires_approval stays as policy default."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                }
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True
        assert decision.requires_approval is False  # DEFAULT_POLICY has require_approval=False


# ---------------------------------------------------------------------------
# Plugin constraint check
# ---------------------------------------------------------------------------


class TestPluginConstraint:
    @pytest.mark.asyncio
    async def test_tool_not_in_plan_plugins_denies(self, policy_service):
        """Tool not in plan_plugins → denied."""
        request = make_spawn_request(
            plan_plugins=["system.echo"],
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "slack.chat",
                    "call": "send",
                    "can_spawn": False,
                }
            ],
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "plan plugins" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_tool_in_plan_plugins_permits(self, policy_service):
        """Tool in plan_plugins → allowed."""
        request = make_spawn_request(
            plan_plugins=["google.calendar"],
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                }
            ],
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Multiple violations
# ---------------------------------------------------------------------------


class TestMultipleViolations:
    @pytest.mark.asyncio
    async def test_multiple_proposed_one_invalid_denies_all(self, policy_service):
        """If one proposed step violates, entire request is denied."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                },
                {
                    "step": 7,
                    "role": "Notifier",
                    "uses": "google.calendar",
                    "call": "notify",
                    "can_spawn": False,
                },
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is False
        assert "Notifier" in decision.reason  # Notifier not in allowed_roles

    @pytest.mark.asyncio
    async def test_all_valid_proposed_steps_permits(self, policy_service):
        """All proposed steps valid → allowed."""
        request = make_spawn_request(
            proposed_steps=[
                {
                    "step": 6,
                    "role": "Fetcher",
                    "uses": "google.calendar",
                    "call": "list",
                    "can_spawn": False,
                },
                {
                    "step": 7,
                    "role": "Analyzer",
                    "uses": "google.calendar",
                    "call": "analyze",
                    "can_spawn": False,
                },
            ]
        )
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# Valid spawn request (happy path)
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_valid_spawn_request_allowed(self, policy_service):
        """Standard valid spawn request → allowed."""
        request = make_spawn_request()
        decision = await policy_service.evaluate_spawn(request)
        assert decision.allowed is True
        assert "default-reasoning" in decision.reason
        assert decision.violations == []

    @pytest.mark.asyncio
    async def test_decision_includes_policy_version(self, policy_service):
        """Decision reason includes policy version."""
        request = make_spawn_request()
        decision = await policy_service.evaluate_spawn(request)
        assert "v1" in decision.reason
