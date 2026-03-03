"""
Evidence Service for History Component

Converts Facts to Evidence Items (GLOBAL_SPEC §2.2).

Reference: LLD.md §4.3, tasks.md T202
"""

from datetime import UTC, datetime

from ..domain.models import Fact


class EvidenceService:
    """
    Service for converting Facts to Evidence Items.

    Evidence Items follow GLOBAL_SPEC §2.2 format with type="history", tier=3.
    """

    def fact_to_evidence(self, fact: Fact) -> dict:
        """
        Convert a Fact to an Evidence Item (GLOBAL_SPEC section 2.2).

        Confidence decays linearly over the fact's TTL:
        - New fact (age=0): confidence ~1.0
        - 50% of TTL: confidence ~0.5
        - Expired fact: confidence 0.0

        Args:
            fact: Fact domain model

        Returns:
            Evidence Item dict conforming to GLOBAL_SPEC §2.2
        """
        now = datetime.now(UTC)

        # Calculate age in days
        age_seconds = (now - fact.created_at).total_seconds()
        age_days = int(age_seconds / 86400)

        # Linear confidence decay: max(0.0, 1.0 - (age_days / ttl_days))
        confidence = max(0.0, 1.0 - age_days / fact.ttl_days) if fact.ttl_days > 0 else 0.0

        # Calculate remaining TTL
        remaining_ttl = max(1, fact.ttl_days - age_days)

        # Evidence Item key: {intent_type}_{date}
        fact_date = fact.created_at.date().isoformat()
        evidence_key = f"{fact.intent_type}_{fact_date}"

        # Source reference: history:facts/{fact_id}
        source_ref = f"history:facts/{fact.fact_id}"

        return {
            "type": "history",
            "key": evidence_key,
            "value": {
                "fact": fact.fact_text,
                "intent_type": fact.intent_type,
                "outcome": fact.outcome,
                "entities": fact.entities,
                "age_days": age_days,
            },
            "confidence": confidence,
            "source_ref": source_ref,
            "ttl_days": remaining_ttl,
            "tier": 3,
        }
