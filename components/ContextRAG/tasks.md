# Tasks: ContextRAG

**Created**: 2026-03-26
**Branch**: `feat/contextrag`
**SPEC**: `specs/013-contextrag/spec.md`
**LLD**: `components/ContextRAG/LLD.md`

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
ContextRAG is a **library component** (no HTTP routes, no database tables) in the Domain/Service Layer.
It follows the same structural pattern as PlanWriter (`components/PlanWriter/`).

Total estimated tests: ~60 across 4 test files.

---

## Phase 0: Setup and Directory Scaffolding

### T000 -- Create directory structure and `__init__.py` files

Create all directories and empty `__init__.py` files for the component.

- [ ] [T000] Create the following files (all empty `__init__.py` except where noted):
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/domain/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/service/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/adapters/__init__.py`
  - `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/__init__.py`

**Verify**: All directories exist; `python -c "import components.ContextRAG"` succeeds.

### T001 -- Verify external dependency availability

No new Python packages required (LLD Section 10.1). Confirm existing dependencies are available.

- [ ] [T001] Verify the following imports resolve without error:
  - `from shared.schemas.intent import Intent`
  - `from shared.schemas.evidence import EvidenceItem`
  - `from shared.database.error_handler import UserNotFoundError, DatabaseConnectionError`
  - `from components.ProfileStore.domain.models import ConsentDeniedError`
  - `from components.History.domain.models import ConsentRequiredError, StorageError, InvalidQueryError, QueryFactsResponse, PatternsResponse`
  - `from components.PlanLibrary.domain.models import InvalidQueryError as PlanInvalidQueryError`
  - `from components.VectorIndex.domain.models import VectorIndexUnavailableError, EmbeddingModelError, HybridSearchResult`
  - `import asyncio, time, logging, uuid` (standard library)
  - `from pydantic import BaseModel, Field, ValidationError`

**Reference**: LLD Section 10, SPEC Section "Dependencies & Risks".

---

## Phase 1: Domain Models (Foundation)

### Acceptance Criteria: FR-001 (return ContextResult), FR-012 (never raise)

### T100 -- Create domain models

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/domain/models.py`

- [ ] [T100] Implement `ContextResult`, `ContextRAGError`, and `SourceQueryError` as specified in LLD Section 5.

  **`ContextResult`** (Pydantic BaseModel):
  - `evidence: list[EvidenceItem]` -- default_factory=list, description: "Budget-trimmed, tier-sorted evidence items"
  - `total_bytes: int` -- default=0, ge=0, description: "Total serialized size of evidence in bytes"
  - `degraded_sources: list[str]` -- default_factory=list, description: "Sources that failed"
  - `query_duration_ms: int` -- default=0, ge=0, description: "Total wall-clock time for gather_evidence() in ms"

  **`ContextRAGError`** (Exception):
  - Base error class for ContextRAG. No additional fields.

  **`SourceQueryError`** (ContextRAGError):
  - `source: str` -- source name (e.g., "profilestore", "history")
  - `reason: str` -- failure reason
  - `__init__(self, source: str, reason: str)` with message `f"Source '{source}' failed: {reason}"`

  **Imports**: `from shared.schemas.evidence import EvidenceItem`

  **Pattern reference**: Follow `components/PlanWriter/domain/models.py` structure.

### T101 -- Write domain model unit tests

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/test_unit.py` (initial section)

- [ ] [T101] Write tests for domain models (~6 tests):
  1. `test_context_result_defaults` -- `ContextResult()` has empty evidence, 0 bytes, empty degraded_sources, 0 duration
  2. `test_context_result_with_evidence` -- construct with evidence list, verify fields
  3. `test_context_result_serialization` -- `model_dump()` and `model_dump_json()` round-trip
  4. `test_source_query_error_fields` -- `SourceQueryError("history", "timeout")` has correct source, reason, str
  5. `test_source_query_error_is_contextrag_error` -- `isinstance(SourceQueryError(...), ContextRAGError)` is True
  6. `test_context_result_total_bytes_validation` -- negative value raises ValidationError

**Run**: `pytest components/ContextRAG/tests/test_unit.py -k "test_context_result or test_source_query"` -- expect RED initially, GREEN after T100.

---

## Phase 2: Budget Manager (Core Logic)

### Acceptance Criteria: FR-007 (2048-byte hard budget), FR-008 (tier+confidence prioritization)

### T200 -- Implement BudgetManager

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/adapters/budget_manager.py`

