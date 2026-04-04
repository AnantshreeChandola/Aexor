# PreviewOrchestrator — Flow Diagrams

## 1. Main Preview Flow

```mermaid
flowchart TD
    START([preview request]) --> DAG_RESOLVE[Resolve DAG levels<br/>via Kahn's algorithm]

    DAG_RESOLVE -- CycleDetectedError --> PREVIEW_ERROR[/PreviewError/]
    DAG_RESOLVE -- success --> LEVEL_LOOP[Process levels<br/>sequentially]

    LEVEL_LOOP --> CLASSIFY{Classify each step<br/>in current level}

    CLASSIFY -- "type=llm_reasoning<br/>or policy_check" --> DEFER_TYPE[Mark DEFERRED<br/>reason: step type]
    CLASSIFY -- "gate_id set" --> DEFER_GATE[Mark DEFERRED<br/>reason: gated]
    CLASSIFY -- "depends on<br/>deferred step" --> DEFER_CASCADE[Mark DEFERRED<br/>reason: dependency_deferred]
    CLASSIFY -- "depends on<br/>failed step" --> SKIP[Mark SKIPPED<br/>reason: dependency_failed]
    CLASSIFY -- "previewable=false<br/>(PluginRegistry)" --> DEFER_PREVIEW[Mark DEFERRED<br/>reason: non_previewable]
    CLASSIFY -- "previewable=true" --> DISPATCH_QUEUE[Add to dispatch queue]

    DEFER_TYPE --> TRACK_DEFERRED[Add to deferred set]
    DEFER_GATE --> TRACK_DEFERRED
    DEFER_CASCADE --> TRACK_DEFERRED
    DEFER_PREVIEW --> TRACK_DEFERRED
    SKIP --> TRACK_FAILED[Add to failed set]

    DISPATCH_QUEUE --> PARALLEL{Dispatch all<br/>queued steps<br/>via asyncio.gather}

    PARALLEL --> RESOLVE_TEMPLATES[Resolve template args<br/>from completed steps]
    RESOLVE_TEMPLATES --> MCP_INVOKE[MCP invoke<br/>dry_run=true<br/>credentials=None]

    MCP_INVOKE -- success --> STEP_COMPLETE[PreviewStepResult<br/>status=completed]
    MCP_INVOKE -- timeout/error --> STEP_FAIL[PreviewStepResult<br/>status=failed]

    STEP_COMPLETE --> RECORD[Record in<br/>step_results]
    STEP_FAIL --> RECORD_FAIL[Record in<br/>step_results + failed set]

    TRACK_DEFERRED --> NEXT_LEVEL{More levels?}
    TRACK_FAILED --> NEXT_LEVEL
    RECORD --> NEXT_LEVEL
    RECORD_FAIL --> NEXT_LEVEL

    NEXT_LEVEL -- yes --> CLASSIFY
    NEXT_LEVEL -- no --> CACHE_STATE

    CACHE_STATE[Cache preview state<br/>in Redis] --> CACHE_CHECK{Redis available?}
    CACHE_CHECK -- yes --> CACHE_STORE[SET preview:user:plan<br/>TTL=900s]
    CACHE_CHECK -- no --> CACHE_WARN[Log warning<br/>cached_state_key=None]

    CACHE_STORE --> BUILD_RESULT
    CACHE_WARN --> BUILD_RESULT

    BUILD_RESULT[Build PreviewResult] --> ALL_FAIL{All steps<br/>failed/deferred?}
    ALL_FAIL -- yes --> RESULT_NO_EXEC[can_execute=false]
    ALL_FAIL -- no --> HAS_FAILURES{Any step<br/>failures?}
    HAS_FAILURES -- yes --> RESULT_PARTIAL[can_execute=true<br/>partial=true]
    HAS_FAILURES -- no --> RESULT_OK[can_execute=true<br/>partial=false]

    RESULT_NO_EXEC --> RETURN([PreviewResult])
    RESULT_PARTIAL --> RETURN
    RESULT_OK --> RETURN

    PREVIEW_ERROR --> END_ERROR([Error propagated<br/>to caller])
```

## 2. Step Classification Decision Tree

