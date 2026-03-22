# PlanWriter -- Low-Level Design

**Component**: `components/PlanWriter/`
**Layer**: Domain / Service Layer (Layer 2)
**Spec**: `specs/011-planwriter/spec.md`
**Created**: 2026-03-19
**Status**: Draft

---

## 1. Purpose & Scope

PlanWriter is a Domain Layer library component that persists execution results back to the Memory Layer after a plan completes. It closes the learning loop: ExecuteOrchestrator and ExecutionMonitor produce outcomes; PlanWriter writes those outcomes to PlanLibrary, derives PII-light facts for History, and triggers embedding storage in VectorIndex so the system improves from past executions.

### Boundaries

- **In scope**: Persist plan outcomes to PlanLibrary, derive and store facts in History, trigger VectorIndex embedding storage, return composite `PersistResult`, bulk persist for backfill/replay
- **Out of scope**: HTTP API routes (NG -- library component, consumed via DI like Signer and VectorIndex), plan generation or modification (Planner's job), signature generation (Signer's job -- PlanWriter receives pre-signed plans), direct database access (delegates to Memory Layer components), retry logic for downstream writes (callers handle retries)

### Layer Placement

PlanWriter is a **Domain / Service Layer** component (Layer 2). It is called by:
- **ExecuteOrchestrator** (to persist outcomes after plan execution)
- **ExecutionMonitor** (to persist outcomes for stuck/failed executions detected via polling)

It writes to three **Memory Layer** components but does NOT own any database tables. All persistence is delegated to downstream services.

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v2.2 (2026-03-05) | SS2.3 Plan, SS2.4 Signature, SS2.6 Execute Wrapper, SS3 NFRs, SS7 Context Policy, SS8 Safety & Governance |
| Project_HLD.md | v4.6 | Layer 2 Domain Services, SS3 PlanWriter component, SS6 Learning step |
| MODULAR_ARCHITECTURE.md | v1.3 | SS4 Component Dependency Matrix -- PlanWriter (PlanLibrary, History, VectorIndex) |
| SHARED_INFRASTRUCTURE.md | v1.0.0 | N/A -- PlanWriter owns no database tables |

---

## 3. Architecture Overview

### Component Structure

```
components/PlanWriter/
|-- __init__.py
|-- domain/
|   |-- __init__.py
|   +-- models.py              # PersistResult, BulkPersistResult, PlanWriterError classes
|-- service/
|   |-- __init__.py
|   +-- plan_writer_service.py # PlanWriterService + factory
|-- adapters/
|   |-- __init__.py
|   +-- fact_deriver.py        # derive_fact() pure function
|-- tests/
|   |-- __init__.py
|   |-- conftest.py            # Fixtures with mocked downstream services
|   |-- test_unit.py           # Unit tests
|   |-- test_contract.py       # Contract tests (SPEC acceptance scenarios)
|   +-- test_observability.py  # Log safety tests
|-- diagrams/
|   +-- flow.md                # Mermaid flow diagrams
+-- LLD.md
```

### Blast Radius Analysis

- **Failure mode**: If PlanWriter is unavailable, execution outcomes are not persisted -- the learning loop breaks but plan execution itself is not affected (PlanWriter is called post-execution).
- **Containment**: PlanWriter is a library with no owned infrastructure. It calls three downstream services; failures in one do not cascade to the others (History and VectorIndex failures are isolated from PlanLibrary).
- **No cascading failures**: PlanWriter introduces no new database connections, queues, or caches. It uses only the downstream services' existing infrastructure.
- **Recovery**: Restart the application process. PlanWriter is stateless -- no state to recover. Missed writes can be replayed via `bulk_persist()` once services recover.

### Isolation Strategy

PlanWriter is **stateless** -- it holds references to three downstream services injected at startup and performs orchestrated write operations. It has no:
- Database connections (delegates to PlanLibrary, History, VectorIndex)
- Redis connections
- External API calls
- Background tasks
- Queues or caches
- In-memory state beyond injected service references

---

## 4. Interfaces

### 4.1 Service Interface