- [ ] [T200] Implement `BudgetManager` as specified in LLD Section 6.6.

  **Class**: `BudgetManager`
  - Class constant: `BUDGET_BYTES: int = 2048`

  **Method `enforce_budget(evidence: list[EvidenceItem]) -> tuple[list[EvidenceItem], int]`**:
  - Sort by priority: tier ASC (Tier 2 before Tier 3), then confidence DESC within same tier
  - Use stable sort to preserve insertion order for equal tier+confidence
  - Measure each item: `len(item.model_dump_json().encode("utf-8"))`
  - Greedy addition: add items left-to-right until next item would exceed BUDGET_BYTES
  - Return `(trimmed_list, total_bytes)`

  **Method `deduplicate(evidence: list[EvidenceItem]) -> list[EvidenceItem]`**:
  - Group by `item.key`
  - When two items share the same key, keep the one with higher `confidence`
  - If confidence is equal, keep the first encountered
  - Return deduplicated list

  **Imports**: `from shared.schemas.evidence import EvidenceItem`

### T201 -- Write BudgetManager unit tests

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/test_unit.py` (append)

- [ ] [T201] Write tests for BudgetManager (~15 tests):

  **enforce_budget tests:**
  1. `test_budget_empty_list` -- empty input returns ([], 0)
  2. `test_budget_single_item_within_budget` -- single small item is kept
  3. `test_budget_single_item_exceeds_budget` -- single item > 2048 bytes is excluded, returns ([], 0)
  4. `test_budget_multiple_items_all_fit` -- 3 small items all fit, total_bytes <= 2048
  5. `test_budget_trim_when_exceeded` -- 5 items totaling ~5KB, only first N fit within 2048
  6. `test_budget_tier_priority` -- Tier 2 items kept before Tier 3 items
  7. `test_budget_confidence_priority_within_tier` -- higher confidence items kept first within same tier
  8. `test_budget_stable_sort` -- items with same tier+confidence maintain original order
  9. `test_budget_hard_cap_2048` -- result total_bytes never exceeds 2048
  10. `test_budget_returns_correct_total_bytes` -- total_bytes matches sum of kept items

  **deduplicate tests:**
  11. `test_dedup_no_duplicates` -- all unique keys, list unchanged
  12. `test_dedup_same_key_keeps_higher_confidence` -- two items key="k1" with conf 0.8 and 0.6, keeps 0.8
  13. `test_dedup_same_key_same_confidence_keeps_first` -- two items key="k1" with same conf, keeps first
  14. `test_dedup_empty_list` -- empty input returns empty list
  15. `test_dedup_preserves_order` -- after dedup, relative order of kept items preserved

**Run**: `pytest components/ContextRAG/tests/test_unit.py -k "test_budget or test_dedup"` -- expect RED, GREEN after T200.

---

## Phase 3: Source Adapters (Memory Layer Integration)

### Acceptance Criteria: FR-002 (ProfileStore), FR-003 (History), FR-004 (PlanLibrary), FR-005 (VectorIndex), FR-006 (tier pre-check), FR-010 (concurrent queries), FR-012 (catch all errors), FR-013 (convert History dicts), FR-014 (convert VectorIndex results)

### T300 -- Implement ProfileStore adapter

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/adapters/profilestore_adapter.py`

- [ ] [T300] Implement `ProfileStoreAdapter` as specified in LLD Section 6.2.

  **Class attributes:**
  - `source_name = "profilestore"`
  - `required_tier = 2`
  - `default_timeout = 0.1` (100ms)

  **Constructor**: `__init__(self, preference_service: Any) -> None`
  - Store `self._service = preference_service`

  **Method `fetch_evidence(self, intent: Intent, timeout_s: float = 0.1) -> list[EvidenceItem]`**:
  - Call `self._service.get_all_preferences(user_id=UUID(intent.user_id), context_tier=intent.context_budget or 3)`
  - Return the result (already `list[EvidenceItem]`)
  - Catch errors and convert to `SourceQueryError("profilestore", reason)`:
    - `ConsentDeniedError` from `components.ProfileStore.domain.models` -> reason: `"consent_denied"`
    - `UserNotFoundError` from `shared.database.error_handler` -> reason: `"user_not_found"`
    - `DatabaseConnectionError` from `shared.database.error_handler` -> reason: `"connection_error"`
    - `Exception` (catch-all) -> log warning, reason: `f"unexpected: {type(e).__name__}"`

