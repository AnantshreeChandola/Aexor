# Modular Architecture — Layered Component Tree

**Status:** Active
**Version:** 1.2
**Conforms to:** GLOBAL_SPEC.md v2.2, Project_HLD.md v4.6

---

## Overview

This document provides a **layered, modularized view** of the Personal-agent architecture with clear separation between:
- **Memory/Persistence Layer** — Database interactions
- **Domain/Service Layer** — Business logic
- **Orchestration Layer** — Workflow execution
- **API/Interface Layer** — Entry points

Each component's database dependencies, component dependencies, and external service dependencies are explicitly mapped.

---

## 1. Layered Architecture Tree

```
┌─────────────────────────────────────────────────────────────────┐
│                    API / INTERFACE LAYER                        │
│  Entry points, HTTP handlers, external integrations             │
└─────────────────────────────────────────────────────────────────┘
                              ▼
        ┌─────────────────────────────────────────┐
        │              Intake                     │
        │  • DB: Redis (sessions)                 │
        │  • Deps: None (entry point)             │
        │  • Ext: FastAPI                         │
        └─────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   ORCHESTRATION LAYER                           │
│  Workflow building, preview, approval, execution                │
└─────────────────────────────────────────────────────────────────┘
                              ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │ WorkflowBuilder  │  │ Preview          │  │ Execute          │
    │                  │  │ Orchestrator     │  │ Orchestrator     │
    │ • DB: None       │  │                  │  │                  │
    │ • Deps:          │  │ • DB: None       │  │ • DB: Redis      │
    │   - PluginReg    │  │ • Deps:          │  │   (idempotency)  │
    │ • Ext: n8n       │  │   - Signer       │  │ • Deps:          │
    │                  │  │   - WorkflowBld  │  │   - Signer       │
    └──────────────────┘  │ • Ext: n8n       │  │   - ApprovalGate │
                          └──────────────────┘  │   - WorkflowBld  │
                                                │   - PlanWriter   │
    ┌──────────────────┐  ┌──────────────────┐  │ • Ext: n8n       │
    │ ApprovalGate     │  │ Execution        │  │                  │
    │                  │  │ Monitor          │  └──────────────────┘
    │ • DB: Redis      │  │                  │
    │   (tokens)       │  │ • DB: PostgreSQL │
    │ • Deps:          │  │   (exec tracker) │
    │   - Preview      │  │ • Deps:          │
    │ • Ext: PyJWT     │  │   - n8n API      │
    └──────────────────┘  │ • Ext: n8n SDK   │
                          └──────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   DOMAIN / SERVICE LAYER                        │
│  Business logic, planning, context, signatures                  │
└─────────────────────────────────────────────────────────────────┘
                              ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │ ContextRAG       │  │ Planner          │  │ Signer           │
    │ (context         │  │                  │  │                  │
    │  assembler)      │  │ • DB: None       │  │ • DB: None       │
    │ • DB: None       │  │ • Deps:          │  │ • Deps: None     │
    │   (queries only) │  │   - ContextRAG   │  │ • Ext:           │
    │ • Deps:          │  │   - PluginReg    │  │   cryptography   │
    │   - ProfileStore │  │ • Ext:           │  │   (Ed25519)      │
    │   - History      │  │   Anthropic API  │  └──────────────────┘
    │   - PlanLibrary  │  └──────────────────┘
    │ • Ext: None      │
    │                  │  ┌──────────────────┐  ┌──────────────────┐
    └──────────────────┘  │ PluginRegistry   │  │ Audit            │
                          │                  │  │                  │
                          │ • DB: PostgreSQL │  │ • DB: PostgreSQL │
                          │   (tools, ops,   │  │   (audit_events) │
                          │    reg_versions) │  │ • Deps: None     │
                          │ • Deps: None     │  │ • Ext:           │
                          │ • Ext: None      │  │   Logging,       │
                          └──────────────────┘
                                                │   Prometheus     │
    ┌──────────────────┐                        └──────────────────┘
    │ PlanWriter       │
    │                  │
    │ • DB: None       │
    │   (writes via    │
    │    Memory Layer) │
    │ • Deps:          │
    │   - PlanLibrary  │
    │   - History      │
    │   - VectorIndex  │
    │ • Ext:           │
    │   OpenAI (embed) │
    └──────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  MEMORY / PERSISTENCE LAYER                     │
│  Database interactions, data storage, retrieval                 │
└─────────────────────────────────────────────────────────────────┘
                              ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │ ProfileStore     │  │ History          │  │ VectorIndex      │
    │                  │  │                  │  │                  │
    │ • DB:            │  │ • DB:            │  │ • DB:            │
    │   - PostgreSQL:  │  │   - PostgreSQL:  │  │   - PostgreSQL:  │
    │     profiles,    │  │     history      │  │     vectors      │
    │     preferences, │  │ • Deps: None     │  │   - pgvector     │
    │     consent      │  │ • Ext: None      │  │ • Deps: None     │
    │ • Deps: None     │  └──────────────────┘  │ • Ext:           │
    │ • Ext: None      │                        │   OpenAI (embed) │
    └──────────────────┘                        └──────────────────┘

    ┌──────────────────┐
    │ PlanLibrary      │
    │                  │
    │ • DB:            │
    │   - PostgreSQL:  │
    │     plans,       │
    │     signatures,  │
    │     outcomes     │
    │ • Deps: None     │
    │ • Ext: None      │
    └──────────────────┘
```

