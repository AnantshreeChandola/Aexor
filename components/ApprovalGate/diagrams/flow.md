# ApprovalGate — Flow Diagrams

## 1. Main Approval Flow

```mermaid
flowchart TD
    START([approve request]) --> VALIDATE[Validate request]
    VALIDATE -->|invalid| ERROR_INVALID[InvalidGateError / ApprovalError]
    VALIDATE -->|valid| IDEMPOTENCY{Gate already approved?}

    IDEMPOTENCY -->|yes| RETURN_EXISTING[Return existing token]
    IDEMPOTENCY -->|no| PREVIEW_STATE[Retrieve preview state]

    PREVIEW_STATE --> PREVIEW_CHECK{PreviewService available?}
    PREVIEW_CHECK -->|yes| GET_PREVIEW[get_preview_state]
    PREVIEW_CHECK -->|no| SKIP_PREVIEW[preview_state = None]
    GET_PREVIEW -->|success| HAS_PREVIEW[Bind preview state]
    GET_PREVIEW -->|failure| SKIP_PREVIEW

    HAS_PREVIEW --> SIGN_JWT
    SKIP_PREVIEW --> SIGN_JWT

    SIGN_JWT[Sign JWT token] --> STORE_GATE[Store gate state in Redis]
    STORE_GATE -->|success| CHECK_POLICY
    STORE_GATE -->|Redis down| CHECK_POLICY_WARN[Log warning + continue]
    CHECK_POLICY_WARN --> CHECK_POLICY

    CHECK_POLICY{policy_matched = False?}
    CHECK_POLICY -->|yes| LEARN[PolicyEngine.learn_from_approval]
    CHECK_POLICY -->|no| RETURN_TOKEN

    LEARN -->|success| RETURN_TOKEN[Return ApprovalToken]
    LEARN -->|failure| LOG_LEARN_FAIL[Log warning] --> RETURN_TOKEN

    style ERROR_INVALID fill:#f66,stroke:#333
    style RETURN_TOKEN fill:#6f6,stroke:#333
    style RETURN_EXISTING fill:#6f6,stroke:#333
    style SKIP_PREVIEW fill:#ff6,stroke:#333
    style CHECK_POLICY_WARN fill:#ff6,stroke:#333
    style LOG_LEARN_FAIL fill:#ff6,stroke:#333
```

---

## 2. Token Validation Flow

```mermaid
flowchart TD
    START([validate_token request]) --> VERIFY[Verify JWT signature + expiry]
    VERIFY -->|expired| ERROR_EXPIRED[TokenExpiredError]
    VERIFY -->|invalid signature| ERROR_INVALID[TokenValidationError: bad signature]
    VERIFY -->|valid| CHECK_PLAN{plan_id matches?}

    CHECK_PLAN -->|no| ERROR_PLAN[TokenValidationError: plan_id_mismatch]
    CHECK_PLAN -->|yes| CHECK_GATE{gate_id matches?}

    CHECK_GATE -->|no| ERROR_GATE[TokenValidationError: gate_id_mismatch]
    CHECK_GATE -->|yes| CHECK_CONSUMED{Token already consumed?}

    CHECK_CONSUMED -->|yes| ERROR_CONSUMED[TokenConsumedError]
    CHECK_CONSUMED -->|no / Redis down| MARK_CONSUMED[mark_consumed SET NX]

    MARK_CONSUMED -->|success| RETURN_CLAIMS[Return decoded claims]
    MARK_CONSUMED -->|already consumed| ERROR_CONSUMED

    style ERROR_EXPIRED fill:#f66,stroke:#333
    style ERROR_INVALID fill:#f66,stroke:#333
    style ERROR_PLAN fill:#f66,stroke:#333
    style ERROR_GATE fill:#f66,stroke:#333
    style ERROR_CONSUMED fill:#f66,stroke:#333
    style RETURN_CLAIMS fill:#6f6,stroke:#333
```

---

## 3. Multi-Gate Execution Flow

