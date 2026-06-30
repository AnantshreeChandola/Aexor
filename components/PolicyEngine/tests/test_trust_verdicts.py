"""
PolicyEngine trust verdict evaluation tests.

Covers:
  T1003 -- evaluate_trust_verdicts (FR-028, FR-029, FR-030, FR-031)
  AC-8  -- trust_verdict_rules escalate steps to HITL
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from components.PolicyEngine.service.policy_service import PolicyService
from shared.schemas.policy import PolicyRule, TrustVerdictRule

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _service() -> PolicyService:
    """Create a PolicyService with mock adapters."""
    db = AsyncMock()
    cache = AsyncMock()
    cache.get_policy = AsyncMock(return_value=None)
    return PolicyService(db_adapter=db, cache_adapter=cache)


def _policy_with_trust_rules(
    rules: list[TrustVerdictRule],
) -> PolicyRule:
    """Create a PolicyRule with trust verdict rules."""
    return PolicyRule(
        policy_id="test-trust-policy",
        name="Trust Test Policy",
        version=1,
        scope="role",
        trust_verdict_rules=rules,
    )


# ===================================================================
# FR-029: Hardcoded defaults
# ===================================================================


class TestHardcodedDefaults:
    """FR-029: injection -> requires_approval by default."""

    @pytest.mark.asyncio
    async def test_injection_verdict_requires_approval(self):
        """Ancestor with injection verdict triggers approval."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Booker", "gate_id": "g1"},
            ancestor_verdicts={2: "injection"},
            scanner_degraded=False,
        )
        assert decision.allowed is True
        assert decision.requires_approval is True
        assert "injection" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_scanner_degraded_requires_approval(self):
        """scanner_degraded=true triggers approval."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Booker", "gate_id": "g1"},
            ancestor_verdicts={2: "clean"},
            scanner_degraded=True,
        )
        assert decision.allowed is True
        assert decision.requires_approval is True
        assert "scanner_degraded" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_clean_verdict_no_escalation(self):
        """Clean verdict with no degradation: no escalation."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Booker"},
            ancestor_verdicts={2: "clean"},
            scanner_degraded=False,
        )
        assert decision.allowed is True
        assert decision.requires_approval is False
        assert "no trust escalation" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_multiple_verdicts_injection_wins(self):
        """Multiple verdicts: if any is injection, requires approval."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Booker", "gate_id": "g1"},
            ancestor_verdicts={2: "clean", 3: "injection"},
            scanner_degraded=False,
        )
        assert decision.requires_approval is True

    @pytest.mark.asyncio
    async def test_empty_verdicts_no_escalation(self):
        """No ancestor verdicts: no escalation."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Booker"},
            ancestor_verdicts={},
            scanner_degraded=False,
        )
        assert decision.allowed is True
        assert decision.requires_approval is False


# ===================================================================
# FR-030: Configurable TrustVerdictRule
# ===================================================================


