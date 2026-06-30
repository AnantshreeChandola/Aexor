"""
Shared schema tests for trust boundary pipeline additions.

Covers:
  T110  -- shared/schemas/trust.py, sanitized_payload.py, reasoner_outputs
  T700  -- Backward compatibility for plan.py (Guard role, sanitizer type)
  T701  -- Backward compatibility for policy.py (TrustVerdictRule)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# -------------------------------------------------------------------
# T110: shared/schemas/trust.py
# -------------------------------------------------------------------


class TestTrustVerdict:
    """Tests for shared.schemas.trust."""

    def test_valid_verdicts(self):
        from shared.schemas.trust import TrustVerdict

        for v in ("clean", "suspicious", "injection"):
            tv = TrustVerdict(
                verdict=v,
                confidence=0.95,
                reason="test",
                stage="s1",
            )
            assert tv.verdict == v

    def test_invalid_verdict_rejected(self):
        from shared.schemas.trust import TrustVerdict

        with pytest.raises(ValidationError):
            TrustVerdict(
                verdict="unknown",
                confidence=0.5,
                reason="test",
                stage="s1",
            )

    def test_confidence_range_enforced(self):
        from shared.schemas.trust import TrustVerdict

        with pytest.raises(ValidationError):
            TrustVerdict(
                verdict="clean",
                confidence=1.5,
                reason="test",
                stage="s1",
            )
        with pytest.raises(ValidationError):
            TrustVerdict(
                verdict="clean",
                confidence=-0.1,
                reason="test",
                stage="s1",
            )

    def test_valid_stages(self):
        from shared.schemas.trust import TrustVerdict

        for s in ("s1", "s2", "s1_only_degraded"):
            tv = TrustVerdict(
                verdict="clean",
                confidence=0.99,
                reason="test",
                stage=s,
            )
            assert tv.stage == s


# -------------------------------------------------------------------
# T110: shared/schemas/sanitized_payload.py
# -------------------------------------------------------------------


class TestSanitizedPayload:
    """Tests for shared.schemas.sanitized_payload."""

    def test_valid_payload(self):
        from shared.schemas.sanitized_payload import SanitizedPayload

        sp = SanitizedPayload(
            original_shape={"events": [{"summary": "test"}]},
            stripped_fields=["events.0.description"],
            trust_verdict="injection",
            confidence=0.95,
            scanner_degraded=False,
            scanner_version="s1+s2:abc123",
            scanned_at="2026-04-01T00:00:00Z",
        )
        assert sp.trust_verdict == "injection"
        assert len(sp.stripped_fields) == 1

    def test_original_shape_preserves_structure(self):
        from shared.schemas.sanitized_payload import SanitizedPayload

        # dict shape
        sp = SanitizedPayload(
            original_shape={"a": 1},
            stripped_fields=[],
            trust_verdict="clean",
            confidence=0.99,
            scanner_degraded=False,
            scanner_version="test",
            scanned_at="2026-04-01T00:00:00Z",
        )
        assert isinstance(sp.original_shape, dict)

        # list shape
        sp2 = SanitizedPayload(
            original_shape=[1, 2, 3],
            stripped_fields=[],
            trust_verdict="clean",
            confidence=0.99,
            scanner_degraded=False,
            scanner_version="test",
            scanned_at="2026-04-01T00:00:00Z",
        )
        assert isinstance(sp2.original_shape, list)

        # string shape
        sp3 = SanitizedPayload(
            original_shape="raw text",
            stripped_fields=[],
            trust_verdict="clean",
            confidence=0.99,
            scanner_degraded=False,
            scanner_version="test",
            scanned_at="2026-04-01T00:00:00Z",
        )
        assert isinstance(sp3.original_shape, str)

    def test_scanner_degraded_flag(self):
        from shared.schemas.sanitized_payload import SanitizedPayload

        sp = SanitizedPayload(
            original_shape={},
            stripped_fields=[],
            trust_verdict="clean",
            confidence=0.5,
            scanner_degraded=True,
            scanner_version="s1-only:degraded",
            scanned_at="2026-04-01T00:00:00Z",
        )
        assert sp.scanner_degraded is True

    def test_model_dump_roundtrip(self):
        from shared.schemas.sanitized_payload import SanitizedPayload

        sp = SanitizedPayload(
            original_shape={"x": "y"},
            stripped_fields=["x"],
            trust_verdict="suspicious",
            confidence=0.75,
            scanner_degraded=False,
            scanner_version="v1",
            scanned_at="2026-04-01T00:00:00Z",
        )
        data = sp.model_dump()
        sp2 = SanitizedPayload.model_validate(data)
        assert sp2 == sp


# -------------------------------------------------------------------
# T110: shared/schemas/reasoner_outputs (SCHEMA_REGISTRY)
# -------------------------------------------------------------------


class TestSchemaRegistry:
    """Tests for shared.schemas.reasoner_outputs.SCHEMA_REGISTRY."""

    def test_registry_has_expected_keys(self):
        from shared.schemas.reasoner_outputs import SCHEMA_REGISTRY

        expected = {
            "slot_proposal_v1",
            "free_slots_v1",
            "flight_recommendation_v1",
            "email_summary_v1",
            "freebusy_sanitized_v1",
        }
        assert set(SCHEMA_REGISTRY.keys()) == expected

    def test_all_registry_values_are_pydantic_models(self):
        from pydantic import BaseModel

        from shared.schemas.reasoner_outputs import SCHEMA_REGISTRY

        for key, cls in SCHEMA_REGISTRY.items():
            assert issubclass(cls, BaseModel), (
                f"{key} is not a Pydantic BaseModel"
            )

    def test_slot_proposal_v1_validates(self):
        from shared.schemas.reasoner_outputs import SlotProposalV1

        sp = SlotProposalV1(
            proposed_start="2026-04-01T10:00:00",
            proposed_end="2026-04-01T10:30:00",
            has_conflict=False,
            conflicts=[],
            reason="Best slot",
        )
        assert sp.has_conflict is False

    def test_slot_proposal_v1_rejects_missing_field(self):
        from shared.schemas.reasoner_outputs import SlotProposalV1

        with pytest.raises(ValidationError):
            SlotProposalV1(
                proposed_start="2026-04-01T10:00:00",
                # missing proposed_end, has_conflict, reason
            )

    def test_free_slots_v1_validates(self):
        from shared.schemas.reasoner_outputs import FreeSlotsV1

        fs = FreeSlotsV1(
            recommended_time="2026-04-01T09:00:00",
            has_conflict=False,
            free_slots=[
                {
                    "start": "2026-04-01T09:00:00",
                    "end": "2026-04-01T10:00:00",
                    "label": "9:00 AM - 10:00 AM",
                },
            ],
            reason="Available all morning",
        )
        assert len(fs.free_slots) == 1

    def test_email_summary_v1_validates(self):
        from shared.schemas.reasoner_outputs import EmailSummaryV1

        es = EmailSummaryV1(
            subject="Project Update",
            sender="alice@example.com",
            summary="Key decisions from yesterday's meeting.",
            action_items=["review PR"],
            priority="high",
        )
        assert es.priority == "high"
        assert es.subject == "Project Update"


# -------------------------------------------------------------------
# T700: plan.py backward compatibility
# -------------------------------------------------------------------


class TestPlanSchemaBackwardCompat:
    """Ensure Guard role and sanitizer type don't break existing plans."""

    def test_existing_api_steps_default_type(self):
        """Steps without explicit type default to 'api'."""
        from shared.schemas.plan import PlanStep

        step = PlanStep(
            step=1,
            mode="interactive",
            role="Fetcher",
            uses="google.calendar",
            call="list_events",
            args={},
        )
        assert step.type == "api"

    def test_guard_role_accepted(self):
        from shared.schemas.plan import PlanStep

        step = PlanStep(
            step=1,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
        )
        assert step.role == "Guard"
        assert step.type == "sanitizer"

    def test_sanitizer_type_accepted(self):
        from shared.schemas.plan import PlanStep

        step = PlanStep(
            step=1,
            mode="interactive",
            role="Guard",
            type="sanitizer",
            uses="trust_filter.scan",
            call="scan",
            args={},
        )
        assert step.type == "sanitizer"

    def test_all_original_roles_still_valid(self):
        from shared.schemas.plan import PlanStep

        for role in [
            "Fetcher", "Analyzer", "Watcher", "Resolver",
            "Booker", "Notifier", "Reasoner",
        ]:
            step = PlanStep(
                step=1,
                mode="interactive",
                role=role,
                uses="test.tool",
                call="test",
                args={},
            )
            assert step.role == role

    def test_all_step_types_valid(self):
        from shared.schemas.plan import PlanStep

        for stype in ["api", "llm_reasoning", "policy_check", "sanitizer"]:
            step = PlanStep(
                step=1,
                mode="interactive",
                role="Fetcher" if stype != "sanitizer" else "Guard",
                type=stype,
                uses="test.tool",
                call="test",
                args={},
            )
            assert step.type == stype

    def test_existing_plan_without_sanitizer_still_valid(self):
        """A plan with only API steps remains valid."""
        from shared.schemas.plan import Plan, PlanMeta

        plan = Plan(
            plan_id="A" * 26,
            intent={
                "intent": "test",
                "entities": {},
                "constraints": {},
                "user_id": "u1",
                "session_id": "s1",
            },
            graph=[
                {
                    "step": 1,
                    "mode": "interactive",
                    "role": "Fetcher",
                    "uses": "test",
                    "call": "test",
                    "args": {},
                },
            ],
            meta=PlanMeta(
                created_at="2026-01-01T00:00:00Z",
                canonical_hash="a" * 64,
            ),
        )
        assert len(plan.graph) == 1
        assert plan.graph[0].type == "api"


