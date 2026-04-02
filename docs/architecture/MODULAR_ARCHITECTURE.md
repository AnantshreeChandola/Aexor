# Modular Architecture — Layered Component Tree

**Status:** Active
**Version:** 2.0
**Conforms to:** GLOBAL_SPEC.md v3.0, Project_HLD.md v6.1

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
    ┌──────────────────┐  ┌──────────────────┐
    │ Preview          │  │ Execute          │
    │ Orchestrator     │  │ Orchestrator     │
    │                  │  │                  │
    │ • DB: None       │  │ • DB: Redis      │
    │ • Deps:          │  │   (idempotency)  │
    │   - Signer       │  │ • Deps:          │
    │   - PluginReg    │  │   - Signer       │
    │ • Ext: MCP       │  │   - ApprovalGate │
    └──────────────────┘  │   - PluginReg    │
                          │   - PolicyEngine │
                          │   - PlanWriter   │
                          │ • Ext: MCP,      │
                          │   Anthropic API  │
    ┌──────────────────┐  │                  │
    │ ApprovalGate     │  └──────────────────┘
    │                  │
    │ • DB: Redis      │  ┌──────────────────┐
    │   (tokens)       │  │ Execution        │
    │ • Deps:          │  │ Monitor          │
    │   - Preview      │  │                  │
    │ • Ext: PyJWT     │  │ • DB: PostgreSQL │
    └──────────────────┘  │   (exec tracker) │
                          │ • Deps:          │
                          │   - PlanWriter   │
                          │ • Ext: None      │
                          └──────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   DOMAIN / SERVICE LAYER                        │