```mermaid
flowchart TD
    USER_REQUEST([User request]) --> PREVIEW[PreviewOrchestrator: preview]
    PREVIEW --> SHOW_PREVIEW[Show preview to user]
    SHOW_PREVIEW --> APPROVE_A[User approves gate-A]

    APPROVE_A --> AG_A[ApprovalGate: approve gate-A]
    AG_A --> TOKEN_A[Token-A issued]
    TOKEN_A --> EXEC_START[ExecuteOrchestrator: validate token-A]
    EXEC_START --> EXEC_STEPS_1[Execute steps 1, 2]

    EXEC_STEPS_1 --> REACH_B{Reach gate-B?}
    REACH_B -->|yes| PAUSE_B[Pause execution]
    REACH_B -->|no more gates| EXEC_DONE

    PAUSE_B --> SHOW_B[Show intermediate results to user]
    SHOW_B --> APPROVE_B[User approves gate-B]
    APPROVE_B --> AG_B[ApprovalGate: approve gate-B]
    AG_B --> TOKEN_B[Token-B issued]
    TOKEN_B --> RESUME_B[ExecuteOrchestrator: validate token-B]
    RESUME_B --> EXEC_STEPS_2[Execute steps 3, 4]

    EXEC_STEPS_2 --> REACH_C{Reach gate-C?}
    REACH_C -->|yes| PAUSE_C[Pause execution]
    REACH_C -->|no more gates| EXEC_DONE

    PAUSE_C --> SHOW_C[Show final state to user]
    SHOW_C --> APPROVE_C[User approves gate-C]
    APPROVE_C --> AG_C[ApprovalGate: approve gate-C]
    AG_C --> TOKEN_C[Token-C issued]
    TOKEN_C --> RESUME_C[ExecuteOrchestrator: validate token-C]
    RESUME_C --> EXEC_STEPS_3[Execute steps 5, 6]

    EXEC_STEPS_3 --> EXEC_DONE[Execution complete]
    EXEC_DONE --> PLAN_WRITER[PlanWriter: persist outcomes]

    style APPROVE_A fill:#69f,stroke:#333
    style APPROVE_B fill:#69f,stroke:#333
    style APPROVE_C fill:#69f,stroke:#333
    style EXEC_DONE fill:#6f6,stroke:#333
    style PAUSE_B fill:#ff6,stroke:#333
    style PAUSE_C fill:#ff6,stroke:#333
```

---

## 4. Spawned Step Gate Flow (Learn from Approval)

```mermaid
flowchart TD
    REASONER([Tier 2 Reasoner spawns step]) --> POLICY[PolicyEngine: evaluate_spawn]
    POLICY --> DECISION{Policy matched?}

    DECISION -->|yes, requires_approval| GATE_SPAWN[Create gate for spawned step]
    DECISION -->|no policy matched| FALLBACK[PolicyDecision: requires_approval=true, policy_matched=false]
    DECISION -->|yes, auto-approve| EXECUTE_SPAWN[Execute spawned step directly]

    FALLBACK --> GATE_SPAWN

    GATE_SPAWN --> SHOW_SPAWN[Show spawned step to user]
    SHOW_SPAWN --> APPROVE_SPAWN[User approves]

    APPROVE_SPAWN --> AG_SPAWN[ApprovalGate: approve]
    AG_SPAWN --> CHECK_MATCHED{policy_matched?}

    CHECK_MATCHED -->|false| LEARN[PolicyEngine: learn_from_approval]
    CHECK_MATCHED -->|true| ISSUE_TOKEN

    LEARN --> ISSUE_TOKEN[Issue token for spawned gate]
    ISSUE_TOKEN --> RESUME_EXEC[ExecuteOrchestrator resumes]

    style FALLBACK fill:#ff6,stroke:#333
    style LEARN fill:#69f,stroke:#333
    style RESUME_EXEC fill:#6f6,stroke:#333
    style EXECUTE_SPAWN fill:#6f6,stroke:#333
```

---

## 5. Graceful Degradation States

```mermaid
flowchart LR
    subgraph Normal
        N_APPROVE[approve: JWT + Redis state]
        N_VALIDATE[validate: JWT + Redis single-use]
        N_STATE[get_state: Redis gate data]
    end

    subgraph Redis_Down[Redis Unavailable]
        R_APPROVE[approve: JWT only, no state stored]
        R_VALIDATE[validate: JWT only, no single-use check]
        R_STATE[get_state: returns None]
    end

    subgraph Preview_Down[PreviewService Unavailable]
        P_APPROVE[approve: JWT + Redis, preview_state=None]
    end

    subgraph Policy_Down[PolicyEngine Unavailable]
        PE_APPROVE[approve: JWT + Redis, no learning]
    end

    style Redis_Down fill:#ff6,stroke:#333
    style Preview_Down fill:#ff6,stroke:#333
    style Policy_Down fill:#ff6,stroke:#333
    style Normal fill:#dfd,stroke:#333
```
