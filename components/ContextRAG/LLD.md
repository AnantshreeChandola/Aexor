# ContextRAG — Low-Level Design

## 1. Purpose & Scope

ContextRAG is a **context assembler** in the **Domain/Service Layer** that gathers relevant, typed evidence from Memory Layer components to support deterministic plan generation. It is a **library component** (no HTTP routes, no database ownership) consumed via dependency injection by the Planner.

**Responsibilities:**
- Accept an `Intent` (GLOBAL_SPEC §2.1) and return a budget-constrained `list[EvidenceItem]` (GLOBAL_SPEC §2.2)
- Query ProfileStore, History, PlanLibrary concurrently; optionally query VectorIndex
- Enforce context tier ceilings from `intent.context_budget`
- Enforce a 2048-byte hard budget on serialized evidence output
- Degrade gracefully when individual sources are unavailable

**Boundaries:**
- Read-only — never mutates Memory Layer data
- Stateless — no caching across calls, no database ownership
- No LLM calls — pure structured query assembly
- No HTTP routes — consumed via DI only

---

## 2. Conformance

- GLOBAL_SPEC.md **v2.2** — §2.1 Intent, §2.2 Evidence Item, §7 Context Policy
- MODULAR_ARCHITECTURE.md **v1.3** — §4 Component Dependencies (ContextRAG row)
- Project_HLD.md **v4.0** — §1 Layer 2 Domain Services, §2 Step 1 Understanding

---

## 3. Architecture Overview

### 3.1 Layer Placement

Domain/Service Layer — stateless, no database ownership, no Redis. Queries Memory Layer services via DI.

### 3.2 Blast Radius Analysis

| Failure mode | Impact | Mitigation |
|---|---|---|
| ContextRAG crashes | Planner receives no evidence → generates generic plan | Planner handles empty evidence |
| Single source timeout | Partial evidence returned | `asyncio.wait_for` per source, `degraded_sources` list |
| All sources down | Empty `ContextResult` returned | Never raises — returns empty evidence |
| Budget calculation bug | Oversized context passed to LLM | Hard cap enforced at serialization boundary |

### 3.3 Component Boundaries

```
                    ┌──────────────────────────┐
                    │        Planner           │
                    │  gather_evidence(intent)  │
                    └────────────┬─────────────┘
                                 │ Intent
                                 ▼
                    ┌──────────────────────────┐
                    │      ContextRAGService    │
                    │                          │
                    │  1. Tier pre-check        │
                    │  2. Concurrent queries     │
                    │  3. Convert & validate     │
                    │  4. Deduplicate           │
                    │  5. Budget trim           │
                    └──┬───┬───┬───┬───────────┘
                       │   │   │   │
            ┌──────────┘   │   │   └──────────┐
            ▼              ▼   ▼              ▼
     ┌────────────┐  ┌────────┐ ┌──────────┐ ┌───────────┐
     │ProfileStore│  │History │ │PlanLibrary│ │VectorIndex│
     │  (Tier 2)  │  │(Tier 3)│ │ (Tier 3) │ │(optional) │
     └────────────┘  └────────┘ └──────────┘ └───────────┘
```

---

## 4. Interfaces

### 4.1 Service Interface

```python
class ContextRAGService:
    """Context assembler — gathers typed evidence from Memory Layer."""

    def __init__(
        self,
        preference_service: PreferenceService,
        fact_service: FactService,
        pattern_service: PatternService,
        plan_service: PlanService,
        vector_index_service: VectorIndexService | None,
    ) -> None: ...

    async def gather_evidence(self, intent: Intent) -> ContextResult:
        """Assemble typed evidence from Memory Layer for plan generation.

        Args:
            intent: Validated Intent model (GLOBAL_SPEC §2.1).

        Returns:
            ContextResult with evidence list, budget info, and degradation metadata.
            Never raises — returns empty ContextResult on total failure.
        """
```

### 4.2 Consumer Contract: Planner

The Planner is the sole consumer of ContextRAG:

