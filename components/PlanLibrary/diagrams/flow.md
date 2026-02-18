# PlanLibrary Flow Diagrams

This document contains the flow diagrams for PlanLibrary component operations.

## Plan Storage Flow (Happy Path)

```mermaid
graph TD
    A[PlanWriter calls store_plan] --> B{Validate Plan ID}
    B -->|Invalid| C[Return Invalid Plan ID Error]
    B -->|Valid| D[Verify Ed25519 Signature]
    D -->|Invalid| E[Return Signature Error]
    D -->|Valid| F[Begin Database Transaction]
    F --> G[Store Plan Record]
    G --> H[Store Plan Outcome]
    H --> I[Store Plan Metrics]
    I --> J[Commit Transaction]
    J --> K[Queue Background Embedding]
    K --> L[Return Storage Success]
    
    %% Background process
    M[Background: Generate Embedding] --> N{OpenAI API Call}
    N -->|Success| O[Store Vector in pgvector]
    N -->|Failure| P[Retry with Backoff]
    P --> Q{Max Retries?}
    Q -->|No| N
    Q -->|Yes| R[Log Embedding Failure]
    
    %% Styling
    classDef success fill:#e1f5fe
    classDef error fill:#ffebee
    classDef process fill:#f3e5f5
    classDef storage fill:#e8f5e8
    
    class A,D,F,G,H,I,J,L success
    class C,E,R error
    class M,N,P process
    class O,K storage
```

## Vector Similarity Search Flow

```mermaid
graph TD
    A[ContextRAG requests similar plans] --> B[Generate Query Embedding]
    B --> C{OpenAI API Available?}
    C -->|No| D[Return Circuit Breaker Error]
    C -->|Yes| E[Get Query Vector]
    E --> F[Execute pgvector Search]
    F --> G[Query: SELECT WITH COSINE SIMILARITY]
    G --> H[Filter by Similarity Threshold]
    H --> I[Order by Similarity + Success Rate]
    I --> J[Apply Result Limit]
    J --> K[Convert to Evidence Items]
    K --> L[Return Similarity Results]

    %% Styling
    classDef start fill:#e1f5fe
    classDef decision fill:#fff3e0
    classDef process fill:#f3e5f5
    classDef storage fill:#e8f5e8

    class A start
    class C decision
    class B,E,F,G,H,I,J,K,L process
```

## Intent-Based Query Flow

```mermaid
graph TD
    A[Planner requests plans by intent] --> D[Query Database by Intent Type]
    D --> E[JOIN plans with plan_success_rates]
    E --> F[Filter by Success Threshold]
    F --> G[Filter by Min Execution Count]
    G --> H[Order by Success Rate DESC]
    H --> I[Apply Result Limit]
    I --> J[Convert to Plan Patterns]
    J --> L[Return Query Results]
    
    %% Error handling
    D --> M{DB Connection Error?}
    M -->|Yes| N[Retry with Backoff]
    N --> O{Max Retries Reached?}
    O -->|No| D
    O -->|Yes| P[Return Database Error]
    
    %% Styling
    classDef start fill:#e1f5fe
    classDef decision fill:#fff3e0
    classDef process fill:#f3e5f5
    classDef error fill:#ffebee

    class A start
    class M,O decision
    class D,E,F,G,H,I,J,L process
    class P error
```

## Error Handling Flows

### Database Connection Failure
```mermaid
graph TD
    A[Database Operation] --> B{Connection Available?}
    B -->|No| C[Attempt Reconnection]
    C --> D{Reconnect Success?}
    D -->|Yes| E[Retry Operation]
    D -->|No| F[Wait with Exponential Backoff]
    F --> G{Max Retries Reached?}
    G -->|No| C
    G -->|Yes| H[Return Connection Error]
    
    %% Success path
    B -->|Yes| I[Execute Operation]
    E --> I
    I --> J{Operation Success?}
    J -->|Yes| K[Return Success]
    J -->|No| L[Handle Operation Error]
    
    %% Styling
    classDef success fill:#e1f5fe
    classDef error fill:#ffebee
    classDef retry fill:#fff3e0
    
    class A,I,E,K success
    class H,L error
    class C,F,G retry
```