```mermaid
flowchart TD
    STEP([PlanStep]) --> DEP_CHECK{Any dependency<br/>in deferred set?}
    DEP_CHECK -- yes --> CASCADE_DEFER[DEFERRED<br/>dependency_deferred]
    DEP_CHECK -- no --> FAIL_CHECK{Any dependency<br/>in failed set?}
    FAIL_CHECK -- yes --> CASCADE_SKIP[SKIPPED<br/>dependency_failed]
    FAIL_CHECK -- no --> TYPE_CHECK{step.type?}

    TYPE_CHECK -- llm_reasoning --> TYPE_DEFER[DEFERRED<br/>llm_reasoning]
    TYPE_CHECK -- policy_check --> TYPE_DEFER_PC[DEFERRED<br/>policy_check]
    TYPE_CHECK -- api --> GATE_CHECK{gate_id set?}

    GATE_CHECK -- yes --> GATE_DEFER[DEFERRED<br/>gated]
    GATE_CHECK -- no --> PREVIEW_CHECK{PluginRegistry:<br/>previewable?}

    PREVIEW_CHECK -- false --> PREVIEW_DEFER[DEFERRED<br/>non_previewable]
    PREVIEW_CHECK -- true --> DISPATCH[DISPATCH<br/>via MCP dry_run]
    PREVIEW_CHECK -- lookup error --> PREVIEW_DEFER
```

## 3. Cache Interaction Flow

```mermaid
sequenceDiagram
    participant C as Caller
    participant PS as PreviewService
    participant RC as PreviewCacheAdapter
    participant R as Redis

    Note over C,R: Store Phase (after preview completes)
    PS->>RC: store(plan_id, user_id, step_results)
    RC->>R: SET preview:{user_id}:{plan_id} JSON TTL=900
    alt Redis available
        R-->>RC: OK
        RC-->>PS: "preview:{user_id}:{plan_id}"
    else Redis down
        R-->>RC: ConnectionError
        RC-->>PS: None (warning logged)
    end

    Note over C,R: Retrieve Phase (downstream consumer)
    C->>PS: get_preview_state(plan_id, user_id)
    PS->>RC: retrieve(plan_id, user_id)
    RC->>R: GET preview:{user_id}:{plan_id}
    alt Cache hit
        R-->>RC: JSON data
        RC-->>PS: dict[int, PreviewStepResult]
        PS-->>C: step results
    else Cache miss (expired/missing)
        R-->>RC: None
        RC-->>PS: None
        PS-->>C: None
    else Redis down
        R-->>RC: ConnectionError
        RC-->>PS: None (warning logged)
        PS-->>C: None
    end
```

## 4. Integration Flow (End-to-End Context)

```mermaid
flowchart LR
    subgraph DOMAIN["Domain Layer"]
        PLANNER[Planner] --> |Plan| PO
    end

    subgraph ORCH["Orchestration Layer"]
        PO[PreviewOrchestrator] --> |PreviewResult| AG[ApprovalGate]
        AG --> |Approval Token<br/>+ cached preview state| EO[ExecuteOrchestrator]
    end

    subgraph EXT["External"]
        MCP[(MCP Servers<br/>read-only)]
        REDIS[(Redis<br/>preview cache)]
    end

    subgraph DEP["Dependencies"]
        PR[PluginRegistry]
    end

    PO --> |dry_run invoke| MCP
    PO --> |cache state| REDIS
    PO --> |check previewable| PR

    AG --> |get_preview_state| PO
    EO --> |get_preview_state| PO
    EO --> |skip preview_only steps<br/>use cached results| REDIS
```

## 5. Parallel Execution Within a Level

```mermaid
gantt
    title Preview Level Execution (Steps 1-3 parallel)
    dateFormat X
    axisFormat %L ms

    section Step 1 (Fetcher)
    Check previewable     :a1, 0, 5
    Resolve templates     :a2, after a1, 2
    MCP invoke (dry_run)  :a3, after a2, 120
    Record result         :a4, after a3, 1

    section Step 2 (Fetcher)
    Check previewable     :b1, 0, 5
    Resolve templates     :b2, after b1, 2
    MCP invoke (dry_run)  :b3, after b2, 95
    Record result         :b4, after b3, 1

    section Step 3 (Analyzer)
    Check previewable     :c1, 0, 5
    Resolve templates     :c2, after c1, 2
    MCP invoke (dry_run)  :c3, after c2, 150
    Record result         :c4, after c3, 1

    section Level total
    asyncio.gather        :d1, 0, 160
```