---

## 2. Memory Layer Module

The **Memory Layer** is a cohesive module containing all database-interaction components:

```
memory/
├── ProfileStore/      # User preferences, consent
├── History/           # Normalized outcome facts
├── VectorIndex/       # Semantic similarity search (deferred — see HLD §12)
└── PlanLibrary/       # Plan storage + retrieval
```

**Shared Characteristics:**
- All interact directly with PostgreSQL
- VectorIndex uses pgvector extension (when implemented)
- Provide CRUD operations for upper layers
- No business logic (thin adapters)
- Reusable across services

**Module Interface:**
```python
# memory/interface.py
class MemoryLayer:
    profile: ProfileStore
    history: History
    vector: VectorIndex
    plans: PlanLibrary
```

---

## 3. Database Schema Ownership Map

### PostgreSQL Tables

| Table Name | Schema | Owner Component | Description |
|------------|--------|-----------------|-------------|
| `users` | `public` | ProfileStore | User accounts |
| `profiles` | `public` | ProfileStore | User profile metadata |
| `preferences` | `public` | ProfileStore | Key-value preferences |
| `consent_flags` | `public` | ProfileStore | Tier access permissions |
| `history` | `public` | History | Normalized outcome facts |
| `plans` | `public` | PlanLibrary | Signed plan records |
| `plan_signatures` | `public` | PlanLibrary | Ed25519 signatures |
| `plan_outcomes` | `public` | PlanLibrary | Execution results |
| `vectors` | `public` | VectorIndex | Embedding vectors (plans, facts, prefs) |
| `tools` | `public` | PluginRegistry | Registered external integrations |
| `operations` | `public` | PluginRegistry | Tool operation metadata (n8n bindings, scopes) |
| `registry_versions` | `public` | PluginRegistry | Monotonic version counter for registry changes |
| `user_integrations` | `public` | Shared (PluginRegistry) | User-to-tool credential ID mapping |
| `audit_events` | `public` | Audit | System audit trail |
| `sessions` | `public` | Intake | (Optional - if not Redis) |

### Redis Key Patterns

| Key Pattern | Owner Component | TTL | Description |
|-------------|-----------------|-----|-------------|
| `session:{user_id}` | Intake | 1h | Session state |
| `approval_token:{token}` | ApprovalGate | 15m | Single-use approval tokens |
| `idempotency:{plan_id}:{step}:{hash}` | ExecuteOrchestrator | 24h | Idempotency keys |
| `lock:{resource}.{entity}.{op}` | ExecuteOrchestrator | 30s | Resource locks |
| `plan_cache:{plan_id}` | PlanLibrary | 1h | Hot plan cache |

### pgvector Indexes

| Index Name | Table | Vector Dimension | Owner Component | Description |
|------------|-------|------------------|-----------------|-------------|
| `idx_vectors_embedding` | `vectors` | 1536 | VectorIndex | HNSW index for ANN search |
| `idx_vectors_namespace_embedding` | `vectors` | 1536 | VectorIndex | Composite index (namespace + vector) |

---

## 4. Component Dependency Matrix

### Memory/Persistence Layer

#### ProfileStore
```
ProfileStore
├── Database Dependencies
│   ├── PostgreSQL: profiles, preferences, consent_flags
│   └── Redis: (none)
├── Component Dependencies
│   └── (none - foundation component)
└── External Dependencies
    └── (none)
```

