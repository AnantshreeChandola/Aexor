"""
ContextRAG Contract Tests

Verifies schema compliance and ContextResult invariants.
Every evidence item must pass EvidenceItem.model_validate(),
have source_ref, tier, and confidence in valid ranges.

Reference: tasks.md T500
"""

from components.ContextRAG.domain.models import ContextResult
from components.ContextRAG.service.context_rag_service import ContextRAGService
from shared.schemas.evidence import EvidenceItem

from .conftest import SAMPLE_INTENT, SAMPLE_TIER2_INTENT

VALID_EVIDENCE_TYPES = {"preference", "history", "contact", "plan", "exemplar"}


# ===================================================================
# EvidenceItem Schema Compliance (SC-004)
# ===================================================================


class TestEvidenceItemSchemaCompliance:
    """All evidence items must pass schema validation."""

    async def test_all_evidence_items_pass_model_validate(
        self, context_rag_service: ContextRAGService
    ):
        """Every item in result.evidence passes model_validate round-trip."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        for item in result.evidence:
            validated = EvidenceItem.model_validate(item.model_dump())
            assert validated.key == item.key

    async def test_evidence_items_have_source_ref(self, context_rag_service: ContextRAGService):
        """Every item has non-empty source_ref (FR-011)."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        for item in result.evidence:
            assert item.source_ref
            assert len(item.source_ref) > 0

    async def test_evidence_items_have_tier(self, context_rag_service: ContextRAGService):
        """Every item has tier in {1, 2, 3, 4} (FR-011)."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        for item in result.evidence:
            assert item.tier in {1, 2, 3, 4}

    async def test_evidence_confidence_in_range(self, context_rag_service: ContextRAGService):
        """Every item has 0.0 <= confidence <= 1.0."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        for item in result.evidence:
            assert 0.0 <= item.confidence <= 1.0

    async def test_evidence_type_is_valid(self, context_rag_service: ContextRAGService):
        """Every item has type in valid set."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        for item in result.evidence:
            assert item.type in VALID_EVIDENCE_TYPES


# ===================================================================
# ContextResult Invariants
# ===================================================================


class TestContextResultInvariants:
    """ContextResult structural invariants."""

    async def test_context_result_never_none(self, context_rag_service: ContextRAGService):
        """gather_evidence() always returns ContextResult, never None."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        assert result is not None
        assert isinstance(result, ContextResult)

    async def test_context_result_evidence_is_list(self, context_rag_service: ContextRAGService):
        """result.evidence is always a list (possibly empty)."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        assert isinstance(result.evidence, list)

    async def test_budget_hard_cap(self, context_rag_service: ContextRAGService):
        """result.total_bytes <= 2048 for all test scenarios (SC-002)."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        assert result.total_bytes <= 2048

    async def test_tier_enforcement_contract(self, context_rag_service: ContextRAGService):
        """With context_budget=2, no Tier 3 items in result (SC-005)."""
        result = await context_rag_service.gather_evidence(SAMPLE_TIER2_INTENT)
        for item in result.evidence:
            assert item.tier <= 2


# ===================================================================
# Intent -> ContextResult Flow
# ===================================================================


class TestIntentToContextResultFlow:
    """End-to-end flow validation."""

    async def test_intent_to_context_result_flow(self, context_rag_service: ContextRAGService):
        """Construct valid Intent, call gather_evidence, verify shape."""
        result = await context_rag_service.gather_evidence(SAMPLE_INTENT)
        assert isinstance(result, ContextResult)
        assert isinstance(result.evidence, list)
        assert isinstance(result.total_bytes, int)
        assert isinstance(result.degraded_sources, list)
        assert isinstance(result.query_duration_ms, int)
        assert result.total_bytes >= 0
        assert result.query_duration_ms >= 0
