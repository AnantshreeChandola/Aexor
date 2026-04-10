# TrustFilter — Flow Diagrams

## 1. Internal Pipeline (S1 → S2 → S3)

```mermaid
flowchart TD
    A[MCP Tool Response<br/>raw JSON payload] --> B{Payload Limits OK?}
    B -->|No: > 1MB| E1[PayloadTooLargeError<br/>step fails hard]
    B -->|No: not JSON| E2[MalformedInputError<br/>step fails hard]
    B -->|Yes| C[TreeWalker<br/>walk JSON tree]
    C -->|depth > 32| E3[PayloadDepthExceededError<br/>step fails hard]
    C -->|yields path,string pairs| D[S1: RegexScanner<br/>scan every string field]
    D --> F{S1 hits found?}
    F -->|No hits| G[Return SanitizedPayload<br/>verdict=clean<br/>confidence=0.99<br/>skip S2]
    F -->|Yes: hits found| H{S2 reachable?}
    H -->|Yes| I[S2: Haiku Judge<br/>classify payload<br/>tools=[] temp=0]
    H -->|No: timeout/error| J[Degrade to S1-only<br/>scanner_degraded=true]
    I --> K[Combine Verdicts<br/>pick more paranoid]
    J --> K
    D -->|S1 exception| K2[Degrade: treat as 0 hits<br/>S2 carries load]
    K2 --> H
    K --> L{Final verdict?}
    L -->|clean| G2[Return SanitizedPayload<br/>verdict=clean<br/>nothing stripped]
    L -->|suspicious<br/>strict_mode=false| G3[Return SanitizedPayload<br/>verdict=suspicious<br/>nothing stripped<br/>downstream HITL decides]
    L -->|suspicious<br/>strict_mode=true| M[Select fields to strip]
    L -->|injection| M
    M --> N{Load-bearing<br/>field flagged?}
    N -->|Yes| E4[LoadBearingFlaggedError<br/>step fails hard]
    N -->|No| O[S3: Strip flagged fields<br/>replace with redacted marker]
    O --> P[Return SanitizedPayload<br/>original_shape preserved<br/>stripped_fields populated<br/>trust_verdict set]

    style E1 fill:#f44,color:white
    style E2 fill:#f44,color:white
    style E3 fill:#f44,color:white
    style E4 fill:#f44,color:white
    style G fill:#4a4,color:white
    style G2 fill:#4a4,color:white
    style G3 fill:#fa0,color:white
    style P fill:#48f,color:white
    style J fill:#fa0,color:white
```

## 2. End-to-End: Poisoned Calendar Meeting Booking

This diagram shows User Story 1 — booking a meeting when Alice's calendar has a prompt-injection payload.

```mermaid
sequenceDiagram
    participant U as User
    participant Orch as ExecuteOrchestrator
    participant MCP as MCP Gateway<br/>(Google Calendar)
    participant TF as TrustFilter<br/>(Guard)
    participant S1 as S1 RegexScanner
    participant S2 as S2 Haiku Judge
    participant PE as PolicyEngine
    participant HITL as HITL Gate
    participant R as Tier 1 Reasoner<br/>(slot_proposal_v1)

    U->>Orch: Intent: "Book meeting with Alice Tue 2pm"
    Note over Orch: Plan: [api_my_cal, api_alice_cal,<br/>sanitizer_my, sanitizer_alice,<br/>tier1_propose_slot, policy_check,<br/>hitl_gate, api_create_event]

    Orch->>MCP: Step 1: fetch my calendar
    MCP-->>Orch: my_cal_response (clean)
    Orch->>MCP: Step 2: fetch Alice's calendar
    MCP-->>Orch: alice_cal_response (poisoned description)

    rect rgb(255, 240, 220)
        Note over Orch,TF: Step 3: Sanitize my calendar
        Orch->>TF: scan(my_cal_response)
        TF->>S1: scan all string fields
        S1-->>TF: 0 hits
        TF-->>Orch: SanitizedPayload(verdict=clean)
    end

    rect rgb(255, 200, 200)
        Note over Orch,S2: Step 4: Sanitize Alice's calendar (INJECTION DETECTED)
        Orch->>TF: scan(alice_cal_response,<br/>load_bearing=["free_slots"])
        TF->>S1: scan all string fields
        S1-->>TF: RuleHit("events[0].description",<br/>rule=ignore_previous_instructions, HIGH)
        TF->>S2: classify(payload, [ignore_prev...])
        S2-->>TF: verdict=injection, confidence=0.94
        Note over TF: Combine: injection (0.95)
        Note over TF: "description" not load-bearing → strip
        TF-->>Orch: SanitizedPayload(<br/>verdict=injection,<br/>stripped=["events[0].description"],<br/>scanner_degraded=false)
    end

    Note over Orch: Propagate trust_verdict=injection<br/>into ExecutionContext

    rect rgb(220, 240, 255)
        Note over Orch,R: Step 5: Tier 1 Reasoner (propose_slot)
        Orch->>R: Sanitized payloads as context<br/>(description already stripped)
        R-->>Orch: {proposed_start, proposed_end, rationale}
        Note over Orch: Validate output against<br/>slot_proposal_v1 schema ✓
    end

    rect rgb(255, 220, 220)
        Note over Orch,HITL: Step 6-7: PolicyEngine + HITL Gate
        Orch->>PE: evaluate step 8 (create_event)<br/>ancestor verdict=injection
        PE-->>Orch: requires_approval=true
        Orch->>HITL: Gate payload with:<br/>- proposed action (create_event)<br/>- trust provenance chain<br/>- stripped field paths<br/>- redacted preview
        U->>HITL: APPROVE
        HITL-->>Orch: approved
    end

    Orch->>MCP: Step 8: create_event(<br/>title=static template,<br/>attendees=[alice],<br/>start/end=from HITL approval,<br/>description="")
    MCP-->>Orch: event created ✓
    Orch-->>U: Meeting booked successfully
```