```python
from uuid import UUID
from typing import Any

from components.PlanWriter.domain.models import PersistResult, BulkPersistResult


class PlanWriterService:
    """Persists plan execution outcomes to Memory Layer."""

    def __init__(
        self,
        plan_service: "PlanService",
        fact_service: "FactService",
        vector_index_service: "VectorIndexService | None",
    ) -> None:
        """
        Initialize with downstream Memory Layer services.

        Args:
            plan_service: PlanLibrary service for plan+outcome storage.
            fact_service: History service for fact storage.
            vector_index_service: VectorIndex service for embedding storage.
                May be None if VectorIndex is unavailable (graceful degradation).
        """

    async def persist_outcome(
        self,
        user_id: UUID,
        plan: Plan,
        signature: Signature,
        outcome: PlanOutcome,
        metrics: PlanMetrics,
    ) -> PersistResult:
        """
        Persist a completed plan execution to all downstream stores.

        Uses shared Pydantic models from shared/schemas/ for type-safe
        contract enforcement. Internally converts to dicts via model_dump()
        before passing to downstream services.

        Execution order:
            1. PlanLibrary.store_plan() -- PRIMARY, must succeed
            2. History.store_fact()     -- SECONDARY, partial failure ok
            3. VectorIndex.store_embedding() -- OPTIONAL, graceful degradation

        Args:
            user_id: User UUID (passed to History for user-scoped facts).
            plan: Typed Plan model (shared.schemas.plan).
            signature: Typed Signature model (shared.schemas.signature).
            outcome: Typed PlanOutcome model (shared.schemas.outcome).
            metrics: Typed PlanMetrics model (shared.schemas.metrics).

        Returns:
            PersistResult with plan_id, fact_id, embedding_stored, status.

        Raises:
            PlanWriterError: If PlanLibrary write fails (wraps downstream error).
        """

    async def bulk_persist(
        self,
        user_id: UUID,
        outcomes: list[dict[str, Any]],
    ) -> BulkPersistResult:
        """
        Persist multiple plan outcomes (backfill/replay).

        Each outcome dict must contain keys: plan, signature, outcome, metrics.
        Processes sequentially; collects individual PersistResults.

        Args:
            user_id: User UUID (applied to all outcomes).
            outcomes: List of outcome dicts.

        Returns:
            BulkPersistResult with individual results and summary counts.

        Raises:
            ValueError: If outcomes list is empty.
        """
```

### 4.2 Consumer Contracts

#### ExecuteOrchestrator -> PlanWriter (post-execution persist)

```python
# ExecuteOrchestrator calls after n8n workflow completes:
result: PersistResult = await plan_writer.persist_outcome(
    user_id=user_id,
    plan=plan_dict,
    signature=signature_dict,
    outcome={
        "success": True,
        "error_type": None,
        "error_details": None,
        "execution_start": "2026-03-19T10:00:00Z",
        "execution_end": "2026-03-19T10:00:01Z",
        "total_steps": 5,
        "failed_step": None,
        "context_data": {},
    },
    metrics={
        "preview_latency_ms": 450,
        "execute_latency_ms": 1200,
        "step_timings": [...],
    },
)

if result.status == "ok":
    logger.info("Outcome persisted", extra={"plan_id": result.plan_id})
elif result.status == "partial":
    logger.warning("Partial persist", extra={
        "plan_id": result.plan_id,
        "errors": result.errors,
    })
```

**Input**: `user_id` (UUID), `plan` (dict), `signature` (dict), `outcome` (dict), `metrics` (dict).
**Output**: `PersistResult` with status, plan_id, fact_id, embedding_stored.
**Errors to handle**: `PlanWriterError` (wraps PlanLibrary failures), `ValueError` (bad input).

#### ExecutionMonitor -> PlanWriter (failed/stuck execution persist)

```python
# ExecutionMonitor calls when it detects a terminal failure:
result: PersistResult = await plan_writer.persist_outcome(
    user_id=user_id,
    plan=plan_dict,
    signature=signature_dict,
    outcome={
        "success": False,
        "error_type": "timeout",
        "error_details": {"reason": "No progress for 5 minutes"},
        "execution_start": "2026-03-19T10:00:00Z",
        "execution_end": "2026-03-19T10:05:30Z",
        "total_steps": 5,
        "failed_step": 3,
        "context_data": {},
    },
    metrics={
        "preview_latency_ms": 450,
        "execute_latency_ms": 330000,
        "step_timings": [...],
    },
)
```

**Input/Output/Errors**: Same contract as ExecuteOrchestrator.

### 4.3 Factory Function

```python
def create_plan_writer_service(
    plan_service: "PlanService",
    fact_service: "FactService",
    vector_index_service: "VectorIndexService | None",
) -> PlanWriterService:
    """
    Create PlanWriterService with downstream Memory Layer services.

    Called once during application lifespan startup in shared/app.py.
    All three services are already initialized at that point.

    Args:
        plan_service: Initialized PlanService from PlanLibrary.
        fact_service: Initialized FactService from History.
        vector_index_service: Initialized VectorIndexService, or None
            if VectorIndex is unavailable.

    Returns:
        Configured PlanWriterService.
    """
```

This function is called once during application lifespan startup in `shared/app.py` and stored on `app.state.plan_writer_service`.

---

## 5. Data Model

### 5.1 Domain Entities

#### PersistResult