### OpenAI API Circuit Breaker
```mermaid
graph TD
    A[Embedding Request] --> B{Circuit Breaker State}
    B -->|Open| C[Return Immediate Failure]
    B -->|Half-Open| D[Allow Single Test Request]
    B -->|Closed| E[Execute API Request]
    
    D --> F{Test Request Success?}
    F -->|Yes| G[Close Circuit Breaker]
    F -->|No| H[Keep Circuit Open]
    
    E --> I{API Request Success?}
    I -->|Yes| J[Return Embedding Result]
    I -->|No| K[Increment Failure Count]
    K --> L{Failure Threshold Reached?}
    L -->|Yes| M[Open Circuit Breaker]
    L -->|No| N[Return API Error]
    
    G --> J
    H --> C
    M --> C
    
    %% Styling
    classDef normal fill:#e1f5fe
    classDef success fill:#e8f5e8
    classDef failure fill:#ffebee
    classDef circuit fill:#fff3e0
    
    class A,E,D,G normal
    class J success
    class C,H,N,M failure
    class B,F,I,K,L circuit
```

## Analytics Flow

### Success Rate Calculation
```mermaid
graph TD
    A[Analytics Request] --> B[Query plan_success_rates View]
    B --> C[GROUP BY intent_type]
    C --> D[Calculate Success Rate = successes/total]
    D --> E[Calculate Confidence Score]
    E --> F[Apply Recency Weighting]
    F --> G[Filter by Min Execution Count]
    G --> H[Order by Weighted Success Rate]
    H --> I[Return Analytics Results]
    
    %% Background view refresh
    J[Plan Outcome Stored] --> K[Trigger View Refresh]
    K --> L[Recalculate Success Rates]
    L --> M[Recalculate Success Rates]
    
    %% Styling
    classDef query fill:#e1f5fe
    classDef calc fill:#f3e5f5
    classDef update fill:#fff3e0
    class A,B,I query
    class C,D,E,F,G,H calc
    class J,K,L,M update
```

## Component Integration Flow

### PlanWriter → PlanLibrary Integration
```mermaid
sequenceDiagram
    participant PW as PlanWriter
    participant PL as PlanLibrary
    participant SV as SignatureVerifier
    participant DB as PostgreSQL

    Note over PW: Plan execution completed
    PW->>+PL: POST /plans (plan, signature, outcome, metrics)
    PL->>+SV: verify_plan_signature(plan, signature)
    SV-->>-PL: signature_valid: true

    PL->>+DB: BEGIN TRANSACTION
    PL->>DB: INSERT INTO plans
    PL->>DB: INSERT INTO plan_outcomes
    PL->>DB: INSERT INTO plan_metrics
    DB-->>-PL: COMMIT SUCCESS

    PL-->>-PW: 200 OK {plan_id, stored_at}
```

### ContextRAG → PlanLibrary Query
```mermaid
sequenceDiagram
    participant CR as ContextRAG
    participant PL as PlanLibrary
    participant DB as PostgreSQL
    participant OAI as OpenAI API

    Note over CR: Need similar plan patterns
    CR->>+PL: similarity_search("schedule meeting with executive")

    PL->>+OAI: generate_embedding(query_text)
    OAI-->>-PL: query_vector[1536]

    PL->>+DB: SELECT plans JOIN embeddings WHERE similarity >= threshold
    DB-->>-PL: similarity_results[]

    PL->>PL: convert_to_evidence_items(results)
    PL-->>-CR: evidence_items[]

    Note over CR: Evidence Items used in planning
```

---

## Flow Diagram Legend

- **Blue boxes**: Normal operations and entry points
- **Orange diamonds**: Decision points and conditions
- **Purple boxes**: Data processing and transformations
- **Green boxes**: Successful outcomes and storage operations
- **Red boxes**: Error conditions and failures

## Performance Annotations

- **Plan Storage Flow**: Target <200ms p95 latency
- **Vector Search Flow**: Target <100ms p95 latency  
- **Intent Query Flow**: Target <150ms p95 latency
- **Background Embedding**: Async, up to 2s for OpenAI API call