## 3. S2 Degradation Flow

```mermaid
sequenceDiagram
    participant Orch as ExecuteOrchestrator
    participant TF as TrustFilter
    participant S1 as S1 RegexScanner
    participant S2 as S2 Haiku Judge
    participant PE as PolicyEngine
    participant HITL as HITL Gate

    Orch->>TF: scan(payload)
    TF->>S1: scan all fields
    S1-->>TF: [RuleHit(zero_width, MED)]

    TF->>S2: classify(payload, s1_hits)
    Note over S2: Anthropic API timeout (3s)
    S2--xTF: HaikuUnreachableError

    Note over TF: Log: s2_unreachable_degrading
    Note over TF: S1 alone: verdict=suspicious
    TF-->>Orch: SanitizedPayload(<br/>verdict=suspicious,<br/>scanner_degraded=true)

    Note over Orch: Copy scanner_degraded=true<br/>into ExecutionContext

    Orch->>PE: evaluate downstream step
    Note over PE: Ancestor scanner_degraded=true<br/>→ requires_approval=true
    PE-->>Orch: requires_approval=true

    Orch->>HITL: Gate with degradation banner:<br/>"Scanner S2 was unavailable.<br/>Verdict based on regex only."
```

## 4. Component Integration Context

```mermaid
graph TB
    subgraph "Orchestration Layer"
        EO[ExecuteOrchestrator]
    end

    subgraph "Domain/Service Layer"
        TF[TrustFilter<br/>role: Guard]
        PL[Planner]
        PE[PolicyEngine]
        PW[PlanWriter]
    end

    subgraph "Shared Schemas"
        SP[SanitizedPayload]
        TV[TrustVerdict]
        PS[PlanStep type=sanitizer<br/>role=Guard]
    end

    subgraph "External"
        API[Anthropic API<br/>claude-haiku-4-5]
    end

    EO -->|"step.type==sanitizer"| TF
    EO -->|"step.type==llm_reasoning"| R[Tier 1/2 Reasoner]
    EO -->|evaluates verdicts| PE
    PL -->|inserts sanitizer steps| PS
    TF -->|S2 classify| API
    TF -->|emits| SP
    TF -->|emits| TV
    PE -->|reads verdicts from| SP

    style TF fill:#48f,color:white
    style SP fill:#6c6,color:white
    style TV fill:#6c6,color:white
```

## 5. Plan Validator Trust Boundary Rules

```mermaid
flowchart TD
    P[Plan JSON from LLM] --> V{Plan Validator}

    V -->|Rule E| RE{Tier 1 reasoner<br/>output_schema_ref = null?}
    RE -->|Yes| REJE[REJECT: Rule E<br/>Missing output_schema_ref]
    RE -->|No| OK1[✓]

    V -->|Rule F| RF{llm_reasoning step<br/>context_from api step<br/>WITHOUT intervening sanitizer?}
    RF -->|Yes| REJF[REJECT: Rule F<br/>Missing sanitizer in path]
    RF -->|No| OK2[✓]

    V -->|Rule G| RG{sanitizer step has<br/>can_spawn=true OR<br/>trust_level set?}
    RG -->|Yes| REJG[REJECT: Rule G<br/>Invalid sanitizer config]
    RG -->|No| OK3[✓]

    V -->|Rule H| RH{Tier 1 reasoner has<br/>can_spawn=true OR<br/>uses real MCP tool?}
    RH -->|Yes| REJH[REJECT: Rule H<br/>Tier 1 must not spawn/dispatch]
    RH -->|No| OK4[✓]

    OK1 --> PASS
    OK2 --> PASS
    OK3 --> PASS
    OK4 --> PASS
    PASS[Plan accepted ✓]

    style REJE fill:#f44,color:white
    style REJF fill:#f44,color:white
    style REJG fill:#f44,color:white
    style REJH fill:#f44,color:white
    style PASS fill:#4a4,color:white
```