```python
from pydantic import BaseModel, Field
from typing import Literal
from uuid import UUID


class PersistResult(BaseModel):
    """Result of persisting a single plan outcome."""

    plan_id: str = Field(
        description="ULID plan identifier",
        min_length=26,
        max_length=26,
    )
    fact_id: UUID | None = Field(
        default=None,
        description="History fact UUID, None if History write failed",
    )
    embedding_stored: bool = Field(
        default=False,
        description="True if VectorIndex embedding was stored successfully",
    )
    status: Literal["ok", "partial", "error"] = Field(
        description=(
            "'ok' = all writes succeeded, "
            "'partial' = PlanLibrary succeeded but History/VectorIndex had errors, "
            "'error' = PlanLibrary write failed (should not normally be returned, "
            "raises PlanWriterError instead)"
        ),
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Human-readable error descriptions for partial failures",
    )
```

#### BulkPersistResult

```python
class BulkPersistResult(BaseModel):
    """Result of bulk persisting multiple plan outcomes."""

    results: list[PersistResult] = Field(
        default_factory=list,
        description="Individual results for each outcome",
    )
    total: int = Field(
        description="Total outcomes submitted",
    )
    succeeded: int = Field(
        default=0,
        description="Count with status='ok'",
    )
    partial: int = Field(
        default=0,
        description="Count with status='partial'",
    )
    failed: int = Field(
        default=0,
        description="Count with status='error'",
    )
```

### 5.2 Error Classes

```python
class PlanWriterError(Exception):
    """Base error for PlanWriter component."""


class PlanLibraryWriteError(PlanWriterError):
    """Raised when the primary PlanLibrary write fails."""

    def __init__(self, plan_id: str, reason: str) -> None:
        self.plan_id = plan_id
        self.reason = reason
        super().__init__(
            f"PlanLibrary write failed for plan {plan_id}: {reason}"
        )


class FactDerivationError(PlanWriterError):
    """Raised when fact derivation fails (non-fatal in persist_outcome)."""

    def __init__(self, plan_id: str, reason: str) -> None:
        self.plan_id = plan_id
        self.reason = reason
        super().__init__(
            f"Fact derivation failed for plan {plan_id}: {reason}"
        )
```

### 5.3 Note on user_id

PlanWriter does **not** store `user_id` locally. It receives `user_id` as a parameter and passes it through to `FactService.store_fact(user_id=..., request=...)` for user-scoped fact storage in History. PlanLibrary's `store_plan()` does not take a `user_id` parameter (plans are system-level resources). VectorIndex's `store_embedding()` does not take a `user_id` parameter (embeddings reference plans, not users).

---

## 6. Database Schema & Migrations

**Not applicable.** PlanWriter owns no database tables. It is a Domain Layer library that delegates all persistence to Memory Layer components:

| Downstream Component | Table(s) Written | Owner |
|---------------------|-------------------|-------|
| PlanLibrary | `plans`, `plan_signatures`, `plan_outcomes`, `plan_metrics` | PlanLibrary |
| History | `history` | History |
| VectorIndex | `plan_embeddings` | VectorIndex |

---

## 7. Adapters

### 7.1 FactDeriver (Pure Function Adapter)

```python
# adapters/fact_deriver.py

from components.History.domain.models import StoreFactRequest
from shared.schemas.outcome import PlanOutcome
from shared.schemas.plan import Plan

# Fact text templates keyed by outcome
_SUCCESS_TEMPLATE = "{action} {entity_summary}"
_FAILURE_TEMPLATE = "Failed to {action}: {error_summary}"
_FALLBACK_TEMPLATE = "Executed {intent_type} plan"

# Default TTL from GLOBAL_SPEC SS7 (Tier 3: 30-day history)
DEFAULT_FACT_TTL_DAYS = 30


def derive_fact(
    plan: Plan,
    outcome: PlanOutcome,
) -> StoreFactRequest:
    """
    Extract a PII-light fact from plan execution context.

    Pure function -- no side effects, no LLM calls. Template-based
    and deterministic: same (plan, outcome) always produces the same
    StoreFactRequest.

    Intent type is read directly from plan.intent.intent (GLOBAL_SPEC
    SS2.1 nested in SS2.3). Entities from plan.intent.entities.

    Args:
        plan: Typed Plan model with plan_id, intent, graph, meta.
        outcome: Typed PlanOutcome model with success, error_type, etc.

    Returns:
        StoreFactRequest ready for FactService.store_fact().

    Raises:
        FactDerivationError: If plan is missing required fields for
            fact derivation.
    """


def _build_entity_summary(entities: dict) -> str:
    """
    Build a human-readable entity summary for fact_text.

    Example: {destination: "NYC", airline: "Delta"} -> "to NYC with Delta"
    Example: {contact: "Alice"} -> "with Alice"
    Example: {} -> ""
    """


def _build_action_summary(intent_type: str) -> str:
    """
    Build a human-readable action summary from intent_type.

    Example: "schedule_meeting" -> "Scheduled meeting"
    Example: "book_flight" -> "Booked flight"
    Example: "search_products" -> "Searched products"
    """


def _build_error_summary(outcome: PlanOutcome) -> str:
    """
    Build a human-readable error summary from outcome.

    Example: PlanOutcome(error_type="timeout", failed_step=3) -> "timeout at step 3"
    Example: PlanOutcome(error_type="api_error") -> "api_error"
    """
```

