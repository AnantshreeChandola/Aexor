# PlanWriter -- Flow Diagrams

**Component**: `components/PlanWriter/`
**Reference**: `components/PlanWriter/LLD.md`
**Created**: 2026-03-19

---

## 1. Component Context Diagram

Shows PlanWriter's position in the system: upstream consumers and downstream dependencies.

```mermaid
graph TB
    subgraph Orchestration Layer
        EO[ExecuteOrchestrator]
        EM[ExecutionMonitor]
    end

    subgraph Domain / Service Layer
        PW[PlanWriter]
        FD[FactDeriver<br/>pure function]
    end

    subgraph Memory / Persistence Layer
        PL[PlanLibrary<br/>PlanService]
        HI[History<br/>FactService]
        VI[VectorIndex<br/>VectorIndexService]
    end

    subgraph Database Layer
        PG[(PostgreSQL<br/>plans, history,<br/>plan_embeddings)]
    end

    EO -- "persist_outcome()" --> PW
    EM -- "persist_outcome()" --> PW
    PW -- "derive_fact()" --> FD
    PW -- "store_plan()" --> PL
    PW -- "store_fact()" --> HI
    PW -. "store_embedding()<br/>(optional)" .-> VI
    PL --> PG
    HI --> PG
    VI --> PG
```

---

## 2. persist_outcome() -- Happy Path Flow

Complete successful execution of `persist_outcome()` with all three downstream writes succeeding.

```mermaid
flowchart TD
    A([persist_outcome called]) --> B{plan is valid?}
    B -- No --> B1[/raise ValueError/]
    B -- Yes --> C[Extract plan_id from plan]

    C --> D[Step 1: Write to PlanLibrary]
    D --> D1[PlanService.store_plan<br/>plan + signature + outcome + metrics]
    D1 --> D2{PlanLibrary write<br/>succeeded?}
    D2 -- No --> D3[/raise PlanLibraryWriteError/]
    D2 -- Yes --> E[StorePlanResponse received]

    E --> F[Step 2: Derive fact]
    F --> F1[FactDeriver.derive_fact<br/>plan + outcome]
    F1 --> F2[StoreFactRequest built:<br/>fact_text, intent_type, entities,<br/>outcome, source_plan_id, ttl_days=30]

    F2 --> G[Step 3: Write to History]
    G --> G1[FactService.store_fact<br/>user_id + StoreFactRequest]
    G1 --> G2{History write<br/>succeeded?}
    G2 -- Yes --> G3[fact_id captured]
    G2 -- No --> G4[Log warning<br/>fact_id = None<br/>add to errors list]

    G3 --> H{VectorIndex<br/>available?}
    G4 --> H

    H -- "service is None" --> H1[Log warning<br/>embedding_stored = False]
    H -- "service exists" --> I[Step 4: Write to VectorIndex]
    I --> I1[VectorIndexService.store_embedding<br/>plan_id + plan_data]
    I1 --> I2{VectorIndex write<br/>succeeded?}
    I2 -- Yes --> I3[embedding_stored = True]
    I2 -- No --> I4[Log warning<br/>embedding_stored = False<br/>add to errors list]

    H1 --> J[Build PersistResult]
    I3 --> J
    I4 --> J

    J --> K{Any errors<br/>in list?}
    K -- No --> K1[status = ok]
    K -- Yes --> K2[status = partial]

    K1 --> L([Return PersistResult])
    K2 --> L
```

---

## 3. persist_outcome() -- Error Handling Decision Tree

Shows the fault isolation and error propagation logic.

```mermaid
flowchart TD
    START([persist_outcome]) --> V{Validate<br/>input}
    V -- invalid --> VE[/raise ValueError/]
    V -- valid --> PL

    PL[PlanLibrary.store_plan] --> PLR{Result?}
    PLR -- success --> FACT
    PLR -- DuplicatePlanError --> DUP[Treat as success<br/>plan already stored]
    PLR -- other error --> FATAL[/raise PlanLibraryWriteError/<br/>History + VectorIndex<br/>NOT attempted]
    DUP --> FACT

    FACT[derive_fact] --> FACTR{Result?}
    FACTR -- success --> HIST
    FACTR -- error --> FACTFAIL[Log error<br/>skip History write<br/>add to errors]
    FACTFAIL --> VI

    HIST[FactService.store_fact] --> HISTR{Result?}
    HISTR -- ok/duplicate --> HISTOK[fact_id captured]
    HISTR -- error --> HISTFAIL[Log warning<br/>fact_id = None<br/>add to errors]
    HISTOK --> VI
    HISTFAIL --> VI

    VI{VectorIndex<br/>service?}
    VI -- None --> SKIP[Skip embedding<br/>embedding_stored = False]
    VI -- exists --> VSTORE[VectorIndexService.store_embedding]
    VSTORE --> VSTORER{Result?}
    VSTORER -- success --> VIOK[embedding_stored = True]
    VSTORER -- error --> VIFAIL[Log warning<br/>embedding_stored = False<br/>add to errors]

    SKIP --> BUILD
    VIOK --> BUILD
    VIFAIL --> BUILD

    BUILD[Build PersistResult] --> STATUS{errors list<br/>empty?}
    STATUS -- yes --> OK[status = ok]
    STATUS -- no --> PARTIAL[status = partial]
    OK --> RET([Return PersistResult])
    PARTIAL --> RET
```