### T301 -- Implement History adapter

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/adapters/history_adapter.py`

- [ ] [T301] Implement `HistoryAdapter` as specified in LLD Section 6.3.

  **Class attributes:**
  - `source_name = "history"`
  - `required_tier = 3`
  - `default_timeout = 0.1` (100ms)

  **Constructor**: `__init__(self, fact_service: Any, pattern_service: Any) -> None`

  **Method `fetch_evidence(self, intent: Intent, timeout_s: float = 0.1) -> list[EvidenceItem]`**:
  - Call `self._fact_service.get_facts_by_intent(user_id=UUID(intent.user_id), intent_type=intent.intent, limit=20, recency_days=30)` -> `QueryFactsResponse`
  - Convert `response.evidence` dicts to `EvidenceItem` via `EvidenceItem.model_validate(item)` per item
  - Drop items that fail `ValidationError` with a warning log (per-item, not adapter-level)
  - Call `self._pattern_service.get_patterns(user_id=UUID(intent.user_id), intent_type=intent.intent, min_confidence=0.5)` -> `PatternsResponse`
  - Convert each pattern dict to `EvidenceItem(type="history", key=pattern["pattern_key"], value=pattern["pattern_description"], confidence=pattern["confidence"], source_ref=f"history:patterns/{pattern['pattern_id']}", ttl_days=30, tier=3)`
  - Return combined list
  - Catch errors and convert to `SourceQueryError("history", reason)`:
    - `ConsentRequiredError` from `components.History.domain.models` -> reason: `"consent_required"`
    - `StorageError` from `components.History.domain.models` -> reason: `"storage_error"`
    - `InvalidQueryError` from `components.History.domain.models` -> reason: `"invalid_query"`
    - `DatabaseConnectionError` from `shared.database.error_handler` -> reason: `"connection_error"`
    - `Exception` (catch-all) -> log warning, reason: `f"unexpected: {type(e).__name__}"`

### T302 -- Implement PlanLibrary adapter

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/adapters/planlibrary_adapter.py`

- [ ] [T302] Implement `PlanLibraryAdapter` as specified in LLD Section 6.4.

  **Class attributes:**
  - `source_name = "planlibrary"`
  - `required_tier = 3`
  - `default_timeout = 0.1` (100ms)

  **Constructor**: `__init__(self, plan_service: Any) -> None`

  **Method `fetch_evidence(self, intent: Intent, timeout_s: float = 0.1) -> list[EvidenceItem]`**:
  - Call `self._service.get_plans_by_intent(intent_type=intent.intent, success_threshold=0.7, limit=5, recency_days=90)`
  - Return the result (already `list[EvidenceItem]`)
  - Catch errors and convert to `SourceQueryError("planlibrary", reason)`:
    - `InvalidQueryError` from `components.PlanLibrary.domain.models` -> reason: `"invalid_query"`
    - `DatabaseConnectionError` from `shared.database.error_handler` -> reason: `"connection_error"`
    - `Exception` (catch-all) -> log warning, reason: `f"unexpected: {type(e).__name__}"`

  **Note**: PlanLibrary does NOT enforce consent tiers -- always queryable when `context_budget >= 3`.

### T303 -- Implement VectorIndex adapter

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/adapters/vectorindex_adapter.py`

- [ ] [T303] Implement `VectorIndexAdapter` as specified in LLD Section 6.5.

  **Class attributes:**
  - `source_name = "vectorindex"`
  - `required_tier = 3`
  - `default_timeout = 0.05` (50ms -- aggressive timeout per LLD)

  **Constructor**: `__init__(self, vector_index_service: Any | None) -> None`
  - Store `self._service = vector_index_service`

  **Method `fetch_evidence(self, intent: Intent, timeout_s: float = 0.05) -> list[EvidenceItem]`**:
  - If `self._service is None`: return `[]` immediately (no error, no degradation entry)
  - Build query: `query_text = f"{intent.intent} {' '.join(str(v) for v in intent.entities.values())}"`
  - Call `self._service.search(query_text=query_text, intent_type=intent.intent, top_k=3)` -> `list[HybridSearchResult]`
  - Convert each result to:
    ```python
    EvidenceItem(
        type="exemplar",
        key=f"similar_plan_{result.plan_id[:8]}",
        value={"plan_id": result.plan_id, "rrf_score": result.rrf_score},
        confidence=min(result.rrf_score, 1.0),
        source_ref=f"vectorindex:search/{result.plan_id}",
        ttl_days=None,
        tier=3,
    )
    ```
  - Catch errors and convert to `SourceQueryError("vectorindex", reason)`:
    - `VectorIndexUnavailableError` from `components.VectorIndex.domain.models` -> reason: `"unavailable"`
    - `EmbeddingModelError` from `components.VectorIndex.domain.models` -> reason: `"model_error"`
    - `ValueError` -> reason: `"invalid_query"`
    - `DatabaseConnectionError` from `shared.database.error_handler` -> reason: `"connection_error"`
    - `Exception` (catch-all) -> log warning, reason: `f"unexpected: {type(e).__name__}"`

### T304 -- Write adapter unit tests

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/test_unit.py` (append)

