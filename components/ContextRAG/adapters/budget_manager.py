"""
BudgetManager -- prioritize and trim evidence to fit within byte budget.

Sorting order: tier ASC (Tier 2 before Tier 3), then confidence DESC.
Budget measurement: len(item.model_dump_json().encode("utf-8")) per item.
Greedy addition until budget is exceeded.

Reference: LLD.md SS6.6
"""

from shared.schemas.evidence import EvidenceItem

BUDGET_BYTES: int = 2048


class BudgetManager:
    """Prioritize and trim evidence to fit within byte budget."""

    BUDGET_BYTES: int = BUDGET_BYTES

    def enforce_budget(
        self,
        evidence: list[EvidenceItem],
    ) -> tuple[list[EvidenceItem], int]:
        """Sort by priority, trim to budget, return (trimmed_list, total_bytes).

        Priority order:
          1. Tier ascending (Tier 2 before Tier 3)
          2. Confidence descending within same tier
          3. Earlier items within same tier+confidence (stable sort)

        Budget measurement: len(item.model_dump_json().encode("utf-8")) per item.
        Items are added greedily until budget is exceeded.

        Returns:
            (evidence_list, total_bytes) -- evidence_list fits within BUDGET_BYTES.
        """
        if not evidence:
            return [], 0

        # Stable sort: tier ASC, confidence DESC
        sorted_evidence = sorted(
            evidence,
            key=lambda item: (item.tier, -item.confidence),
        )

        trimmed: list[EvidenceItem] = []
        total_bytes = 0

        for item in sorted_evidence:
            item_bytes = len(item.model_dump_json().encode("utf-8"))
            if total_bytes + item_bytes > self.BUDGET_BYTES:
                break
            trimmed.append(item)
            total_bytes += item_bytes

        return trimmed, total_bytes

    def deduplicate(
        self,
        evidence: list[EvidenceItem],
    ) -> list[EvidenceItem]:
        """Remove duplicate evidence items by key.

        When two items share the same key, keep the one with higher confidence.
        If confidence is equal, keep the first encountered.

        Returns:
            Deduplicated list preserving relative order of kept items.
        """
        if not evidence:
            return []

        best_by_key: dict[str, EvidenceItem] = {}
        seen_order: list[str] = []

        for item in evidence:
            if item.key not in best_by_key:
                best_by_key[item.key] = item
                seen_order.append(item.key)
            elif item.confidence > best_by_key[item.key].confidence:
                best_by_key[item.key] = item

        return [best_by_key[key] for key in seen_order]