#### History
```
History
├── Database Dependencies
│   └── PostgreSQL: history
├── Component Dependencies
│   └── (none - foundation component)
└── External Dependencies
    └── (none)
```

#### VectorIndex
```
VectorIndex
├── Database Dependencies
│   ├── PostgreSQL: vectors (with pgvector extension)
│   └── pgvector: idx_vectors_embedding
├── Component Dependencies
│   └── (none - foundation component)
└── External Dependencies
    └── OpenAI API (embeddings only)
```

#### PlanLibrary
```
PlanLibrary
├── Database Dependencies
│   ├── PostgreSQL: plans, plan_signatures, plan_outcomes
│   └── Redis: plan_cache:{plan_id}
├── Component Dependencies
│   └── (none - foundation component)
└── External Dependencies
    └── (none)
```

---

### Domain/Service Layer

#### Intake
```
Intake
├── Database Dependencies
│   └── Redis: session:{user_id}
├── Component Dependencies
│   └── (none - entry point)
└── External Dependencies
    └── FastAPI (HTTP server)
```

#### ContextRAG
```
ContextRAG (context assembler — structured queries, not embedding-based RAG)
├── Database Dependencies
│   └── (none - queries via component dependencies)
├── Component Dependencies
│   ├── → ProfileStore (Tier 2: stable prefs)
│   ├── → History (Tier 3: recent history)
│   └── → PlanLibrary (Tier 3: successful plans)
└── External Dependencies
    └── (none)
```

#### Planner
```
Planner
├── Database Dependencies
│   └── (none - stateless)
├── Component Dependencies
│   ├── → ContextRAG (Evidence input)
│   └── → PluginRegistry (tool catalog)
└── External Dependencies
    └── Anthropic Claude API (plan generation, temperature=0)
```

#### Signer
```
Signer
├── Database Dependencies
│   └── (none - key storage in env/secrets)
├── Component Dependencies
│   └── (none - security primitive)
└── External Dependencies
    └── cryptography library (Ed25519)
```

#### PluginRegistry
```
PluginRegistry
├── Database Dependencies
│   └── PostgreSQL: tools, operations, registry_versions
├── Component Dependencies
│   └── (none - configuration source)
└── External Dependencies
    └── (none)
```

#### PlanWriter
```
PlanWriter
├── Database Dependencies
│   └── (none - writes via Memory Layer)
├── Component Dependencies
│   ├── → PlanLibrary (persist outcomes)
│   ├── → History (persist facts)
│   └── → VectorIndex (trigger re-indexing — deferred, see HLD §12)
└── External Dependencies
    └── OpenAI API (embeddings for new facts — deferred with VectorIndex)
```

#### Audit
```
Audit
├── Database Dependencies
│   └── PostgreSQL: audit_events
├── Component Dependencies
│   └── (none - cross-cutting concern)
└── External Dependencies
    ├── Python logging
    └── Prometheus/CloudWatch (optional)
```

---

### Orchestration Layer

#### WorkflowBuilder
```
WorkflowBuilder
├── Database Dependencies
│   └── (none - in-memory workflow generation)
├── Component Dependencies
│   └── → PluginRegistry (connector bindings)
└── External Dependencies
    ├── NetworkX (dependency graph analysis)
    └── n8n (workflow JSON format)
```

#### PreviewOrchestrator
```
PreviewOrchestrator
├── Database Dependencies
│   └── (none - executes read-only workflows)
├── Component Dependencies
│   ├── → Signer (signature verification)
│   └── → WorkflowBuilder (mode="preview")
└── External Dependencies
    └── n8n API (workflow execution)
```

#### ApprovalGate
```
ApprovalGate
├── Database Dependencies
│   └── Redis: approval_token:{token}
├── Component Dependencies
│   └── (receives Preview wrapper, no direct calls)
└── External Dependencies
    └── PyJWT (token generation)
```