- [ ] [T304] Write unit tests for all 4 adapters (~15 tests):

  **ProfileStore adapter tests:**
  1. `test_profilestore_happy_path` -- mock returns list[EvidenceItem], adapter returns same
  2. `test_profilestore_consent_denied` -- mock raises ConsentDeniedError, adapter raises SourceQueryError with reason "consent_denied"
  3. `test_profilestore_user_not_found` -- mock raises UserNotFoundError, adapter raises SourceQueryError
  4. `test_profilestore_db_error` -- mock raises DatabaseConnectionError, adapter raises SourceQueryError

  **History adapter tests:**
  5. `test_history_happy_path_facts_and_patterns` -- mock fact_service returns QueryFactsResponse, pattern_service returns PatternsResponse, adapter returns combined EvidenceItem list
  6. `test_history_invalid_fact_dict_dropped` -- one dict fails model_validate, it is dropped, others kept
  7. `test_history_consent_required` -- mock raises ConsentRequiredError, adapter raises SourceQueryError
  8. `test_history_pattern_conversion` -- verify pattern dict is correctly wrapped into EvidenceItem with type="history", correct source_ref format

  **PlanLibrary adapter tests:**
  9. `test_planlibrary_happy_path` -- mock returns list[EvidenceItem], adapter returns same
  10. `test_planlibrary_db_error` -- mock raises DatabaseConnectionError, adapter raises SourceQueryError

  **VectorIndex adapter tests:**
  11. `test_vectorindex_happy_path` -- mock returns list[HybridSearchResult], adapter converts to EvidenceItem type="exemplar"
  12. `test_vectorindex_service_none` -- service is None, returns empty list (no error)
  13. `test_vectorindex_unavailable` -- mock raises VectorIndexUnavailableError, adapter raises SourceQueryError
  14. `test_vectorindex_embedding_error` -- mock raises EmbeddingModelError, adapter raises SourceQueryError
  15. `test_vectorindex_confidence_capped` -- HybridSearchResult with rrf_score=1.5, confidence capped to 1.0

**Run**: `pytest components/ContextRAG/tests/test_unit.py -k "test_profilestore or test_history or test_planlibrary or test_vectorindex"` -- expect RED, GREEN after T300-T303.

---

## Phase 4: Service Layer (Orchestration)

### Acceptance Criteria: FR-001 (Intent -> ContextResult), FR-006 (tier pre-check), FR-009 (read-only), FR-010 (asyncio.gather), FR-011 (source_ref and tier on all items), FR-012 (never raise)

