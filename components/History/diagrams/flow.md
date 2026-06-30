# History Component -- Flow Diagrams

## 1. Fact Storage Flow (PlanWriter -> History -> DB)

```mermaid
graph TD
    Start([PlanWriter calls store_fact])

    %% Validation
    AuthCheck{Auth + Tier 3<br/>consent?}
    AuthDenied[403 CONSENT_REQUIRED]

    ValidateText{fact_text<br/>non-empty?}
    InvalidFact[400 INVALID_FACT]

    ValidateSize{fact_text<br/><= 4KB?}
    TooLarge[400 FACT_TOO_LARGE]

    ValidateTimestamp{timestamp<br/>not future?}
    InvalidTs[400 INVALID_TIMESTAMP]

    %% Core logic
    ComputeHash[Compute fact_hash<br/>SHA256 of user_id +<br/>intent_type + fact_text + date]

    InsertDB[INSERT INTO history<br/>ON CONFLICT DO NOTHING]

    IsDuplicate{Duplicate<br/>fact_hash?}

    ReturnDuplicate[Return existing fact<br/>status = duplicate]

    %% Pattern update
    UpdatePattern[Upsert fact_pattern<br/>increment occurrence_count<br/>update last_seen]

    InvalidateCache[Invalidate Redis<br/>cache for user_id]

    ReturnSuccess[Return fact_id<br/>status = ok]

    %% Flow
    Start --> AuthCheck
    AuthCheck -->|Denied| AuthDenied
    AuthCheck -->|Granted| ValidateText
    ValidateText -->|Empty| InvalidFact
    ValidateText -->|Valid| ValidateSize
    ValidateSize -->|Too large| TooLarge
    ValidateSize -->|OK| ValidateTimestamp
    ValidateTimestamp -->|Future| InvalidTs
    ValidateTimestamp -->|OK| ComputeHash
    ComputeHash --> InsertDB
    InsertDB --> IsDuplicate
    IsDuplicate -->|Yes| ReturnDuplicate
    IsDuplicate -->|No| UpdatePattern
    UpdatePattern --> InvalidateCache
    InvalidateCache --> ReturnSuccess

    %% Styling
    classDef errorNode fill:#ffcccc,stroke:#cc0000
    classDef successNode fill:#ccffcc,stroke:#00cc00
    classDef decisionNode fill:#ffffcc,stroke:#cccc00
    classDef processNode fill:#cce5ff,stroke:#0066cc

    class AuthDenied,InvalidFact,TooLarge,InvalidTs errorNode
    class ReturnSuccess,ReturnDuplicate successNode
    class AuthCheck,ValidateText,ValidateSize,ValidateTimestamp,IsDuplicate decisionNode
    class ComputeHash,InsertDB,UpdatePattern,InvalidateCache processNode
```

---

## 2. Fact Query Flow (ContextRAG -> History -> Evidence Items)

