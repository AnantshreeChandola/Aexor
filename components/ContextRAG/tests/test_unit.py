"""
ContextRAG Unit Tests

Tests for domain models (ContextResult, SourceQueryError),
BudgetManager (enforce_budget, deduplicate), and source adapters.

Reference: tasks.md T101, T201, T304
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from components.ContextRAG.adapters.budget_manager import BudgetManager
from components.ContextRAG.adapters.history_adapter import HistoryAdapter
from components.ContextRAG.adapters.planlibrary_adapter import PlanLibraryAdapter
from components.ContextRAG.adapters.profilestore_adapter import ProfileStoreAdapter
from components.ContextRAG.adapters.vectorindex_adapter import VectorIndexAdapter
from components.ContextRAG.domain.models import (
    ContextRAGError,
    ContextResult,
    SourceQueryError,
)
from components.History.domain.models import (
    ConsentRequiredError,
    InvalidQueryError,
    PatternsResponse,
    QueryFactsResponse,
    StorageError,
)
from components.ProfileStore.domain.models import ConsentDeniedError
from components.VectorIndex.domain.models import (
    EmbeddingModelError,
    HybridSearchResult,
    VectorIndexUnavailableError,
)
from shared.database.error_handler import DatabaseConnectionError, UserNotFoundError
from shared.schemas.evidence import EvidenceItem
from shared.schemas.intent import Intent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_USER_ID = str(uuid4())


def _make_evidence(
    key: str = "test_key",
    tier: int = 2,
    confidence: float = 0.9,
    ev_type: str = "preference",
) -> EvidenceItem:
    return EvidenceItem(
        type=ev_type,
        key=key,
        value="test_value",
        confidence=confidence,
        source_ref=f"test:{key}",
        ttl_days=None,
        tier=tier,
    )


def _make_intent(**overrides) -> Intent:
    defaults = {
        "intent": "schedule_meeting",
        "entities": {"person": "Alice"},
        "constraints": {},
        "user_id": SAMPLE_USER_ID,
        "context_budget": 3,
    }
    defaults.update(overrides)
    return Intent(**defaults)


# ===================================================================
# Phase 1: Domain Model Tests (T101)
# ===================================================================


class TestContextResult:
    """Tests for ContextResult Pydantic model."""

    def test_context_result_defaults(self):
        """ContextResult() has empty evidence, 0 bytes, empty degraded, 0 duration."""
        result = ContextResult()
        assert result.evidence == []
        assert result.total_bytes == 0
        assert result.degraded_sources == []
        assert result.query_duration_ms == 0

    def test_context_result_with_evidence(self):
        """Construct with evidence list, verify fields."""
        items = [_make_evidence(key="k1"), _make_evidence(key="k2")]
        result = ContextResult(
            evidence=items,
            total_bytes=256,
            degraded_sources=["history"],
            query_duration_ms=42,
        )
        assert len(result.evidence) == 2
        assert result.total_bytes == 256
        assert result.degraded_sources == ["history"]
        assert result.query_duration_ms == 42

    def test_context_result_serialization(self):
        """model_dump() and model_dump_json() round-trip."""
        items = [_make_evidence(key="k1")]
        original = ContextResult(evidence=items, total_bytes=100)
        dumped = original.model_dump()
        restored = ContextResult.model_validate(dumped)
        assert restored.total_bytes == 100
        assert len(restored.evidence) == 1

        json_str = original.model_dump_json()
        assert "total_bytes" in json_str

    def test_context_result_total_bytes_validation(self):
        """Negative total_bytes raises ValidationError."""
        with pytest.raises(ValidationError):
            ContextResult(total_bytes=-1)

    def test_context_result_duration_validation(self):
        """Negative query_duration_ms raises ValidationError."""
        with pytest.raises(ValidationError):
            ContextResult(query_duration_ms=-5)


class TestSourceQueryError:
    """Tests for SourceQueryError domain error."""

    def test_source_query_error_fields(self):
        """SourceQueryError has correct source, reason, and str."""
        err = SourceQueryError("history", "timeout")
        assert err.source == "history"
        assert err.reason == "timeout"
        assert "history" in str(err)
        assert "timeout" in str(err)

    def test_source_query_error_is_contextrag_error(self):
        """SourceQueryError is a ContextRAGError subclass."""
        err = SourceQueryError("test", "reason")
        assert isinstance(err, ContextRAGError)
        assert isinstance(err, Exception)


# ===================================================================
# Phase 2: BudgetManager Tests (T201)
# ===================================================================


class TestBudgetEnforce:
    """Tests for BudgetManager.enforce_budget()."""

    def setup_method(self):
        self.bm = BudgetManager()

    def test_budget_empty_list(self):
        """Empty input returns ([], 0)."""
        result, total = self.bm.enforce_budget([])
        assert result == []
        assert total == 0

    def test_budget_single_item_within_budget(self):
        """Single small item is kept."""
        item = _make_evidence(key="small")
        result, total = self.bm.enforce_budget([item])
        assert len(result) == 1
        assert total > 0
        assert total <= BudgetManager.BUDGET_BYTES

    def test_budget_single_item_exceeds_budget(self):
        """Single item > 2048 bytes is excluded."""
        item = EvidenceItem(
            type="preference",
            key="huge_key",
            value="x" * 3000,
            confidence=1.0,
            source_ref="test:huge",
            ttl_days=None,
            tier=2,
        )
        result, total = self.bm.enforce_budget([item])
        assert result == []
        assert total == 0

    def test_budget_multiple_items_all_fit(self):
        """3 small items all fit, total_bytes <= 2048."""
        items = [_make_evidence(key=f"k{i}") for i in range(3)]
        result, total = self.bm.enforce_budget(items)
        assert len(result) == 3
        assert total <= BudgetManager.BUDGET_BYTES

    def test_budget_trim_when_exceeded(self):
        """Items are trimmed when total exceeds budget."""
        # Create items that each take ~200+ bytes. ~10 items should exceed 2048.
        items = [
            EvidenceItem(
                type="preference",
                key=f"key_{i:03d}",
                value=f"value_{'y' * 100}",
                confidence=0.9,
                source_ref=f"test:key_{i:03d}",
                ttl_days=None,
                tier=2,
            )
            for i in range(15)
        ]
        result, total = self.bm.enforce_budget(items)
        assert len(result) < 15
        assert total <= BudgetManager.BUDGET_BYTES

    def test_budget_tier_priority(self):
        """Tier 2 items kept before Tier 3 items."""
        tier3 = _make_evidence(key="t3", tier=3, confidence=1.0)
        tier2 = _make_evidence(key="t2", tier=2, confidence=0.5)
        result, _ = self.bm.enforce_budget([tier3, tier2])
        assert result[0].tier == 2
        assert result[1].tier == 3

    def test_budget_confidence_priority_within_tier(self):
        """Higher confidence items kept first within same tier."""
        low = _make_evidence(key="low", tier=2, confidence=0.3)
        high = _make_evidence(key="high", tier=2, confidence=0.9)
        result, _ = self.bm.enforce_budget([low, high])
        assert result[0].confidence == 0.9
        assert result[1].confidence == 0.3

    def test_budget_stable_sort(self):
        """Items with same tier+confidence maintain original order."""
        a = _make_evidence(key="aaa", tier=2, confidence=0.5)
        b = _make_evidence(key="bbb", tier=2, confidence=0.5)
        result, _ = self.bm.enforce_budget([a, b])
        assert result[0].key == "aaa"
        assert result[1].key == "bbb"

    def test_budget_hard_cap_2048(self):
        """Result total_bytes never exceeds 2048."""
        items = [
            EvidenceItem(
                type="preference",
                key=f"item_{i}",
                value=f"data_{'z' * 50}",
                confidence=float(i) / 100,
                source_ref=f"test:item_{i}",
                ttl_days=None,
                tier=2,
            )
            for i in range(50)
        ]
        _, total = self.bm.enforce_budget(items)
        assert total <= BudgetManager.BUDGET_BYTES

    def test_budget_returns_correct_total_bytes(self):
        """total_bytes matches sum of kept items."""
        items = [_make_evidence(key=f"k{i}") for i in range(3)]
        result, total = self.bm.enforce_budget(items)
        expected = sum(len(item.model_dump_json().encode("utf-8")) for item in result)
        assert total == expected


class TestBudgetDeduplicate:
    """Tests for BudgetManager.deduplicate()."""

    def setup_method(self):
        self.bm = BudgetManager()

    def test_dedup_no_duplicates(self):
        """All unique keys, list unchanged."""
        items = [_make_evidence(key=f"unique_{i}") for i in range(3)]
        result = self.bm.deduplicate(items)
        assert len(result) == 3

    def test_dedup_same_key_keeps_higher_confidence(self):
        """Two items key='k1' with conf 0.8 and 0.6, keeps 0.8."""
        low = _make_evidence(key="k1", confidence=0.6)
        high = _make_evidence(key="k1", confidence=0.8)
        result = self.bm.deduplicate([low, high])
        assert len(result) == 1
        assert result[0].confidence == 0.8

    def test_dedup_same_key_same_confidence_keeps_first(self):
        """Two items key='k1' with same conf, keeps first."""
        first = EvidenceItem(
            type="preference",
            key="k1",
            value="first_value",
            confidence=0.7,
            source_ref="test:first",
            tier=2,
        )
        second = EvidenceItem(
            type="preference",
            key="k1",
            value="second_value",
            confidence=0.7,
            source_ref="test:second",
            tier=2,
        )
        result = self.bm.deduplicate([first, second])
        assert len(result) == 1
        assert result[0].source_ref == "test:first"

    def test_dedup_empty_list(self):
        """Empty input returns empty list."""
        result = self.bm.deduplicate([])
        assert result == []

    def test_dedup_preserves_order(self):
        """After dedup, relative order of kept items preserved."""
        a = _make_evidence(key="aaa", confidence=0.9)
        b = _make_evidence(key="bbb", confidence=0.8)
        c = _make_evidence(key="ccc", confidence=0.7)
        result = self.bm.deduplicate([a, b, c])
        assert [r.key for r in result] == ["aaa", "bbb", "ccc"]


# ===================================================================
# Phase 3: Adapter Unit Tests (T304)
# ===================================================================


class TestProfileStoreAdapter:
    """Tests for ProfileStoreAdapter."""

    @pytest.fixture()
    def adapter(self):
        service = AsyncMock()
        service.get_all_preferences.return_value = [_make_evidence(key="pref1", tier=2)]
        return ProfileStoreAdapter(service), service

    async def test_profilestore_happy_path(self, adapter):
        a, _svc = adapter
        intent = _make_intent()
        result = await a.fetch_evidence(intent)
        assert len(result) == 1
        assert result[0].key == "pref1"

    async def test_profilestore_consent_denied(self):
        svc = AsyncMock()
        svc.get_all_preferences.side_effect = ConsentDeniedError(
            user_id=uuid4(), required_tier=2, current_tier=1
        )
        a = ProfileStoreAdapter(svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "consent_denied"

    async def test_profilestore_user_not_found(self):
        svc = AsyncMock()
        svc.get_all_preferences.side_effect = UserNotFoundError(uuid4())
        a = ProfileStoreAdapter(svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "user_not_found"

    async def test_profilestore_db_error(self):
        svc = AsyncMock()
        svc.get_all_preferences.side_effect = DatabaseConnectionError("down")
        a = ProfileStoreAdapter(svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "connection_error"


class TestHistoryAdapter:
    """Tests for HistoryAdapter."""

    def _make_adapter(self, facts_evidence=None, patterns=None):
        fact_svc = AsyncMock()
        pattern_svc = AsyncMock()

        if facts_evidence is None:
            facts_evidence = [
                {
                    "type": "history",
                    "key": "fact_1",
                    "value": "v1",
                    "confidence": 0.9,
                    "source_ref": "history:facts/1",
                    "ttl_days": 30,
                    "tier": 3,
                }
            ]

        fact_svc.get_facts_by_intent.return_value = QueryFactsResponse(
            evidence=facts_evidence,
            total_count=len(facts_evidence),
            returned_count=len(facts_evidence),
        )

        if patterns is None:
            patterns = [
                {
                    "pattern_id": str(uuid4()),
                    "pattern_key": "weekly_standup",
                    "pattern_description": "Recurring weekly meeting",
                    "confidence": 0.7,
                }
            ]

        pattern_svc.get_patterns.return_value = PatternsResponse(
            patterns=patterns,
            total_count=len(patterns),
        )

        return HistoryAdapter(fact_svc, pattern_svc), fact_svc, pattern_svc

    async def test_history_happy_path_facts_and_patterns(self):
        a, _, _ = self._make_adapter()
        result = await a.fetch_evidence(_make_intent())
        # 1 fact + 1 pattern = 2 items
        assert len(result) == 2
        types = {r.type for r in result}
        assert "history" in types

    async def test_history_invalid_fact_dict_dropped(self):
        """Invalid fact dict is dropped, valid ones kept."""
        facts = [
            {
                "type": "history",
                "key": "good",
                "value": "v",
                "confidence": 0.9,
                "source_ref": "history:facts/1",
                "tier": 3,
            },
            {"bad_field": "no_type"},  # invalid
        ]
        a, _, _ = self._make_adapter(facts_evidence=facts, patterns=[])
        result = await a.fetch_evidence(_make_intent())
        assert len(result) == 1
        assert result[0].key == "good"

    async def test_history_consent_required(self):
        fact_svc = AsyncMock()
        fact_svc.get_facts_by_intent.side_effect = ConsentRequiredError(
            user_id=uuid4(), current_tier=1
        )
        pattern_svc = AsyncMock()
        a = HistoryAdapter(fact_svc, pattern_svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "consent_required"

    async def test_history_pattern_conversion(self):
        """Pattern dict is correctly wrapped into EvidenceItem."""
        pid = str(uuid4())
        a, _, _ = self._make_adapter(
            facts_evidence=[],
            patterns=[
                {
                    "pattern_id": pid,
                    "pattern_key": "daily_scrum",
                    "pattern_description": "Daily standup at 9am",
                    "confidence": 0.85,
                }
            ],
        )
        result = await a.fetch_evidence(_make_intent())
        assert len(result) == 1
        item = result[0]
        assert item.type == "history"
        assert item.key == "daily_scrum"
        assert item.confidence == 0.85
        assert item.source_ref == f"history:patterns/{pid}"
        assert item.tier == 3
        assert item.ttl_days == 30

    async def test_history_storage_error(self):
        fact_svc = AsyncMock()
        fact_svc.get_facts_by_intent.side_effect = StorageError("disk full")
        pattern_svc = AsyncMock()
        a = HistoryAdapter(fact_svc, pattern_svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "storage_error"

    async def test_history_invalid_query(self):
        fact_svc = AsyncMock()
        fact_svc.get_facts_by_intent.side_effect = InvalidQueryError("bad")
        pattern_svc = AsyncMock()
        a = HistoryAdapter(fact_svc, pattern_svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "invalid_query"


class TestPlanLibraryAdapter:
    """Tests for PlanLibraryAdapter."""

    async def test_planlibrary_happy_path(self):
        svc = AsyncMock()
        svc.get_plans_by_intent.return_value = [_make_evidence(key="plan1", tier=3, ev_type="plan")]
        a = PlanLibraryAdapter(svc)
        result = await a.fetch_evidence(_make_intent())
        assert len(result) == 1
        assert result[0].key == "plan1"

    async def test_planlibrary_db_error(self):
        svc = AsyncMock()
        svc.get_plans_by_intent.side_effect = DatabaseConnectionError("down")
        a = PlanLibraryAdapter(svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "connection_error"

    async def test_planlibrary_invalid_query(self):
        from components.PlanLibrary.domain.models import (
            InvalidQueryError as PlanIQE,
        )

        svc = AsyncMock()
        svc.get_plans_by_intent.side_effect = PlanIQE("bad params")
        a = PlanLibraryAdapter(svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "invalid_query"


class TestVectorIndexAdapter:
    """Tests for VectorIndexAdapter."""

    async def test_vectorindex_happy_path(self):
        svc = AsyncMock()
        svc.search.return_value = [
            HybridSearchResult(
                plan_id="01HXYZ1234567890ABCDEFGHJK",
                intent_type="schedule_meeting",
                rrf_score=0.82,
                keyword_rank=1,
                semantic_rank=2,
            )
        ]
        a = VectorIndexAdapter(svc)
        result = await a.fetch_evidence(_make_intent())
        assert len(result) == 1
        assert result[0].type == "exemplar"
        assert result[0].tier == 3
        assert "01HXYZ12" in result[0].key

    async def test_vectorindex_service_none(self):
        """Service is None, returns empty list (no error)."""
        a = VectorIndexAdapter(None)
        result = await a.fetch_evidence(_make_intent())
        assert result == []

    async def test_vectorindex_unavailable(self):
        svc = AsyncMock()
        svc.search.side_effect = VectorIndexUnavailableError()
        a = VectorIndexAdapter(svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "unavailable"

    async def test_vectorindex_embedding_error(self):
        svc = AsyncMock()
        svc.search.side_effect = EmbeddingModelError("all-MiniLM", "corrupt")
        a = VectorIndexAdapter(svc)
        with pytest.raises(SourceQueryError) as exc_info:
            await a.fetch_evidence(_make_intent())
        assert exc_info.value.reason == "model_error"

    async def test_vectorindex_confidence_capped(self):
        """HybridSearchResult with rrf_score=1.5, confidence capped to 1.0."""
        svc = AsyncMock()
        svc.search.return_value = [
            HybridSearchResult(
                plan_id="01HXYZ1234567890ABCDEFGHJK",
                intent_type="schedule_meeting",
                rrf_score=1.5,
            )
        ]
        a = VectorIndexAdapter(svc)
        result = await a.fetch_evidence(_make_intent())
        assert result[0].confidence == 1.0