class TestConfigurableRules:
    """FR-030: policy-defined trust_verdict_rules."""

    @pytest.mark.asyncio
    async def test_suspicious_require_approval_rule(self):
        """Suspicious verdict with require_approval rule triggers."""
        svc = _service()
        rule = TrustVerdictRule(
            verdict="suspicious",
            action="require_approval",
        )
        policy = _policy_with_trust_rules([rule])

        decision = await svc.evaluate_trust_verdicts(
            step_dict={
                "step": 5,
                "role": "Booker",
                "gate_id": "g1",
            },
            ancestor_verdicts={2: "suspicious"},
            scanner_degraded=False,
            policy_rule=policy,
        )
        assert decision.requires_approval is True
        assert "TrustVerdictRule" in decision.reason

    @pytest.mark.asyncio
    async def test_block_rule_denies(self):
        """Block rule returns allowed=False."""
        svc = _service()
        rule = TrustVerdictRule(
            verdict="injection",
            action="block",
        )
        policy = _policy_with_trust_rules([rule])

        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Booker"},
            ancestor_verdicts={2: "injection"},
            scanner_degraded=False,
            policy_rule=policy,
        )
        assert decision.allowed is False
        assert "blocked" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_disabled_rule_ignored(self):
        """Disabled rule is not evaluated."""
        svc = _service()
        rule = TrustVerdictRule(
            verdict="injection",
            action="block",
            enabled=False,
        )
        policy = _policy_with_trust_rules([rule])

        decision = await svc.evaluate_trust_verdicts(
            step_dict={
                "step": 5,
                "role": "Booker",
                "gate_id": "g1",
            },
            ancestor_verdicts={2: "injection"},
            scanner_degraded=False,
            policy_rule=policy,
        )
        # Block rule disabled: falls through to hardcoded default
        # injection -> requires_approval
        assert decision.allowed is True
        assert decision.requires_approval is True

    @pytest.mark.asyncio
    async def test_role_scoped_rule_matches(self):
        """Rule with roles filter only applies to matching roles."""
        svc = _service()
        rule = TrustVerdictRule(
            verdict="suspicious",
            action="require_approval",
            roles=["Booker"],
        )
        policy = _policy_with_trust_rules([rule])

        # Booker matches
        decision = await svc.evaluate_trust_verdicts(
            step_dict={
                "step": 5,
                "role": "Booker",
                "gate_id": "g1",
            },
            ancestor_verdicts={2: "suspicious"},
            scanner_degraded=False,
            policy_rule=policy,
        )
        assert decision.requires_approval is True

    @pytest.mark.asyncio
    async def test_role_scoped_rule_skips_non_matching(self):
        """Rule with roles filter skips non-matching roles."""
        svc = _service()
        rule = TrustVerdictRule(
            verdict="suspicious",
            action="require_approval",
            roles=["Booker"],
        )
        policy = _policy_with_trust_rules([rule])

        # Fetcher does NOT match role filter
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Fetcher"},
            ancestor_verdicts={2: "suspicious"},
            scanner_degraded=False,
            policy_rule=policy,
        )
        assert decision.requires_approval is False


# ===================================================================
# FR-031: gate_id enforcement
# ===================================================================


class TestGateIdEnforcement:
    """FR-031: requires_approval without gate_id returns failure."""

    @pytest.mark.asyncio
    async def test_requires_approval_without_gate_id_fails(self):
        """Step needs approval but has no gate_id: not allowed."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Booker"},
            ancestor_verdicts={2: "injection"},
            scanner_degraded=False,
        )
        assert decision.allowed is False
        assert decision.requires_approval is True
        assert "no_gate" in decision.reason

    @pytest.mark.asyncio
    async def test_requires_approval_with_gate_id_succeeds(self):
        """Step needs approval and has gate_id: allowed."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={
                "step": 5,
                "role": "Booker",
                "gate_id": "gate-booking",
            },
            ancestor_verdicts={2: "injection"},
            scanner_degraded=False,
        )
        assert decision.allowed is True
        assert decision.requires_approval is True

    @pytest.mark.asyncio
    async def test_no_approval_needed_gate_irrelevant(self):
        """When no approval needed, missing gate_id is fine."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Fetcher"},
            ancestor_verdicts={2: "clean"},
            scanner_degraded=False,
        )
        assert decision.allowed is True
        assert decision.requires_approval is False


# ===================================================================
# Edge cases and combinations
# ===================================================================


class TestEdgeCases:
    """Edge cases for trust verdict evaluation."""

    @pytest.mark.asyncio
    async def test_both_injection_and_degraded(self):
        """Both injection AND degraded: requires approval."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={
                "step": 5,
                "role": "Booker",
                "gate_id": "g1",
            },
            ancestor_verdicts={2: "injection"},
            scanner_degraded=True,
        )
        assert decision.requires_approval is True
        assert "injection" in decision.reason.lower()
        assert "scanner_degraded" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_block_rule_overrides_approval(self):
        """Block rule takes precedence over require_approval."""
        svc = _service()
        rules = [
            TrustVerdictRule(
                verdict="injection",
                action="block",
            ),
        ]
        policy = _policy_with_trust_rules(rules)

        decision = await svc.evaluate_trust_verdicts(
            step_dict={
                "step": 5,
                "role": "Booker",
                "gate_id": "g1",
            },
            ancestor_verdicts={2: "injection"},
            scanner_degraded=False,
            policy_rule=policy,
        )
        # Block should return allowed=False
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_suspicious_without_rule_no_escalation(self):
        """Suspicious verdict without a matching rule: no escalation
        (suspicious is not hardcoded like injection)."""
        svc = _service()
        decision = await svc.evaluate_trust_verdicts(
            step_dict={"step": 5, "role": "Fetcher"},
            ancestor_verdicts={2: "suspicious"},
            scanner_degraded=False,
        )
        assert decision.allowed is True
        assert decision.requires_approval is False