### T400 -- Implement ContextRAGService

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/service/context_rag_service.py`

- [ ] [T400] Implement `ContextRAGService` and `create_context_rag_service()` as specified in LLD Sections 4.1, 7.1, and 9.2.

  **Class `ContextRAGService`**:

  **Constructor:**
  ```python
  def __init__(
      self,
      preference_service: Any,
      fact_service: Any,
      pattern_service: Any,
      plan_service: Any,
      vector_index_service: Any | None,
  ) -> None:
  ```
  - Create internal adapter instances:
    - `self._profilestore_adapter = ProfileStoreAdapter(preference_service)`
    - `self._history_adapter = HistoryAdapter(fact_service, pattern_service)`
    - `self._planlibrary_adapter = PlanLibraryAdapter(plan_service)`
    - `self._vectorindex_adapter = VectorIndexAdapter(vector_index_service)`
  - Create `self._budget_manager = BudgetManager()`

  **Method `async def gather_evidence(self, intent: Intent) -> ContextResult`:**

  Follow LLD Section 7.1 flow exactly:

  1. `start = time.monotonic()`
  2. `effective_budget = intent.context_budget or 3` (default Tier 3)
  3. Determine eligible sources based on tier:
     - If `effective_budget >= 2`: include ProfileStore adapter
     - If `effective_budget >= 3`: include History, PlanLibrary adapters; if VectorIndex adapter has service (not None), include it too
  4. If no sources (Tier 1 early return): return `ContextResult(query_duration_ms=_elapsed_ms(start))`
  5. Concurrent fetch via `asyncio.gather(*[asyncio.wait_for(adapter.fetch_evidence(intent, adapter.default_timeout), timeout=adapter.default_timeout) for adapter in sources], return_exceptions=True)`
  6. Iterate results: if `SourceQueryError` or `BaseException`, add adapter.source_name to `degraded` list and log warning; else `all_evidence.extend(result)`
  7. Deduplicate: `all_evidence = self._budget_manager.deduplicate(all_evidence)`
  8. Budget enforce: `trimmed, total_bytes = self._budget_manager.enforce_budget(all_evidence)`
  9. Return `ContextResult(evidence=trimmed, total_bytes=total_bytes, degraded_sources=degraded, query_duration_ms=_elapsed_ms(start))`

  **Helper**: `def _elapsed_ms(start: float) -> int: return int((time.monotonic() - start) * 1000)`

  **Factory function `create_context_rag_service(...) -> ContextRAGService`:**
  - Accept same params as constructor
  - Log `"context_rag_service_created"` with `vectorindex_available` status
  - Return `ContextRAGService(...)`

  **Logging**: Use `logger = logging.getLogger("contextrag")`. Log:
  - `"gather_evidence_start"` with intent_type, user_id, effective_budget
  - `"source_degraded"` per failed source (warning) with source, reason, intent_type
  - `"gather_evidence_complete"` with evidence_count, total_bytes, degraded_sources, duration_ms
  - Never log intent.entities values, intent.constraints values, or EvidenceItem.value contents

### T401 -- Write conftest.py with shared fixtures

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/conftest.py`

- [ ] [T401] Create shared test fixtures:

  **Mock services** (using `unittest.mock.AsyncMock`):
  - `mock_preference_service` -- `get_all_preferences` returns list of 2 sample `EvidenceItem(type="preference", tier=2, confidence=1.0)`
  - `mock_fact_service` -- `get_facts_by_intent` returns `QueryFactsResponse(evidence=[...2 valid dicts...], total_count=2, returned_count=2)`
  - `mock_pattern_service` -- `get_patterns` returns `PatternsResponse(patterns=[...1 valid pattern dict...], total_count=1)`
  - `mock_plan_service` -- `get_plans_by_intent` returns list of 2 sample `EvidenceItem(type="plan", tier=3)`
  - `mock_vector_index_service` -- `search` returns list of 2 `HybridSearchResult` objects

  **Sample data:**
  - `SAMPLE_USER_ID` -- valid UUID string
  - `SAMPLE_INTENT` -- `Intent(intent="schedule_meeting", entities={"person": "Alice"}, constraints={}, user_id=SAMPLE_USER_ID, context_budget=3)`
  - `SAMPLE_TIER2_INTENT` -- same but `context_budget=2`
  - `SAMPLE_TIER1_INTENT` -- same but `context_budget=1`

  **Composite fixtures:**
  - `context_rag_service` -- `ContextRAGService` with all mocked services
  - `context_rag_service_no_vectorindex` -- same but vector_index_service=None

  **Pattern reference**: Follow `components/PlanWriter/tests/conftest.py` structure.

