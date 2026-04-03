# GLOBAL SPEC — Operating Contract (v3.0)

**Status:** Active
**Applies to:** All components in this repository
**Deployment model:** Self-hosted, single-tenant, multi-user (no `tenant_id` — one instance, many users, each with their own integration accounts)
**Default timezone:** America/Chicago

---

## 0) Purpose
Define the universal rules that govern this system:

**For Use Cases** (end-to-end user flows):
- The **safety model** (Preview vs Execute vs Durable) for user-facing operations
- Canonical **I/O contracts** (Intent, Evidence, Plan, Preview, Execute, Approvals, PolicyEngine)
- **Pure agentic execution with full auditability** — deterministic planning with policy-bounded LLM reasoning at runtime

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
- Runs **MCP tool invocations** in read-only mode.
- Returns a **Preview wrapper** with normalized payload + optional evidence.

### Execute (via MCP)
- Allowed **only after explicit human approval** with a valid approval token.
- Calls real providers under **least-privilege** credentials.
- **Idempotency required**: All side-effecting steps (Booker role) use scoped keys (`user:integration:plan:step:op:hash`) to prevent duplicate operations across users.
- **Retry safety**: Step-level retries for transient failures (503, timeout) with idempotency preventing duplicates. For hybrid plans, LLM reasoning steps handle step-level failure recovery within PolicyEngine bounds (§1 Adaptive). Failed plans that exhaust policy-bounded retries are terminal — user must start a new plan.
- Returns an **Execute wrapper**.

**Note**: MVP uses Python/FastAPI ExecuteOrchestrator for all step execution. API steps dispatch via MCP tool invocations. LLM reasoning steps call Anthropic API directly. ExecutionMonitor detects stuck executions (hung asyncio tasks, time budget violations). Step-level failures are recovered inline by LLM reasoning — no workflow-level replay needed.

### Adaptive (LLM Reasoning)
- LLM reasoning steps execute during the Execute phase in the **Python ExecuteOrchestrator** (via Anthropic API).
- Each reasoning step declares a `policy_ref` linking to a **PolicyEngine** rule that governs its behavior.
- **Non-critical actions** (reads, analysis, ranking) auto-execute if the PolicyEngine allows them.
- **Critical actions** (writes, deletes, payments via Booker role) always require HITL via ApprovalGate — the PolicyEngine injects a `gate_id` automatically for spawned Booker steps.
- LLM reasoning steps may **spawn new plan steps** at runtime (e.g., "I need more data, let me add a Fetch step"), subject to PolicyEngine approval and spawning constraints (§2.3.2).
- **Credential isolation**: Runtime LLM reasoning steps have **ZERO access** to credential values — same isolation boundary as the Planner LLM.
- PolicyEngine evaluation is synchronous and fast (<5ms target) — deny-by-default for unmatched rules.

---

## 2) Canonical Contracts

### 2.0 Deterministic Planning → Adaptive Execution
The **initial plan** is a pure function of a frozen tuple:
- Intent vN (finalized)
- Evidence vK (typed, small)
- Registry vR (MCP server catalog snapshot)
- Policy vC (GLOBAL_SPEC version)
- PolicyVersion vP (PolicyEngine rules version snapshot)

Same tuple ⇒ same canonical plan bytes ⇒ same plan_hash.

**Deterministic graph, adaptive execution**: The Planner produces a fixed DAG of steps (same inputs → same graph). The **initial plan (revision 0)** is immutable, identified by a SHA-256 plan_hash for data integrity. At runtime, Reasoner steps observe previous step outputs via `context_from`, make judgments, and may spawn new steps within PolicyEngine bounds — each spawn event increments `plan_revision` and creates a new plan revision. The original graph is never mutated; spawned steps extend it. Runtime revisions receive **PolicyEngine attestations** (§2.4.1) as audit records. See Project_HLD §2a–§2c for concrete examples.

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

