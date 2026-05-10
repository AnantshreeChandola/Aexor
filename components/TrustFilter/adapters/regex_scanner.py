"""
RegexScanner -- S1 deterministic pattern-based injection detection.

LLD Section 6.1, FR-003. Compiles rules at __init__, not per-call.
"""

from __future__ import annotations

import re
from re import Pattern

from components.TrustFilter.domain.errors import S1InternalError
from components.TrustFilter.domain.models import RuleHit
from components.TrustFilter.domain.regex_rules import (
    RulePack,
    load_default_rule_pack,
)
from shared.schemas.trust import Verdict

CompiledRule = tuple[str, Pattern[str], str]  # (rule_id, compiled, severity)


class RegexScanner:
    """S1 -- deterministic pattern-based injection detection."""

    def __init__(
        self, rule_pack: RulePack | None = None
    ) -> None:
        self._rule_pack = rule_pack or load_default_rule_pack()
        try:
            self._compiled: list[CompiledRule] = [
                (
                    rule.rule_id,
                    re.compile(rule.pattern, rule.flags),
                    rule.severity,
                )
                for rule in self._rule_pack.rules
            ]
        except re.error as exc:
            raise S1InternalError(
                f"Failed to compile rule pack: {exc}"
            ) from exc

    @property
    def rule_pack_sha(self) -> str:
        """Return the rule pack SHA-256 prefix."""
        return self._rule_pack.sha256

    def scan_string(
        self, value: str, field_path: str
    ) -> list[RuleHit]:
        """Return all rule hits on a single string field.

        Args:
            value: The string content to scan.
            field_path: Dotted path for reporting.

        Returns:
            List of RuleHit for each matching rule.
        """
        hits: list[RuleHit] = []
        for rule_id, compiled, severity in self._compiled:
            try:
                match = compiled.search(value)
                if match:
                    hits.append(RuleHit(
                        field_path=field_path,
                        rule_id=rule_id,
                        severity=severity,
                        matched_substring=match.group(0)[:200],
                    ))
            except Exception:
                # Individual rule failure should not stop scan
                continue
        return hits

    def aggregate(
        self, hits: list[RuleHit]
    ) -> tuple[Verdict, float]:
        """Aggregate hits into a verdict + confidence.

        Rules:
        - Any 'high' severity hit -> injection, 0.95
        - >= 2 'med' severity hits -> injection, 0.85
        - 1 'med' severity hit -> suspicious, 0.60
        - Only 'low' hits -> clean, 0.70
        - No hits -> clean, 0.99
        """
        if not hits:
            return "clean", 0.99

        high_count = sum(
            1 for h in hits if h.severity == "high"
        )
        med_count = sum(
            1 for h in hits if h.severity == "med"
        )

        if high_count > 0:
            return "injection", 0.95
        if med_count >= 2:
            return "injection", 0.85
        if med_count == 1:
            return "suspicious", 0.60
        # Only low hits
        return "clean", 0.70