#### ExecuteOrchestrator
```
ExecuteOrchestrator
├── Database Dependencies
│   ├── Redis: idempotency:{plan_id}:{step}:{hash}
│   └── Redis: lock:{resource}.{entity}.{op}
├── Component Dependencies
│   ├── → Signer (signature verification)
│   ├── → ApprovalGate (token validation + multi-gate HITL)
│   ├── → WorkflowBuilder (mode="execute" with HITL gates)
│   └── → PlanWriter (outcome persistence)
└── External Dependencies
    └── n8n API (all workflows with Wait nodes for HITL)

NOTE: Execute workflows contain embedded HITL approval gates (gate-A, gate-B, etc.)
      Each gate_id maps to an n8n Wait node that pauses execution until
      ApprovalGate issues a continuation token. Multi-gate workflows require
      sequential approvals (e.g., shopping: gate-A for cart, gate-B for purchase).
```

#### ExecutionMonitor
```
ExecutionMonitor
├── Database Dependencies
│   └── PostgreSQL (execution_tracker table)
├── Component Dependencies
│   └── → PlanWriter (outcome persistence)
└── External Dependencies
    └── n8n REST API (GET /executions for polling)

Purpose: Background polling service (runs every 30s) that:
- Detects stuck n8n executions (no progress for 5+ minutes)
- Triggers workflow-level retries with exponential backoff (60s, 300s, 900s)
- Enforces time budgets (cancel after 60 minutes)
- Notifies users of terminal failures (max 3 attempts exhausted)
```

---

## 5. Dependency Flow Diagram

### Forward Flow (Request → Response)

