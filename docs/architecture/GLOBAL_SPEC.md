# GLOBAL SPEC — Operating Contract (v2)

**Status:** Active  
**Applies to:** All components in this repository  
**Default timezone:** America/Chicago

---

## 0) Purpose
Define the universal rules that govern this system:

**For Use Cases** (end-to-end user flows):
- The **safety model** (Preview vs Execute vs Durable) for user-facing operations
- Canonical **I/O contracts** (Intent, Evidence, Plan, Signature, Preview, Execute, Approvals)
- **Determinism & auditability** across all planning/execution

**For Components** (internal building blocks):
- **Evidence Item format** for data sources (GLOBAL_SPEC §2.2)
- **Context tier** policy for privacy-aware data fetching (GLOBAL_SPEC §7)
- Baseline **non-functional requirements** (latency, availability, observability)
- **Schemas & validation** expectations for typed contracts

Each `SPEC.md` (component or use case) **inherits** these rules and may only deviate if explicitly stated (with rationale).

---

## 1) Safety Model (applies to user-facing plans)

**Important**: This safety model applies to **user-facing plans** (Intent → Plan → Preview → Execute), NOT to internal component operations. Internal components (ProfileStore, Intake, History) execute operations directly without Preview/Execute wrappers.

### Preview
- **Side-effect free**: stubs/mocks only, no writes or external mutations.
- Runs **n8n connectors** only in previewable/read-only mode.
- Returns a **Preview wrapper** with normalized payload + optional evidence.

### Execute (Short Jobs, via n8n)
- Allowed **only after explicit human approval** with a valid approval token and verified plan signature.
- Calls real providers under **least-privilege** credentials.
- Idempotency required (`plan_id:step:arg_hash`).
- Returns an **Execute wrapper**.

### Durable (Long Jobs, via Temporal)
- Handles long-running/stateful work (poll, retry, signals, compensation).
- Deterministic workflow core, Activities for I/O.
- Resilient to restarts; must support **ContinueAsNew** and compensation.  

---

## 2) Canonical Contracts

### 2.0 Deterministic Inputs (Planner)
The planner is a pure function of a frozen tuple:
- Intent vN (finalized)
- Evidence vK (typed, small)
- Registry vR (connector catalog snapshot)
- Policy vC (GLOBAL_SPEC version)

Same tuple ⇒ same canonical plan bytes ⇒ same hash/signature.

### 2.1 Intent (input)
~~~json
{
  "intent": "<action>",
  "entities": {},
  "constraints": {},
  "tz": "America/Chicago",
  "user_id": "<uuid>",
  "context_budget": 1
}
~~~

### 2.2 Evidence Item
~~~json
{
  "type": "preference|history|contact|plan|exemplar",
  "key": "meeting_duration_min",
  "value": 30,
  "confidence": 0.82,
  "source_ref": "kv:prefs/duration",
  "ttl_days": 365
}
~~~

### 2.3 Plan (deterministic; supports HITL gates)
~~~json
{
  "plan_id": "<ulid>",
  "intent": {},
  "graph": [
    {
      "step": 1,
      "mode": "interactive|durable",
      "role": "Fetcher|Analyzer|Watcher|Resolver|Booker|Notifier",
      "uses": "<tool_id>",
      "call": "<operation>",
      "args": {},
      "after": [/* deps, optional */],
      "gate_id": "gate-A",
      "dry_run": true
    }
  ],
  "constraints": { "scopes": ["calendar.write"], "ttl_s": 900 },
  "plugins": ["<plugin_id>"],
  "meta": { "created_at": "<iso>", "author": "planner" }
}
~~~

### 2.4 Plan Signature
~~~json
{
  "algo": "Ed25519",
  "signer": "planner@system",
  "ts": "<iso>",
  "nonce": "<ulid>",
  "signature": "<base64>",
  "pubkey_id": "k1"
}
~~~

### 2.5 Preview Wrapper
~~~json
{
  "normalized": {},
  "source": "preview",
  "can_execute": true,
  "evidence": []
}
~~~

### 2.6 Execute Wrapper
~~~json
{
  "provider": "<connector_id>",
  "result": { "id": "<external_id>", "link": "<optional>" },
  "status": "created|updated|skipped|error"
}
~~~

### 2.7 Approval Token
~~~json
{
  "token": "<jwt|ulid>",
  "plan_hash": "<sha256>",
  "user_id": "<uuid>",
  "exp": "<iso>",
  "scopes": ["shopping.write"]
}
~~~

### 2.8 Runtime Agent Roles

