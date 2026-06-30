"""Tests for TrustFilter domain models -- T206."""

import pytest
from pydantic import ValidationError

from components.TrustFilter.domain.models import (
    RuleHit,
    S1Result,
    S2Result,
    ScanContext,
)


class TestScanContext:
    def test_valid_construction(self) -> None:
        ctx = ScanContext(
            plan_id="plan_01",
            step_number=3,
            trace_id="trace_01",
            load_bearing_fields={"free_slots"},
            strict_mode=True,
        )
        assert ctx.plan_id == "plan_01"
        assert ctx.step_number == 3
        assert "free_slots" in ctx.load_bearing_fields
        assert ctx.strict_mode is True

    def test_defaults(self) -> None:
        ctx = ScanContext(
            plan_id="p", step_number=1, trace_id="t"
        )
        assert ctx.load_bearing_fields == set()
        assert ctx.strict_mode is False


class TestRuleHit:
    def test_valid_construction(self) -> None:
        hit = RuleHit(
            field_path="a.b[0].c",
            rule_id="test_rule",
            severity="high",
            matched_substring="ignore previous",
        )
        assert hit.field_path == "a.b[0].c"
        assert hit.severity == "high"

    def test_severity_validation(self) -> None:
        with pytest.raises(ValidationError):
            RuleHit(
                field_path="x",
                rule_id="r",
                severity="critical",  # type: ignore[arg-type]
            )


class TestS1Result:
    def test_valid(self) -> None:
        r = S1Result(
            verdict="injection",
            confidence=0.95,
            hits=[],
            fields_scanned=10,
        )
        assert r.verdict == "injection"

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            S1Result(
                verdict="clean", confidence=1.5, hits=[]
            )


class TestS2Result:
    def test_valid(self) -> None:
        r = S2Result(
            verdict="suspicious",
            confidence=0.6,
            reason="ambiguous",
            degraded=False,
        )
        assert r.verdict == "suspicious"

    def test_degraded_flag(self) -> None:
        r = S2Result(
            verdict="clean",
            confidence=0.5,
            reason="degraded",
            degraded=True,
        )
        assert r.degraded is True
