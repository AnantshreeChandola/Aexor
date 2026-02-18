"""
Evidence Service for PlanLibrary

Converts plan data to Evidence Item format (GLOBAL_SPEC 2.2).
Provides batch conversion helpers.

Reference: LLD.md, tasks.md T203
"""

import logging
from typing import Any

from shared.schemas.evidence import EvidenceItem

logger = logging.getLogger(__name__)


class EvidenceService:
    """
    Evidence Item conversion service.

    Converts plan data to Evidence Item format for ContextRAG integration.
    Plans are type="plan", tier=3, ttl_days=None.
    """

    def to_evidence_item(
        self,
        plan_data: dict[str, Any],
    ) -> EvidenceItem:
        """
        Convert plan data to Evidence Item format.

        Args:
            plan_data: Plan dict with success_rate, intent_type, etc.

        Returns:
            EvidenceItem (GLOBAL_SPEC 2.2 format)
        """
        plan_id = plan_data.get("plan_id", "unknown")
        intent_type = plan_data.get("intent_type", "unknown")
        success_rate = float(plan_data.get("success_rate", 0.0))
        avg_time_ms = float(plan_data.get("avg_execution_time_ms", 0.0))
        step_count = plan_data.get("step_count", 0)

        # Build pattern summary
        pattern_summary = plan_data.get(
            "pattern_summary",
            f"{intent_type} plan with {step_count} steps",
        )

        # Confidence = success_rate for intent-based queries
        confidence = min(max(success_rate, 0.0), 1.0)

        return EvidenceItem(
            type="plan",
            key=f"{intent_type}_pattern_{plan_id[:8]}",
            value={
                "intent": intent_type,
                "success_rate": round(success_rate, 3),
                "avg_execution_time_ms": round(avg_time_ms, 1),
                "steps_count": step_count,
                "pattern_summary": pattern_summary,
            },
            confidence=confidence,
            source_ref=f"planlibrary:plans/{plan_id}",
            ttl_days=None,
            tier=3,
        )

    def to_evidence_items(
        self,
        plans: list[dict[str, Any]],
    ) -> list[EvidenceItem]:
        """
        Batch convert plan data to Evidence Items.

        Args:
            plans: List of plan data dicts

        Returns:
            List of Evidence Items
        """
        return [self.to_evidence_item(plan) for plan in plans]