---

## 4. Fact Derivation Flow

Shows how `derive_fact()` transforms plan + outcome into a `StoreFactRequest`.

```mermaid
flowchart TD
    A([derive_fact called<br/>plan + outcome]) --> B[Extract intent_type<br/>from plan.meta.intent_type<br/>or plan.intent.intent<br/>or 'unknown']

    B --> C[Extract entities<br/>from plan.intent.entities<br/>or plan.entities<br/>or empty dict]

    C --> D{outcome.success?}

    D -- True --> E[Build success fact_text<br/>template: action + entity_summary<br/>e.g. 'Booked flight to NYC with Delta']
    D -- False --> F[Build failure fact_text<br/>template: 'Failed to action: error_summary'<br/>e.g. 'Failed to book flight: API timeout at step 3']

    E --> G[Build StoreFactRequest]
    F --> G

    G --> H[fact_text = derived text<br/>intent_type = extracted type<br/>entities = extracted entities<br/>outcome = success boolean<br/>source_plan_id = plan.plan_id<br/>ttl_days = 30]

    H --> I([Return StoreFactRequest])
```

---

## 5. Write Ordering and Dependency

Shows why writes must happen in a specific order and what happens on failure at each step.

```mermaid
sequenceDiagram
    participant Caller as ExecuteOrchestrator
    participant PW as PlanWriterService
    participant PL as PlanLibrary
    participant FD as FactDeriver
    participant HI as History
    participant VI as VectorIndex

    Note over PW: Step 1: PRIMARY write (must succeed)
    Caller->>PW: persist_outcome(user_id, plan, sig, outcome, metrics)
    PW->>PL: store_plan(plan, signature, outcome, metrics)

    alt PlanLibrary succeeds
        PL-->>PW: StorePlanResponse(plan_id, stored_at)

        Note over PW: Step 2: Derive fact (pure function)
        PW->>FD: derive_fact(plan, outcome)
        FD-->>PW: StoreFactRequest

        Note over PW: Step 3: SECONDARY write (partial failure ok)
        PW->>HI: store_fact(user_id, request)
        alt History succeeds
            HI-->>PW: StoreFactResponse(fact_id)
        else History fails
            HI-->>PW: HistoryError (caught, logged)
            Note over PW: fact_id = None, add to errors
        end

        Note over PW: Step 4: OPTIONAL write (graceful degradation)
        alt VectorIndex is None
            Note over PW: Skip, log warning
        else VectorIndex available
            PW->>VI: store_embedding(plan_id, plan_data)
            alt VectorIndex succeeds
                VI-->>PW: None (success)
            else VectorIndex fails
                VI-->>PW: Error (caught, logged)
                Note over PW: embedding_stored = False
            end
        end

        PW-->>Caller: PersistResult(status=ok|partial)

    else PlanLibrary fails
        PL-->>PW: Error
        Note over PW: History + VectorIndex NOT attempted
        PW-->>Caller: raise PlanLibraryWriteError
    end
```

---

## 6. Idempotency Flow

Shows how duplicate `persist_outcome()` calls are handled safely.

```mermaid
sequenceDiagram
    participant Caller
    participant PW as PlanWriterService
    participant PL as PlanLibrary
    participant HI as History
    participant VI as VectorIndex

    Note over Caller: First call (original)
    Caller->>PW: persist_outcome(plan_id=X)
    PW->>PL: store_plan(plan_id=X)
    PL-->>PW: StorePlanResponse (new row inserted)
    PW->>HI: store_fact(...)
    HI-->>PW: StoreFactResponse(status=ok, fact_id=abc)
    PW->>VI: store_embedding(plan_id=X)
    VI-->>PW: None (new row inserted)
    PW-->>Caller: PersistResult(status=ok)

    Note over Caller: Second call (retry/duplicate)
    Caller->>PW: persist_outcome(plan_id=X)
    PW->>PL: store_plan(plan_id=X)
    PL-->>PW: DuplicatePlanError (unique constraint)
    Note over PW: Caught, treated as success

    PW->>HI: store_fact(...)
    HI-->>PW: StoreFactResponse(status=duplicate, fact_id=abc)
    Note over PW: Same fact_hash, existing fact returned

    PW->>VI: store_embedding(plan_id=X)
    VI-->>PW: None (ON CONFLICT DO UPDATE)
    Note over PW: Upsert, row updated

    PW-->>Caller: PersistResult(status=ok)
    Note over Caller: Same result, safe to retry
```

---

## 7. bulk_persist() Flow

Shows how bulk persistence processes multiple outcomes.