# -------------------------------------------------------------------
# T701: policy.py backward compatibility
# -------------------------------------------------------------------


class TestPolicySchemaBackwardCompat:
    """Ensure TrustVerdictRule addition doesn't break existing policies."""

    def test_policy_rule_without_trust_rules_valid(self):
        """PolicyRule with empty trust_verdict_rules (default) is valid."""
        from shared.schemas.policy import PolicyRule

        rule = PolicyRule(
            policy_id="test",
            name="Test Policy",
            version=1,
            scope="role",
        )
        assert rule.trust_verdict_rules == []

    def test_policy_rule_with_trust_rules_valid(self):
        from shared.schemas.policy import PolicyRule, TrustVerdictRule

        rule = PolicyRule(
            policy_id="test",
            name="Test Policy",
            version=1,
            scope="role",
            trust_verdict_rules=[
                TrustVerdictRule(
                    verdict="injection",
                    action="block",
                ),
            ],
        )
        assert len(rule.trust_verdict_rules) == 1

    def test_trust_verdict_rule_valid_verdicts(self):
        from shared.schemas.policy import TrustVerdictRule

        for v in ("clean", "suspicious", "injection"):
            r = TrustVerdictRule(verdict=v, action="require_approval")
            assert r.verdict == v

    def test_trust_verdict_rule_valid_actions(self):
        from shared.schemas.policy import TrustVerdictRule

        for a in ("require_approval", "block"):
            r = TrustVerdictRule(verdict="injection", action=a)
            assert r.action == a

    def test_trust_verdict_rule_invalid_action_rejected(self):
        from shared.schemas.policy import TrustVerdictRule

        with pytest.raises(ValidationError):
            TrustVerdictRule(verdict="injection", action="ignore")

    def test_policy_decision_unchanged(self):
        """PolicyDecision still works without trust fields."""
        from shared.schemas.policy import PolicyDecision

        d = PolicyDecision(
            allowed=True,
            reason="test",
        )
        assert d.allowed is True
        assert d.policy_matched is True

    def test_existing_policy_rule_serialization(self):
        """PolicyRule model_dump roundtrip preserves all fields."""
        from shared.schemas.policy import PolicyRule

        rule = PolicyRule(
            policy_id="test",
            name="Test",
            version=1,
            scope="step",
            allowed_tools=["google.calendar"],
            allowed_roles=["Booker"],
            max_spawned_steps=5,
            require_approval=True,
            forbidden_actions=["delete_all"],
        )
        data = rule.model_dump()
        rule2 = PolicyRule.model_validate(data)
        assert rule2.policy_id == rule.policy_id
        assert rule2.trust_verdict_rules == []
