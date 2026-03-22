# Feature Specification: PlanWriter

**Feature Branch**: `feat/planwriter`
**Created**: 2026-03-16
**Status**: Draft
**Component**: `components/PlanWriter/`

## Overview

PlanWriter is a Domain Layer library component that persists execution results back to the Memory Layer after a plan completes. It receives Execute wrappers (outcomes) from ExecuteOrchestrator and atomically writes to three downstream components: PlanLibrary (plan + signature + outcome), History (derived PII-light facts), and VectorIndex (plan embedding for future similarity search). PlanWriter is the write-side counterpart to ContextRAG (read-side), closing the learning loop so the system improves from past executions.

## Goals

- Persist plan execution outcomes atomically across PlanLibrary, History, and VectorIndex
- Derive PII-light facts from execution results for History storage
- Trigger embedding generation for new plans via VectorIndex
- Provide a clean service interface for ExecuteOrchestrator and ExecutionMonitor
- Handle partial failures gracefully (e.g., VectorIndex down but PlanLibrary/History succeed)

## Non-Goals

- No HTTP API routes (library component consumed via DI, like Signer and VectorIndex)
- No direct database access (delegates to Memory Layer components)
- No plan generation or modification (that's Planner's job)
- No signature generation (that's Signer's job — PlanWriter receives pre-signed plans)
- No real-time streaming of outcomes
- No retry logic for downstream writes (callers handle retries)

---

## User Scenarios & Testing

### User Story 1 — Persist Successful Execution (Priority: P1)

After ExecuteOrchestrator completes a plan, PlanWriter stores the plan, signature, outcome, and metrics in PlanLibrary, derives a fact for History, and triggers VectorIndex embedding.

**Why this priority**: Core functionality — without this, the system has no learning loop.

**Independent Test**: Call `persist_outcome()` with a successful plan execution and verify all three downstream services are called with correct data.

**Acceptance Scenarios**:

1. **Given** a completed plan with signature, outcome (success=true), and metrics, **When** `persist_outcome()` is called, **Then** PlanLibrary.store_plan() is called with the plan, signature, outcome, and metrics, AND History.store_fact() is called with a derived fact, AND VectorIndex.store_embedding() is called with the plan data.
2. **Given** a completed plan, **When** `persist_outcome()` succeeds, **Then** it returns a `PersistResult` with `plan_id`, `fact_id`, `embedding_stored=True`, and `status="ok"`.
3. **Given** a completed plan, **When** PlanLibrary.store_plan() is called, **Then** the plan's `canonical_json`, `signature_data`, outcome, and metrics are passed exactly as received (no transformation).

---

### User Story 2 — Persist Failed Execution (Priority: P1)

When a plan execution fails (partially or fully), PlanWriter still records the outcome and derived fact so the system learns from failures.

**Why this priority**: Failures are as valuable as successes for the learning loop.

**Independent Test**: Call `persist_outcome()` with outcome success=false and verify PlanLibrary and History receive the failure data.

**Acceptance Scenarios**:

1. **Given** a plan execution that failed at step 3 of 5, **When** `persist_outcome()` is called with `outcome.success=False`, **Then** PlanLibrary.store_plan() is called with the failure outcome (including `error_type`, `error_details`, `failed_step`), AND History.store_fact() is called with `outcome=False`.
2. **Given** a failed execution, **When** the derived fact is stored in History, **Then** the fact_text describes the failure (e.g., "Failed to book flight: API timeout at step 3").

---

### User Story 3 — Graceful Degradation on VectorIndex Failure (Priority: P2)

If VectorIndex is unavailable (pgvector missing, ONNX model not loaded), PlanWriter still persists to PlanLibrary and History. The embedding is skipped with a warning log.

**Why this priority**: VectorIndex is optional — plan/fact storage must not fail because of it.

**Independent Test**: Call `persist_outcome()` with VectorIndex service set to None and verify PlanLibrary and History succeed, embedding_stored=False.

**Acceptance Scenarios**:

1. **Given** VectorIndex service is None (graceful degradation), **When** `persist_outcome()` is called, **Then** PlanLibrary and History writes succeed, `PersistResult.embedding_stored=False`, and a warning is logged.
2. **Given** VectorIndex service is available but `store_embedding()` raises an error, **When** `persist_outcome()` is called, **Then** PlanLibrary and History writes still succeed, `PersistResult.embedding_stored=False`, and the error is logged (not raised).

---

### User Story 4 — Derive Facts from Execution (Priority: P1)

PlanWriter extracts a PII-light fact from the plan execution for History storage. The fact includes intent_type, a human-readable summary, entities, outcome, and a link back to the plan.

**Why this priority**: Fact derivation is the core transformation PlanWriter provides.

**Independent Test**: Call the fact derivation function with various plan/outcome combinations and verify the derived StoreFactRequest.

**Acceptance Scenarios**:

1. **Given** a successful "book_flight" plan with entities `{destination: "NYC", airline: "Delta"}`, **When** a fact is derived, **Then** `fact_text` reads something like "Booked flight to NYC with Delta" (PII-light, derived), `intent_type="book_flight"`, `entities={destination: "NYC", airline: "Delta"}`, `outcome=True`, `source_plan_id=plan_id`.
2. **Given** any plan execution, **When** a fact is derived, **Then** `fact_text` does not contain raw API responses, credentials, or full plan JSON.
3. **Given** a plan with no entities, **When** a fact is derived, **Then** `entities={}` and `fact_text` uses intent_type only.

---

### User Story 5 — Bulk Persist (Priority: P3)

PlanWriter can persist multiple plan outcomes in a batch (for backfill or replay scenarios).

**Why this priority**: Useful for data migration and recovery, not needed for MVP flow.

**Independent Test**: Call `bulk_persist()` with a list of plan outcomes and verify all are stored.

**Acceptance Scenarios**:

1. **Given** a list of 10 plan outcomes, **When** `bulk_persist()` is called, **Then** all 10 are persisted to PlanLibrary and History, and VectorIndex bulk_store is called once.
2. **Given** an empty list, **When** `bulk_persist()` is called, **Then** ValueError is raised.

---

### Edge Cases

- What happens when PlanLibrary.store_plan() fails? → Entire persist_outcome() fails (PlanLibrary is the primary store). History and VectorIndex writes are not attempted.
- What happens when History.store_fact() fails after PlanLibrary succeeds? → PersistResult reflects partial success (`fact_id=None`, `status="partial"`). VectorIndex still attempted.
- What happens when the same plan_id is persisted twice? → Idempotent: PlanLibrary uses upsert semantics, History deduplicates via fact_hash, VectorIndex upserts on plan_id.
- What happens with very large plan JSON (>100KB)? → PlanLibrary handles size validation (max 1MB). PlanWriter passes through without modification.

---

## Requirements

### Functional Requirements

