# Intake — Flow Diagrams

## 1. Message Processing Flow (Main)

```mermaid
flowchart TD
    A[Client: POST /intake/message] --> B{JWT Valid?}
    B -- No --> B1[401 Unauthorized]
    B -- Yes --> C{Pydantic Validation}
    C -- Fail --> C1[422 Validation Error]
    C -- Pass --> D[IntakeService.process_message]

    D --> E{session_id provided?}
    E -- Yes --> F[SessionStore.get]
    E -- No --> H[Create new Session]

    F --> G{Session found?}
    G -- Yes --> I[Load session]
    G -- No --> H

    H --> J{Max turns?}
    I --> J
    J -- Exceeded --> J1[400 MAX_TURNS_EXCEEDED]
    J -- OK --> K[IntentParser.parse via LLM]

    K --> K1{LLM OK?}
    K1 -- Error --> K2[Fallback: empty ParseResult]
    K1 -- OK --> K3[ParseResult with intent/entities]

    K2 --> L[Record turn in session]
    K3 --> L
    L --> M[Merge entities & intent into session]
    M --> N[Planner.get_required_entities]

    N --> N1{Planner OK?}
    N1 -- Error --> N2[Fallback heuristic: intent + ≥1 entity?]
    N1 -- ToolNotAvailableError --> N4[422 TOOL_NOT_AVAILABLE]
    N1 -- OK --> N3[RequiredEntitiesResult]

    N2 --> O{Ready?}
    N3 --> P{Missing entities?}

    P -- None missing --> O2[Ready = True]
    P -- Has missing --> Q{context_tier ≥ 2?}

    Q -- Tier 1 --> T[Build follow_up: ask for all missing]
    Q -- Tier 2+ --> R[Check ProfileStore for defaults]

    R --> R1{Defaults found?}
    R1 -- Yes --> S[Build follow_up with default offers]
    R1 -- No --> T

    S --> U[Return collecting + follow_up]
    T --> U

    O -- No --> U
    O -- Yes --> V[Build Intent from shared/schemas/intent.py]
    O2 --> V

    V --> W[SessionStore.save]
    U --> W
    W --> X{Redis OK?}
    X -- No --> X1[503 SESSION_STORE_UNAVAILABLE]
    X -- Yes --> Y{Ready?}
    Y -- Yes --> Z[200 status=ready + Intent JSON]
    Y -- No --> Z1[200 status=collecting + follow_up]
```

## 2. Session Reset Flow

```mermaid
flowchart TD
    A[Client: DELETE /intake/session/id] --> B{JWT Valid?}
    B -- No --> B1[401 Unauthorized]
    B -- Yes --> C[IntakeService.reset_session]
    C --> D[SessionStore.delete]
    D --> E{Redis OK?}
    E -- No --> E1[503 SESSION_STORE_UNAVAILABLE]
    E -- Yes --> F{Deleted?}
    F -- Yes --> G[200 status=reset]
    F -- No --> H[404 SESSION_NOT_FOUND]
```

## 3. Intent Parser Flow (LLMBasedParser)

```mermaid
flowchart TD
    A[parse message, context] --> B{Context provided?}
    B -- Yes --> C[Build prompt with prior intent + entities]
    B -- No --> D[Build prompt with message only]

    C --> E[LLM call via AnthropicAdapter]
    D --> E

    E --> F{LLM response OK?}
    F -- Timeout/Error --> G[Raise IntentParserError]
    F -- OK --> H[Parse JSON response]

    H --> I{Valid JSON?}
    I -- No --> G
    I -- Yes --> J[Extract intent, entities, constraints]

    J --> K[Return ParseResult]
    G --> L[Caller catches → empty ParseResult]
```

## 4. Readiness Check Flow (via Planner)