```
┌──────────┐
│  Client  │
└────┬─────┘
     │ HTTP POST
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ API LAYER                                                       │
│  ┌────────┐                                                     │
│  │ Intake │ ─────────────────────────────────────────────┐     │
│  └────┬───┘                                               │     │
└───────┼───────────────────────────────────────────────────┼─────┘
        │                                                   │
        │ Intent JSON                                       │ Session
        ▼                                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ DOMAIN LAYER                                                    │
│  ┌────────────┐                                                 │
│  │ ContextRAG │◄─────┐                                          │
│  └─────┬──────┘      │                                          │
│        │ Evidence[]  │                                          │
│        ▼             │                                          │
│  ┌────────┐         │                              ┌──────────┐│
│  │Planner │         │                              │  Signer  ││
│  └───┬────┘         │                              └────┬─────┘│
│      │ Plan         │                                   │      │
│      ▼              │                                   │      │
│     ┌───────────────┴────────┐                          │      │
│     │Queries Memory Layer    │                     Signature   │
│     │- ProfileStore (prefs)  │                          │      │
│     │- History (facts)       │                          │      │
│     │- PlanLibrary (plans)   │                          │      │
│     └───────────┬────────────┘                          │      │
└─────────────────┼───────────────────────────────────────┼──────┘
                  │                                       │
                  ▼                                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ MEMORY LAYER                                                    │
│  ┌──────────────┐  ┌─────────┐  ┌────────────┐  ┌───────────┐ │
│  │ ProfileStore │  │ History │  │PlanLibrary │  │VectorIndex│ │
│  └──────┬───────┘  └────┬────┘  └─────┬──────┘  └─────┬─────┘ │
└─────────┼───────────────┼─────────────┼───────────────┼────────┘
          │               │             │               │
          ▼               ▼             ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│ DATABASE LAYER                                                  │
│  ┌────────────┐  ┌────────────┐                                │
│  │ PostgreSQL │  │   Redis    │                                │
│  └────────────┘  └────────────┘                                │
└─────────────────────────────────────────────────────────────────┘

                  Signed Plan
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│ ORCHESTRATION LAYER                                             │
│  ┌────────────────┐                                             │
│  │WorkflowBuilder │                                             │
│  └───────┬────────┘                                             │
│          │ n8n Workflow JSON                                    │
│          ▼                                                      │
│  ┌───────────────────┐                                          │
│  │Preview Orchestrator│                                         │
│  └────────┬──────────┘                                          │
│           │ Preview Wrapper                                     │
│           ▼                                                     │
│  ┌──────────────┐                                               │
│  │ApprovalGate  │  (Initial approval: gate-A)                   │
│  └──────┬───────┘                                               │
│         │ Approval Token (gate-A)                               │
│         ▼                                                       │
│  ┌────────────────────┐                                          │
│  │Execute Orchestrator│  (triggers n8n workflow)                │
│  │                    │                                         │
│  │ ┌────────────────┐ │                                         │
│  │ │ n8n Workflow   │ │                 │                      │
│  │ │ with HITL gates│ │                 │                      │
│  │ │                │ │                 │                      │
│  │ │ Step 1,2,3     │ │                 │                      │
│  │ │    ↓           │ │                 │                      │
│  │ │ Wait(gate-B) ──┼─┼─────────┐       │                      │
│  │ │    ↓           │ │         │       │                      │
│  │ │ Step 4,5       │ │         │       │                      │
│  │ │    ↓           │ │         │       │                      │
│  │ │ Wait(gate-C) ──┼─┼─────┐   │       │                      │
│  │ │    ↓           │ │     │   │       │                      │
│  │ │ Step 6,7       │ │     │   │       │                      │
│  │ └────────────────┘ │     │   │       │                      │
│  └──────┬─────────────┘     │   │       │                      │
│         │                   │   │       │                      │
│         │                   ▼   ▼       │                      │
│         │            ┌──────────────┐   │                      │
│         │            │ApprovalGate  │   │                      │
│         │            │ Multi-gate   │   │                      │
│         │            │ gate-B, C... │   │                      │
│         │            └──────┬───────┘   │                      │
│         │                   │ Resume    │                      │
│         │                   │ Tokens    │                      │
│         │                   └───────────┘                      │
└─────────┼──────────────────────────────────────────────────────┘
          │
          │ Execute Wrapper[]
          ▼                               │
┌─────────────────────────────────────────┼──────────────────────┐
│ DOMAIN LAYER                            │                      │
│  ┌───────────┐                          │                      │
│  │PlanWriter │◄─────────────────────────┘                      │
│  └─────┬─────┘                                                 │
└────────┼───────────────────────────────────────────────────────┘
         │ Persist outcomes
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ MEMORY LAYER                                                    │
│  ┌────────────┐  ┌─────────┐  ┌────────────┐                   │
│  │PlanLibrary │  │ History │  │VectorIndex │                   │
│  └──────┬─────┘  └────┬────┘  └─────┬──────┘                   │
└─────────┼─────────────┼─────────────┼────────────────────────────┘
          │             │             │
          ▼             ▼             ▼
┌─────────────────────────────────────────────────────────────────┐
│ DATABASE LAYER                                                  │
│  PostgreSQL: plans, history, vectors                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Module Groupings for Parallel Development

### Group 1: Memory Module (Foundation)
**Can build in parallel:**
- ProfileStore
- History
- PlanLibrary
- VectorIndex (deferred — see HLD §12)

**Timeline:** Sprint 1 (2 weeks)
**Agents:** 3 parallel agents (VectorIndex deferred)

---

### Group 2: Security & Configuration
**Can build in parallel:**
- Signer (cryptography primitive)
- PluginRegistry (tool catalog — PostgreSQL + Redis cache)
- Audit (logging infrastructure)

**Timeline:** Sprint 1 (concurrent with Memory Module)
**Agents:** 3 parallel agents

---

### Group 3: Planning & Context
**Sequential dependencies:**
1. Intake (minimal deps)
2. ContextRAG (depends on Memory Module)
3. Planner (depends on ContextRAG)

**Timeline:** Sprint 2-3 (2 weeks)
**Agents:** Can parallelize Intake + ContextRAG, then Planner

---

### Group 4: Orchestration Foundation
**Sequential dependencies:**
1. WorkflowBuilder (depends on PluginRegistry)
2. PreviewOrchestrator (depends on WorkflowBuilder + Signer)
3. ApprovalGate (depends on Preview output)

**Timeline:** Sprint 4 (2 weeks)
**Agents:** Sequential with handoffs

---

### Group 5: Execution & Persistence
**Parallel start, converge at end:**
- ExecuteOrchestrator (depends on all Group 4)
- ExecutionMonitor (depends on n8n API, execution_tracker table)
- PlanWriter (depends on Memory Module)

**Timeline:** Sprint 5-6 (3 weeks)
**Agents:** 2-3 parallel agents

---

## 7. Multi-Gate HITL (Human-in-the-Loop) Execution Flow

### Overview

Execute workflows support **multi-gate approval** where execution pauses at designated gate points (gate-A, gate-B, gate-C...) waiting for human approval before proceeding. This is critical for high-risk operations like financial transactions or booking confirmations.

### Execution Flow with Multi-Gate Approvals

```
1. Initial Preview & Approval (gate-A)
   ┌──────────────────────────────────────────────────┐
   │ User Request → Preview → ApprovalGate(gate-A)    │
   │ User approves gate-A → Receives token-A          │
   └──────────────────────────────────────────────────┘
                         ↓