- **FR-001**: PlanWriter MUST persist plan + signature + outcome + metrics to PlanLibrary via `PlanService.store_plan()`.
- **FR-002**: PlanWriter MUST derive a PII-light fact from the execution and store it in History via `FactService.store_fact()`.
- **FR-003**: PlanWriter MUST trigger embedding storage in VectorIndex via `VectorIndexService.store_embedding()` when VectorIndex is available.
- **FR-004**: PlanWriter MUST handle VectorIndex unavailability gracefully (skip embedding, log warning, continue).
- **FR-005**: PlanWriter MUST handle History write failures gracefully (log error, return partial result, don't block VectorIndex).
- **FR-006**: PlanWriter MUST fail if PlanLibrary write fails (primary store — no partial plan storage).
- **FR-007**: PlanWriter MUST derive fact_text that is PII-light (no raw API responses, credentials, or full plan JSON).
- **FR-008**: PlanWriter MUST link derived facts to source plans via `source_plan_id`.
- **FR-009**: PlanWriter MUST be idempotent — persisting the same plan_id twice produces the same result (via downstream upsert semantics).
- **FR-010**: PlanWriter MUST support bulk persistence for backfill/replay scenarios.
- **FR-011**: PlanWriter MUST log structured events for each operation with plan_id correlation.
- **FR-012**: PlanWriter MUST never log raw plan content, embedding vectors, or credentials.

### Key Entities

- **PersistResult**: Result of persisting a plan outcome. Fields: `plan_id`, `fact_id` (UUID | None), `embedding_stored` (bool), `status` ("ok" | "partial" | "error"), `errors` (list[str]).
- **FactDerivation**: Internal representation of the derived fact before it becomes a StoreFactRequest.

### Service Interface

```python
class PlanWriterService:
    async def persist_outcome(
        self,
        user_id: UUID,
        plan: dict,
        signature: dict,
        outcome: dict,
        metrics: dict,
    ) -> PersistResult:
        """Persist a completed plan execution to all downstream stores."""

    async def bulk_persist(
        self,
        user_id: UUID,
        outcomes: list[dict],
    ) -> list[PersistResult]:
        """Persist multiple plan outcomes (backfill/replay)."""
```

### Fact Derivation Logic

```python
def derive_fact(plan: dict, outcome: dict) -> StoreFactRequest:
    """
    Extract a PII-light fact from plan execution.

    Inputs:
        plan: Canonical plan JSON (contains intent, graph, entities, constraints)
        outcome: Execution result (success, error_type, error_details, failed_step)

    Output:
        StoreFactRequest with:
        - fact_text: Human-readable summary (e.g., "Booked 30min meeting with Alice")
        - intent_type: From plan (e.g., "schedule_meeting")
        - entities: Extracted from plan intent (e.g., {contact: "Alice"})
        - outcome: bool (success/failure)
        - source_plan_id: plan_id linking back to PlanLibrary
        - ttl_days: 30 (default, from GLOBAL_SPEC)
    """
```

---

## Interfaces & Contracts (conform to GLOBAL_SPEC v2.2)

PlanWriter is a **library component** (no HTTP API). It receives data from the Orchestration Layer and writes to the Memory Layer.

### Input: Execute Wrappers + Plan + Signature

PlanWriter receives the full plan context after execution:

```json
{
  "plan": { "plan_id": "<ulid>", "intent": {}, "graph": [...], ... },
  "signature": { "algo": "Ed25519", "signer": "planner@system", "signature": "<base64>", "pubkey_id": "k1", "plan_hash": "<sha256>" },
  "outcome": { "success": true, "error_type": null, "error_details": null, "execution_start": "<iso>", "execution_end": "<iso>", "total_steps": 5, "failed_step": null, "context_data": {} },
  "metrics": { "preview_latency_ms": 450, "execute_latency_ms": 1200, "step_timings": [...] }
}
```

### Output: PersistResult

```json
{
  "plan_id": "<ulid>",
  "fact_id": "<uuid or null>",
  "embedding_stored": true,
  "status": "ok|partial|error",
  "errors": []
}
```

Reference: docs/architecture/GLOBAL_SPEC.md (v2.2)

---

## Component Mapping

- **Target**: `components/PlanWriter/`
- **Files expected**:
  - `components/PlanWriter/__init__.py` — public exports
  - `components/PlanWriter/domain/__init__.py`
  - `components/PlanWriter/domain/models.py` — PersistResult, PlanWriterError, error classes
  - `components/PlanWriter/service/__init__.py`
  - `components/PlanWriter/service/plan_writer_service.py` — PlanWriterService + factory
  - `components/PlanWriter/adapters/__init__.py`
  - `components/PlanWriter/adapters/fact_deriver.py` — derive_fact() pure function
  - `components/PlanWriter/tests/__init__.py`
  - `components/PlanWriter/tests/conftest.py` — fixtures with mocked downstream services
  - `components/PlanWriter/tests/test_unit.py` — unit tests
  - `components/PlanWriter/tests/test_contract.py` — contract tests (SPEC acceptance scenarios)
  - `components/PlanWriter/tests/test_observability.py` — log safety tests
- **Shared files modified**:
  - `shared/app.py` — add PlanWriterService initialization in lifespan
  - `shared/dependencies.py` — add `get_plan_writer_service()` DI getter

---

## Dependencies & Risks

### Component Dependencies (all already implemented)
- **PlanLibrary** (`PlanService.store_plan()`) — primary store for plans + outcomes
- **History** (`FactService.store_fact()`) — fact storage for learning loop
- **VectorIndex** (`VectorIndexService.store_embedding()`) — optional, graceful degradation

### External Dependencies
- None (PlanWriter is a pure orchestrator over Memory Layer components)

### Risks
- **R1**: Partial failure — PlanLibrary succeeds but History fails. Mitigation: return partial result, log error, let caller decide whether to retry.
- **R2**: Fact derivation quality — generating meaningful, PII-light fact_text from arbitrary plan structures. Mitigation: use intent_type + entity keys as template, avoid raw values.
- **R3**: Idempotency across three stores — all downstream services must handle duplicate writes. Mitigated: PlanLibrary uses DB-level constraints, History deduplicates via fact_hash, VectorIndex upserts on plan_id.

---

## Non-Functional Requirements

- **Latency**: `persist_outcome()` p95 < 200ms (3 async calls, PlanLibrary and History can be parallelized after PlanLibrary succeeds; VectorIndex embedding ~10ms)
- **Observability**: Structured logging with `plan_id` correlation. Events: `outcome_persisted`, `fact_derived`, `embedding_stored`, `persist_partial_failure`, `persist_failed`.
- **Safety**: No secrets, PII, raw API responses, or full plan JSON in logs. Fact derivation produces PII-light text only.
- **Idempotency**: Same plan_id persisted twice produces same result (downstream upsert semantics).
- **Availability**: If VectorIndex is down, PlanWriter still succeeds for PlanLibrary + History.

---

## Open Questions

1. **Fact text generation**: Should fact_text be a simple template (`"{action} {entity_summary}"`) or use an LLM for natural language? Recommendation: template-based for now (deterministic, fast, no API cost).
2. **TTL for facts**: Should PlanWriter always use 30-day TTL, or derive it from plan constraints? Recommendation: default 30 days, configurable per intent_type later.
3. **Metrics passthrough**: Should PlanWriter validate the metrics dict or pass it through to PlanLibrary as-is? Recommendation: pass through (PlanLibrary validates).

---

## Conformance

This work conforms to docs/architecture/GLOBAL_SPEC.md v2.2.

- PlanWriter uses canonical contracts: Plan (§2.3), Signature (§2.4), Execute Wrapper (§2.6)
- PlanWriter respects Evidence Item format (§2.2) via History's StoreFactRequest
- PlanWriter meets observability requirements (§3): structured logs, plan_id correlation, no PII
- PlanWriter is idempotent (§8): duplicate writes produce same result
- PlanWriter handles graceful degradation (§7): VectorIndex optional
