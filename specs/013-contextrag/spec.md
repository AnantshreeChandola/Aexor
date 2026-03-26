# Feature Spec — ContextRAG

**Feature Branch**: `feat/contextrag`
**Created**: 2026-03-20
**Status**: Draft
**Target Component**: `components/ContextRAG/`

---

## Overview

ContextRAG is a **context assembler** in the Domain/Service Layer that gathers relevant, typed evidence from the Memory Layer to support deterministic plan generation. Given an Intent (GLOBAL_SPEC §2.1), it queries ProfileStore, History, PlanLibrary, and optionally VectorIndex, enforcing a strict **2 KB budget** and **consent tier** policy (GLOBAL_SPEC §7). The output is a prioritized list of `EvidenceItem` objects (GLOBAL_SPEC §2.2) ready for the Planner. Despite the name, ContextRAG performs **structured API queries** — not embedding-based retrieval — and must meet a **p95 < 150 ms** latency target.

---

## Goals

- Assemble typed `EvidenceItem[]` from Memory Layer components for a given Intent
- Enforce context tier budgets (Tier 1–4) and user consent before fetching
- Enforce a hard 2 KB serialized budget on the returned evidence set
- Provide graceful degradation when individual data sources are unavailable
- Return results within p95 < 150 ms

## Non-Goals