```python
# In Planner service:
context = await self.context_rag_service.gather_evidence(intent)
evidence: list[EvidenceItem] = context.evidence
# evidence is guaranteed to be:
#   - ≤ 2048 bytes serialized
#   - All items pass EvidenceItem.model_validate()
#   - Sorted by tier ASC, confidence DESC
#   - Empty list on total failure (never None)
```

The Planner must handle:
- Empty `evidence` list (all sources degraded)
- `context.degraded_sources` non-empty (partial data)

### 4.3 No HTTP Routes

ContextRAG is a library component. It is NOT exposed via FastAPI routes. The Planner calls it directly via DI.

---

## 5. Data Model

### 5.1 Domain Models (`domain/models.py`)

```python
from pydantic import BaseModel, Field
from shared.schemas.evidence import EvidenceItem


class ContextResult(BaseModel):
    """Result of context assembly."""

    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Budget-trimmed, tier-sorted evidence items",
    )

    total_bytes: int = Field(
        default=0,
        ge=0,
        description="Total serialized size of evidence in bytes",
    )

    degraded_sources: list[str] = Field(
        default_factory=list,
        description="Sources that failed (e.g., ['history', 'vectorindex'])",
    )

    query_duration_ms: int = Field(
        default=0,
        ge=0,
        description="Total wall-clock time for gather_evidence() in ms",
    )
```

### 5.2 Domain Errors (`domain/models.py`)

```python
class ContextRAGError(Exception):
    """Base error for ContextRAG component."""

class SourceQueryError(ContextRAGError):
    """A single source query failed (non-fatal, logged and degraded)."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"Source '{source}' failed: {reason}")
```

**Note:** ContextRAG does NOT define fatal errors — `gather_evidence()` never raises. All errors are caught, logged, and reflected in `degraded_sources`.

### 5.3 No Database Schema

ContextRAG owns no tables and has no Redis keys. It is stateless.

---

## 6. Adapters

ContextRAG does not have traditional database adapters. Instead, it has **source adapters** — thin wrappers around Memory Layer service calls that handle error catching and Evidence Item conversion.

### 6.1 Adapter Interface

Each source adapter implements a common protocol:

```python
from typing import Protocol

class SourceAdapter(Protocol):
    """Protocol for Memory Layer source adapters."""

    source_name: str
    required_tier: int

    async def fetch_evidence(
        self,
        intent: Intent,
        timeout_s: float,
    ) -> list[EvidenceItem]:
        """Fetch evidence from this source.

        Raises SourceQueryError on any failure (never other exceptions).
        """
```

### 6.2 ProfileStore Adapter

```python
class ProfileStoreAdapter:
    source_name = "profilestore"
    required_tier = 2

    def __init__(self, preference_service: PreferenceService) -> None:
        self._service = preference_service

    async def fetch_evidence(
        self,
        intent: Intent,
        timeout_s: float = 0.1,
    ) -> list[EvidenceItem]:
        """Call PreferenceService.get_all_preferences().

        Returns list[EvidenceItem] with type="preference", tier=2, confidence=1.0.
        """
```

**Method called:** `get_all_preferences(user_id=UUID(intent.user_id), context_tier=effective_budget)`
**Returns:** `list[EvidenceItem]` — already in correct format, no conversion needed.

**Errors caught:**
- `ConsentDeniedError` (from `components.ProfileStore.domain.models`)
- `UserNotFoundError` (from `shared.database.error_handler`)
- `DatabaseConnectionError` (from `shared.database.error_handler`)
- `asyncio.TimeoutError`
- `Exception` (unexpected — logged as warning)

All caught → `SourceQueryError(source="profilestore", reason=...)`.

### 6.3 History Adapter

```python
class HistoryAdapter:
    source_name = "history"
    required_tier = 3

    def __init__(
        self,
        fact_service: FactService,
        pattern_service: PatternService,
    ) -> None:
        self._fact_service = fact_service
        self._pattern_service = pattern_service

    async def fetch_evidence(
        self,
        intent: Intent,
        timeout_s: float = 0.1,
    ) -> list[EvidenceItem]:
        """Call FactService.get_facts_by_intent() and PatternService.get_patterns().

        Returns list[EvidenceItem] with type="history", tier=3.
        QueryFactsResponse.evidence dicts are validated via EvidenceItem.model_validate().
        Pattern dicts are manually wrapped into EvidenceItem.
        """
```