│  Business logic, planning, context, signatures                  │
└─────────────────────────────────────────────────────────────────┘
                              ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │ ContextRAG       │  │ Planner          │  │ Signer           │
    │ (context         │  │                  │  │                  │
    │ • DB: None       │  │ • Deps:          │  │ • Deps: None     │
    │   (queries only) │  │   - ContextRAG   │  │ • Ext:           │
    │ • Deps:          │  │   - PluginReg    │  │   cryptography   │
    │   - ProfileStore │  │   - Signer       │  │   (Ed25519)      │
    │   - History      │  │   - PlanLibrary  │  └──────────────────┘
    │   - PlanLibrary  │  │     (fallback)   │
    │   - VectorIndex  │  │   - PolicyEngine │  ┌──────────────────┐
    │     (optional)   │  │ • Ext:           │  │ PolicyEngine     │
    │ • Ext: None      │  │   Anthropic API  │  │                  │
    │                  │  └──────────────────┘  │ • DB: PostgreSQL │
    └──────────────────┘                        │   (policies,     │
                          ┌──────────────────┐  │    policy_       │
                          │ PluginRegistry   │  │    attestations) │
                          │                  │  │ • Redis:         │
                          │ • DB: PostgreSQL │  │   policy_cache   │
                          │   (tools, ops,   │  │ • Deps:          │
                          │    reg_versions) │  │   - PluginReg    │
                          │ • Deps: None     │  │   - Audit        │
                          │ • Ext: None      │  │ • Ext: None      │
                          └──────────────────┘  └──────────────────┘

    ┌──────────────────┐  ┌──────────────────┐
    │ PlanWriter       │  │ Audit            │
    │                  │  │                  │
    │ • DB: None       │  │ • DB: PostgreSQL │
    │   (writes via    │  │   (audit_events) │
    │    Memory Layer) │  │ • Deps: None     │
    │ • Deps:          │  │ • Ext:           │
    │   - PlanLibrary  │  │   Logging,       │
    │   - History      │  │   Prometheus     │
    │   - VectorIndex  │  └──────────────────┘
    │ • Ext: None      │
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
    │     profiles,    │  │     history      │  │     plan_        │
    │     preferences, │  │ • Deps: None     │  │     embeddings   │
    │     consent      │  │ • Ext: None      │  │   - pgvector     │
    │ • Deps: None     │  └──────────────────┘  │ • Deps: None     │
    │ • Ext: None      │                        │ • Ext: ONNX      │
    └──────────────────┘                        │   Runtime(local) │
                                                └──────────────────┘

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
├── VectorIndex/       # Hybrid search (BM25 + semantic) via pgvector + tsvector
└── PlanLibrary/       # Plan storage + retrieval
```

**Shared Characteristics:**
- All interact directly with PostgreSQL
- VectorIndex uses pgvector extension + tsvector for hybrid search (BM25 + cosine RRF)
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
| `plan_embeddings` | `public` | VectorIndex | Hybrid search: 384-dim embeddings + tsvector for plans |
| `tools` | `public` | PluginRegistry | Registered external integrations |
| `operations` | `public` | PluginRegistry | Tool operation metadata (MCP tool bindings, scopes) |
| `registry_versions` | `public` | PluginRegistry | Monotonic version counter for registry changes |
| `user_integrations` | `public` | Shared (PluginRegistry) | User-to-tool credential ID mapping |
| `audit_events` | `public` | Audit | System audit trail |
| `sessions` | `public` | Intake | (Optional - if not Redis) |
| `policies` | `public` | PolicyEngine | Policy rules governing LLM reasoning steps |
| `policy_attestations` | `public` | PolicyEngine | Signed attestation records for runtime step spawning |
| `credential_vault` | `public` | PluginRegistry | AES-256-GCM encrypted credentials (vault IDs, encrypted values, key versions) |
| `plan_revisions` | `public` | PlanLibrary | Audit trail of each spawn event (timestamp, spawning step, new steps, policy decision) |

### Redis Key Patterns

| Key Pattern | Owner Component | TTL | Description |
|-------------|-----------------|-----|-------------|
| `session:{user_id}` | Intake | 1h | Session state |
| `approval_token:{token}` | ApprovalGate | 15m | Single-use approval tokens |
| `idempotency:{plan_id}:{step}:{hash}` | ExecuteOrchestrator | 24h | Idempotency keys |
| `lock:{resource}.{entity}.{op}` | ExecuteOrchestrator | 30s | Resource locks |
| `plan_cache:{plan_id}` | PlanLibrary | 1h | Hot plan cache |
| `policy_cache:{policy_id}:{version}` | PolicyEngine | 5m | Cached policy rules for fast evaluation |
| `reasoning_context:{plan_id}:{step}` | ExecuteOrchestrator | 1h | Context data for LLM reasoning steps |

### pgvector Indexes

| Index Name | Table | Type | Owner Component | Description |
|------------|-------|------|-----------------|-------------|
| `idx_plan_embeddings_hnsw` | `plan_embeddings` | HNSW (vector_cosine_ops, 384-dim) | VectorIndex | ANN cosine similarity search |
| `idx_plan_embeddings_tsv` | `plan_embeddings` | GIN (tsvector) | VectorIndex | BM25 keyword search via `@@` |
| `idx_plan_embeddings_intent` | `plan_embeddings` | B-tree | VectorIndex | Fast intent_type filtering |

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
│   ├── PostgreSQL: plan_embeddings (with pgvector extension)
│   ├── pgvector: idx_plan_embeddings_hnsw (HNSW, 384-dim)
│   ├── GIN: idx_plan_embeddings_tsv (tsvector)
│   └── B-tree: idx_plan_embeddings_intent (intent_type)
├── Component Dependencies
│   └── (none - foundation component)
└── External Dependencies
    └── ONNX Runtime (local CPU inference, all-MiniLM-L6-v2)
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
ContextRAG (context assembler — structured queries + optional hybrid search)
├── Database Dependencies
│   └── (none - queries via component dependencies)
├── Component Dependencies
│   ├── → ProfileStore (Tier 2: stable prefs)
│   ├── → History (Tier 3: recent history)
│   ├── → PlanLibrary (Tier 3: successful plans)
│   └── → VectorIndex (optional: hybrid search for similar plans, graceful degradation)
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
│   ├── → PluginRegistry (tool catalog)
│   ├── → Signer (plan signing after generation)
│   ├── → PlanLibrary (fallback template retrieval)
│   └── → PolicyEngine (policy_version snapshot, policy_ref assignment)
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
│   ├── → PlanLibrary (persist outcomes + final_graph + plan_revisions)
│   ├── → History (persist facts)
│   └── → VectorIndex (store plan embeddings for hybrid search)
└── External Dependencies
    └── (none)
```

#### PolicyEngine
```
PolicyEngine
├── Database Dependencies
│   ├── PostgreSQL: policies, policy_attestations
│   └── Redis: policy_cache:{policy_id}:{version}
├── Component Dependencies
│   ├── → PluginRegistry (validate tool availability for spawned steps)
│   └── → Audit (log policy decisions)
└── External Dependencies
    └── (none)
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

#### PreviewOrchestrator
```
PreviewOrchestrator
├── Database Dependencies
│   └── (none - executes read-only steps)
├── Component Dependencies
│   ├── → Signer (signature verification)
│   └── → PluginRegistry (MCP server resolution for preview steps)
└── External Dependencies
    └── MCP (read-only tool invocations)
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
ExecuteOrchestrator (absorbs WorkflowBuilder responsibilities in v2.0)
├── Database Dependencies
│   ├── Redis: idempotency:{plan_id}:{step}:{hash}
│   ├── Redis: lock:{resource}.{entity}.{op}
│   └── Redis: reasoning_context:{plan_id}:{step}
├── Component Dependencies
│   ├── → Signer (signature verification)
│   ├── → ApprovalGate (token validation + multi-gate HITL)
│   ├── → PluginRegistry (MCP server resolution, credential vault IDs)
│   ├── → PolicyEngine (runtime policy evaluation for spawned steps)
│   └── → PlanWriter (outcome persistence)
└── External Dependencies
    ├── MCP (API step tool invocations)
    └── Anthropic API (LLM reasoning steps, via LLMAdapter protocol)

NOTE: Execute flows contain HITL approval gates (gate-A, gate-B, etc.)
      Each gate_id maps to a Redis-backed async approval gate that pauses
      execution until ApprovalGate issues a continuation token. Multi-gate
      flows require sequential approvals (e.g., shopping: gate-A for cart,
      gate-B for purchase).
```

#### ExecutionMonitor
```
ExecutionMonitor
├── Database Dependencies
│   └── PostgreSQL (execution_tracker table)
├── Component Dependencies
│   └── → PlanWriter (outcome persistence)
└── External Dependencies
    └── (none - monitors internal asyncio tasks)

Purpose: Background polling service (runs every 30s) for infrastructure-level failures:
- Detects stuck execution tasks (no progress for 5+ minutes)
- Enforces time budgets (cancel after 60 minutes)
- Notifies users of terminal failures
- Note: Step-level failures are handled inline by LLM reasoning steps
  (PolicyEngine-bounded recovery). ExecutionMonitor only handles
  infrastructure failures (hung tasks, server crashes, network partitions).
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
│  ┌───────────────────┐                                          │
│  │Preview Orchestrator│ (MCP read-only invocations)             │
│  └────────┬──────────┘                                          │
│           │ Preview Wrapper                                     │
│           ▼                                                     │
│  ┌──────────────┐                                               │
│  │ApprovalGate  │  (Initial approval: gate-A)                   │
│  └──────┬───────┘                                               │
│         │ Approval Token (gate-A)                               │
│         ▼                                                       │
│  ┌────────────────────┐                                          │
│  │Execute Orchestrator│  (MCP + Anthropic API)                  │
│  │                    │                                         │
│  │ ┌────────────────┐ │                                         │
│  │ │ Plan Steps     │ │                 │                      │
│  │ │ via MCP tools  │ │                 │                      │
│  │ │                │ │                 │                      │
│  │ │ Step 1,2,3     │ │                 │                      │
│  │ │    ↓           │ │                 │                      │
│  │ │ Gate(gate-B) ──┼─┼─────────┐       │                      │
│  │ │    ↓           │ │         │       │                      │
│  │ │ Step 4,5       │ │         │       │                      │
│  │ │    ↓           │ │         │       │                      │
│  │ │ Gate(gate-C) ──┼─┼─────┐   │       │                      │
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
│  PostgreSQL: plans, history, plan_embeddings                    │
└─────────────────────────────────────────────────────────────────┘
```

### Pure Agentic Execution Flow (Python + MCP)

When a plan contains mixed step types, the Python ExecuteOrchestrator dispatches each step directly:

```
┌─────────────────────────────────────────────────────────────────┐
│ Python ExecuteOrchestrator (Pure Agentic Execution)             │
│                                                                  │
│  ┌─────────────┐     ┌──────────────────┐     ┌──────────────┐ │
│  │ API Step    │────→│ LLM Reasoning    │────→│ PolicyEngine │ │
│  │ (Fetcher)   │     │ (Anthropic API)  │     │ Evaluation   │ │
│  │ type: "api" │     │ type:            │     │              │ │
│  │ via MCP     │     │ "llm_reasoning"  │     │ Evaluates    │ │
│  └─────────────┘     │                  │     │ spawned steps│ │
│                      │ Two-tier trust:  │     │              │ │
│                      │ Tier 1: sandbox  │     │ PolicyEngine │ │
│                      │ Tier 2: agent    │     └──────┬───────┘ │
│                      └──────────────────┘            │         │
│                                                      │         │
│                                            ┌─────────▼───────┐ │
│                                            │ MCP: Spawned    │ │
│                                            │ API Steps       │ │
│                                            │                 │ │
│                                            │ Fetcher/Analyzer│ │
│                                            │ via MCP tool    │ │
│                                            │ invocations     │ │
│                                            │                 │ │
│                                            └─────────┬───────┘ │
│                                                      │         │
│                                            ┌─────────▼───────┐ │
│                                            │ Python: Next    │ │
│                                            │ Plan Steps      │ │
│                                            └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Pure agentic execution**: All steps run within the Python ExecuteOrchestrator. API steps dispatch via MCP tool invocations. LLM reasoning steps call Anthropic API with two-tier trust model. Spawned API steps are dispatched via MCP directly -- no intermediate workflow generation needed.

**Data trust boundary** (HLD §v6.1): All external API responses are untrusted by default. Before a Tier 2 Reasoner can act on API output, it must pass through a Tier 1 sanitization step (no tools, strict output schema). The Planner inserts these steps at plan creation; the plan validator rejects plans that violate this rule. See Project_HLD.md "Data Trust Boundary" for the full classification table.

---

## 6. Module Groupings for Parallel Development

### Group 1: Memory Module (Foundation)
**Can build in parallel:**
- ProfileStore
- History
- PlanLibrary
- VectorIndex (hybrid BM25 + semantic search)

**Timeline:** Sprint 1 (2 weeks)
**Agents:** 4 parallel agents

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

### Group 3.5: Policy Infrastructure
**Can build in parallel with Group 3:**
- PolicyEngine (depends on PluginRegistry, Audit)

**Timeline:** Sprint 3 (2 weeks, parallel with Planning & Context)
**Agents:** 1 agent

---

### Group 4: Orchestration Foundation
**Sequential dependencies:**
1. PreviewOrchestrator (depends on Signer + PluginRegistry)
2. ApprovalGate (depends on Preview output)

**Timeline:** Sprint 4 (2 weeks)
**Agents:** Sequential with handoffs

---

### Group 5: Execution & Persistence
**Parallel start, converge at end:**
- ExecuteOrchestrator (depends on all Group 4)
- ExecutionMonitor (depends on execution_tracker table)
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
   │ Validates token-A → Resolves plan DAG             │
   │ Execution pauses at Redis-backed gates for gate-B, gate-C │
   └──────────────────────────────────────────────────┘
                         ↓
3. Workflow Execution (Partial)
   ┌──────────────────────────────────────────────────┐
   │ Execute steps 1, 2, 3 (e.g., search products)    │
   │ Reach Redis gate(gate-B) → Pause execution        │
   │ Generate intermediate preview for gate-B         │
   └──────────────────────────────────────────────────┘
                         ↓
4. Intermediate Approval (gate-B)
   ┌──────────────────────────────────────────────────┐
   │ Present results to user (e.g., shopping cart)    │
   │ ApprovalGate(gate-B) → User approves → token-B   │
   │ Resume execution with token-B                    │
   └──────────────────────────────────────────────────┘
                         ↓
5. Continue Execution
   ┌──────────────────────────────────────────────────┐
   │ Execute steps 4, 5 (e.g., calculate total)       │
   │ Reach Redis gate(gate-C) → Pause again            │
   │ Generate final preview for gate-C                │
   └──────────────────────────────────────────────────┘
                         ↓
6. Final Approval (gate-C)
   ┌──────────────────────────────────────────────────┐
   │ Present final state (e.g., purchase total)       │
   │ ApprovalGate(gate-C) → User approves → token-C   │
   │ Resume execution with token-C                    │
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

### ExecuteOrchestrator Gate Handling

```python
# ExecuteOrchestrator handles gates natively (no n8n Wait nodes)
class GateHandler:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def pause_at_gate(self, plan_id: str, gate_id: str, preview_data: dict):
        """Pause execution and wait for approval."""
        gate_key = f"gate:{plan_id}:{gate_id}"
        await self.redis.hset(gate_key, mapping={
            "status": "pending",
            "preview_data": json.dumps(preview_data),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # Notify user that approval is needed
        await self._notify_gate_pending(plan_id, gate_id, preview_data)

    async def wait_for_approval(self, plan_id: str, gate_id: str, timeout_s: int = 900):
        """Block until gate is approved or timeout."""
        gate_key = f"gate:{plan_id}:{gate_id}"
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            status = await self.redis.hget(gate_key, "status")
            if status == "approved":
                token = await self.redis.hget(gate_key, "token")
                return token
            await asyncio.sleep(1)
        raise GateTimeoutError(f"Gate {gate_id} timed out after {timeout_s}s")

    async def approve_gate(self, plan_id: str, gate_id: str, token: str):
        """Approve a pending gate (called by ApprovalGate API)."""
        gate_key = f"gate:{plan_id}:{gate_id}"
        await self.redis.hset(gate_key, mapping={
            "status": "approved",
            "token": token,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        })
```

### ExecuteOrchestrator Gate Responsibilities

1. **Parse gate_id from plan steps**
2. **Pause at Redis-backed gates** when steps with gate_id are reached
3. **Notify user** at each gate for review (webhook/push notification)
4. **Wait for approval token** before continuing execution
5. **Validate token on resume** before proceeding to next steps

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
2. **ExecuteOrchestrator handles gate logic inline** - Converts gate_id to Redis-backed gates
3. **ApprovalGate caches preview state** - Token includes user selections from preview
4. **Redis manages gate state** - Pending gates persist in Redis with TTL
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

-- VectorIndex (hybrid search: BM25 + semantic + RRF)
CREATE TABLE plan_embeddings (
    plan_id VARCHAR(128) PRIMARY KEY REFERENCES plans(plan_id),
    embedding vector(384) NOT NULL,       -- all-MiniLM-L6-v2 via ONNX Runtime
    intent_type VARCHAR(64) NOT NULL,     -- denormalized from plans for filter perf
    search_text TEXT NOT NULL,            -- "{intent} | {actions} | {constraints} | {entities}"
    tsv tsvector NOT NULL,               -- auto-generated from search_text via trigger
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_plan_embeddings_hnsw ON plan_embeddings
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_plan_embeddings_tsv ON plan_embeddings USING gin (tsv);
CREATE INDEX idx_plan_embeddings_intent ON plan_embeddings (intent_type);
```

### Phase 1.5: PluginRegistry & User Integrations Tables
```sql
-- PluginRegistry (MCP connector model)
CREATE TABLE tools (tool_id VARCHAR(128) PRIMARY KEY, mcp_server VARCHAR(128) NOT NULL, transport VARCHAR(32) NOT NULL DEFAULT 'stdio', ...);
CREATE TABLE operations (id UUID PRIMARY KEY, tool_id VARCHAR(128) REFERENCES tools, mcp_tool VARCHAR(255) NOT NULL, ...);
CREATE TABLE registry_versions (version INTEGER PRIMARY KEY, ...);

-- User Integrations (shared infrastructure)
CREATE TABLE user_integrations (id UUID PRIMARY KEY, user_id UUID REFERENCES users, tool_id VARCHAR(128), ...);

-- Credential Vault
CREATE TABLE credential_vault (
    credential_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    tool_id VARCHAR(128) NOT NULL REFERENCES tools(tool_id) ON DELETE CASCADE,
    encrypted_value BYTEA NOT NULL,
    iv BYTEA NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_credential_vault_user_tool ON credential_vault (user_id, tool_id);
CREATE INDEX idx_credential_vault_user_id ON credential_vault (user_id);
```

### Phase 2: Planning Tables
```sql
-- PlanLibrary
CREATE TABLE plans (...);
CREATE TABLE plan_signatures (...);
CREATE TABLE plan_outcomes (...);
```

### Phase 2.5: Policy & Hybrid Execution Tables
```sql
-- PolicyEngine
CREATE TABLE policies (
    policy_id VARCHAR(128) PRIMARY KEY,
    name VARCHAR(256) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    scope VARCHAR(32) NOT NULL CHECK (scope IN ('step', 'role', 'system')),
    allowed_tools JSONB NOT NULL DEFAULT '["*"]',
    allowed_roles JSONB NOT NULL DEFAULT '[]',
    max_spawned_steps INTEGER NOT NULL DEFAULT 3,
    require_approval BOOLEAN NOT NULL DEFAULT false,
    data_access JSONB NOT NULL DEFAULT '["tier1"]',
    forbidden_actions JSONB NOT NULL DEFAULT '[]',
    token_budget INTEGER NOT NULL DEFAULT 8192,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE policy_attestations (
    attestation_id VARCHAR(26) PRIMARY KEY,
    plan_id VARCHAR(26) NOT NULL REFERENCES plans(plan_id),
    plan_revision INTEGER NOT NULL,
    spawned_by_step INTEGER NOT NULL,
    new_steps JSONB NOT NULL,
    policy_id VARCHAR(128) NOT NULL REFERENCES policies(policy_id),
    policy_version INTEGER NOT NULL,
    decision JSONB NOT NULL,
    attested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- PlanLibrary (additive)
CREATE TABLE plan_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id VARCHAR(26) NOT NULL REFERENCES plans(plan_id),
    revision INTEGER NOT NULL,
    spawned_by_step INTEGER NOT NULL,
    new_steps JSONB NOT NULL,
    policy_decision JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (plan_id, revision)
);

-- Additive column on existing table
ALTER TABLE plan_outcomes ADD COLUMN IF NOT EXISTS final_graph_json JSONB;
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

4. **PostgreSQL for Persistent State** (with pgvector + tsvector)
   - Relational data (profiles, plans, history)
   - Hybrid search: BM25 keyword (tsvector/tsquery) + semantic (pgvector HNSW) + RRF score fusion
   - ONNX Runtime for local embeddings (all-MiniLM-L6-v2, 384-dim, ~10ms inference)
   - Single database reduces operational complexity

5. **Component Ownership**
   - Each table owned by exactly one component
   - Cross-component queries go through well-defined interfaces

6. **Preview State Caching**
   - ApprovalGate stores preview results with approval tokens
   - ExecuteOrchestrator skips preview-only steps
   - User selections preserved, no re-execution of expensive operations

7. **Pure Agentic Execution**
   - All steps execute via Python ExecuteOrchestrator with MCP tool invocations (no n8n)
   - Two-tier LLM execution: sandboxed Tier 1 (untrusted data) + capable Tier 2 (agent reasoning)
   - AES-256-GCM encrypted credential vault in PostgreSQL (LLM never sees values)
   - WorkflowBuilder absorbed into ExecuteOrchestrator (17→16 components)
   - Spawned API steps dispatched via MCP tool invocations by ExecuteOrchestrator

8. **Deterministic Graph, Adaptive Execution** (HLD v6.1)
   - Plan graph is a fixed DAG — same inputs always produce the same topology
   - Adaptation happens INSIDE Reasoner steps (observe outputs, spawn steps within PolicyEngine bounds)
   - All external API outputs are untrusted by default — must pass through Tier 1 sanitization before Tier 2 Reasoners
   - Plan validator enforces: Tier 2 Reasoner's `context_from` cannot reference API steps without intervening Tier 1 step

---

## Next Steps

1. **Implement Memory Module** (ProfileStore, History, PlanLibrary, VectorIndex)
2. **Set up database schemas** and migrations
3. **Build Security & Config** (Signer, PluginRegistry, Audit)
4. **Implement Planning Layer** (Intake → ContextRAG → Planner)
5. **Build Orchestration** (Preview → Approval → Execute)
6. **Integrate end-to-end** with use case tests

This modular structure enables **parallel development** while maintaining clear separation of concerns and ownership boundaries.

---

**Document Version**: MODULAR_ARCHITECTURE v2.1
**Last Updated**: 2026-03-31
**Changes from v2.0**: **HLD v6.1 alignment.** (1) Updated conformance to HLD v6.1. (2) Added data trust boundary note to Pure Agentic Execution Flow (§5). (3) Added key decision #8: "Deterministic Graph, Adaptive Execution" with trust boundary rule.
**Changes from v1.5**: **Pure Agentic Execution + MCP.** (1) Removed WorkflowBuilder component -- absorbed into ExecuteOrchestrator. (2) Replaced n8n with MCP tool invocations throughout. (3) Updated ExecuteOrchestrator dependencies (added PluginRegistry, removed WorkflowBuilder, n8n->MCP). (4) Updated PreviewOrchestrator (removed WorkflowBuilder dep, added PluginRegistry). (5) Updated ExecutionMonitor (removed n8n dependency). (6) Added credential_vault table to schema. (7) Updated PluginRegistry tables (n8n_credential_type->mcp_server, n8n_node->mcp_tool). (8) Replaced hybrid execution sub-flow with pure agentic execution flow. (9) Replaced n8n Wait nodes with Redis-backed async approval gates in S7. (10) Added credential_vault DDL to S9. (11) Conforms to GLOBAL_SPEC v3.0 + HLD v6.0.
**Changes from v1.4**: **Hybrid Execution Split + VectorIndex.** (1) WorkflowBuilder: removed PolicyEngine dependency and custom n8n nodes, scoped to API nodes only (S1, S4). (2) ExecuteOrchestrator: added Anthropic API as external dependency for LLM reasoning (S1, S4). (3) Updated hybrid execution sub-flow diagram: Python orchestrator + n8n for API steps (S5). (4) Simplified Group 3.5: removed custom n8n node line, reduced agents from 2 to 1 (S6). (5) Updated Group 4: WorkflowBuilder depends on PluginRegistry only (S6). (6) Updated key decision #7: spawned steps fed to n8n by Python ExecuteOrchestrator (S10). (7) Conforms to GLOBAL_SPEC v2.4 + HLD v5.1.
**Changes from v1.3**: Hybrid Execution Model — (1) Added PolicyEngine to Domain/Service Layer (§1) with DB: PostgreSQL policies/policy_attestations, Redis policy_cache, Deps: PluginRegistry/Audit. (2) Updated WorkflowBuilder with custom n8n nodes (LLM Reasoning, Policy Check) and PolicyEngine dependency. (3) Updated Planner and ExecuteOrchestrator to add PolicyEngine dependency. (4) Added policies, policy_attestations, plan_revisions tables and policy_cache/reasoning_context Redis keys to §3. (5) Added PolicyEngine entry to §4 dependency matrix. (6) Added hybrid execution sub-flow diagram (§5). (7) Added Group 3.5: Policy & Adaptive Infrastructure to §6. (8) Added Phase 2.5 policy DDL to §9. (9) Added Hybrid Execution Model key decision to §10.