- Embedding-based semantic retrieval (that is VectorIndex's job; ContextRAG just calls its API)
- Mutating any Memory Layer data (strictly read-only)
- Multi-turn conversation management (that is Intake's job)
- LLM calls of any kind
- Caching evidence across requests (stateless per call)
- HTTP routes (ContextRAG is a **library component** consumed via DI by Planner)

---

## User Scenarios & Testing

### User Story 1 — Assemble context for a known intent (Priority: P1)

When the Planner needs context for a well-known intent (e.g., `schedule_meeting`), ContextRAG fetches preferences, recent history, and similar past plans, returning a budget-compliant evidence set.

**Why this priority**: This is the primary flow — every plan generation starts with context assembly.

**Independent Test**: Call `gather_evidence(intent)` with a valid `schedule_meeting` intent and mock Memory Layer responses; verify returned `EvidenceItem[]` contains preference, history, and plan evidence within 2 KB.

**Acceptance Scenarios**:

1. **Given** an Intent with `intent="schedule_meeting"` and `entities={"person": "Alice"}`, **When** `gather_evidence()` is called, **Then** it returns `EvidenceItem[]` with items from ProfileStore (Tier 2), History (Tier 3), and PlanLibrary (Tier 3), totaling ≤ 2048 bytes serialized.
2. **Given** an Intent with `context_budget=2` (Tier 2 only), **When** `gather_evidence()` is called, **Then** only ProfileStore (Tier 2) evidence is included; History and PlanLibrary (Tier 3) are skipped.

---

### User Story 2 — Graceful degradation when a data source is down (Priority: P1)

When one Memory Layer component is unavailable, ContextRAG returns partial results from the remaining sources rather than failing entirely.

**Why this priority**: Availability is critical — the system must not block planning due to a single source failure.

**Independent Test**: Call `gather_evidence()` with History service raising `DatabaseConnectionError`; verify ProfileStore and PlanLibrary evidence is still returned with a degradation warning.

**Acceptance Scenarios**:

1. **Given** History raises `DatabaseConnectionError`, **When** `gather_evidence()` is called, **Then** it returns evidence from ProfileStore and PlanLibrary, and includes `degraded_sources: ["history"]` in the result metadata.
2. **Given** all Memory Layer services raise `DatabaseConnectionError`, **When** `gather_evidence()` is called, **Then** it returns an empty `EvidenceItem[]` with `degraded_sources` listing all failed sources (does NOT raise an exception).
3. **Given** ProfileStore raises `ConsentDeniedError` (user hasn't granted Tier 2), **When** `gather_evidence()` is called, **Then** it skips ProfileStore, adds `"profilestore"` to `degraded_sources`, and still returns History/PlanLibrary evidence.
4. **Given** VectorIndex raises `VectorIndexUnavailableError`, **When** `gather_evidence()` is called, **Then** it returns evidence from the other three sources without exemplar items.
5. **Given** a source query exceeds the per-source timeout (100 ms), **When** `gather_evidence()` is called, **Then** `asyncio.TimeoutError` is caught, source is added to `degraded_sources`, and remaining results are returned.

---

### User Story 3 — Budget enforcement with prioritization (Priority: P2)

When the combined evidence from all sources exceeds 2 KB, ContextRAG prioritizes by tier and confidence, trimming lower-priority items.

**Why this priority**: Budget enforcement prevents LLM context bloat and keeps plan generation fast.

**Independent Test**: Return mock evidence totaling 5 KB; verify output is trimmed to ≤ 2048 bytes with higher-confidence and lower-tier items retained.

**Acceptance Scenarios**:

1. **Given** ProfileStore returns 1 KB and History returns 2 KB of evidence, **When** budget is 2048 bytes, **Then** all ProfileStore evidence (Tier 2, higher priority) is kept and History evidence is trimmed to fit.
2. **Given** multiple evidence items with varying confidence scores, **When** budget trimming is needed, **Then** items with higher confidence are retained over lower-confidence items within the same tier.

---

### User Story 4 — VectorIndex hybrid search integration (Priority: P3)

When VectorIndex is available, ContextRAG optionally queries it for similar past plans to enrich context with exemplar evidence.

**Why this priority**: VectorIndex is optional and provides enrichment, not core functionality.

**Independent Test**: Call `gather_evidence()` with VectorIndex available; verify `EvidenceItem` of type `"exemplar"` is included. Call again with VectorIndex unavailable; verify no error and no exemplar evidence.

**Acceptance Scenarios**:

1. **Given** VectorIndex is available, **When** `gather_evidence()` is called, **Then** it includes up to 3 exemplar `EvidenceItem` objects from similar past plans.
2. **Given** VectorIndex is unavailable (graceful degradation), **When** `gather_evidence()` is called, **Then** it completes normally without exemplar evidence and without error.

---

### Edge Cases

- Intent with empty `entities` — still fetches all preferences via `get_all_preferences()` and general history via `get_facts_by_intent(intent_type=intent.intent)`
- Intent with `context_budget=1` (Tier 1 only) — returns empty `ContextResult` (no Memory Layer queries, Tier 1 = session only). All sources are Tier 2+.
- Intent with `context_budget=None` — default to `3` (include all tiers up to Tier 3)
- Intent with unknown `intent` type — still queries all sources; `get_facts_by_intent()` and `get_plans_by_intent()` may return empty lists
- History returns malformed evidence dict that fails `EvidenceItem.model_validate()` — drop that item, log warning, continue with valid items
- Evidence item with `value` that is very large (e.g., 3 KB single item) — excluded by budget enforcement
- VectorIndex service is `None` in DI (not wired) — skip without adding to `degraded_sources` (it's optional by design)
- VectorIndex raises `EmbeddingModelError` (ONNX model not loaded) — catch, add to `degraded_sources`, continue
- `UserNotFoundError` from ProfileStore — skip source, add to `degraded_sources` (this is from `shared.database.error_handler`, NOT from ProfileStore domain)
- Concurrent calls for same user — must be safe (stateless, no shared mutable state)

---

## Requirements

### Functional Requirements

- **FR-001**: System MUST accept an `Intent` (GLOBAL_SPEC §2.1) and return a `ContextResult` containing `list[EvidenceItem]` (GLOBAL_SPEC §2.2)
- **FR-002**: System MUST call `PreferenceService.get_all_preferences(user_id, context_tier, plan_id=None)` for Tier 2 preferences when `context_budget >= 2`
- **FR-003**: System MUST call `FactService.get_facts_by_intent(user_id, intent_type, limit=20, recency_days=30)` for Tier 3 facts when `context_budget >= 3`, and validate returned dicts via `EvidenceItem.model_validate()`
- **FR-004**: System MUST call `PlanService.get_plans_by_intent(intent_type, success_threshold=0.7, limit=5, recency_days=90)` for Tier 3 successful past plans when `context_budget >= 3`
- **FR-005**: System SHOULD call `VectorIndexService.search(query_text, intent_type, top_k=3)` for similar plans when available and `context_budget >= 3`; MUST convert `HybridSearchResult` to `EvidenceItem(type="exemplar")`; MUST degrade gracefully if service is `None` or raises `VectorIndexError`
- **FR-006**: System MUST pre-check `context_budget` tier ceiling from the Intent — skip sources above the budget tier **before calling** (do not call and catch consent errors as flow control)
- **FR-007**: System MUST enforce a 2048-byte hard budget on the serialized evidence set
- **FR-008**: System MUST prioritize evidence by tier (lower tier = higher priority), then by confidence (descending)
- **FR-009**: System MUST NOT mutate any data in Memory Layer components (only call read methods)
- **FR-010**: System MUST query eligible Memory Layer sources concurrently via `asyncio.gather(return_exceptions=True)` with per-source timeouts
- **FR-011**: System MUST include `source_ref` and `tier` on every returned `EvidenceItem`
- **FR-012**: System MUST catch all errors per source (consent, domain, database, timeout) and add source to `degraded_sources` — never raise from `gather_evidence()`
- **FR-013**: System MUST convert History `QueryFactsResponse.evidence` dicts to `EvidenceItem` model instances; drop items that fail validation with a warning log
- **FR-014**: System MUST convert VectorIndex `HybridSearchResult` objects to `EvidenceItem(type="exemplar")` with `confidence=min(rrf_score, 1.0)`

### Key Entities

- **`EvidenceItem`** (from `shared/schemas/evidence.py`): The output unit — typed, confidence-scored, tier-tagged evidence
- **`Intent`** (from `shared/schemas/intent.py`): The input — user intent with entities, constraints, and context_budget
- **`ContextResult`** (new domain model): Wrapper containing `evidence: list[EvidenceItem]`, `total_bytes: int`, `degraded_sources: list[str]`, `query_duration_ms: int`

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: `gather_evidence()` returns valid `EvidenceItem[]` within p95 < 150 ms for typical intents
- **SC-002**: Serialized evidence never exceeds 2048 bytes across all test cases
- **SC-003**: System returns partial evidence (not error) when 1+ sources are down
- **SC-004**: All returned `EvidenceItem` objects pass `EvidenceItem.model_validate()` (schema compliance)
- **SC-005**: Tier enforcement — no Tier 3 evidence returned when `context_budget=2`

---

## Interfaces & Contracts (conform to GLOBAL_SPEC v2.2)

### Input: Intent (GLOBAL_SPEC §2.1)

```json
{
  "intent": "schedule_meeting",
  "entities": {"person": "Alice", "timeframe": "next week"},
  "constraints": {"duration_min": 30},
  "tz": "America/Chicago",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "context_budget": 3
}
```

### Output: ContextResult (domain model)

```json
{
  "evidence": [
    {
      "type": "preference",
      "key": "meeting_duration_min",
      "value": 30,
      "confidence": 1.0,
      "source_ref": "profilestore:prefs/meeting_duration_min",
      "ttl_days": null,
      "tier": 2
    },
    {
      "type": "history",
      "key": "meeting_pattern_alice",
      "value": "Usually meets Alice on Tuesdays at 10 AM",
      "confidence": 0.85,
      "source_ref": "history:facts/meeting-alice-pattern",
      "ttl_days": 30,
      "tier": 3
    },
    {
      "type": "plan",
      "key": "similar_plan_schedule_meeting",
      "value": {"plan_id": "01HXYZ...", "success_rate": 0.95},
      "confidence": 0.90,
      "source_ref": "planlibrary:plans/by-intent/schedule_meeting",
      "ttl_days": 30,
      "tier": 3
    }
  ],
  "total_bytes": 512,
  "degraded_sources": [],
  "query_duration_ms": 45
}
```

### Service Interface (library component — no HTTP routes)

```python
class ContextRAGService:
    async def gather_evidence(self, intent: Intent) -> ContextResult:
        """Assemble typed evidence from Memory Layer for plan generation."""
```

**Note**: ContextRAG is a **library component** (like Signer, PlanWriter, VectorIndex). It has no HTTP routes. It is consumed via dependency injection by the Planner component.

### Consumer: Planner

The Planner calls `gather_evidence(intent)` and passes the resulting `EvidenceItem[]` to its LLM prompt as frozen context for deterministic plan generation.

Reference: docs/architecture/GLOBAL_SPEC.md v2.2

---

## Component Mapping

- **Target**: `components/ContextRAG/`
- **Files expected**:
  - `__init__.py`
  - `domain/__init__.py`
  - `domain/models.py` — `ContextResult`, domain errors (`ContextBudgetExceededError`, `SourceQueryError`)
  - `service/__init__.py`
  - `service/context_rag_service.py` — `ContextRAGService` with `gather_evidence()`
  - `adapters/__init__.py`
  - `adapters/profilestore_adapter.py` — Adapter querying ProfileStore service
  - `adapters/history_adapter.py` — Adapter querying History service
  - `adapters/planlibrary_adapter.py` — Adapter querying PlanLibrary service
  - `adapters/vectorindex_adapter.py` — Adapter querying VectorIndex service (optional)
  - `adapters/budget_manager.py` — Evidence prioritization and 2 KB budget trimming
  - `tests/__init__.py`
  - `tests/conftest.py` — Shared fixtures (mock adapters, sample intents, evidence)
  - `tests/test_unit.py` — Unit tests for budget manager, tier enforcement, evidence assembly
  - `tests/test_service.py` — Service-level tests with mock adapters
  - `tests/test_contract.py` — Contract tests (EvidenceItem schema compliance, ContextResult shape)
  - `tests/test_observability.py` — Logging, metrics, no PII tests

---

## Dependencies & Risks

### Component Dependencies — Exact Service Interfaces

ContextRAG calls Memory Layer services **directly via DI** (not HTTP). Below are the exact method signatures, parameters, return types, and errors for each.

#### ProfileStore — `PreferenceService`

**Method used by ContextRAG:**
```python
async def get_all_preferences(
    self,
    user_id: UUID,
    context_tier: int,        # ContextRAG passes intent.context_budget or default 3
    plan_id: str | None = None
) -> list[EvidenceItem]
```
- **Returns**: `list[EvidenceItem]` with `type="preference"`, `tier=2`, `confidence=1.0`, `ttl_days=None`
- **`source_ref` format**: `"profilestore:prefs/{key}"`
- **Includes schema defaults** for unset preferences (not just explicitly set ones)

**Errors to handle:**

| Error | Module | Trigger | ContextRAG action |
|-------|--------|---------|-------------------|
| `ConsentDeniedError` | `ProfileStore.domain.models` | `context_tier < 2` | Skip source, add to `degraded_sources` with reason `"consent_denied"` |
| `UserNotFoundError` | `shared.database.error_handler` | `user_id` does not exist | Skip source, add to `degraded_sources` |
| `DatabaseConnectionError` | `shared.database.error_handler` | DB unreachable | Skip source, add to `degraded_sources` |

**Note**: ContextRAG should pre-check `context_budget >= 2` before calling ProfileStore. If `context_budget < 2`, skip entirely (don't call and catch `ConsentDeniedError`).

---

#### History — `FactService`

**Method used by ContextRAG:**
```python
async def get_facts_by_intent(
    self,
    user_id: UUID,
    intent_type: str | None = None,   # pass intent.intent
    limit: int = 50,                   # ContextRAG default: 20 (budget-conscious)
    recency_days: int | None = None,   # ContextRAG default: 30
) -> QueryFactsResponse
```
- **Returns**: `QueryFactsResponse` with:
  - `evidence: list` — list of Evidence Item **dicts** (not `EvidenceItem` model instances)
  - `total_count: int`
  - `returned_count: int`
- **Evidence Item format**: `type="history"`, `tier=3`, `confidence` = linear decay (`max(0.0, 1.0 - age_days / ttl_days)`), `ttl_days` = remaining TTL
- **`source_ref` format**: `"history:facts/{fact_id}"`
- **`value` is a dict**: `{"fact": str, "intent_type": str, "outcome": bool, "entities": dict, "age_days": int}`

**ContextRAG must convert** `QueryFactsResponse.evidence` dicts to `EvidenceItem` model instances via `EvidenceItem.model_validate()`.

**Errors to handle:**

| Error | Module | Trigger | ContextRAG action |
|-------|--------|---------|-------------------|
| `ConsentRequiredError` | `History.domain.models` | `context_tier < 3` | Skip source, add to `degraded_sources` |
| `InvalidQueryError` | `History.domain.models` | Invalid limit/recency_days | Should not occur (ContextRAG controls params) — log and skip |
| `StorageError` | `History.domain.models` | DB operation failed | Skip source, add to `degraded_sources` |
| `DatabaseConnectionError` | `shared.database.error_handler` | DB unreachable | Skip source, add to `degraded_sources` |

**Note**: History also has a `PatternService.get_patterns()` method that returns raw pattern dicts (NOT Evidence Items). ContextRAG may optionally convert these to `EvidenceItem(type="history")` for pattern-based evidence.

---

#### History — `PatternService` (optional enrichment)

```python
async def get_patterns(
    self,
    user_id: UUID,
    intent_type: str | None = None,
    min_confidence: float = 0.5,
) -> PatternsResponse
```
- **Returns**: `PatternsResponse` with `patterns: list[dict]`, `total_count: int`
- **Pattern dict fields**: `pattern_id`, `pattern_key`, `pattern_description`, `entity_pattern`, `occurrence_count`, `last_seen`, `confidence`
- **NOT `EvidenceItem` format** — ContextRAG must wrap in `EvidenceItem(type="history", key=pattern_key, value=pattern_description, confidence=pattern_confidence, source_ref="history:patterns/{pattern_id}", tier=3)` if used

---

#### PlanLibrary — `PlanService`

**Method used by ContextRAG:**
```python
async def get_plans_by_intent(
    self,
    intent_type: str,                    # pass intent.intent
    success_threshold: float = 0.7,      # ContextRAG default: 0.7
    limit: int = 50,                     # ContextRAG default: 5 (budget-conscious)
    recency_days: int | None = None,     # ContextRAG default: 90
) -> list[EvidenceItem]
```
- **Returns**: `list[EvidenceItem]` sorted by `success_rate DESC, total_executions DESC`
- **Evidence Item fields**: `type="plan"`, `tier=3`, `confidence=success_rate`, `ttl_days=None`
- **`source_ref` format**: `"planlibrary:plans/{plan_id}"`
- **`key` format**: `"{intent_type}_pattern_{plan_id[:8]}"`
- **`value` is a dict**: `{"intent": str, "success_rate": float, "avg_execution_time_ms": float, "steps_count": int, "pattern_summary": str}`

**No consent tier check** — PlanLibrary does not enforce consent tiers.

**Errors to handle:**

| Error | Module | Trigger | ContextRAG action |
|-------|--------|---------|-------------------|
| `InvalidQueryError` | `PlanLibrary.domain.models` | Invalid params | Should not occur — log and skip |
| `DatabaseConnectionError` | `shared.database.error_handler` | DB unreachable | Skip source, add to `degraded_sources` |

---

#### VectorIndex — `VectorIndexService` (optional)

**Method used by ContextRAG:**
```python
async def search(
    self,
    query_text: str,             # build from f"{intent.intent} {' '.join(intent.entities.values())}"
    intent_type: str | None = None,  # pass intent.intent for B-tree pre-filter
    top_k: int = 5,              # ContextRAG default: 3
) -> list[HybridSearchResult]
```
- **Returns**: `list[HybridSearchResult]` sorted by `rrf_score` descending
- **`HybridSearchResult` fields**: `plan_id: str`, `intent_type: str`, `rrf_score: float`, `keyword_rank: int | None`, `semantic_rank: int | None`
- **Returns plan IDs only** — ContextRAG must convert to `EvidenceItem(type="exemplar", key=f"similar_plan_{plan_id[:8]}", value={"plan_id": plan_id, "rrf_score": rrf_score}, confidence=min(rrf_score, 1.0), source_ref=f"vectorindex:search/{plan_id}", tier=3)`

**Errors to handle:**

| Error | Module | Trigger | ContextRAG action |
|-------|--------|---------|-------------------|
| `VectorIndexUnavailableError` | `VectorIndex.domain.models` | pgvector not installed | Skip entirely, add to `degraded_sources` |
| `EmbeddingModelError` | `VectorIndex.domain.models` | ONNX model not loaded | Skip entirely, add to `degraded_sources` |
| `ValueError` | builtin | Empty query_text or invalid top_k | Should not occur — log and skip |
| `DatabaseConnectionError` | `shared.database.error_handler` | DB unreachable | Skip entirely, add to `degraded_sources` |

**Note**: VectorIndex is entirely optional. If the service is `None` (not wired in DI), ContextRAG skips it without error.

---

### Error Handling Strategy Summary

```
Catch hierarchy (per source):
  1. Component-specific consent errors → skip source, reason="consent_denied"
  2. Component-specific domain errors → skip source, reason="query_error"
  3. DatabaseConnectionError → skip source, reason="connection_error"
  4. asyncio.TimeoutError → skip source, reason="timeout"
  5. Exception (unexpected) → skip source, reason="unexpected_error", log warning

All caught errors → add source name to degraded_sources, continue to next source
Never raise from gather_evidence() — always return ContextResult (possibly empty)
```

### Shared Infrastructure

| Dependency | Usage |
|------------|-------|
| `shared/schemas/intent.py` | Input contract (`Intent` model) |
| `shared/schemas/evidence.py` | Output contract (`EvidenceItem` model) |
| `shared/database/error_handler.py` | `UserNotFoundError`, `DatabaseConnectionError` |
| `shared/app.py` | DI wiring via lifespan |
| `shared/dependencies.py` | `Depends(get_context_rag_service)` |

### Risks

- **Latency risk**: Concurrent queries to 3–4 sources must complete within 150 ms p95. Mitigation: `asyncio.gather()` with per-source timeouts (100 ms, 50 ms for VectorIndex).
- **Budget calculation accuracy**: Serialized size depends on JSON encoding; need deterministic measurement. Mitigation: Use `len(item.model_dump_json().encode())` consistently.
- **Adapter interface coupling**: ContextRAG depends on Memory Layer service method signatures remaining stable. Mitigation: Contract tests validate expected method signatures and return types.
- **VectorIndex availability**: Optional dependency; must not block or slow down the critical path. Mitigation: Short timeout (50 ms), graceful skip on any `VectorIndexError`.
- **History returns dicts, not models**: `QueryFactsResponse.evidence` contains raw dicts, not `EvidenceItem` instances. ContextRAG must validate via `EvidenceItem.model_validate()` — invalid items are dropped with a warning log.
- **Pattern data is not Evidence format**: `PatternService.get_patterns()` returns raw pattern dicts. ContextRAG must manually wrap them into `EvidenceItem` shape if patterns are used.

---

## Non-Functional Requirements

### Performance (deltas from baseline)

| Metric | Target | Baseline |
|--------|--------|----------|
| `gather_evidence()` p95 | < 150 ms | Preview p95 < 800 ms |
| `gather_evidence()` p99 | < 250 ms | — |
| Per-source query timeout | 100 ms | — |
| VectorIndex query timeout | 50 ms | — |

### Observability

- Structured logging correlated by `intent.trace_id` and `intent.user_id`
- No PII in logs — log intent_type and entity keys, never entity values
- Log per-source query durations and budget utilization
- Prometheus metrics:
  - `contextrag_gather_duration_seconds` (histogram, labels: `intent_type`)
  - `contextrag_source_query_duration_seconds` (histogram, labels: `source`)
  - `contextrag_source_error_total` (counter, labels: `source`, `error_type`)
  - `contextrag_budget_utilization_ratio` (histogram) — 0.0 to 1.0
  - `contextrag_evidence_count` (histogram, labels: `intent_type`)

### Safety

- Read-only: no writes to any data source
- No secrets or PII in logs
- Consent tier enforcement is non-negotiable — never fetch data above the user's `context_budget`

---

## Open Questions

1. **Entity-to-preference mapping**: How does ContextRAG know which ProfileStore preference keys to fetch for a given intent? Options: (a) hardcoded intent-to-preference map, (b) fetch all preferences and let budget manager trim, (c) intent-driven heuristic. Recommendation: (b) for MVP — fetch all, trim by budget.
2. **History query scope**: Should ContextRAG query by intent_type only, or also by entity keys (e.g., `person=Alice`)? Current History API supports `intent_type` filter. Entity-level filtering may need a History API extension.
3. **Evidence deduplication**: If ProfileStore and History return overlapping evidence (e.g., both mention meeting duration), should ContextRAG deduplicate? Recommendation: yes, by `key` — keep higher-confidence item.

---

## Conformance

This work conforms to docs/architecture/GLOBAL_SPEC.md v2.2.
This work conforms to docs/architecture/MODULAR_ARCHITECTURE.md v1.3.
This work conforms to docs/architecture/Project_HLD.md v4.0.