2. Execute Orchestrator Starts
   ┌──────────────────────────────────────────────────┐
   │ Validates token-A → Builds n8n workflow          │
   │ Workflow contains Wait nodes for gate-B, gate-C  │
   └──────────────────────────────────────────────────┘
                         ↓
3. Workflow Execution (Partial)
   ┌──────────────────────────────────────────────────┐
   │ Execute steps 1, 2, 3 (e.g., search products)    │
   │ Reach Wait(gate-B) → Pause execution             │
   │ Generate intermediate preview for gate-B         │
   └──────────────────────────────────────────────────┘
                         ↓
4. Intermediate Approval (gate-B)
   ┌──────────────────────────────────────────────────┐
   │ Present results to user (e.g., shopping cart)    │
   │ ApprovalGate(gate-B) → User approves → token-B   │
   │ Resume workflow with token-B                     │
   └──────────────────────────────────────────────────┘
                         ↓
5. Continue Execution
   ┌──────────────────────────────────────────────────┐
   │ Execute steps 4, 5 (e.g., calculate total)       │
   │ Reach Wait(gate-C) → Pause again                 │
   │ Generate final preview for gate-C                │
   └──────────────────────────────────────────────────┘
                         ↓
6. Final Approval (gate-C)
   ┌──────────────────────────────────────────────────┐
   │ Present final state (e.g., purchase total)       │
   │ ApprovalGate(gate-C) → User approves → token-C   │
   │ Resume workflow with token-C                     │
   └──────────────────────────────────────────────────┘
                         ↓
7. Complete Execution
   ┌──────────────────────────────────────────────────┐
   │ Execute final steps 6, 7 (e.g., purchase)        │
   │ Return Execute wrappers → PlanWriter             │
   └──────────────────────────────────────────────────┘
```

### Example: Shopping Flow (3 Gates)

```json
{
  "plan_id": "01HXYZ...",
  "graph": [
    {
      "step": 1,
      "role": "Fetcher",
      "uses": "amazon.product",
      "call": "search",
      "after": [],
      "gate_id": null
    },
    {
      "step": 2,
      "role": "Analyzer",
      "uses": "internal.compare",
      "call": "rank_by_price",
      "after": [1],
      "gate_id": null
    },
    {
      "step": 3,
      "role": "Booker",
      "uses": "amazon.cart",
      "call": "add_items",
      "after": [2],
      "gate_id": "gate-B"  // ← PAUSE HERE for cart approval
    },
    {
      "step": 4,
      "role": "Fetcher",
      "uses": "amazon.cart",
      "call": "calculate_total",
      "after": [3],
      "gate_id": null
    },
    {
      "step": 5,
      "role": "Booker",
      "uses": "amazon.checkout",
      "call": "purchase",
      "after": [4],
      "gate_id": "gate-C"  // ← PAUSE HERE for purchase approval
    },
    {
      "step": 6,
      "role": "Notifier",
      "uses": "email",
      "call": "send_confirmation",
      "after": [5],
      "gate_id": null
    }
  ]
}
```

### n8n Workflow Structure with Wait Nodes

```javascript
// WorkflowBuilder generates this n8n workflow
{
  "nodes": [
    { "id": "step1", "type": "HTTP Request", "name": "Search Products" },
    { "id": "step2", "type": "Function", "name": "Rank by Price" },

    // Wait node for gate-B
    {
      "id": "gate-B-wait",
      "type": "Wait",
      "parameters": {
        "resume": "webhook",
        "webhookSuffix": "gate-B-{{$json.plan_id}}",
        "approvalRequired": true
      }
    },

    { "id": "step3", "type": "HTTP Request", "name": "Add to Cart" },
    { "id": "step4", "type": "HTTP Request", "name": "Calculate Total" },

    // Wait node for gate-C
    {
      "id": "gate-C-wait",
      "type": "Wait",
      "parameters": {
        "resume": "webhook",
        "webhookSuffix": "gate-C-{{$json.plan_id}}",
        "approvalRequired": true
      }
    },

    { "id": "step5", "type": "HTTP Request", "name": "Purchase" },
    { "id": "step6", "type": "HTTP Request", "name": "Send Confirmation" }
  ],
  "connections": {
    "step1": { "main": [[{ "node": "step2" }]] },
    "step2": { "main": [[{ "node": "gate-B-wait" }]] },
    "gate-B-wait": { "main": [[{ "node": "step3" }]] },
    "step3": { "main": [[{ "node": "step4" }]] },
    "step4": { "main": [[{ "node": "gate-C-wait" }]] },
    "gate-C-wait": { "main": [[{ "node": "step5" }]] },
    "step5": { "main": [[{ "node": "step6" }]] }
  }
}
```

### WorkflowBuilder Responsibilities

1. **Parse gate_id from plan steps**
2. **Insert Wait nodes** before steps with gate_id != null
3. **Configure webhook URLs** for each gate (unique per plan_id + gate_id)
4. **Generate intermediate previews** at each gate for user review
5. **Validate token on resume** before continuing execution

### ApprovalGate Token Management

```python
# ApprovalGate issues gate-specific tokens
class ApprovalToken:
    token: str           # JWT with short TTL (15 min)
    plan_hash: str       # Binds to specific plan
    user_id: str
    gate_id: str         # "gate-A", "gate-B", "gate-C"
    plan_id: str
    scopes: list[str]
    single_use: bool     # Consumed after resume
    preview_state: dict  # ⭐ NEW: Cached preview results (user selections, search results)