### T402 -- Write service-level tests

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/test_service.py`

- [ ] [T402] Write service-level tests for `gather_evidence()` (~15 tests):

  **Happy path:**
  1. `test_gather_evidence_happy_path` -- all sources succeed, returns ContextResult with evidence from all sources, degraded_sources empty
  2. `test_gather_evidence_evidence_types` -- verify returned evidence includes types: "preference", "history", "plan", "exemplar"
  3. `test_gather_evidence_sorted_by_tier_then_confidence` -- Tier 2 items before Tier 3 in result
  4. `test_gather_evidence_within_budget` -- total_bytes <= 2048
  5. `test_gather_evidence_duration_ms_positive` -- query_duration_ms > 0

  **Tier enforcement (FR-006):**
  6. `test_tier1_returns_empty` -- context_budget=1, returns empty evidence (Tier 1 = session only, no Memory Layer queries)
  7. `test_tier2_only_profilestore` -- context_budget=2, only ProfileStore queried, History/PlanLibrary/VectorIndex not called
  8. `test_tier3_all_sources` -- context_budget=3 (default), all sources queried
  9. `test_none_budget_defaults_to_3` -- context_budget=None, all sources queried

  **Graceful degradation (FR-012):**
  10. `test_single_source_failure` -- History raises DatabaseConnectionError, result has evidence from other sources, degraded_sources=["history"]
  11. `test_all_sources_fail` -- all 4 raise errors, returns empty evidence, degraded_sources has all 4 names
  12. `test_vectorindex_none_not_degraded` -- VectorIndex service is None, NOT added to degraded_sources
  13. `test_timeout_adds_to_degraded` -- source raises asyncio.TimeoutError, added to degraded_sources
  14. `test_consent_denied_adds_to_degraded` -- ProfileStore raises ConsentDeniedError (via adapter -> SourceQueryError), added to degraded_sources

  **Concurrent execution (FR-010):**
  15. `test_sources_called_concurrently` -- verify all source adapters' fetch_evidence is called (not blocked by other source failures)

**Run**: `pytest components/ContextRAG/tests/test_service.py` -- expect RED, GREEN after T400.

---

## Phase 5: Contract Tests and Schema Compliance

### Acceptance Criteria: SC-001 to SC-005 (all success criteria), FR-011 (source_ref + tier on all items)

### T500 -- Write contract tests

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/test_contract.py`

- [ ] [T500] Write contract tests verifying schema compliance and invariants (~10 tests):

  **EvidenceItem schema compliance (SC-004):**
  1. `test_all_evidence_items_pass_model_validate` -- every item in result.evidence passes `EvidenceItem.model_validate(item.model_dump())`
  2. `test_evidence_items_have_source_ref` -- every item has non-empty `source_ref` (FR-011)
  3. `test_evidence_items_have_tier` -- every item has `tier` in {1, 2, 3, 4} (FR-011)
  4. `test_evidence_confidence_in_range` -- every item has `0.0 <= confidence <= 1.0`
  5. `test_evidence_type_is_valid` -- every item has type in {"preference", "history", "contact", "plan", "exemplar"}

  **ContextResult invariants:**
  6. `test_context_result_never_none` -- `gather_evidence()` always returns ContextResult, never None
  7. `test_context_result_evidence_is_list` -- `result.evidence` is always a list (possibly empty)
  8. `test_budget_hard_cap` -- `result.total_bytes <= 2048` for all test scenarios (SC-002)
  9. `test_tier_enforcement_contract` -- with context_budget=2, no Tier 3 items in result (SC-005)

  **Intent -> ContextResult flow:**
  10. `test_intent_to_context_result_flow` -- construct valid Intent, call gather_evidence(), verify ContextResult shape matches LLD Section 5.1

**Run**: `pytest components/ContextRAG/tests/test_contract.py` -- expect RED, GREEN after Phase 4.

---

## Phase 6: Observability and Safety

### From LLD Section 11, SPEC "Observability", constitution rules

### T600 -- Write observability tests

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/tests/test_observability.py`

- [ ] [T600] Write observability and safety tests (~5 tests):

  1. `test_no_pii_in_logs` -- call `gather_evidence()` with intent containing `entities={"person": "Alice", "ssn": "123-45-6789"}`, capture log output, verify neither "Alice" nor "123-45-6789" nor any entity values appear in logs. Only keys ("person", "ssn") and metadata (intent_type, user_id UUID, counts) should be logged.
  2. `test_log_contains_intent_type` -- verify "schedule_meeting" (intent_type, which is safe) appears in structured log
  3. `test_log_contains_duration_ms` -- verify duration_ms field is logged on completion
  4. `test_log_degraded_source_warning` -- when a source fails, verify a warning-level log with source name and reason is emitted
  5. `test_log_correlation_fields` -- verify user_id and trace_id from intent appear in log records

**Run**: `pytest components/ContextRAG/tests/test_observability.py` -- expect RED, GREEN after Phase 4.

---

## Phase 7: DI Wiring (Shared Infrastructure Integration)

### From LLD Section 9.1, 9.2

### T700 -- Add ContextRAG to shared/app.py lifespan

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`

- [ ] [T700] Add ContextRAG service initialization to the lifespan function, after PlanWriter initialization (line ~119).

  Add the following block:
  ```python
  # ContextRAG service (library -- no routes)
  from components.ContextRAG.service.context_rag_service import (
      create_context_rag_service,
  )

  app.state.context_rag_service = create_context_rag_service(
      preference_service=app.state.preference_service,
      fact_service=app.state.fact_service,
      pattern_service=app.state.pattern_service,
      plan_service=app.state.plan_service,
      vector_index_service=app.state.vector_index_service,
  )
  ```

  **Dependencies required on app.state before this block:**
  - `preference_service` (from ProfileStore init)
  - `fact_service` (from History init)
  - `pattern_service` (from History init)
  - `plan_service` (from PlanLibrary init)
  - `vector_index_service` (from VectorIndex init, may be None)

  All of these are already initialized earlier in the lifespan. Place the ContextRAG block after all of them.