```mermaid
flowchart TD
    A([bulk_persist called<br/>user_id + outcomes list]) --> B{outcomes<br/>empty?}
    B -- Yes --> B1[/raise ValueError/]
    B -- No --> C[Initialize counters:<br/>succeeded=0, partial=0, failed=0<br/>results list = empty]

    C --> D[For each outcome<br/>in outcomes list]
    D --> E[Extract plan, signature,<br/>outcome, metrics from dict]

    E --> F[Call persist_outcome<br/>user_id, plan, signature,<br/>outcome, metrics]

    F --> G{Result?}
    G -- "PersistResult<br/>status=ok" --> G1[succeeded += 1<br/>append to results]
    G -- "PersistResult<br/>status=partial" --> G2[partial += 1<br/>append to results]
    G -- "PlanLibraryWriteError<br/>caught" --> G3[failed += 1<br/>append error result]

    G1 --> H{More outcomes?}
    G2 --> H
    G3 --> H

    H -- Yes --> D
    H -- No --> I[Build BulkPersistResult<br/>results, total, succeeded,<br/>partial, failed]

    I --> J([Return BulkPersistResult])
```

---

## 8. DI Wiring Diagram

Shows how PlanWriterService is initialized and wired during application startup.

```mermaid
sequenceDiagram
    participant Lifespan as shared/app.py lifespan
    participant DB as SharedDatabaseAdapter
    participant PLS as PlanService
    participant FS as FactService
    participant VIS as VectorIndexService
    participant PWS as PlanWriterService
    participant State as app.state

    Note over Lifespan: Phase 1: Memory Layer init
    Lifespan->>DB: SharedDatabaseAdapter()
    Lifespan->>PLS: PlanService(db_adapter=plan_db)
    Lifespan->>State: app.state.plan_service = PLS
    Lifespan->>FS: FactService(db_adapter, evidence_svc, pattern_svc)
    Lifespan->>State: app.state.fact_service = FS

    alt VectorIndex available
        Lifespan->>VIS: create_vector_index_service(db)
        Lifespan->>State: app.state.vector_index_service = VIS
    else VectorIndex unavailable
        Note over Lifespan: Caught exception, graceful degradation
        Lifespan->>State: app.state.vector_index_service = None
    end

    Note over Lifespan: Phase 2: Domain Layer init
    Lifespan->>PWS: create_plan_writer_service(<br/>plan_service=PLS,<br/>fact_service=FS,<br/>vector_index_service=VIS or None)
    Lifespan->>State: app.state.plan_writer_service = PWS
```

---

## 9. System-Level Data Flow

Shows PlanWriter's role in the end-to-end flow from user request to learning loop closure.

```mermaid
flowchart LR
    subgraph "User Request Flow"
        U[User] --> IN[Intake]
        IN --> CR[ContextRAG]
        CR --> PL_Q[PlanLibrary<br/>query plans]
        CR --> HI_Q[History<br/>query facts]
        CR --> VI_Q[VectorIndex<br/>search similar]
        CR --> PLN[Planner]
        PLN --> SIG[Signer]
    end

    subgraph "Execution Flow"
        SIG --> PV[PreviewOrchestrator]
        PV --> AG[ApprovalGate]
        AG --> EO[ExecuteOrchestrator]
        EO --> N8N[n8n Workflow]
    end

    subgraph "Learning Loop (PlanWriter)"
        N8N --> PW[PlanWriter]
        PW --> PL_W[PlanLibrary<br/>store plan + outcome]
        PW --> HI_W[History<br/>store derived fact]
        PW --> VI_W[VectorIndex<br/>store embedding]
    end

    PL_W -.-> PL_Q
    HI_W -.-> HI_Q
    VI_W -.-> VI_Q

    style PW fill:#f9f,stroke:#333,stroke-width:2px
    style PL_W fill:#bbf,stroke:#333
    style HI_W fill:#bbf,stroke:#333
    style VI_W fill:#bbf,stroke:#333
```

---

## 10. Failure Modes Summary

```mermaid
flowchart TD
    subgraph "Failure at PlanLibrary (FATAL)"
        F1[PlanLibrary.store_plan fails]
        F1 --> F1A[History NOT attempted]
        F1 --> F1B[VectorIndex NOT attempted]
        F1 --> F1C[raise PlanLibraryWriteError]
    end

    subgraph "Failure at History (PARTIAL)"
        F2[History.store_fact fails]
        F2 --> F2A[PlanLibrary already succeeded]
        F2 --> F2B[VectorIndex still attempted]
        F2 --> F2C[PersistResult status=partial<br/>fact_id=None]
    end

    subgraph "Failure at VectorIndex (PARTIAL)"
        F3A[VectorIndex is None]
        F3B[VectorIndex.store_embedding fails]
        F3A --> F3C[embedding_stored=False<br/>status=ok]
        F3B --> F3D[embedding_stored=False<br/>status=partial]
    end

    subgraph "Failure at Fact Derivation (PARTIAL)"
        F4[derive_fact fails]
        F4 --> F4A[History write skipped]
        F4 --> F4B[VectorIndex still attempted]
        F4 --> F4C[PersistResult status=partial<br/>fact_id=None]
    end

    style F1 fill:#f66,stroke:#333,color:#fff
    style F2 fill:#fa0,stroke:#333
    style F3A fill:#ff0,stroke:#333
    style F3B fill:#fa0,stroke:#333
    style F4 fill:#fa0,stroke:#333
```