**Design rationale**: Fact derivation is template-based and deterministic (no LLM) per spec Open Question #1. This keeps fact generation fast (~microseconds), free (no API cost), predictable (same input = same output), and testable (pure function with no dependencies).

### 7.2 Shared Infrastructure Usage

| Shared utility | Usage in PlanWriter |
|---------------|---------------------|
| `shared/database/adapter.py` | Not used (no direct DB access) |
| `shared/database/error_handler.py` | Not used (no direct DB access) |
| `shared/api/error_handlers.py` | Not used (no API routes -- library component) |
| `shared/dependencies.py` | `get_plan_writer_service()` added for DI |
| `shared/app.py` | `create_plan_writer_service()` called in lifespan |

### 7.3 Dependency Injection Integration

```python
# shared/app.py -- add to lifespan (after PlanLibrary, History, VectorIndex init):
from components.PlanWriter.service.plan_writer_service import (
    create_plan_writer_service,
)

app.state.plan_writer_service = create_plan_writer_service(
    plan_service=app.state.plan_service,
    fact_service=app.state.fact_service,
    vector_index_service=app.state.vector_index_service,  # may be None
)

# shared/dependencies.py -- add:
def get_plan_writer_service(request: Request) -> Any:
    """Get PlanWriterService singleton from app state."""
    return request.app.state.plan_writer_service
```

### 7.4 Idempotency

PlanWriter is idempotent via downstream upsert semantics:

| Downstream | Idempotency Mechanism |
|-----------|----------------------|
| PlanLibrary | DB-level unique constraint on `plan_id`. `DuplicatePlanError` is caught and treated as success (plan already stored). |
| History | `fact_hash` deduplication (SHA-256 of `user_id + intent_type + fact_text + date`). Duplicate inserts return existing fact with `status="duplicate"`. |
| VectorIndex | `INSERT ... ON CONFLICT (plan_id) DO UPDATE` upsert. Duplicate calls update the existing embedding row. |

If `persist_outcome()` is called twice with the same `plan_id`:
1. PlanLibrary raises `DuplicatePlanError` -- PlanWriter catches it and treats as success
2. History returns `StoreFactResponse(status="duplicate")` -- PlanWriter uses existing `fact_id`
3. VectorIndex upserts -- no error, existing row updated

---

## 8. Sequences

### 8.1 Happy Path (All Three Writes Succeed)

```
ExecuteOrchestrator       PlanWriterService        FactDeriver        PlanService       FactService      VectorIndexService
   |                           |                       |                  |                  |                  |
   |  persist_outcome(         |                       |                  |                  |                  |
   |    user_id, plan,         |                       |                  |                  |                  |
   |    signature, outcome,    |                       |                  |                  |                  |
   |    metrics)               |                       |                  |                  |                  |
   |-------------------------->|                       |                  |                  |                  |
   |                           |                       |                  |                  |                  |
   |                           |  [1] store_plan(plan, signature, outcome, metrics)         |                  |
   |                           |------------------------------------------------->|         |                  |
   |                           |  StorePlanResponse(plan_id, stored_at)            |         |                  |
   |                           |<-------------------------------------------------|         |                  |
   |                           |                       |                  |                  |                  |
   |                           |  [2] derive_fact(plan, outcome)          |                  |                  |
   |                           |---------------------->|                  |                  |                  |
   |                           |  StoreFactRequest     |                  |                  |                  |
   |                           |<----------------------|                  |                  |                  |
   |                           |                       |                  |                  |                  |
   |                           |  [3] store_fact(user_id, request)                           |                  |
   |                           |---------------------------------------------------------------->|             |
   |                           |  StoreFactResponse(fact_id, status="ok")                       |             |
   |                           |<----------------------------------------------------------------|             |
   |                           |                       |                  |                  |                  |
   |                           |  [4] store_embedding(plan_id, plan_data)                                      |
   |                           |---------------------------------------------------------------------------------->|
   |                           |  None (success)                                                                   |
   |                           |<----------------------------------------------------------------------------------|
   |                           |                       |                  |                  |                  |
   |  PersistResult(           |                       |                  |                  |                  |
   |    plan_id=...,           |                       |                  |                  |                  |
   |    fact_id=...,           |                       |                  |                  |                  |
   |    embedding_stored=True, |                       |                  |                  |                  |
   |    status="ok",           |                       |                  |                  |                  |
   |    errors=[])             |                       |                  |                  |                  |
   |<--------------------------|                       |                  |                  |                  |
```