### T701 -- Add ContextRAG dependency to shared/dependencies.py

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py`

- [ ] [T701] Add the `get_context_rag_service` dependency function:

  ```python
  def get_context_rag_service(request: Request) -> Any:
      """Get ContextRAGService singleton from app state."""
      return request.app.state.context_rag_service
  ```

  Place after the existing `get_plan_writer_service` function.

### T702 -- Verify DI wiring

- [ ] [T702] Write a quick smoke test or manually verify:
  - Application starts without import errors
  - `app.state.context_rag_service` is a `ContextRAGService` instance
  - The service has non-None adapters for ProfileStore, History, PlanLibrary
  - The VectorIndex adapter may have None service (graceful degradation)

**Run**: `python -c "from shared.app import create_app; app = create_app(); print('OK')"` -- verify no import errors.

---

## Phase 8: Adapter __init__.py Exports

### T800 -- Set up adapter module exports

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/adapters/__init__.py`

- [ ] [T800] Export all adapter classes and BudgetManager from `adapters/__init__.py`:

  ```python
  from .budget_manager import BudgetManager
  from .history_adapter import HistoryAdapter
  from .planlibrary_adapter import PlanLibraryAdapter
  from .profilestore_adapter import ProfileStoreAdapter
  from .vectorindex_adapter import VectorIndexAdapter

  __all__ = [
      "BudgetManager",
      "HistoryAdapter",
      "PlanLibraryAdapter",
      "ProfileStoreAdapter",
      "VectorIndexAdapter",
  ]
  ```

### T801 -- Set up domain module exports

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/domain/__init__.py`

- [ ] [T801] Export domain models:

  ```python
  from .models import ContextRAGError, ContextResult, SourceQueryError

  __all__ = ["ContextRAGError", "ContextResult", "SourceQueryError"]
  ```

### T802 -- Set up service module exports

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/service/__init__.py`

- [ ] [T802] Export service class and factory:

  ```python
  from .context_rag_service import ContextRAGService, create_context_rag_service

  __all__ = ["ContextRAGService", "create_context_rag_service"]
  ```

### T803 -- Set up top-level component export

**File**: `/Users/anantshreechandola/Desktop/Personal-agent/components/ContextRAG/__init__.py`

- [ ] [T803] Export key public API:

  ```python
  from .domain.models import ContextRAGError, ContextResult, SourceQueryError
  from .service.context_rag_service import ContextRAGService, create_context_rag_service

  __all__ = [
      "ContextRAGError",
      "ContextResult",
      "ContextRAGService",
      "SourceQueryError",
      "create_context_rag_service",
  ]
  ```

---

## Phase 9: Final Verification

### T900 -- Run full test suite

- [ ] [T900] Run all ContextRAG tests and verify they pass:
  ```
  pytest components/ContextRAG/tests/ -v --tb=short
  ```
  - Expected: ~60 tests passing
  - Coverage target: > 80% for all ContextRAG source files

### T901 -- Run linting and type checking

- [ ] [T901] Verify ruff and mypy pass:
  ```
  ruff check components/ContextRAG/ --fix
  ruff format components/ContextRAG/
  mypy components/ContextRAG/ --strict
  ```

### T902 -- Run existing test suite (regression check)

- [ ] [T902] Verify no regressions in existing tests:
  ```
  pytest tests/ -v --tb=short
  pytest components/PlanWriter/tests/ -v --tb=short
  pytest components/ProfileStore/tests/ -v --tb=short
  pytest components/History/tests/ -v --tb=short
  ```

### T903 -- Verify CI gates

- [ ] [T903] Push branch `feat/contextrag` and verify CI passes:
  - All tests pass
  - Ruff check clean
  - Mypy clean
  - Coverage meets threshold

---

## Task Summary