# ===================================================================
# T1002: evaluate_spawn trust verdict integration
# ===================================================================


class TestEvaluateSpawnTrustIntegration:
    """Verify trust verdict evaluation is integrated into evaluate_spawn."""

    @pytest.mark.asyncio
    async def test_spawn_with_injection_verdict_requires_approval(self):
        """Spawn request with ancestor injection verdict requires approval."""
        from components.PolicyEngine.domain.models import SpawnRequest

        svc = _service()
        # Seed a matching policy so evaluate_spawn doesn't early-exit
        svc._db.get_policy = AsyncMock(return_value=None)
        svc._db.list_policies = AsyncMock(return_value=[])

        request = SpawnRequest(
            plan_id="A" * 26,
            plan_revision=1,
            spawning_step=3,
            proposed_steps=[
                {"step": 4, "role": "Fetcher", "uses": "test.tool", "call": "test"},
            ],
            current_step_count=3,
            ancestor_verdicts={2: "injection"},
            scanner_degraded=False,
        )
        # evaluate_spawn with no policy -> falls to user approval (requires_approval=True)
        # Trust verdicts also flag injection -> still requires_approval=True
        decision = await svc.evaluate_spawn(request)
        assert decision.requires_approval is True

    @pytest.mark.asyncio
    async def test_spawn_with_clean_verdicts_no_extra_escalation(self):
        """Spawn request with clean verdicts does not add extra escalation."""
        from components.PolicyEngine.domain.models import SpawnRequest

        svc = _service()
        svc._db.get_policy = AsyncMock(return_value=None)

        request = SpawnRequest(
            plan_id="A" * 26,
            plan_revision=1,
            spawning_step=3,
            proposed_steps=[
                {"step": 4, "role": "Fetcher", "uses": "test.tool", "call": "test"},
            ],
            current_step_count=3,
            ancestor_verdicts={2: "clean"},
            scanner_degraded=False,
        )
        decision = await svc.evaluate_spawn(request)
        # No policy found -> requires_approval from fallback, not from trust
        assert decision.requires_approval is True  # policy fallback
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_spawn_with_degraded_scanner_requires_approval(self):
        """Spawn request with scanner_degraded=True requires approval."""
        from components.PolicyEngine.domain.models import SpawnRequest

        svc = _service()
        svc._db.get_policy = AsyncMock(return_value=None)

        request = SpawnRequest(
            plan_id="A" * 26,
            plan_revision=1,
            spawning_step=3,
            proposed_steps=[
                {"step": 4, "role": "Fetcher", "uses": "test.tool", "call": "test"},
            ],
            current_step_count=3,
            ancestor_verdicts={},
            scanner_degraded=True,
        )
        decision = await svc.evaluate_spawn(request)
        assert decision.requires_approval is True

    @pytest.mark.asyncio
    async def test_spawn_request_backward_compat_no_verdicts(self):
        """SpawnRequest without verdicts works as before (default empty)."""
        from components.PolicyEngine.domain.models import SpawnRequest

        request = SpawnRequest(
            plan_id="A" * 26,
            plan_revision=1,
            spawning_step=3,
            proposed_steps=[
                {"step": 4, "role": "Fetcher", "uses": "test.tool", "call": "test"},
            ],
            current_step_count=3,
        )
        assert request.ancestor_verdicts == {}
        assert request.scanner_degraded is False