### 8.2 Partial Failure (History Write Fails)

```
ExecuteOrchestrator       PlanWriterService        FactDeriver        PlanService       FactService      VectorIndexService
   |                           |                       |                  |                  |                  |
   |  persist_outcome(...)     |                       |                  |                  |                  |
   |-------------------------->|                       |                  |                  |                  |
   |                           |                       |                  |                  |                  |
   |                           |  [1] store_plan(...)  OK                 |                  |                  |
   |                           |------------------------------------------------->|         |                  |
   |                           |  StorePlanResponse    |                  |                  |                  |
   |                           |<-------------------------------------------------|         |                  |
   |                           |                       |                  |                  |                  |
   |                           |  [2] derive_fact(...) OK                 |                  |                  |
   |                           |---------------------->|                  |                  |                  |
   |                           |  StoreFactRequest     |                  |                  |                  |
   |                           |<----------------------|                  |                  |                  |
   |                           |                       |                  |                  |                  |
   |                           |  [3] store_fact(...)  FAILS                                 |                  |
   |                           |---------------------------------------------------------------->|             |
   |                           |  HistoryError (logged, not re-raised)                          |             |
   |                           |<----------------------------------------------------------------|             |
   |                           |                       |                  |                  |                  |
   |                           |  [4] store_embedding(...)  (still attempted)                                  |
   |                           |---------------------------------------------------------------------------------->|
   |                           |  None (success)                                                                   |
   |                           |<----------------------------------------------------------------------------------|
   |                           |                       |                  |                  |                  |
   |  PersistResult(           |                       |                  |                  |                  |
   |    plan_id=...,           |                       |                  |                  |                  |
   |    fact_id=None,          |                       |                  |                  |                  |
   |    embedding_stored=True, |                       |                  |                  |                  |
   |    status="partial",      |                       |                  |                  |                  |
   |    errors=["History write failed: ..."])           |                  |                  |                  |
   |<--------------------------|                       |                  |                  |                  |
```

### 8.3 VectorIndex Down (Graceful Degradation)

```
ExecuteOrchestrator       PlanWriterService        PlanService       FactService      (VectorIndex = None)
   |                           |                       |                  |
   |  persist_outcome(...)     |                       |                  |
   |-------------------------->|                       |                  |
   |                           |                       |                  |
   |                           |  [1] store_plan(...)  OK                 |
   |                           |---------------------->|                  |
   |                           |  StorePlanResponse    |                  |
   |                           |<----------------------|                  |
   |                           |                       |                  |
   |                           |  [2-3] derive + store_fact(...)  OK      |
   |                           |---------------------------------------->|
   |                           |  StoreFactResponse(fact_id)              |
   |                           |<-----------------------------------------|
   |                           |                       |                  |
   |                           |  [4] vector_index_service is None        |
   |                           |      log warning, skip embedding         |
   |                           |                       |                  |
   |  PersistResult(           |                       |                  |
   |    plan_id=...,           |                       |                  |
   |    fact_id=...,           |                       |                  |
   |    embedding_stored=False,|                       |                  |
   |    status="ok",           |                       |                  |
   |    errors=[])             |                       |                  |
   |<--------------------------|                       |                  |
```

**Note**: When VectorIndex is None, `status` is still `"ok"` because VectorIndex is optional. The `embedding_stored=False` flag communicates the skip to the caller.

### 8.4 PlanLibrary Write Fails (Fatal)

```
ExecuteOrchestrator       PlanWriterService        PlanService
   |                           |                       |
   |  persist_outcome(...)     |                       |
   |-------------------------->|                       |
   |                           |                       |
   |                           |  [1] store_plan(...)  |
   |                           |---------------------->|
   |                           |  PlanTooLargeError    |
   |                           |<----------------------|
   |                           |                       |
   |                           |  (History and VectorIndex NOT attempted)
   |                           |                       |
   |  PlanLibraryWriteError    |                       |
   |    plan_id=...,           |                       |
   |    reason="Plan size..."  |                       |
   |<--------------------------|                       |
```

### 8.5 Idempotent Retry (Duplicate plan_id)