# Redis tracking for single-use enforcement + state caching
redis.setex(
    f"gate_token:{token}",
    900,  # 15 min TTL
    json.dumps({
        "valid": True,
        "preview_state": preview_wrapper["cached_state"]  # ⭐ Cache user selections
    })
)

# On resume, check and consume
token_data = redis.get(f"gate_token:{token}")
if not token_data:
    raise TokenExpiredOrUsed()

# Retrieve cached preview state
preview_state = json.loads(token_data)["preview_state"]

# Consume token (single-use)
redis.delete(f"gate_token:{token}")
```

### Key Design Points

1. **Plan gates are declarative** - Planner inserts `gate_id` in plan graph
2. **WorkflowBuilder handles mapping** - Converts gate_id → n8n Wait nodes
3. **ApprovalGate caches preview state** - Token includes user selections from preview
4. **n8n manages workflow state** - Paused workflows persist in n8n database
5. **Each gate gets unique token** - gate-A token cannot resume gate-B
6. **Idempotency preserved** - Plan steps still have idempotency keys
7. **Compensation supported** - If gate-C rejected, compensate steps 1-4
8. **Preview step reuse** - ExecuteOrchestrator skips preview-only steps, uses cached state

---

## 8. Preview State Caching & Step Reuse

### Problem

When users interact with preview (searching, selecting options), we shouldn't repeat those steps in execute:

**Inefficient (what we DON'T want):**
```
Preview: Search sweaters → User picks "Blue Nike"
Execute: Search sweaters AGAIN → User picks AGAIN → Add to cart
```

**Efficient (what we DO want):**
```
Preview: Search sweaters → User picks "Blue Nike" → Cache selection
Execute: Retrieve cached selection → Add to cart (skip search/selection)
```

### Solution: Preview State Caching

#### 1. Preview Wrapper Includes Cached State

```python
# PreviewOrchestrator caches intermediate results
preview_wrapper = {
    "normalized": {
        "search_results": [...],
        "user_selection": {"product_id": "sweater-1", "size": "L", "price": 50}
    },
    "source": "preview",
    "can_execute": True,
    "cached_state": {
        "step_1_result": {"search_results": [...]},
        "step_2_result": {"selected_product": "sweater-1", "size": "L"}
    }
}
```

#### 2. Plan Marks Preview-Only Steps

```json
{
  "plan_id": "plan-shop-001",
  "graph": [
    {
      "step": 1,
      "role": "Fetcher",
      "uses": "amazon.product",
      "call": "search",
      "execute_mode": "preview_only",  // ⭐ Skip in execute
      "dry_run": true
    },
    {
      "step": 2,
      "role": "Resolver",
      "uses": "internal.ui",
      "call": "user_select",
      "execute_mode": "preview_only",  // ⭐ Skip in execute
      "dry_run": true
    },
    {
      "step": 3,
      "role": "Booker",
      "uses": "amazon.cart",
      "call": "add_to_cart",
      "args": {
        "product_id": "{{preview.cached_state.step_2_result.selected_product}}"
      },
      "gate_id": "gate-A"
    }
  ]
}
```

#### 3. ExecuteOrchestrator Retrieves Cached State

```python
# ExecuteOrchestrator receives approval token with cached state
token_data = redis.get(f"gate_token:{approval_token}")
preview_state = token_data["preview_state"]

