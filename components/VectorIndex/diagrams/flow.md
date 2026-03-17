# VectorIndex — Flow Diagrams

## 1. Store Embedding Flow

```mermaid
flowchart TD
    A[PlanWriter calls store_embedding] --> B{plan_data empty?}
    B -->|Yes| C[Raise ValueError]
    B -->|No| D[TextBuilder: build_search_text]
    D --> E[TextBuilder: extract_intent_type]
    E --> F[EmbeddingAdapter: embed via ONNX ~10ms]
    F --> G{ONNX error?}
    G -->|Yes| H[Log error, raise EmbeddingModelError]
    G -->|No| I[PgvectorAdapter: upsert_embedding]
    I --> J{DB error?}
    J -->|Yes| K[DatabaseConnectionError - caller handles]
    J -->|No| L[Log embedding_stored]
    L --> M[Return None - success]

    style C fill:#f66
    style H fill:#f66
    style K fill:#f66
    style M fill:#6f6
```

## 2. Hybrid Search Flow (RRF)

```mermaid
flowchart TD
    A[ContextRAG/Planner calls search] --> B{Validate inputs}
    B -->|top_k invalid| C[Raise ValueError]
    B -->|query empty| C
    B -->|Valid| D[EmbeddingAdapter: embed query ~10ms]
    D --> E{ONNX error?}
    E -->|Yes| F[Raise EmbeddingModelError]
    E -->|No| G[PgvectorAdapter: hybrid_search]

    G --> H[CTE: BM25 keyword search]
    G --> I[CTE: Semantic cosine search]
    H --> J{intent_type filter?}
    I --> J
    J -->|Yes| K[Add WHERE intent_type = ?]
    J -->|No| L[No WHERE filter]
    K --> M[FULL OUTER JOIN + RRF score]
    L --> M
    M --> N[ORDER BY rrf_score DESC LIMIT top_k]

    N --> O{DB error?}
    O -->|Yes| P[DatabaseConnectionError]
    O -->|No| Q{Results empty?}
    Q -->|Yes| R[Return empty list]
    Q -->|No| S[Map to HybridSearchResult]
    S --> T[Log hybrid_search metrics]
    T --> U[Return results]

    style C fill:#f66
    style F fill:#f66
    style P fill:#f66
    style R fill:#ff9
    style U fill:#6f6
```

## 3. Application Startup Flow

```mermaid
flowchart TD
    A[Application lifespan start] --> B[Load ONNX model path from env]
    B --> C[Create EmbeddingAdapter]
    C --> D{Model file exists?}
    D -->|No| E[Auto-download from HuggingFace Hub]
    E --> F{Download success?}
    F -->|No| G[Raise EmbeddingModelError - app fails to start]
    F -->|Yes| H[Load ONNX InferenceSession]
    D -->|Yes| H
    H --> I{ONNX load success?}
    I -->|No| G
    I -->|Yes| J[Create PgvectorAdapter with SharedDatabaseAdapter]
    J --> K[Check pgvector extension installed]
    K --> L{pgvector available?}
    L -->|No| M[Log warning - VectorIndex disabled]
    L -->|Yes| N[Create VectorIndexService]
    N --> O[Store on app.state.vector_index_service]
    O --> P[Startup complete]

    style G fill:#f66
    style M fill:#ff9
    style P fill:#6f6
```

## 4. Graceful Degradation Flow

```mermaid
flowchart TD
    A[ContextRAG needs similar plans] --> B{VectorIndex available?}
    B -->|Yes| C[search via VectorIndex]
    C --> D{Search succeeds?}
    D -->|Yes| E[Use hybrid search results]
    D -->|No - DB error| F[Log warning]
    B -->|No - disabled at startup| F

    F --> G[Fallback: PlanLibrary structured query]
    G --> H[SELECT * FROM plans WHERE intent_type = ? ORDER BY stored_at DESC]
    H --> I[Use structured query results]

    E --> J[Build evidence items from results]
    I --> J
    J --> K[Continue context assembly]

    style E fill:#6f6
    style I fill:#ff9
    style K fill:#6f6
```

## 5. RRF Score Fusion Detail

```mermaid
flowchart LR
    subgraph BM25_Leg[BM25 Keyword Leg]
        B1[tsv @@ plainto_tsquery] --> B2[ts_rank_cd scoring]
        B2 --> B3[ROW_NUMBER → rank_kw]
        B3 --> B4[LIMIT 20 candidates]
    end

    subgraph Semantic_Leg[Semantic Cosine Leg]
        S1[embedding <=> query_vec] --> S2[cosine distance scoring]
        S2 --> S3[ROW_NUMBER → rank_vec]
        S3 --> S4[LIMIT 20 candidates]
    end

    B4 --> RRF[FULL OUTER JOIN on plan_id]
    S4 --> RRF
    RRF --> Score["rrf_score = 1/(60+rank_kw) + 1/(60+rank_vec)"]
    Score --> Sort[ORDER BY rrf_score DESC]
    Sort --> TopK[LIMIT top_k]

    style RRF fill:#69f
    style Score fill:#69f
```

## 6. Delete Embedding Flow

```mermaid
flowchart TD
    A[Caller: delete_embedding plan_id] --> B[PgvectorAdapter: DELETE WHERE plan_id = ?]
    B --> C{Row existed?}
    C -->|Yes| D[Row deleted]
    C -->|No| E[No-op - idempotent]
    D --> F[Log embedding_deleted]
    E --> F
    F --> G[Return None]

    style G fill:#6f6
```