```
Caller                    PlanWriterService        PlanService       FactService      VectorIndexService
   |                           |                       |                  |                  |
   |  persist_outcome(...)     |                       |                  |                  |
   |  (same plan_id as before) |                       |                  |                  |
   |-------------------------->|                       |                  |                  |
   |                           |                       |                  |                  |
   |                           |  [1] store_plan(...)  |                  |                  |
   |                           |---------------------->|                  |                  |
   |                           |  DuplicatePlanError   |                  |                  |
   |                           |  (caught, treated     |                  |                  |
   |                           |   as success)         |                  |                  |
   |                           |<----------------------|                  |                  |
   |                           |                       |                  |                  |
   |                           |  [2-3] derive + store_fact(...)          |                  |
   |                           |---------------------------------------->|                  |
   |                           |  StoreFactResponse(status="duplicate")   |                  |
   |                           |<-----------------------------------------|                  |
   |                           |                       |                  |                  |
   |                           |  [4] store_embedding(...)  (upsert)                         |
   |                           |---------------------------------------------------------------->|
   |                           |  None (upserted)                                               |
   |                           |<----------------------------------------------------------------|
   |                           |                       |                  |                  |
   |  PersistResult(           |                       |                  |                  |
   |    status="ok",           |                       |                  |                  |
   |    ...)                   |                       |                  |                  |
   |<--------------------------|                       |                  |                  |
```

---

## 9. Dependencies & External Integrations

### 9.1 Python Packages

No new Python packages required. PlanWriter uses only existing dependencies:

| Package | Version | Justification |
|---------|---------|---------------|
| `pydantic` | `>=2.0` | PersistResult, BulkPersistResult models (already in pyproject.toml) |

### 9.2 Internal Component Dependencies

| Component | Dependency Type | Direction | Interface Used |
|-----------|----------------|-----------|----------------|
| PlanLibrary | Downstream write | PlanWriter -> PlanLibrary | `PlanService.store_plan(plan, signature, outcome, metrics)` |
| History | Downstream write | PlanWriter -> History | `FactService.store_fact(user_id, StoreFactRequest)` |
| VectorIndex | Downstream write (optional) | PlanWriter -> VectorIndex | `VectorIndexService.store_embedding(plan_id, plan_data)` |
| ExecuteOrchestrator | Upstream consumer | ExecuteOrchestrator -> PlanWriter | `PlanWriterService.persist_outcome(...)` |
| ExecutionMonitor | Upstream consumer | ExecutionMonitor -> PlanWriter | `PlanWriterService.persist_outcome(...)` |

This matches MODULAR_ARCHITECTURE v1.3 SS4 Domain/Service Layer dependency graph.

### 9.3 External Services

None. PlanWriter is fully self-contained with no external API calls. All external integrations are handled by the downstream Memory Layer components.

---

## 10. Observability & Safety

### 10.1 Structured Logging

```python
import logging

logger = logging.getLogger("planwriter")

# Successful persist
logger.info("outcome_persisted", extra={
    "plan_id": plan_id,
    "fact_id": str(fact_id),
    "embedding_stored": True,
    "status": "ok",
    "plan_library_latency_ms": round(pl_ms, 2),
    "history_latency_ms": round(h_ms, 2),
    "vectorindex_latency_ms": round(vi_ms, 2),
    "total_latency_ms": round(total_ms, 2),
    "component": "PlanWriter",
    "op": "persist_outcome",
})

# Fact derived
logger.info("fact_derived", extra={
    "plan_id": plan_id,
    "intent_type": intent_type,
    "outcome_success": outcome.get("success"),
    "component": "PlanWriter",
    "op": "derive_fact",
})

# Partial failure (History)
logger.warning("persist_partial_failure", extra={
    "plan_id": plan_id,
    "failed_step": "history",
    "error": str(e),
    "component": "PlanWriter",
    "op": "persist_outcome",
})

# Partial failure (VectorIndex)
logger.warning("persist_partial_failure", extra={
    "plan_id": plan_id,
    "failed_step": "vectorindex",
    "error": str(e),
    "component": "PlanWriter",
    "op": "persist_outcome",
})

# VectorIndex unavailable (None)
logger.warning("vectorindex_unavailable", extra={
    "plan_id": plan_id,
    "component": "PlanWriter",
    "op": "persist_outcome",
})

# PlanLibrary failure (fatal)
logger.error("persist_failed", extra={
    "plan_id": plan_id,
    "error": str(e),
    "component": "PlanWriter",
    "op": "persist_outcome",
})

# Embedding stored
logger.info("embedding_stored", extra={
    "plan_id": plan_id,
    "component": "PlanWriter",
    "op": "persist_outcome",
})

# Bulk persist summary
logger.info("bulk_persist_completed", extra={
    "total": total,
    "succeeded": succeeded,
    "partial": partial_count,
    "failed": failed,
    "total_latency_ms": round(total_ms, 2),
    "component": "PlanWriter",
    "op": "bulk_persist",
})
```

**Never log**: Raw plan content (canonical JSON), embedding vectors, signature bytes, credentials, full API responses, raw metric payloads. Only log `plan_id`, `intent_type`, boolean outcomes, and latency numbers.

