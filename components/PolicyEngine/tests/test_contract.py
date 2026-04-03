"""
PolicyEngine contract compliance tests — GLOBAL_SPEC §2.9.

Validates that domain models conform to the spec schema and that
edge cases in the contract are handled correctly.
~10 tests.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from components.PolicyEngine.domain.models import SpawnRequest
from components.PolicyEngine.tests.conftest import DEFAULT_POLICY, SAMPLE_PLAN_ID
from shared.schemas.policy import PolicyAttestation, PolicyDecision, PolicyRule

# ---------------------------------------------------------------------------
# PolicyRule model conformance
# ---------------------------------------------------------------------------


class TestPolicyRuleConformance:
    def test_default_policy_validates(self):
        """The default policy from §2.9 validates correctly."""
        assert DEFAULT_POLICY.policy_id == "default-reasoning"
        assert DEFAULT_POLICY.scope in ("step", "role", "system")
        assert DEFAULT_POLICY.version >= 1
        assert DEFAULT_POLICY.max_spawned_steps <= 10

    def test_policy_rule_scope_values(self):
        """PolicyRule.scope accepts step, role, system."""
        for scope in ("step", "role", "system"):
            rule = PolicyRule(
                policy_id=f"test-{scope}",
                name=f"Test {scope}",
                scope=scope,
            )
            assert rule.scope == scope

    def test_policy_rule_wildcard_tools(self):
        """PolicyRule defaults to wildcard allowed_tools."""
        rule = PolicyRule(policy_id="test", name="Test", scope="step")
        assert rule.allowed_tools == ["*"]

    def test_policy_rule_default_token_budget(self):
        """PolicyRule defaults to 8192 token budget."""
        rule = PolicyRule(policy_id="test", name="Test", scope="step")
        assert rule.token_budget == 8192

    def test_policy_rule_max_spawned_steps_range(self):
        """max_spawned_steps must be 0-10."""
        rule = PolicyRule(policy_id="test", name="Test", scope="step", max_spawned_steps=0)
        assert rule.max_spawned_steps == 0
        rule = PolicyRule(policy_id="test", name="Test", scope="step", max_spawned_steps=10)
        assert rule.max_spawned_steps == 10


# ---------------------------------------------------------------------------
# PolicyDecision model conformance
# ---------------------------------------------------------------------------


class TestPolicyDecisionConformance:
    def test_decision_allowed(self):
        """PolicyDecision with allowed=True conforms to §2.9."""
        decision = PolicyDecision(allowed=True, reason="Approved", violations=[])
        assert decision.allowed is True
        assert decision.violations == []

    def test_decision_denied_with_violations(self):
        """PolicyDecision with violations conforms to §2.9."""
        decision = PolicyDecision(
            allowed=False,
            reason="Denied: forbidden action",
            violations=["forbidden_action: delete_all"],
        )
        assert decision.allowed is False
        assert len(decision.violations) == 1

    def test_decision_requires_approval(self):
        """PolicyDecision.requires_approval for HITL gating."""
        decision = PolicyDecision(allowed=True, reason="OK", requires_approval=True)
        assert decision.requires_approval is True

    def test_decision_policy_matched_default(self):
        """PolicyDecision.policy_matched defaults to True."""
        decision = PolicyDecision(allowed=True, reason="OK")
        assert decision.policy_matched is True

    def test_decision_policy_matched_false(self):
        """PolicyDecision.policy_matched can be set to False."""
        decision = PolicyDecision(allowed=True, reason="Fallback", policy_matched=False)
        assert decision.policy_matched is False


# ---------------------------------------------------------------------------
# PolicyAttestation model conformance (§2.4.1)
# ---------------------------------------------------------------------------


class TestPolicyAttestationConformance:
    def test_attestation_ulid_format(self):
        """Attestation ID must be 26-char ULID."""
        att = PolicyAttestation(
            attestation_id="01JBXYZ1234567890ABCDEFGHI",
            plan_id=SAMPLE_PLAN_ID,
            plan_revision=1,
            spawned_by_step=5,
            new_steps=[{"step": 6, "role": "Fetcher"}],
            policy_id="test-pol",
            policy_version=1,
            decision=PolicyDecision(allowed=True, reason="OK"),
            attested_at="2026-03-29T12:00:00Z",
        )
        assert len(att.attestation_id) == 26

    def test_attestation_requires_plan_id(self):
        """Attestation requires a valid plan_id."""
        with pytest.raises(ValidationError):
            PolicyAttestation(
                attestation_id="01JBXYZ1234567890ABCDEFGHI",
                plan_id="short",  # too short
                plan_revision=1,
                spawned_by_step=5,
                new_steps=[],
                policy_id="test-pol",
                policy_version=1,
                decision=PolicyDecision(allowed=True, reason="OK"),
                attested_at="2026-03-29T12:00:00Z",
            )


# ---------------------------------------------------------------------------
# SpawnRequest edge cases
# ---------------------------------------------------------------------------


class TestSpawnRequestEdgeCases:
    def test_empty_proposed_steps_rejected(self):
        """SpawnRequest with empty proposed_steps → validation error."""
        with pytest.raises(ValidationError):
            SpawnRequest(
                plan_id=SAMPLE_PLAN_ID,
                plan_revision=1,
                spawning_step=5,
                proposed_steps=[],
                current_step_count=5,
            )

    def test_max_step_count_boundary(self):
        """SpawnRequest with 0 current steps is valid."""
        request = SpawnRequest(
            plan_id=SAMPLE_PLAN_ID,
            plan_revision=1,
            spawning_step=1,
            proposed_steps=[{"step": 2, "role": "Fetcher"}],
            current_step_count=0,
        )
        assert request.current_step_count == 0

    def test_plan_id_must_be_26_chars(self):
        """SpawnRequest.plan_id must be exactly 26 characters."""
        with pytest.raises(ValidationError):
            SpawnRequest(
                plan_id="short",
                plan_revision=1,
                spawning_step=1,
                proposed_steps=[{"step": 2}],
                current_step_count=1,
            )