**Methods called:**
1. `get_facts_by_intent(user_id=UUID(intent.user_id), intent_type=intent.intent, limit=20, recency_days=30)` → `QueryFactsResponse`
2. `get_patterns(user_id=UUID(intent.user_id), intent_type=intent.intent, min_confidence=0.5)` → `PatternsResponse`

**Conversion required:**
- `QueryFactsResponse.evidence` → list of dicts → `EvidenceItem.model_validate(item)` per item; drop items that fail validation
- `PatternsResponse.patterns` → manually wrap each:
  ```python
  EvidenceItem(
      type="history",
      key=pattern["pattern_key"],
      value=pattern["pattern_description"],
      confidence=pattern["confidence"],
      source_ref=f"history:patterns/{pattern['pattern_id']}",
      ttl_days=30,
      tier=3,
  )
  ```

**Errors caught:**
- `ConsentRequiredError` (from `components.History.domain.models`)
- `StorageError` (from `components.History.domain.models`)
- `InvalidQueryError` (from `components.History.domain.models`)
- `DatabaseConnectionError` (from `shared.database.error_handler`)
- `asyncio.TimeoutError`
- `ValidationError` (from Pydantic, when `model_validate()` fails on a dict — per-item, not adapter-level)
- `Exception` (unexpected — logged as warning)

### 6.4 PlanLibrary Adapter

```python
class PlanLibraryAdapter:
    source_name = "planlibrary"
    required_tier = 3

    def __init__(self, plan_service: PlanService) -> None:
        self._service = plan_service

    async def fetch_evidence(
        self,
        intent: Intent,
        timeout_s: float = 0.1,
    ) -> list[EvidenceItem]:
        """Call PlanService.get_plans_by_intent().

        Returns list[EvidenceItem] with type="plan", tier=3.
        Already in EvidenceItem format from PlanService.
        """
```

**Method called:** `get_plans_by_intent(intent_type=intent.intent, success_threshold=0.7, limit=5, recency_days=90)`
**Returns:** `list[EvidenceItem]` — already in correct format, no conversion needed.

**Errors caught:**
- `InvalidQueryError` (from `components.PlanLibrary.domain.models`)
- `DatabaseConnectionError` (from `shared.database.error_handler`)
- `asyncio.TimeoutError`
- `Exception` (unexpected — logged as warning)

**Note:** PlanLibrary does NOT enforce consent tiers — always queryable.

### 6.5 VectorIndex Adapter

```python
class VectorIndexAdapter:
    source_name = "vectorindex"
    required_tier = 3

    def __init__(self, vector_index_service: VectorIndexService | None) -> None:
        self._service = vector_index_service

    async def fetch_evidence(
        self,
        intent: Intent,
        timeout_s: float = 0.05,  # 50ms — aggressive timeout
    ) -> list[EvidenceItem]:
        """Call VectorIndexService.search() and convert HybridSearchResult to EvidenceItem.

        Returns list[EvidenceItem] with type="exemplar", tier=3.
        Returns empty list if service is None (not wired).
        """
```

**Method called:** `search(query_text=f"{intent.intent} {' '.join(str(v) for v in intent.entities.values())}", intent_type=intent.intent, top_k=3)`
**Returns:** `list[HybridSearchResult]` — must convert each to:
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

**Errors caught:**
- `VectorIndexUnavailableError` (from `components.VectorIndex.domain.models`)
- `EmbeddingModelError` (from `components.VectorIndex.domain.models`)
- `ValueError` (empty query_text)
- `DatabaseConnectionError` (from `shared.database.error_handler`)
- `asyncio.TimeoutError`
- `Exception` (unexpected — logged as warning)

**Special behavior:** If `self._service is None`, returns empty list immediately (no error, no degradation entry).

### 6.6 Budget Manager