```mermaid
graph TD
    Start([ContextRAG calls get_facts])

    %% Auth
    AuthCheck{Auth + Tier 3<br/>consent?}
    AuthDenied[403 CONSENT_REQUIRED]

    %% Validation
    ValidateParams{Query params<br/>valid?}
    InvalidQuery[400 INVALID_QUERY]

    %% Cache check
    CacheCheck{Redis cache<br/>available?}
    CacheLookup{Cache hit?}
    ReturnCached[Return cached<br/>Evidence Items]

    %% Database query
    QueryDB[SELECT FROM history<br/>WHERE user_id AND intent_type<br/>AND NOT expired<br/>AND NOT deleted<br/>ORDER BY created_at DESC<br/>LIMIT N]

    HasResults{Results<br/>found?}
    EmptyResult[Return empty<br/>evidence list]

    %% Evidence conversion
    ConvertEvidence[Convert each Fact<br/>to EvidenceItem<br/>type=history, tier=3]

    ComputeConfidence[Compute confidence<br/>= 1.0 - age_days/ttl_days]

    CacheResults[Cache results in Redis<br/>TTL = 5 min]

    ReturnEvidence[Return Evidence Items<br/>with total_count]

    %% Flow
    Start --> AuthCheck
    AuthCheck -->|Denied| AuthDenied
    AuthCheck -->|Granted| ValidateParams
    ValidateParams -->|Invalid| InvalidQuery
    ValidateParams -->|Valid| CacheCheck
    CacheCheck -->|Available| CacheLookup
    CacheCheck -->|Unavailable| QueryDB
    CacheLookup -->|Hit| ReturnCached
    CacheLookup -->|Miss| QueryDB
    QueryDB --> HasResults
    HasResults -->|No| EmptyResult
    HasResults -->|Yes| ConvertEvidence
    ConvertEvidence --> ComputeConfidence
    ComputeConfidence --> CacheResults
    CacheResults --> ReturnEvidence

    %% Styling
    classDef errorNode fill:#ffcccc,stroke:#cc0000
    classDef successNode fill:#ccffcc,stroke:#00cc00
    classDef decisionNode fill:#ffffcc,stroke:#cccc00
    classDef processNode fill:#cce5ff,stroke:#0066cc

    class AuthDenied,InvalidQuery errorNode
    class ReturnEvidence,ReturnCached,EmptyResult successNode
    class AuthCheck,ValidateParams,CacheCheck,CacheLookup,HasResults decisionNode
    class QueryDB,ConvertEvidence,ComputeConfidence,CacheResults processNode
```

---

## 3. Pattern Detection Flow

```mermaid
graph TD
    subgraph On-Write Path
        NewFact([New fact stored])
        ExtractKey[Extract pattern key<br/>intent_type + entity_key + day_of_week]
        LookupPattern{Existing pattern<br/>for this key?}

        CreatePattern[INSERT fact_pattern<br/>occurrence_count = 1<br/>confidence = 0.2]

        IncrementPattern[UPDATE fact_pattern<br/>SET occurrence_count += 1<br/>last_seen = NOW]

        ComputeConfidence[confidence =<br/>min 1.0, count / 5]

        PatternDone([Pattern updated])
    end

    subgraph On-Read Path
        QueryRequest([Pattern query request])
        AuthGate{Tier 3<br/>consent?}
        Denied[403 CONSENT_REQUIRED]
        QueryPatterns[SELECT FROM fact_patterns<br/>WHERE user_id AND intent_type<br/>AND confidence >= min_confidence]
        FilterStale[Exclude patterns with<br/>last_seen > 30 days ago]
        ReturnPatterns([Return FactPattern list])
    end

    %% On-Write connections
    NewFact --> ExtractKey
    ExtractKey --> LookupPattern
    LookupPattern -->|No| CreatePattern
    LookupPattern -->|Yes| IncrementPattern
    CreatePattern --> ComputeConfidence
    IncrementPattern --> ComputeConfidence
    ComputeConfidence --> PatternDone

    %% On-Read connections
    QueryRequest --> AuthGate
    AuthGate -->|Denied| Denied
    AuthGate -->|Granted| QueryPatterns
    QueryPatterns --> FilterStale
    FilterStale --> ReturnPatterns

    %% Styling
    classDef errorNode fill:#ffcccc,stroke:#cc0000
    classDef successNode fill:#ccffcc,stroke:#00cc00
    classDef decisionNode fill:#ffffcc,stroke:#cccc00
    classDef processNode fill:#cce5ff,stroke:#0066cc

    class Denied errorNode
    class PatternDone,ReturnPatterns successNode
    class LookupPattern,AuthGate decisionNode
    class ExtractKey,CreatePattern,IncrementPattern,ComputeConfidence,QueryPatterns,FilterStale processNode
```

---

## 4. TTL Cleanup Flow