# Skip preview-only steps, use cached state for args
for step in plan["graph"]:
    if step.get("execute_mode") == "preview_only":
        continue  # Skip - already executed in preview

    # Resolve template args from preview cache
    args = resolve_template_args(step["args"], preview_state)
    # {"product_id": "sweater-1"}  ← from cached state

    execute_step(step, args)
```

#### 4. Benefits

- **Efficiency**: Don't re-execute expensive API calls
- **UX**: User selections preserved (don't ask twice)
- **Cost**: Fewer external API calls
- **Consistency**: Execute uses exact same data user saw in preview
- **Performance**: Execute phase is faster

### execute_mode Values

| Mode | Preview | Execute | Use Case |
|------|---------|---------|----------|
| `preview_only` | ✓ Run | ✗ Skip | Search, user selection, UI interactions |
| `execute_only` | ✗ Skip | ✓ Run | Write operations, purchases, bookings |
| `both` (default) | ✓ Run | ✓ Run | Idempotent operations, reads |

---

## 9. Database Migration Strategy

### Phase 1: Core Tables
```sql
-- ProfileStore
CREATE TABLE users (user_id UUID PRIMARY KEY, ...);
CREATE TABLE profiles (...);
CREATE TABLE preferences (...);
CREATE TABLE consent_flags (...);

-- History
CREATE TABLE history (fact_id UUID PRIMARY KEY, ...);

-- VectorIndex
CREATE TABLE vectors (
    id UUID PRIMARY KEY,
    namespace VARCHAR,
    embedding vector(1536),
    metadata JSONB
);
CREATE INDEX idx_vectors_embedding ON vectors USING hnsw (embedding vector_cosine_ops);
```

### Phase 1.5: PluginRegistry & User Integrations Tables
```sql
-- PluginRegistry
CREATE TABLE tools (tool_id VARCHAR(128) PRIMARY KEY, ...);
CREATE TABLE operations (id UUID PRIMARY KEY, tool_id VARCHAR(128) REFERENCES tools, ...);
CREATE TABLE registry_versions (version INTEGER PRIMARY KEY, ...);

-- User Integrations (shared infrastructure)
CREATE TABLE user_integrations (id UUID PRIMARY KEY, user_id UUID REFERENCES users, tool_id VARCHAR(128), ...);
```

### Phase 2: Planning Tables
```sql
-- PlanLibrary
CREATE TABLE plans (...);
CREATE TABLE plan_signatures (...);
CREATE TABLE plan_outcomes (...);
```

### Phase 3: Observability
```sql
-- Audit
CREATE TABLE audit_events (...);
```

---

## 10. Summary

### Key Architectural Decisions

1. **Memory Layer Separation**
   - All database interactions isolated in 4 components
   - Clean adapter interfaces for upper layers
   - Enables independent scaling/optimization

2. **Stateless Service Layer**
   - Planner, ContextRAG, Signer have no persistent state
   - Simplifies testing and horizontal scaling

3. **Redis for Ephemeral State**
   - Sessions, tokens, idempotency keys, preview state caching
   - Short TTLs prevent state accumulation

4. **PostgreSQL for Persistent State** (pgvector deferred with VectorIndex)
   - Relational data (profiles, plans, history)
   - Vector search deferred until structured queries prove insufficient (see HLD §12)
   - Single database reduces operational complexity

5. **Component Ownership**
   - Each table owned by exactly one component
   - Cross-component queries go through well-defined interfaces

6. **Preview State Caching**
   - ApprovalGate stores preview results with approval tokens
   - ExecuteOrchestrator skips preview-only steps
   - User selections preserved, no re-execution of expensive operations

---

## Next Steps

1. **Implement Memory Module** (ProfileStore, History, PlanLibrary — VectorIndex deferred)
2. **Set up database schemas** and migrations
3. **Build Security & Config** (Signer, PluginRegistry, Audit)
4. **Implement Planning Layer** (Intake → ContextRAG → Planner)
5. **Build Orchestration** (WorkflowBuilder → Preview → Approval → Execute)
6. **Integrate end-to-end** with use case tests

This modular structure enables **parallel development** while maintaining clear separation of concerns and ownership boundaries.