```python
class BudgetManager:
    """Prioritize and trim evidence to fit within byte budget."""

    BUDGET_BYTES: int = 2048

    def enforce_budget(
        self,
        evidence: list[EvidenceItem],
    ) -> tuple[list[EvidenceItem], int]:
        """Sort by priority, trim to budget, return (trimmed_list, total_bytes).

        Priority order:
          1. Tier ascending (Tier 2 before Tier 3)
          2. Confidence descending within same tier
          3. Earlier items within same tier+confidence

        Budget measurement: len(item.model_dump_json().encode("utf-8")) per item.
        Items are added greedily until budget is exceeded.

        Returns:
            (evidence_list, total_bytes) — evidence_list fits within BUDGET_BYTES.
        """

    def deduplicate(
        self,
        evidence: list[EvidenceItem],
    ) -> list[EvidenceItem]:
        """Remove duplicate evidence items by key.

        When two items share the same key, keep the one with higher confidence.
        """
```

---

## 7. Service Implementation

### 7.1 `gather_evidence()` Flow

```python
async def gather_evidence(self, intent: Intent) -> ContextResult:
    start = time.monotonic()
    effective_budget = intent.context_budget or 3  # Default Tier 3

    # 1. Determine eligible sources based on tier
    sources: list[SourceAdapter] = []
    if effective_budget >= 2:
        sources.append(self._profilestore_adapter)
    if effective_budget >= 3:
        sources.append(self._history_adapter)
        sources.append(self._planlibrary_adapter)
        if self._vectorindex_adapter._service is not None:
            sources.append(self._vectorindex_adapter)

    # 2. Tier 1 early return (no Memory Layer sources)
    if not sources:
        return ContextResult(query_duration_ms=_elapsed_ms(start))

    # 3. Concurrent fetch with per-source timeouts
    results = await asyncio.gather(
        *[
            asyncio.wait_for(
                adapter.fetch_evidence(intent, adapter.default_timeout),
                timeout=adapter.default_timeout,
            )
            for adapter in sources
        ],
        return_exceptions=True,
    )

    # 4. Collect evidence and degraded sources
    all_evidence: list[EvidenceItem] = []
    degraded: list[str] = []

    for adapter, result in zip(sources, results):
        if isinstance(result, SourceQueryError):
            degraded.append(adapter.source_name)
            logger.warning(
                "source_degraded",
                source=adapter.source_name,
                reason=result.reason,
                intent_type=intent.intent,
                trace_id=intent.trace_id,
            )
        elif isinstance(result, BaseException):
            # asyncio.TimeoutError or unexpected
            degraded.append(adapter.source_name)
            logger.warning(
                "source_degraded",
                source=adapter.source_name,
                reason=type(result).__name__,
                intent_type=intent.intent,
                trace_id=intent.trace_id,
            )
        else:
            all_evidence.extend(result)

    # 5. Deduplicate by key
    all_evidence = self._budget_manager.deduplicate(all_evidence)

    # 6. Budget enforcement (sort + trim)
    trimmed, total_bytes = self._budget_manager.enforce_budget(all_evidence)

    # 7. Return result
    return ContextResult(
        evidence=trimmed,
        total_bytes=total_bytes,
        degraded_sources=degraded,
        query_duration_ms=_elapsed_ms(start),
    )
```

### 7.2 Error Handling Inside Adapters

Each adapter wraps its service calls in a try/except that converts all errors to `SourceQueryError`:

```python
# Example: ProfileStore adapter
async def fetch_evidence(self, intent, timeout_s):
    try:
        user_id = UUID(intent.user_id)
        effective_tier = intent.context_budget or 3
        items = await self._service.get_all_preferences(
            user_id=user_id,
            context_tier=effective_tier,
        )
        return items
    except ConsentDeniedError as e:
        raise SourceQueryError("profilestore", f"consent_denied: tier {e.current_tier} < {e.required_tier}")
    except UserNotFoundError as e:
        raise SourceQueryError("profilestore", f"user_not_found: {e.user_id}")
    except DatabaseConnectionError:
        raise SourceQueryError("profilestore", "connection_error")
    except Exception as e:
        logger.warning("profilestore_unexpected_error", error=str(e), error_type=type(e).__name__)
        raise SourceQueryError("profilestore", f"unexpected: {type(e).__name__}")
```

---

## 8. Sequences

### 8.1 Happy Path