```mermaid
flowchart TD
    A[Check readiness after parse] --> B{Intent detected?}
    B -- No --> C[Return collecting: "What would you like help with?"]
    B -- Yes --> D[Call Planner.get_required_entities]

    D --> E{Planner available?}
    E -- No --> F[Fallback: intent + ≥1 entity → ready]
    E -- ToolNotAvailableError --> E1[422: No tool for this intent]
    E -- Yes --> G[Get RequiredEntitiesResult]

    G --> H{Missing entities?}
    H -- None --> I[Return ready = True]
    H -- Has missing --> J{User consent tier?}

    J -- Tier 1 --> K[Ask user for all missing entities directly]
    J -- Tier 2+ --> L[Check ProfileStore for each missing entity with pref_key]

    L --> M{Defaults found?}
    M -- Some found --> N[Offer defaults: "Use X or specify different?"]
    M -- None found --> K

    N --> O[Return collecting with follow_up + default offers]
    K --> P[Return collecting with follow_up]

    F --> Q{Heuristic ready?}
    Q -- Yes --> I
    Q -- No --> R[Return collecting with generic prompt]
```

## 5. Multi-Turn Sequence (with Planner + ProfileStore)

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Routes
    participant S as IntakeService
    participant SS as SessionStore
    participant IP as IntentParser (LLM)
    participant PL as Planner
    participant PS as ProfileStore

    Note over C,PS: Turn 1: "I need to meet with Alice"

    C->>R: POST /intake/message
    R->>R: get_auth_context() → user_id, tier=2
    R->>S: process_message(user_id, msg, tier=2)
    S->>SS: get(user_id, None)
    SS-->>S: None
    S->>S: Create new session

    S->>IP: parse("meet with Alice", None)
    IP->>IP: LLM call (Haiku)
    IP-->>S: ParseResult(intent=schedule_meeting, entities={attendee: Alice})

    S->>S: Update session state

    S->>PL: get_required_entities("schedule_meeting", {attendee: Alice})
    PL-->>S: RequiredEntitiesResult(missing=[time(no pref), duration_min(pref="default_meeting_duration")])

    Note right of S: Tier 2 → check ProfileStore for defaults
    S->>PS: get_preference(user_id, "default_meeting_duration", tier=2)
    PS-->>S: EvidenceItem(value=30)

    S->>S: Build follow_up with default offer
    S->>SS: save(session)
    S-->>R: IntakeResponse(collecting)
    R-->>C: 200 {status: collecting, follow_up: "Duration: use 30 min? Time: when?"}

    Note over C,PS: Turn 2: "Tuesday at 10 AM, yes use the default"

    C->>R: POST /intake/message {session_id}
    R->>R: get_auth_context() → user_id, tier=2
    R->>S: process_message(user_id, msg, tier=2, session_id)
    S->>SS: get(user_id, session_id)
    SS-->>S: Session (with prior state + offered defaults)

    S->>IP: parse("Tuesday at 10 AM, yes use default", context=session)
    IP->>IP: LLM call with context
    IP-->>S: ParseResult(entities={time: "10 AM", date: "Tuesday", duration_min: 30})

    S->>S: Update session (merge entities)

    S->>PL: get_required_entities("schedule_meeting", {attendee, time, date, duration_min})
    PL-->>S: RequiredEntitiesResult(missing=[])

    S->>S: Build Intent (shared/schemas/intent.py)
    S->>SS: save(session)
    S-->>R: IntakeResponse(ready, intent={...})
    R-->>C: 200 {status: ready, intent: {...}}
```

## 6. Graceful Degradation Paths

```mermaid
flowchart TD
    subgraph Normal
        A[LLM Parse] --> B[Planner Query] --> C{Tier 2+?}
        C -- Yes --> D[ProfileStore Lookup]
        C -- No --> E[Ask User Directly]
        D --> F[Offer Defaults]
    end

    subgraph LLM Down
        A2[LLM Parse FAILS] --> A3[Empty ParseResult]
        A3 --> A4[Return collecting: generic prompt]
    end

    subgraph Planner Down
        B2[Planner Query FAILS] --> B3[Heuristic: intent + ≥1 entity]
        B3 --> B4{Heuristic ready?}
        B4 -- Yes --> B5[Build Intent]
        B4 -- No --> B6[Return collecting: generic prompt]
    end

    subgraph Tool Not Available
        T1[Planner: ToolNotAvailableError] --> T2[422 TOOL_NOT_AVAILABLE]
        T2 --> T3[User informed: intent cannot be fulfilled]
    end

    subgraph ProfileStore Down
        D2[ProfileStore FAILS] --> D3[Skip defaults]
        D3 --> D4[Ask user for all missing directly]
    end

    subgraph Redis Down
        R1[Redis FAILS] --> R2[503 SESSION_STORE_UNAVAILABLE]
    end
```