```mermaid
graph TD
    Start([Scheduled cleanup trigger])

    %% Phase 1: Soft-delete
    SoftDelete[UPDATE history<br/>SET deleted_at = NOW<br/>WHERE expires_at < NOW<br/>AND deleted_at IS NULL<br/>LIMIT batch_size]

    SoftCount{Rows<br/>affected?}
    SoftLog[Log soft-delete count<br/>Increment cleanup metric]
    SoftDone{More rows<br/>remaining?}

    %% Phase 2: Hard-delete
    HardDelete[DELETE FROM history<br/>WHERE deleted_at IS NOT NULL<br/>AND deleted_at < NOW - 90 days<br/>LIMIT batch_size]

    HardCount{Rows<br/>affected?}
    HardLog[Log hard-delete count<br/>Increment cleanup metric]
    HardDone{More rows<br/>remaining?}

    %% Stale patterns
    StalePatterns[UPDATE fact_patterns<br/>SET confidence = 0<br/>WHERE last_seen < NOW - 30 days<br/>AND confidence > 0]

    Complete([Cleanup complete])

    %% Flow
    Start --> SoftDelete
    SoftDelete --> SoftCount
    SoftCount -->|0 rows| HardDelete
    SoftCount -->|N rows| SoftLog
    SoftLog --> SoftDone
    SoftDone -->|Yes| SoftDelete
    SoftDone -->|No| HardDelete

    HardDelete --> HardCount
    HardCount -->|0 rows| StalePatterns
    HardCount -->|N rows| HardLog
    HardLog --> HardDone
    HardDone -->|Yes| HardDelete
    HardDone -->|No| StalePatterns

    StalePatterns --> Complete

    %% Styling
    classDef successNode fill:#ccffcc,stroke:#00cc00
    classDef decisionNode fill:#ffffcc,stroke:#cccc00
    classDef processNode fill:#cce5ff,stroke:#0066cc

    class Complete successNode
    class SoftCount,SoftDone,HardCount,HardDone decisionNode
    class SoftDelete,SoftLog,HardDelete,HardLog,StalePatterns processNode
```

---

## 5. Error Handling Paths

```mermaid
graph TD
    Request([Incoming request])

    %% Error checks
    AuthMissing{Auth header<br/>present?}
    E401[401 Unauthorized<br/>Authentication required]

    TierCheck{context_tier<br/>>= 3?}
    E403[403 Forbidden<br/>CONSENT_REQUIRED]

    UserAccess{user_id matches<br/>auth user?}
    E403b[403 Forbidden<br/>Cannot access other user]

    InputValid{Input<br/>valid?}
    E400[400 Bad Request<br/>INVALID_FACT / INVALID_QUERY]

    %% Database errors
    DBOp[Execute DB operation]
    DBError{Database<br/>error?}
    Transient{Transient<br/>error?}
    Retry[Retry with<br/>exponential backoff<br/>1s, 2s, 4s]
    RetryExhausted{Retries<br/>exhausted?}
    E503[503 Service Unavailable<br/>STORAGE_ERROR]

    CacheOp[Cache operation]
    CacheError{Cache<br/>error?}
    CacheFallback[Silently fall back<br/>to DB-only path]

    Success[200 OK<br/>Return response]

    %% Flow
    Request --> AuthMissing
    AuthMissing -->|No| E401
    AuthMissing -->|Yes| TierCheck
    TierCheck -->|No| E403
    TierCheck -->|Yes| UserAccess
    UserAccess -->|No| E403b
    UserAccess -->|Yes| InputValid
    InputValid -->|No| E400
    InputValid -->|Yes| CacheOp
    CacheOp --> CacheError
    CacheError -->|Yes| CacheFallback
    CacheError -->|No| DBOp
    CacheFallback --> DBOp
    DBOp --> DBError
    DBError -->|No| Success
    DBError -->|Yes| Transient
    Transient -->|No| E503
    Transient -->|Yes| Retry
    Retry --> RetryExhausted
    RetryExhausted -->|No| DBOp
    RetryExhausted -->|Yes| E503

    %% Styling
    classDef errorNode fill:#ffcccc,stroke:#cc0000
    classDef successNode fill:#ccffcc,stroke:#00cc00
    classDef decisionNode fill:#ffffcc,stroke:#cccc00
    classDef processNode fill:#cce5ff,stroke:#0066cc

    class E401,E403,E403b,E400,E503 errorNode
    class Success successNode
    class AuthMissing,TierCheck,UserAccess,InputValid,DBError,Transient,RetryExhausted,CacheError decisionNode
    class DBOp,Retry,CacheOp,CacheFallback processNode
```