```
Planner                 ContextRAGService           ProfileStore  History  PlanLibrary  VectorIndex
  │                          │                         │           │         │            │
  │──gather_evidence(intent)─▶│                         │           │         │            │
  │                          │──get_all_preferences()──▶│           │         │            │
  │                          │──get_facts_by_intent()───│──────────▶│         │            │
  │                          │──get_plans_by_intent()───│───────────│────────▶│            │
  │                          │──search()────────────────│───────────│─────────│───────────▶│
  │                          │                         │           │         │            │
  │                          │◀──list[EvidenceItem]─────│           │         │            │
  │                          │◀──QueryFactsResponse─────│───────────│         │            │
  │                          │◀──list[EvidenceItem]─────│───────────│─────────│            │
  │                          │◀──list[HybridSearchResult]│──────────│─────────│────────────│
  │                          │                         │           │         │            │
  │                          │ validate + convert      │           │         │            │
  │                          │ deduplicate             │           │         │            │
  │                          │ budget trim             │           │         │            │
  │                          │                         │           │         │            │
  │◀──ContextResult──────────│                         │           │         │            │
```

### 8.2 Partial Degradation (History down)

```
Planner                 ContextRAGService           ProfileStore  History
  │                          │                         │           │
  │──gather_evidence(intent)─▶│                         │           │
  │                          │──get_all_preferences()──▶│           │
  │                          │──get_facts_by_intent()───│──────────▶│
  │                          │                         │           │
  │                          │◀──list[EvidenceItem]─────│           │
  │                          │◀──DatabaseConnectionError│───────────│
  │                          │                         │           │
  │                          │ catch → degraded_sources=["history"]│
  │                          │ continue with other evidence        │
  │                          │ budget trim                         │
  │                          │                                     │
  │◀──ContextResult(degraded_sources=["history"])──────│           │
```

### 8.3 Tier 2 Budget (History/PlanLibrary/VectorIndex skipped)

```
Planner                 ContextRAGService           ProfileStore
  │                          │                         │
  │──gather_evidence(intent)─▶│                         │
  │  (context_budget=2)      │                         │
  │                          │ Pre-check: skip Tier 3  │
  │                          │──get_all_preferences()──▶│
  │                          │◀──list[EvidenceItem]─────│
  │                          │ budget trim             │
  │◀──ContextResult──────────│                         │
```

### 8.4 Total Failure

```
Planner                 ContextRAGService
  │                          │
  │──gather_evidence(intent)─▶│
  │                          │ all 4 sources raise errors
  │                          │ degraded_sources=["profilestore","history","planlibrary","vectorindex"]
  │                          │ evidence=[]
  │◀──ContextResult(evidence=[], degraded_sources=[...])
```

### 8.5 Retry / Idempotency

ContextRAG is stateless — every call to `gather_evidence()` is independent. If the Planner retries after a network failure, ContextRAG simply re-queries all sources. There is no idempotency key because there is no mutation.

---

## 9. Shared Infrastructure Usage

### 9.1 Dependency Injection

ContextRAG follows the same pattern as PlanWriter and VectorIndex:

**`shared/app.py` lifespan addition:**
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

**`shared/dependencies.py` addition:**
```python
def get_context_rag_service(request: Request) -> Any:
    """Get ContextRAGService singleton from app state."""
    return request.app.state.context_rag_service
```

### 9.2 Factory Function

```python
def create_context_rag_service(
    preference_service: Any,
    fact_service: Any,
    pattern_service: Any,
    plan_service: Any,
    vector_index_service: Any | None,
) -> ContextRAGService:
    """Create ContextRAGService with Memory Layer services.

    Called once during application lifespan startup in shared/app.py.
    """
```

### 9.3 No Database Usage

ContextRAG does not use `SharedDatabaseAdapter`, `@with_db_error_handling`, or any database utilities directly. It delegates all database access to the Memory Layer services it receives via DI.

### 9.4 No API Error Handling

ContextRAG has no HTTP routes, so it does not use `ErrorResponse` or `APIErrorHandler`. Domain errors are defined in `domain/models.py` but are only used internally.

### 9.5 Shared Schemas

| Schema | Module | Usage |
|--------|--------|-------|
| `Intent` | `shared.schemas.intent` | Input to `gather_evidence()` |
| `EvidenceItem` | `shared.schemas.evidence` | Output items in `ContextResult.evidence` |