Runtime agents are **asynchronous execution instances** that execute plan steps. Each step spawns a new agent instance (n8n sub-workflow or Temporal activity). Multiple instances of the same role can run concurrently.

**Six roles for responsibility isolation:**

- **Fetcher** — One-time read operations (preview fetches, API calls, data retrieval)
- **Analyzer** — Data processing, comparison, research, ranking, synthesis
- **Watcher** — Long-running monitoring (polls, subscriptions, continuous observation)
- **Resolver** — Disambiguation, user clarification, conflict resolution
- **Booker** — Writes with idempotency and compensation
- **Notifier** — Updates, alerts, summaries, progress reports

**Execution model:**
- Roles are for **responsibility classification**, not concurrency primitives
- Parallelism comes from **orchestrator logic** (n8n branches, Temporal child workflows)
- Steps with `after: []` (no dependencies) execute **immediately in parallel**
- Steps with `after: [1, 2]` wait for dependencies, then execute
- Resource locks prevent conflicting writes (fine-grained: `resource.entity.write`)

---

## 3) Non-Functional Requirements
- **Preview latency:** p95 < 800 ms  
- **Short Execute latency:** p95 < 2 s  
- **ContextRAG:** p95 < 150 ms  
- **Plan Retrieval:** p95 < 200 ms  
- **Vector search:** < 100 ms  
- **Durable flows:** survive restarts; ContinueAsNew daily  
- **Availability:** 99.9% Intake/Preview, 99.5% Execute/Durable  
- **Observability:** structured logs, correlated by `plan_id`; no raw secrets/PII  

---

## 4) Schemas & Validation
- **Component-specific schemas** in `components/<Name>/schemas/`  
- **Shared contracts** in `shared/schemas/` (Intent, Evidence, Plan, Signature, Wrappers)  
- **Use case plans** in `usecases/<UseCase>/plans/` must validate against the Plan schema in this file  
- **Tests must validate** against schemas; **no schema drift**  

---

## 5) Conformance
- Each `SPEC.md` must declare conformance to `GLOBAL_SPEC.md v2` and list deltas.  
- Handlers are thin: validate Intent → call service → return wrapped Preview/Execute.  
- `preview()` must never mutate; `execute()` only after valid approval & signature.  

---

## 6) Versioning
- Breaking changes require version bump and ADR.  
- Components must indicate which version they conform to.  

---

## 7) Context Policy
- **Tier 1:** session only (current conversation, temporary context extracted from any source)
- **Tier 2:** stable prefs (user preferences, settings; includes sensitive data with encryption flags)
- **Tier 3:** recent history (past interactions with 30-day TTL)
- **Tier 4:** live signals (real-time data fetched on-demand from external APIs; not stored)
- ContextRAG enforces tier budgets and produces typed `evidence[]`.

**Notes:**
- Tier 2 sensitive data (passport numbers, health info) stored encrypted at rest with `sensitive: true` flag
- Tier 4 data is never persisted; fetched fresh during each planning cycle
- Privacy consent is cumulative (Tier 3 consent includes Tier 1+2)  

---

## 8) Safety & Governance
- **Signature verification** required at Preview/Execute.  
- **Approval tokens** required for writes (per gate).  
- **Idempotency** enforced via datastore.  
- **Compensation** supported when declared in Registry.  
- **Privacy:** derived facts only; TTL/forget/export enforced.  
- **Observability:** plan_id correlation, latency/error metrics.  

---

## 9) Repository Structure

### Component-First Architecture
Each `components/<Name>/` is a self-contained module with:
- **SPEC.md** — Requirements, user stories, acceptance criteria
- **LLD.md** — Low-level design, interfaces, dependencies
- **schemas/** — JSON schemas for component-specific contracts
- **tests/** — Contract tests, unit tests, integration tests
- **Code** — api/, service/, domain/, adapters/ subdirectories

### Optional Use-Case Specifications
Use-case packets in `usecases/<UseCase>/` (when needed):
- **SPEC.md** — End-to-end scenario requirements
- **LLD.md** — Flow orchestration across components
- **plans/** — Workflow definitions
- **tests/** — End-to-end acceptance tests
- **fixtures/** — Test data

**Global contracts** (Intent, Evidence, Plan schemas) live in this GLOBAL_SPEC file.

---

## 10) End-to-End Examples
- **Meeting flow:** Intent → ContextRAG → Plan → Preview → Gate A → Execute → Audit/PlanWriter  
- **Shopping flow:** multi-gate approval before cart/purchase  
- **Visa watcher:** Temporal watcher signals approval gate → execute booking → notify  
