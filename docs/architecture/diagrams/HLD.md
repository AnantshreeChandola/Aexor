```mermaid
  flowchart LR
  U[User] --> IN[Intake & Reason]
  IN --> RAG((ContextRAG: prefs/history/exemplars))
  IN --> PLIB[Plan Library]
  PLIB --> RET[Retrieve & Score Plans]
  IN --> REG[Plugin Registry- MCP bindings]
  REG --> SEL[Select Tools]
  SEL --> PLAN[Planner dry_run plan]
  PLAN --> SIG[Signer Ed25519]
  SIG --> PREV[Preview Orchestrator MCP, read-only]
  PREV -->|Preview card + evidence| U
  U -->|Approve Gate A/B/…| GATE[Approval Gates]
  GATE --> EXE[ExecuteOrchestrator MCP + Anthropic API]
  EXE --> CRED[Credential Vault AES-256-GCM decrypt]
  EXE --> MCP[MCP Tool Invocations GCal/Gmail/HTTP/Slack…]
  EXE --> SCHED[APScheduler + Redis for long jobs]
  MCP --> AUD[Audit & Metrics]
  SCHED --> AUD
  MCP --> PW[PlanWriter → Plan Library + History]
  SCHED --> PW
  AUD --> U
  PW --> RAG
  ```

  context aware, personalized, self learning.