### 2.3 Plan (hybrid; supports HITL gates and LLM reasoning)
~~~json
{
  "plan_id": "<ulid>",
  "plan_revision": 0,
  "intent": {},
  "graph": [
    {
      "step": 1,
      "mode": "interactive|durable",
      "type": "api|llm_reasoning|policy_check",
      "trust_level": null,
      "role": "Fetcher|Analyzer|Watcher|Resolver|Booker|Notifier|Reasoner",
      "uses": "<tool_id>",
      "call": "<operation>",
      "args": {},
      "after": [/* ordering deps, optional */],
      "context_from": [/* steps whose output is passed as context, optional */],
      "gate_id": "gate-A",
      "dry_run": true,
      "can_spawn": false,
      "max_spawned_steps": 3,
      "spawned_by": null,
      "policy_ref": null,
      "reasoning_config": null,
      "status": "pending|running|completed|failed|skipped",
      "result": null,
      "error": null
    }
  ],
  "constraints": { "scopes": ["calendar.write"], "ttl_s": 900, "policy_version": 1 },
  "plugins": ["<plugin_id>"],
  "meta": { "created_at": "<iso>", "author": "planner" }
}
~~~

**New PlanStep fields** (all optional with backward-compatible defaults):

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `type` | `"api" \| "llm_reasoning" \| "policy_check"` | `"api"` | How the step executes |
| `trust_level` | `"untrusted_input" \| "trusted" \| null` | `null` | Trust tier for LLM steps (Tier 1 sandboxed vs Tier 2 agent reasoning) |
| `context_from` | `list[int]` | `[]` | Steps whose output is passed as context (distinct from `after` which is ordering) |
| `can_spawn` | `bool` | `false` | Whether this step can create new steps at runtime |
| `max_spawned_steps` | `int` | `3` (absolute max 10) | Max new steps this step can create |
| `spawned_by` | `int \| null` | `null` | Which step spawned this (null for original plan steps) |
| `policy_ref` | `str \| null` | `null` | PolicyEngine rule governing this step |
| `reasoning_config` | `ReasoningConfig \| null` | `null` | LLM configuration for reasoning steps (§2.3.1) |
| `status` | `str` | `"pending"` | Step execution status |
| `result` | `object \| null` | `null` | Step execution result |
| `error` | `object \| null` | `null` | Step error details if failed |

**New Plan-level fields**:
- `plan_revision: int` — Starts at 0, increments on each spawn event during execution
- `constraints.policy_version: int` — PolicyEngine rules version snapshot at plan creation time

**Backward compatibility**: All new fields have defaults. Existing plans with all `type=api` steps skip PolicyEngine entirely. `plan_revision=0` for all existing plans.

#### 2.3.1 ReasoningConfig

Configuration for LLM reasoning steps (`type: "llm_reasoning"`):

~~~json
{
  "model": "claude-sonnet-4-5-20250929",
  "temperature": 0.3,
  "max_tokens": 2048,
  "system_prompt_ref": "<prompt_template_id>",
  "output_schema_ref": "<json_schema_id|null>"
}
~~~

| Field | Type | Default | Constraints |
|-------|------|---------|-------------|
| `model` | `str` | `"claude-sonnet-4-5-20250929"` | Must be an allowed model |
| `temperature` | `float` | `0.3` | 0.0–1.0 |
| `max_tokens` | `int` | `2048` | 256–8192 |
| `system_prompt_ref` | `str` | (required) | Reference to prompt template |
| `output_schema_ref` | `str \| null` | `null` | JSON schema for structured output |

#### 2.3.2 Spawned Step Rules

When a reasoning step (`can_spawn=true`) generates new steps at runtime:

1. **Per-step limit**: `max_spawned_steps` per reasoning step (default 3, absolute max 10)
2. **Plan-level limit**: Total steps (original + spawned) cannot exceed 100
3. **No recursive spawning**: Spawned steps CANNOT have `can_spawn=true`
4. **Inherited plugins**: Spawned steps can only use tools in the plan's `plugins` array (no new tool access)
5. **Booker HITL**: Spawned steps with `role=Booker` ALWAYS get a `gate_id` injected (non-overridable)
6. **Deny-by-default**: If no PolicyEngine rule matches the spawned step, the action is denied
7. **Audit trail**: Each spawn event increments `plan_revision` and creates a PolicyAttestation (§2.4.1)

### 2.4 Plan Hash (Data Integrity)

The plan_hash is a SHA-256 checksum of the canonical plan bytes (sorted keys, deterministic serialization). It serves as a data integrity check to detect plan tampering between planning and execution.