| Phase | Tasks | IDs | Description |
|-------|-------|-----|-------------|
| Phase 0: Setup | 2 | T000-T001 | Directory scaffolding, dependency verification |
| Phase 1: Domain | 2 | T100-T101 | ContextResult, ContextRAGError, SourceQueryError + tests |
| Phase 2: Budget | 2 | T200-T201 | BudgetManager (enforce_budget, deduplicate) + tests |
| Phase 3: Adapters | 5 | T300-T304 | 4 source adapters + adapter unit tests |
| Phase 4: Service | 3 | T400-T402 | ContextRAGService, conftest.py, service tests |
| Phase 5: Contract | 1 | T500 | Contract tests (schema compliance, invariants) |
| Phase 6: Observability | 1 | T600 | Observability/safety tests (no PII, logging) |
| Phase 7: DI Wiring | 3 | T700-T702 | shared/app.py + shared/dependencies.py + smoke test |
| Phase 8: Exports | 4 | T800-T803 | Module `__init__.py` exports |
| Phase 9: Verification | 4 | T900-T903 | Full test run, lint, regression, CI |
| **Total** | **27** | | |

---

## Dependencies

### External (from LLD Section 10.1)

No new Python packages required. ContextRAG uses only:
- Standard library: `asyncio`, `time`, `logging`, `uuid`
- Existing project dependency: `pydantic` (v2)
- Test dependencies: `pytest`, `pytest-asyncio`, `unittest.mock.AsyncMock`

### Internal (from LLD Section 10.2)

| Component | Service Class | Required | Tier | Already in app.state |
|-----------|--------------|----------|------|---------------------|
| ProfileStore | `PreferenceService` | Yes | 2 | `app.state.preference_service` |
| History | `FactService` | Yes | 3 | `app.state.fact_service` |
| History | `PatternService` | Yes | 3 | `app.state.pattern_service` |
| PlanLibrary | `PlanService` | Yes | 3 | `app.state.plan_service` |
| VectorIndex | `VectorIndexService` | No | 3 | `app.state.vector_index_service` (may be None) |

### Shared Infrastructure

| Module | Usage |
|--------|-------|
| `shared/schemas/intent.py` | `Intent` model (input) |
| `shared/schemas/evidence.py` | `EvidenceItem` model (output) |
| `shared/database/error_handler.py` | `UserNotFoundError`, `DatabaseConnectionError` |
| `shared/app.py` | DI wiring via lifespan (modified in T700) |
| `shared/dependencies.py` | `get_context_rag_service` (added in T701) |

---

## Architectural Considerations

### Blast Radius (from LLD Section 3.2)

- **If ContextRAG crashes**: Planner receives no evidence, generates a generic plan. This is degraded but not fatal.
- **If a single source times out**: Partial evidence returned. The `degraded_sources` list tells the Planner which data is missing.
- **If all sources are down**: Empty `ContextResult` returned. Never raises.
- **Containment**: Per-source `asyncio.wait_for` timeouts (100ms/50ms), `return_exceptions=True` in `asyncio.gather`, adapter-level try/except converting all errors to `SourceQueryError`.

### Determinism (from LLD Section 13.2)

- **Tier filtering**: Deterministic (pre-check `context_budget` before querying).
- **Deduplication**: Deterministic (keep highest confidence per key).
- **Budget sorting**: Deterministic (tier ASC, confidence DESC, stable sort).
- **Budget trimming**: Deterministic (greedy left-to-right after sort).
- Same Memory Layer state + same Intent = same `ContextResult.evidence` list.

### State Management (from LLD Section 13.3)

- Fully stateless. No instance-level caches, no request-scoped state, no background tasks.
- Each `gather_evidence()` call is independent and thread-safe.
- No mutations to any Memory Layer data (read-only, FR-009).

### Performance Targets (from LLD Section 12.1, GLOBAL_SPEC Section 3)

- `gather_evidence()` p95 < 150ms (cloud), < 200ms (local)
- Per-source timeout: 100ms (ProfileStore, History, PlanLibrary)
- VectorIndex timeout: 50ms (aggressive, optional source)

---

## Implementation Order (Critical Path)

```
T000 (scaffolding)
  |
  v
T100 (domain models) --> T101 (domain tests)
  |
  v
T200 (budget manager) --> T201 (budget tests)
  |
  v
T300-T303 (4 adapters, can be parallel) --> T304 (adapter tests)
  |
  v
T401 (conftest) --> T400 (service) --> T402 (service tests)
  |
  v
T500 (contract tests) + T600 (observability tests)  [parallel]
  |
  v
T800-T803 (exports)  [can be done alongside Phase 5-6]
  |
  v
T700-T702 (DI wiring)
  |
  v
T900-T903 (verification)
```