### 10.2 Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `planwriter_persist_duration_seconds` | Histogram | `status` | Total time for `persist_outcome()` |
| `planwriter_persist_total` | Counter | `status` | Total persist operations |
| `planwriter_planlibrary_duration_seconds` | Histogram | `status` | Time for PlanLibrary write |
| `planwriter_history_duration_seconds` | Histogram | `status` | Time for History write |
| `planwriter_vectorindex_duration_seconds` | Histogram | `status` | Time for VectorIndex write |
| `planwriter_fact_derivation_duration_seconds` | Histogram | `status` | Time for fact derivation |
| `planwriter_partial_failures_total` | Counter | `failed_step` | Partial failures by component |
| `planwriter_bulk_persist_total` | Counter | `status` | Total bulk persist operations |

Labels:
- `status`: `success`, `partial`, `error`
- `failed_step`: `history`, `vectorindex`, `fact_derivation`

### 10.3 Error Classes Summary

| Error | When | Caller Action |
|-------|------|---------------|
| `PlanLibraryWriteError` | PlanLibrary.store_plan() fails | Caller retries or reports failure |
| `FactDerivationError` | Fact derivation fails (caught internally) | Logged as partial failure, not re-raised |
| `ValueError` | Empty plan, missing plan_id, empty outcomes list | Caller fixes input |

---

## 11. Non-Functional Requirements

### 11.1 Performance

| Operation | p95 Target | p99 Target | Notes |
|-----------|-----------|-----------|-------|
| `persist_outcome()` (total) | < 200 ms | < 350 ms | PlanLibrary write (~50-100ms) + History write (~30-50ms) + VectorIndex (~25ms) |
| PlanLibrary write | < 100 ms | < 150 ms | Single DB transaction (plan + outcome + metrics) |
| History fact derivation | < 1 ms | < 2 ms | Pure function, template-based |
| History write | < 50 ms | < 80 ms | Single INSERT with dedup |
| VectorIndex write | < 25 ms | < 40 ms | ONNX embed (~10ms) + upsert (~15ms) |
| `bulk_persist()` (10 items) | < 2 s | < 3.5 s | Sequential, 10x single persist |

**Note**: History and VectorIndex writes are independent and could be parallelized using `asyncio.gather()` after PlanLibrary succeeds. This optimization is deferred for MVP -- sequential writes keep error handling straightforward and the total latency is within the 200ms p95 target.

### 11.2 Availability

- PlanWriter depends on PlanLibrary availability (required) and History availability (secondary)
- If VectorIndex is down, PlanWriter still succeeds for PlanLibrary + History
- Target: Same as system baseline (99.9% cloud, best-effort local)
- PlanLibrary is the availability gate -- PlanWriter's effective availability equals PlanLibrary's

### 11.3 Scalability

- Stateless -- horizontal scaling is automatic with application instances
- No shared state between instances (all state in downstream Memory Layer services)
- Bulk persist is sequential per instance; parallelism is achieved via multiple callers

### 11.4 Testing Strategy

| Test Type | File | Coverage |
|-----------|------|----------|
| Unit -- persist_outcome | `test_unit.py` | Happy path, partial failures, VectorIndex None, PlanLibrary failure |
| Unit -- fact_deriver | `test_unit.py` | Success/failure templates, entity extraction, edge cases (no entities, unknown intent) |
| Unit -- bulk_persist | `test_unit.py` | Multiple outcomes, empty list validation |
| Contract | `test_contract.py` | PersistResult schema, SPEC acceptance scenarios (US1-US5) |
| Observability | `test_observability.py` | No plan content in logs, no embedding vectors in logs |
| Edge cases | `test_unit.py` | Duplicate plan_id, empty plan, very large plan, failed execution |

**Test fixtures**: Tests use mock PlanService, FactService, and VectorIndexService. No real database needed for unit tests. Integration tests with real services require the full Docker Compose setup.

---

## 12. Architectural Considerations

### 12.1 Blast Radius Containment

- PlanWriter failure does not prevent plan execution (called post-execution)
- PlanLibrary failure is the only fatal case; History and VectorIndex failures are contained
- No new infrastructure (no DB connections, no Redis, no queues) -- PlanWriter cannot cause resource exhaustion
- If downstream services are slow, PlanWriter's `persist_outcome()` blocks the caller but does not affect other concurrent requests (no shared state)

### 12.2 Fault Isolation