~~~json
{
  "plan_hash": "<sha256>"
}
~~~

#### 2.4.1 Policy Attestation

When LLM reasoning steps spawn new steps at runtime, the PolicyEngine issues an attestation as an audit record for the runtime modification.

~~~json
{
  "attestation_id": "<ulid>",
  "plan_id": "<ulid>",
  "plan_revision": 1,
  "spawned_by_step": 3,
  "new_steps": [
    { "step": 8, "role": "Fetcher", "uses": "flights.api", "call": "search", "spawned_by": 3 }
  ],
  "policy_id": "policy-default-reasoning",
  "policy_version": 1,
  "decision": { "allowed": true, "requires_approval": false, "reason": "Fetcher role, read-only", "violations": [] },
  "attested_at": "<iso>"
}
~~~

**Audit chain**: `plan_hash + policy_attestations[] = full execution provenance`.

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

**Important (MVP)**: Roles are **logical plan-step categories**, NOT separate runtime services. API steps execute via MCP tool invocations; LLM reasoning steps execute in Python. Roles determine policies (idempotency, retry, compensation requirements).

**Seven roles for responsibility classification:**

- **Fetcher** — One-time read operations (preview fetches, API calls, data retrieval). No idempotency needed (read-only).
- **Analyzer** — Data processing, comparison, research, ranking, synthesis. No idempotency needed (pure computation).
- **Watcher** — Long-running monitoring (polls, subscriptions, continuous observation). Aggressive retry policy.
- **Resolver** — Disambiguation, user clarification, conflict resolution. Requires HITL (human-in-the-loop).
- **Booker** — Writes with **idempotency required** and compensation. Resource locking enforced.
- **Notifier** — Updates, alerts, summaries, progress reports. Best-effort delivery.
- **Reasoner** — LLM-based adaptive decisions (ranking options, generating summaries, analyzing data with judgment). Bounded by PolicyEngine. May spawn new steps (`can_spawn=true`). Not side-effecting itself — spawned Booker steps handle writes. Policy metadata: `{ policy_ref: required, can_spawn: true|false, max_spawned_steps: 3, credential_access: false }`.

**Execution model:**
- Roles determine **policy metadata** (idempotency requirement, retry strategy, compensation needed)
- API steps execute via **MCP tool invocations** (ExecuteOrchestrator dispatches directly); LLM reasoning steps execute in **Python** (ExecuteOrchestrator)
- Parallelism: `asyncio.gather()` for steps with no dependencies (`after: []`)
- Dependencies: Steps with `after: [1, 2]` wait for completion before executing
- Resource locks: Scoped by `user_id:integration_account_id:resource:entity` (prevent cross-user conflicts within the single-tenant deployment)

### 2.9 PolicyEngine Contract

The PolicyEngine is the safety boundary for all runtime LLM decisions. It evaluates whether a reasoning step's proposed action (including spawning new steps) is allowed.

#### PolicyRule
~~~json
{
  "policy_id": "<string>",
  "name": "<human-readable>",
  "version": 1,
  "scope": "step|role|system",
  "allowed_tools": ["<tool_id>"],
  "allowed_roles": ["Fetcher", "Analyzer", "Reasoner"],
  "max_spawned_steps": 3,
  "require_approval": false,
  "data_access": ["tier1", "tier2"],
  "forbidden_actions": ["delete", "payment"],
  "token_budget": 8192
}
~~~

#### PolicyDecision
~~~json
{
  "allowed": true,
  "requires_approval": false,
  "reason": "<explanation>",
  "violations": []
}
~~~