---

## 10. Dependencies & External Integrations

### 10.1 Python Packages

No new packages required. ContextRAG uses only standard library (`asyncio`, `time`, `logging`, `uuid`) and existing project dependencies (`pydantic`).

### 10.2 Component Dependencies

| Component | Service | Required | Tier |
|-----------|---------|----------|------|
| ProfileStore | `PreferenceService` | Yes | 2 |
| History | `FactService` | Yes | 3 |
| History | `PatternService` | Yes | 3 |
| PlanLibrary | `PlanService` | Yes | 3 |
| VectorIndex | `VectorIndexService` | No (optional) | 3 |

All are already initialized in `shared/app.py` lifespan.

### 10.3 Development & Testing

- `pytest` + `pytest-asyncio` — async test execution
- `unittest.mock.AsyncMock` — mock Memory Layer services

---

## 11. Observability & Safety

### 11.1 Structured Logging

All log calls include correlation fields:

```python
logger.info(
    "gather_evidence_complete",
    intent_type=intent.intent,           # safe — not PII
    user_id=intent.user_id,              # UUID, not PII
    trace_id=intent.trace_id,            # correlation
    evidence_count=len(result.evidence),
    total_bytes=result.total_bytes,
    degraded_sources=result.degraded_sources,
    duration_ms=result.query_duration_ms,
)
```

**PII safety:** Never log `intent.entities` values, `intent.constraints` values, or `EvidenceItem.value` contents. Only log keys, counts, and metadata.

### 11.2 Domain Errors

| Error class | Module | When raised | Handled by |
|-------------|--------|-------------|------------|
| `ContextRAGError` | `domain/models.py` | Base class | — |
| `SourceQueryError` | `domain/models.py` | Any source fails | `gather_evidence()` catches and adds to `degraded_sources` |

### 11.3 Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `contextrag_gather_duration_seconds` | Histogram | `intent_type` | Total `gather_evidence()` wall time |
| `contextrag_source_query_duration_seconds` | Histogram | `source` | Per-source query time |
| `contextrag_source_error_total` | Counter | `source`, `error_type` | Source query failures |
| `contextrag_budget_utilization_ratio` | Histogram | — | `total_bytes / 2048` (0.0–1.0) |
| `contextrag_evidence_count` | Histogram | `intent_type` | Number of evidence items returned |

### 11.4 No HITL Gates

ContextRAG is internal — no user-facing interactions or approval flows.

---

## 12. Non-Functional Requirements

### 12.1 Performance

| Metric | Local target | Cloud target |
|--------|-------------|-------------|
| `gather_evidence()` p95 | < 200 ms | < 150 ms |
| `gather_evidence()` p99 | < 350 ms | < 250 ms |
| Per-source timeout | 100 ms | 100 ms |
| VectorIndex timeout | 50 ms | 50 ms |

### 12.2 Availability

| Tier | Target |
|------|--------|
| Cloud | 99.9% (inherits from Memory Layer) |
| Local | Best-effort (graceful degradation) |

### 12.3 Testing Strategy

| Category | Count target | Focus |
|----------|-------------|-------|
| Unit tests | ~30 | Budget manager, tier enforcement, dedup, adapter error handling |
| Service tests | ~15 | `gather_evidence()` with mocked adapters, all error combos |
| Contract tests | ~10 | EvidenceItem schema compliance, ContextResult shape |
| Observability tests | ~5 | No PII in logs, metric labels, correlation IDs |

**Test file mapping:**
- `tests/test_unit.py` — `BudgetManager` (sort, trim, dedup), tier pre-check logic, adapter conversion
- `tests/test_service.py` — `ContextRAGService.gather_evidence()` with `AsyncMock` services
- `tests/test_contract.py` — Evidence schema compliance, ContextResult invariants
- `tests/test_observability.py` — Log message structure, no PII leakage

---

## 13. Architectural Considerations

### 13.1 Fault Isolation

ContextRAG is the system's primary fault isolation boundary for context gathering. A failure in any single Memory Layer component must NOT prevent plan generation. This is achieved through:

1. **Per-source try/except** — errors never propagate between sources
2. **Per-source timeouts** — slow sources are abandoned, not awaited
3. **`return_exceptions=True` in gather** — prevents one source failure from cancelling others
4. **Never-raise contract** — `gather_evidence()` always returns `ContextResult`

### 13.2 Determinism

ContextRAG output is deterministic given the same Memory Layer state:
- Tier filtering is deterministic (pre-check before query)
- Deduplication is deterministic (keep highest confidence)
- Budget sorting is deterministic (tier ASC, confidence DESC, stable sort)
- Budget trimming is deterministic (greedy, left-to-right after sort)

### 13.3 State Management

Fully stateless. No instance-level caches, no request-scoped state, no background tasks. Each `gather_evidence()` call is independent.

### 13.4 Cross-Component Interactions

ContextRAG receives all services via constructor injection and calls only read methods:

| Service | Method | Mutates? |
|---------|--------|----------|
| `PreferenceService.get_all_preferences()` | Read | No |
| `FactService.get_facts_by_intent()` | Read | No |
| `PatternService.get_patterns()` | Read | No |
| `PlanService.get_plans_by_intent()` | Read | No |
| `VectorIndexService.search()` | Read | No |

---

## 14. Architecture Decision Records

### Referenced ADRs

- **ADR-0001**: Component-first architecture — ContextRAG follows the `components/<Name>/` structure with `domain/`, `service/`, `adapters/`, `tests/` subdirectories.

### Decisions Made

- **Library component**: ContextRAG has no HTTP routes (same as Signer, PlanWriter, VectorIndex). It is consumed via DI. This avoids unnecessary network overhead between Planner and ContextRAG.
- **Direct service calls (not HTTP)**: ContextRAG calls Memory Layer services directly via DI rather than making HTTP requests. This keeps latency well within the 150ms p95 budget.
- **Pattern enrichment included**: `PatternService.get_patterns()` is called alongside `FactService.get_facts_by_intent()` to provide behavioral pattern evidence. Patterns are manually wrapped into `EvidenceItem` format.
- **No adapter abstractions**: Source adapters are concrete classes, not abstract base classes. There is no foreseeable need for alternative implementations.

---

## 15. Risks & Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Latency exceeds 150ms with 4 concurrent queries | Medium | Per-source timeouts (100ms/50ms), VectorIndex optional |
| History returns malformed evidence dicts | Low | Per-item `model_validate()`, drop invalid items |
| Budget calculation differs between Python versions | Low | Consistent `model_dump_json().encode("utf-8")` |
| All sources degraded → Planner generates poor plan | Medium | Planner must handle empty evidence explicitly |

### Open Questions (from SPEC)

1. **Entity-to-preference mapping**: MVP fetches all preferences via `get_all_preferences()` and trims by budget. No intent→preference mapping needed yet.
2. **History query scope**: MVP queries by `intent_type` only. Entity-level filtering deferred until proven needed.
3. **Evidence deduplication**: Deduplicate by `key` — keep higher-confidence item. Implemented in `BudgetManager.deduplicate()`.

---

## 16. Post-Generation Validation Checklist

- [x] Data model fields match GLOBAL_SPEC §2 (Intent §2.1, EvidenceItem §2.2) — imported from `shared/schemas/`
- [x] `user_id` used in all service calls (extracted from `intent.user_id`)
- [x] Conformance header references current versions (GLOBAL_SPEC v2.2, MODULAR_ARCHITECTURE v1.3, Project_HLD v4.0)
- [x] No table ownership (ContextRAG owns no tables) — matches MODULAR_ARCHITECTURE
- [x] Component dependencies match MODULAR_ARCHITECTURE (ProfileStore, History, PlanLibrary, VectorIndex optional)
- [x] Upstream consumer contract documented (Planner)
- [x] N/A: No storage APIs (read-only component)
- [x] N/A: No DDL (no owned tables)
- [x] Prometheus metrics defined with names and types
- [x] No new dependencies needed
- [x] Evidence Item keys use deterministic source_ref format (no Python `hash()`)
- [x] N/A: No API error handling (no routes)
- [x] N/A: No database adapter (no direct DB access)