- **PlanLibrary down**: `persist_outcome()` raises `PlanLibraryWriteError`. History and VectorIndex are NOT attempted (PlanLibrary is the primary store -- partial plan storage is not allowed).
- **History down**: `persist_outcome()` catches the error, logs a warning, continues to VectorIndex. Returns `PersistResult(status="partial", fact_id=None)`.
- **VectorIndex down (None)**: `persist_outcome()` logs a warning, skips embedding. Returns `PersistResult(embedding_stored=False)`. Status is `"ok"` because VectorIndex is optional.
- **VectorIndex down (raises)**: Same as VectorIndex=None -- error is caught, logged, `embedding_stored=False`.
- **Fact derivation fails**: Error is caught, logged. History write is skipped. VectorIndex still attempted. Returns `PersistResult(status="partial")`.

### 12.3 Determinism

- `derive_fact()` is deterministic: same (plan, outcome) always produces the same StoreFactRequest (template-based, no LLM, no randomness)
- `persist_outcome()` side effects are deterministic given downstream service behavior (same inputs to downstream services)
- Idempotency is guaranteed via downstream upsert semantics

### 12.4 Statelessness

PlanWriter holds no mutable state at runtime. The three service references injected at startup are immutable. There is no:
- In-memory cache
- Background task
- Queue or buffer
- Pending write queue
- Retry queue (callers handle retries)

### 12.5 Write Ordering Rationale

The write ordering (PlanLibrary -> History -> VectorIndex) is deliberate:

1. **PlanLibrary first**: It is the primary store. If it fails, no downstream writes are attempted. This prevents orphaned facts (History) or embeddings (VectorIndex) that reference non-existent plans.
2. **History second**: Facts reference `source_plan_id` which must exist in PlanLibrary. History write is secondary -- if it fails, the plan is still stored and the system can derive the fact later.
3. **VectorIndex last**: Embeddings are the most optional. VectorIndex may not even be available. The embedding can always be backfilled later via `bulk_store()`.

---

## 13. Risks & Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Partial persistence (PlanLibrary ok, History fails) -- system has plan but no learning fact | Medium | Return partial result with errors list. Caller logs and can retry. `bulk_persist()` enables backfill. |
| Fact derivation quality -- template-based `fact_text` may be too generic for complex plans | Low | Start with templates (deterministic, fast). Upgrade to LLM-based derivation later if needed (YAGNI). |
| Idempotency edge case -- PlanLibrary raises `DuplicatePlanError` but History has a different fact_hash (different day) | Low | Acceptable: the new fact records the retry event on a different day. History dedup is per-day. |
| VectorIndex consistently unavailable -- embeddings never stored, similarity search degraded | Low | `bulk_store()` enables backfill. ContextRAG falls back to structured PlanLibrary queries. |
| Large burst of `persist_outcome()` calls saturate downstream services | Low | PlanWriter is sequential; callers (ExecuteOrchestrator, ExecutionMonitor) naturally throttle. Bulk operations use `bulk_persist()` which is also sequential. |

### Open Questions

1. **Should History and VectorIndex writes be parallelized?** -- Recommendation: defer for MVP. Sequential writes simplify error handling and total latency is within the 200ms p95 target. Add `asyncio.gather()` if profiling shows it is a bottleneck.
2. **Should `persist_outcome()` accept a `DuplicatePlanError` from PlanLibrary silently?** -- Recommendation: yes, treat as idempotent success. Log at INFO level. The spec requires idempotency (FR-009).
3. **Should fact TTL be configurable per intent_type?** -- Recommendation: use default 30 days for now (GLOBAL_SPEC SS7 Tier 3). Add per-intent configuration later if needed.
4. **Should PlanWriter validate the metrics dict or pass it through?** -- Recommendation: pass through. PlanLibrary validates size and schema. PlanWriter should not duplicate validation (YAGNI, single responsibility).

---

## 14. Post-Generation Validation Checklist

- [x] No owned tables (Domain Layer, not Memory Layer)
- [x] Dependencies match MODULAR_ARCHITECTURE: PlanLibrary, History, VectorIndex
- [x] Upstream consumers documented: ExecuteOrchestrator, ExecutionMonitor (SS4.2)
- [x] Idempotent via downstream upsert semantics (SS7.4)
- [x] `user_id` passed to `History.store_fact()` but not stored locally (SS5.3)
- [x] No new Python packages needed (SS9.1)
- [x] Error handling: domain errors in `domain/models.py` (SS5.2)
- [x] Conformance header references current document versions (SS2)
- [x] Every upstream consumer has documented interface contract (SS4.2)
- [x] Prometheus metrics defined with names and types (SS10.2)
- [x] Structured logging with `plan_id` correlation, no PII (SS10.1)
- [x] Factory function documented for DI wiring (SS4.3, SS7.3)
- [x] Sequences cover happy path, partial failure, VectorIndex down, retry/idempotency (SS8)
- [x] Blast radius analysis documented (SS3)
- [x] Write ordering rationale documented (SS12.5)