#### Policy Hierarchy
Policies are evaluated in order of specificity:
1. **Step-level** (`policy_ref` on individual step) — most specific
2. **Role-level** (default policy for the step's role) — fallback
3. **System-level** (global default policy) — last resort

#### Evaluation Rules
- **Target latency**: <5ms per evaluation (Redis-cached policies)
- **Deny-by-default**: If no policy matches, the action is denied
- **Booker always needs HITL**: Spawned steps with `role=Booker` always get `require_approval=true` regardless of policy (non-overridable)
- **No recursive spawning**: PolicyEngine rejects any spawned step with `can_spawn=true`

#### Default Policy for LLM Reasoning
~~~json
{
  "policy_id": "policy-default-reasoning",
  "name": "Default LLM Reasoning Policy",
  "version": 1,
  "scope": "role",
  "allowed_tools": ["*"],
  "allowed_roles": ["Fetcher", "Analyzer", "Reasoner", "Notifier"],
  "max_spawned_steps": 3,
  "require_approval": false,
  "data_access": ["tier1", "tier2", "tier3"],
  "forbidden_actions": ["delete", "payment", "create_recurring"],
  "token_budget": 8192
}
~~~

**Note**: The default policy forbids Booker role for spawned steps, limits to 3 spawned steps, and denies writes by default. Explicit step-level policies can override these for specific use cases (but Booker HITL is non-overridable).

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
- **Shared contracts** in `shared/schemas/` (Intent, Evidence, Plan, Wrappers)
- **Use case plans** in `usecases/<UseCase>/plans/` must validate against the Plan schema in this file  
- **Tests must validate** against schemas; **no schema drift**  

---

## 5) Conformance
- Each `SPEC.md` must declare conformance to `GLOBAL_SPEC.md v3` and list deltas.  
- Handlers are thin: validate Intent → call service → return wrapped Preview/Execute.  
- `preview()` must never mutate; `execute()` only after valid approval token.

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
- **Plan hash verification** required at Preview/Execute (data integrity check).
- **Approval tokens** required for writes (per gate).
- **Idempotency** enforced via datastore.
- **Compensation** supported when declared in Registry.
- **Privacy:** derived facts only; TTL/forget/export enforced.
- **Observability:** plan_id correlation, latency/error metrics.
- **PolicyEngine governance:**
  - All runtime LLM decisions (reasoning steps, step spawning) are bounded by PolicyEngine rules
  - Deny-by-default: unmatched actions are rejected
  - Booker-role spawned steps always require HITL (non-overridable)
  - Policy attestations provide audit trail for runtime modifications (§2.4.1)
- **Credential isolation:**
  - Storage: Encrypted credential vault (AES-256-GCM in PostgreSQL, master key from env)
  - LLM boundary: Planner/LLM has **ZERO access** to credential values
  - Runtime LLM boundary: Reasoning steps have **ZERO access** to credential values (same isolation as Planner)
  - Plan format: Plans reference credential vault IDs, resolved by ExecuteOrchestrator at execution time
  - Deployment: Anthropic Claude API (via LLMAdapter protocol). LLM never sees credential values regardless of provider. System can run inside NemoClaw for infrastructure-level security.

### 8.1 Credential Vault & LLM Isolation

**Architecture**: All credentials (OAuth tokens, API keys) are stored in an encrypted credential vault in PostgreSQL using AES-256-GCM encryption. A master key from environment variables encrypts/decrypts values. The LLM never sees plaintext credentials.

**Credential lifecycle**:
1. User registers integration → credential encrypted with AES-256-GCM → stored in `credential_vault` table
2. Planner generates plan with credential vault IDs (never values)
3. ExecuteOrchestrator decrypts credential at step execution time → passes to MCP tool invocation
4. Credential value exists in memory only during the MCP call → zeroed after

**Key rotation**: `key_version` column supports rolling key rotation without re-encrypting all credentials simultaneously.

### 8.2 Two-Tier LLM Execution (Prompt Injection Defense)

LLM reasoning steps operate in one of two trust tiers, declared via `trust_level` on PlanStep:

| Tier | trust_level | Capabilities | Use Case |
|------|-------------|-------------|----------|
| **Tier 1** (Sandboxed) | `"untrusted_input"` | No tools, strict output schema, input sanitization | Processing user-provided data, parsing external content |
| **Tier 2** (Agent) | `"trusted"` | MCP tool access, may spawn steps, PolicyEngine-bounded | Agent reasoning, decision-making with clean data |

**Default-Untrusted Rule**: ALL external API responses (MCP tool invocation results) are classified as untrusted. A Tier 2 Reasoner step MUST NOT have `context_from` referencing an API step without an intervening Tier 1 sanitization step. The plan validator enforces this at plan creation time. Pure API plans (API step → API step via template args) are exempt — no LLM processes the data. Step failure error objects are system-generated metadata and can go directly to Tier 2 Reasoners.

**5-layer defense stack**:
1. **Input sanitization**: Strip control characters, enforce size limits before LLM call
2. **Trust tier enforcement**: Tier 1 LLM calls have tools disabled, structured output required
3. **Output validation**: LLM output validated against declared schema before use
4. **PolicyEngine governance**: All spawned steps evaluated by PolicyEngine (deny-by-default)
5. **Credential isolation**: Neither tier can access plaintext credentials

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
- **Visa watcher:** APScheduler polling task → detects slot availability → signals Redis-backed approval gate → MCP tool invocation executes booking → notify

---

**Document Version**: GLOBAL_SPEC v3.0
**Last Updated**: 2026-03-31
**v3.0 addendum (v6.1 HLD alignment)**: Added default-untrusted rule to §8.2 (all external API responses are untrusted; Tier 2 Reasoner's context_from must not reference API steps without intervening Tier 1 sanitization). Updated §2.0 terminology: "Hybrid Execution" → "Adaptive Execution" to align with HLD v6.1 "Deterministic Planning with Adaptive Execution".
**Changes from v2.4**: **Pure Agentic Execution + MCP + Security Model.** (1) Dropped n8n — all execution via Python/FastAPI ExecuteOrchestrator with MCP tool invocations. (2) Replaced n8n Secrets Vault with AES-256-GCM encrypted credential vault in PostgreSQL. (3) Added §8.1 Credential Vault & LLM Isolation. (4) Added §8.2 Two-Tier LLM Execution with trust_level field and 5-layer prompt injection defense. (5) Updated §2.3 Plan schema with trust_level field. (6) Updated §2.8 execution model: MCP tool invocations + asyncio.gather() parallelism. (7) WorkflowBuilder absorbed into ExecuteOrchestrator. (8) NemoClaw deployment compatibility for infrastructure-level security.
**Changes from v2.3**: **Hybrid Execution Split.** (1) Updated §1 Execute note: n8n for API steps, Python/FastAPI for LLM reasoning steps. (2) Updated §1 Adaptive: LLM reasoning executes in Python ExecuteOrchestrator (not custom n8n nodes). (3) Updated §2.8 execution model: API steps in n8n, LLM reasoning in Python. (4) Updated §8 credential isolation: reasoning steps in Python service have ZERO access to n8n Secrets Vault.
**Changes from v2.2**: Hybrid Execution Model — (1) Added PolicyEngine to canonical I/O contracts (§0). (2) Added Adaptive (LLM Reasoning) subsection to Safety Model (§1). (3) Updated §2.0 to Hybrid Planning — plan is deterministic, execution may vary within policy bounds. (4) Extended Plan schema (§2.3) with new PlanStep fields: type, context_from, can_spawn, max_spawned_steps, spawned_by, policy_ref, reasoning_config, status, result, error. Added plan_revision and constraints.policy_version. (5) Added §2.3.1 ReasoningConfig and §2.3.2 Spawned Step Rules. (6) Added §2.4.1 Policy Attestation — runtime modifications get attestation instead of re-signing. (7) Added Reasoner as 7th runtime agent role (§2.8). (8) Added §2.9 PolicyEngine Contract with PolicyRule, PolicyDecision, hierarchy, evaluation rules, and default policy. (9) Added PolicyEngine governance and runtime LLM credential isolation to §8.
**Changes from v2.1**: (1) Defined deployment model as self-hosted, single-tenant, multi-user. (2) Removed `tenant_id` from idempotency key scoping — system uses `user:integration:plan:step:op:hash`. (3) Added deployment model line to document header.
**Changes from v2.0**: MVP scope clarification - (1) Updated §1 Execute to use n8n for all workflows (removed Temporal/Durable distinction for MVP), added idempotency scoping by user/integration, added retry safety (node-level + workflow-level with ExecutionMonitor). (2) Updated §2.8 Runtime Agent Roles to clarify roles are logical categories (NOT separate services), all execution in n8n, roles determine policy metadata (idempotency, retry, compensation). (3) Updated resource lock scoping to include user_id and integration_account_id.
