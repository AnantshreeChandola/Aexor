# Personal Agent — High-Level Design (HLD) v6.1
_Preview-first • Human-approved • Pure agentic execution • Policy-bounded • MCP connectors_

**Purpose:** System architecture overview with clear component responsibilities and real-world examples.
**Audience:** Developers, architects, and stakeholders.

---

## Architecture Overview

```
User Request → Preview → Approval → Execute → Learn
     ↓           ↓          ↓          ↓        ↓
  [Intent]   [Show Me]  [Confirm]  [Do It]  [Remember]
```

### Deployment Model
**Self-hosted, single-tenant, multi-user.** One deployment instance serves multiple users, each with their own integration accounts. There is no multi-tenancy — `tenant_id` is not used anywhere in the system.

### Core Idea
1. **Never do anything without showing the user first** (Preview-first safety)
2. **Plans are deterministic, immutable graphs with adaptive execution points** — The Planner produces a fixed DAG of steps (same inputs → same graph). The **initial plan (revision 0)** is immutable, identified by a SHA-256 plan_hash for data integrity. At runtime, Reasoner steps can spawn new steps within PolicyEngine bounds — each spawn event increments `plan_revision` and is recorded as a PolicyAttestation. The original graph is never mutated; spawned steps extend it into a new revision. API steps execute exactly as specified. Only Reasoner steps introduce runtime variability.
3. **Pure agentic execution** — all steps execute via Python/FastAPI ExecuteOrchestrator:
   - **API steps**: Dispatched via MCP tool invocations (community-maintained connector ecosystem)
   - **LLM reasoning steps**: Anthropic API with two-tier trust model (sandboxed Tier 1 for untrusted data, capable Tier 2 for agent reasoning)
   - **Credential isolation**: AES-256-GCM encrypted vault in PostgreSQL; LLM never sees plaintext values
4. **PolicyEngine is the safety moat** — All runtime LLM decisions are bounded by explicit policy rules. Deny-by-default. Critical actions always require human approval.

### Key Innovation
**Preview State Caching**: User choices made during preview are reused during execution—no need to repeat steps.

**Example**: Shopping flow
- Preview: Search 10 sweaters → User picks one
- Execute: Only buy that sweater (skip the search)

### Execution Model: Deterministic Graph, Adaptive Execution

The system supports three execution patterns. The plan graph is ALWAYS fully specified upfront — all output-dependent decisions go through explicit Reasoner steps. There is no conditional branching in the graph itself.

#### A) Pure API Plans (no Reasoner steps)
- Every step is `type: "api"` — fully deterministic
- Step outputs flow via template resolution: `args: {"content": "{{step_1.result.event_id}}"}`
- No LLM processes the data at execution time → no prompt injection surface
- If a step fails after step-level retries → plan is terminal
- Example: "Book meeting with Alice" (§2a below)

#### B) Adaptive Plans with Reasoner (has `type: "llm_reasoning"` steps)
- The initial plan (revision 0) is immutable. Reasoner steps are explicit decision points in the DAG.
- Reasoner steps see previous outputs via `context_from` and make judgments
- Reasoners can spawn new steps (Fetcher, Analyzer) within PolicyEngine bounds — each spawn creates a new plan revision with a PolicyAttestation
- **All data flowing from API steps → Reasoner goes through Tier 1 sanitization first** (see Data Trust Boundary below)
- Example: "Find best flights to Tokyo" (§2b below)

#### C) Failure Recovery (step fails, Reasoner adapts)
- When API step N fails, the error object (system-generated, NOT the raw API response) routes to the nearest Reasoner with `can_spawn=true`
- Reasoner proposes a recovery action (retry with different params, alternative API, skip)
- Recovery action goes through PolicyEngine evaluation. **"Deny-by-default"** means nothing is implicitly allowed — but if an explicit policy rule matches the proposed action (tool in `plugins`, role in `allowed_recovery_roles`, within `max_recovery_actions` limit), PolicyEngine **approves automatically**. Write operations (Booker role) always require HITL approval via ApprovalGate, same as any other Booker step.
- If recovery is exhausted or PolicyEngine denies (no matching rule) → plan is terminal
- Example: Flight API returns 503 → Reasoner spawns Fetcher with alternative date range (§2c below)

### Data Trust Boundary

**RULE**: All data crossing the system boundary (API responses, external content) is UNTRUSTED. Before a Tier 2 Reasoner can act on it, it MUST pass through a Tier 1 sanitization step.

| Data Origin | Trust Level | Needs Tier 1 Sanitization? |
|-------------|-------------|---------------------------|
| System-generated (plan metadata, PolicyEngine decisions, error objects) | Trusted | No |
| Internal computation (Analyzer output, Tier 1 output) | Trusted | No |
| External API responses (ANY MCP tool invocation result) | Untrusted | Yes — before Tier 2 Reasoner |
| User raw input | Untrusted | Handled by Intake (separate component) |

**Data flow through the trust boundary:**

```
External API (Fetcher)  →  raw response (UNTRUSTED)
                              ↓
                        Tier 1 Reasoner (trust_level: "untrusted_input")
                          - No tools, no spawning, no MCP access
                          - Strict output_schema_ref (validates output shape)
                          - Extracts structured facts, strips free-text injection
                              ↓
                        Schema-validated output (TRUSTED)
                              ↓
                        Tier 2 Reasoner (trust_level: "trusted")
                          - Can spawn steps, PolicyEngine-bounded
                          - Receives ONLY clean structured data
                          - Makes adaptive decisions
```

**Planner responsibility**: The Planner (temperature=0) MUST insert a Tier 1 sanitization step between any API step and a Tier 2 Reasoner step. This is a **plan validation rule** — the plan validator rejects plans where a Tier 2 Reasoner's `context_from` includes an API step without an intervening Tier 1 step.

**Pure API plans exempt**: When API step output flows to another API step via template args (`{{step_1.result.id}}`), no sanitization is needed — no LLM processes the data.

**Failure errors exempt**: Step failure objects (`{ error_type, error_details, status_code }`) are system-generated metadata, not external data. They can go directly to a Tier 2 Reasoner for recovery decisions.

---

## 1) System Layers

The system has **4 layers** that work together:

### Layer 1: Memory & Persistence
**What it does**: Stores everything the system knows
- **ProfileStore**: Your stable preferences (work hours, meeting duration)
- **History**: What you've done before ("usually meets Alice on Tuesdays")
- **PlanLibrary**: Reusable successful plans
- **VectorIndex**: Hybrid BM25 + semantic similarity search via pgvector + tsvector (ONNX Runtime, all-MiniLM-L6-v2, 384-dim)

**Example**: When you say "book a meeting," the system remembers you prefer 30-minute meetings at 10 AM.

### Layer 2: Domain Services
**What it does**: Understands your request, builds a plan, and enforces policies
- **Intake**: Figures out what you want across multiple messages
- **ContextRAG**: Assembles relevant context from Memory Layer via structured queries + optional VectorIndex hybrid search (≤2KB budget, consent tier enforcement, graceful degradation)
- **Planner**: Creates a step-by-step plan (deterministic, immutable) — may include LLM reasoning steps for open-ended tasks
- **PolicyEngine**: Governs all runtime LLM decisions — evaluates policy rules, issues attestations for spawned steps, enforces HITL for critical actions. Technology: PostgreSQL (policy rules) + Redis (cached policies, <5ms evaluation)
- **PluginRegistry**: Knows what tools are available (Google Calendar, Slack, etc.)

**Example**: "Book meeting with Alice" → Intent + Context → Plan with 4 steps (pure API)
**Example**: "Plan my trip to Tokyo" → Intent + Context → Hybrid plan with Reasoner steps that can spawn additional data-fetching steps

### Layer 3: Orchestration
**What it does**: Previews and executes plans safely
- **PreviewOrchestrator**: Shows you what will happen (no side effects)
- **ApprovalGate**: Waits for your confirmation
- **ExecuteOrchestrator**: Does the actual work — dispatches API steps via MCP tool invocations, runs LLM reasoning with two-tier trust model, evaluates spawned steps via PolicyEngine

**Example (deterministic)**: Shows you 3 time slots → You pick one → Creates the calendar event (all via MCP)
**Example (adaptive)**: Python Reasoner analyzes flight options → Spawns Fetcher for adjacent dates → PolicyEngine approves → MCP tool invocation executes spawned step → Results merged

### Layer 4: API & Frontend
**What it does**: Your interface to the system
- FastAPI endpoints for all interactions
- React/Next.js UI for approvals and previews

---

## 2a) How It Works: Pure API Plan (Meeting Booking)

> **Pure API plan** — Every step is `type: "api"`. Fully deterministic, no LLM at execution time, no prompt injection surface. Step outputs flow via template args. If a step fails after step-level retries → plan is terminal.

**User Request**: "Book a meeting with Alice next week"

### Step 1: Understanding (Intake + ContextRAG)
```
User: "Book a meeting with Alice next week"
  ↓
Intake: Parses intent → "schedule_meeting"
  ↓
ContextRAG: Gathers context
  - Preference: "30-minute meetings"
  - History: "Usually meets Alice on Tuesdays at 10 AM"
  - Contact: "alice@company.com"
  ↓
Intent + Evidence → Ready for planning
```

### Step 2: Planning (Planner)
```
Planner receives:
  - Intent: "schedule_meeting with Alice next week"
  - Evidence: [30min preference, Tuesday pattern, Alice's email]
  - Available tools: Google Calendar, Slack

Creates Plan (all steps type: "api" — no Reasoner, no trust tier needed):
  Step 1 (Fetcher, api): Get Alice's availability  [parallel]
  Step 2 (Fetcher, api): Get your availability      [parallel]
  Step 3 (Analyzer, api): Find overlapping slots   [after 1,2]
  Step 4 (Resolver, api): User picks slot          [gate-A]
  Step 5 (Booker, api): Create calendar event      [after 4]
    args: { slot: "{{step_4.result.selected_slot}}" }  ← template resolution
  Step 6 (Notifier, api): Send confirmation        [after 5]
    args: { content: "Meeting booked: {{step_5.result.event_id}}" }  ← template resolution

Plan hash: "sha256:abc123..." (data integrity checksum)
```

### Step 3: Preview (PreviewOrchestrator)
```
PreviewOrchestrator:
  ✓ Verifies plan hash (data integrity)
  ✓ Runs steps 1-3 in READ-ONLY mode

MCP tool invocations execute:
  [Fetch Alice's calendar] ──┐
                             ├→ [Find overlap] → Results
  [Fetch your calendar]   ───┘

Preview shows:
  Option 1: Tuesday 10:00-10:30 ✓ (Alice's usual time)
  Option 2: Thursday 14:00-14:30
  Option 3: Friday 11:00-11:30

User selects: Option 1
```

### Step 4: Approval (ApprovalGate)
```
User clicks "Approve Option 1"
  ↓
ApprovalGate:
  - Validates plan_hash matches
  - Creates approval token (JWT, 15min TTL)
  - Caches preview state:
    {
      "selected_slot": "Tuesday 10:00-10:30",
      "attendees": ["alice@company.com"],
      "preview_results": { ... }
    }
  ↓
Returns token: "jwt:eyJ..."
```

### Step 5: Execute (ExecuteOrchestrator)
```
ExecuteOrchestrator:
  ✓ Verifies plan hash (data integrity)
  ✓ Verifies approval token
  ✓ Retrieves cached preview state (no need to re-fetch calendars!)

Skips steps 1-3 (already done in preview)

Executes step 5:
  - Checks idempotency key: "plan_id:5:args_hash"
  - Not found → Proceeds
  - Calls Google Calendar API: create_event(
      summary: "Meeting with Alice",
      start: "2025-01-14T10:00:00-06:00",
      end: "2025-01-14T10:30:00-06:00",
      attendees: ["alice@company.com"]
    )
  - Stores result: event_id = "gcal_123456"

Executes step 6:
  - Sends Slack message: "✓ Meeting booked with Alice, Tue Jan 14 at 10 AM"
```

### Step 6: Learning (PlanWriter + Audit)
```
PlanWriter:
  - Saves to Plan Library:
    - Plan + outcome (success)
    - Embedding for future similarity search

  - Saves to History:
    - "Booked 30min meeting with Alice on Tuesday 10 AM"
    - (PII-light, derived fact only)

Audit:
  - Logs all steps with plan_id correlation
  - Metrics: Preview: 650ms, Execute: 1.2s ✓
  - No secrets/PII in logs
```

---

## 2b) How Adaptive Execution Works: Travel Planning Example

**User Request**: "Find me the best flights to Tokyo next month and summarize options"

> **Adaptive plan with Reasoner** — The initial plan (revision 0) is immutable, identified by plan_hash. Reasoner steps are explicit decision points that may spawn new steps at runtime, creating new plan revisions. All external API data passes through Tier 1 sanitization before reaching Tier 2 Reasoners.

This example demonstrates **adaptive execution** — the plan includes LLM reasoning steps that can spawn new steps at runtime, with the Data Trust Boundary enforced via explicit Tier 1 sanitization.

### The Plan (Generated by Planner)

```json
{
  "plan_id": "01JXYZ...",
  "plan_revision": 0,
  "graph": [
    {
      "step": 1, "type": "api", "role": "Fetcher",
      "uses": "flights.api", "call": "search",
      "args": {"origin": "ORD", "dest": "NRT", "dates": "2026-04-01..2026-04-30"},
      "after": []
    },
    {
      "step": 2, "type": "api", "role": "Fetcher",
      "uses": "hotels.api", "call": "search",
      "args": {"city": "Tokyo", "dates": "2026-04-01..2026-04-30"},
      "after": []
    },
    {
      "step": 3, "type": "llm_reasoning", "role": "Reasoner",
      "trust_level": "untrusted_input",
      "context_from": [1, 2],
      "can_spawn": false,
      "policy_ref": "policy-travel-sanitize",
      "reasoning_config": {
        "model": "claude-sonnet-4-5-20250929",
        "temperature": 0.1,
        "max_tokens": 2048,
        "system_prompt_ref": "travel_data_sanitize_v1",
        "output_schema_ref": "travel_data_sanitized_v1"
      },
      "after": [1, 2]
    },
    {
      "step": 4, "type": "llm_reasoning", "role": "Reasoner",
      "trust_level": "trusted",
      "context_from": [3],
      "can_spawn": true, "max_spawned_steps": 3,
      "policy_ref": "policy-travel-reasoning",
      "reasoning_config": {
        "model": "claude-sonnet-4-5-20250929",
        "temperature": 0.3,
        "max_tokens": 2048,
        "system_prompt_ref": "travel_analysis_v1"
      },
      "after": [3]
    },
    {
      "step": 5, "type": "llm_reasoning", "role": "Reasoner",
      "trust_level": "trusted",
      "context_from": [3, 4],
      "can_spawn": false,
      "policy_ref": "policy-travel-reasoning",
      "reasoning_config": {
        "model": "claude-sonnet-4-5-20250929",
        "temperature": 0.3,
        "max_tokens": 4096,
        "system_prompt_ref": "travel_summary_v1"
      },
      "after": [4]
    },
    {
      "step": 6, "type": "api", "role": "Notifier",
      "uses": "slack", "call": "send_message",
      "args": {"channel": "user-dm", "content": "{{step_5.result}}"},
      "after": [5]
    }
  ],
  "constraints": {"scopes": ["flights.read", "hotels.read"], "ttl_s": 1800, "policy_version": 1},
  "plugins": ["flights.api", "hotels.api", "slack"]
}
```

**Notice the trust boundary**: Steps 1-2 (API) → Step 3 (Tier 1 sanitizer) → Steps 4-5 (Tier 2 Reasoners). The Planner inserted Step 3 as a Tier 1 sanitization step because Steps 4-5 are Tier 2 Reasoners that consume API output. The plan validator would reject a plan where Step 4's `context_from` referenced Step 1 directly.

### Runtime Execution (Pure Agentic: Python + MCP)

```
t=0ms:    Steps 1,2 execute in parallel via asyncio.gather() (Fetcher, MCP tool invocations)
t=400ms:  Both fetches complete → raw API responses cached (UNTRUSTED)

t=401ms:  Step 3 (Tier 1 Reasoner, trust_level="untrusted_input") executes:
          - Receives raw flight and hotel API responses
          - No tools, no spawning, no MCP access
          - Extracts structured facts:
            { "flights": [{"airline": "ANA", "price_usd": 1200, "dates": "Apr 15-22"}...],
              "hotels": [{"name": "Park Hyatt", "price_per_night": 350}...] }
          - Strips free-text descriptions (potential injection vector):
            e.g., a hotel description containing "Ignore previous instructions..."
            → silently dropped, only structured fields retained
          - Output validated against travel_data_sanitized_v1 schema
t=500ms:  Step 3 output is now TRUSTED (schema-validated structured data)

t=501ms:  Step 4 (Tier 2 Reasoner, trust_level="trusted") executes:
          - Receives ONLY clean structured data from Step 3
          - Analyzes prices: "Flights Apr 15-20 are $1200+, adjacent dates may be cheaper"
          → Proposes spawned step: Fetcher, flights.api, search(dates: April 10-14)
t=502ms:  PolicyEngine evaluates spawned step:
          ✓ flights.api is in plan's plugins array
          ✓ Fetcher role (read-only, no HITL needed)
          ✓ 1 spawned step ≤ max_spawned_steps (3)
          ✓ Total steps: 6 + 1 = 7 ≤ 100
          → PolicyDecision: { allowed: true, requires_approval: false }
          → PolicyAttestation created, plan_revision → 1
t=503ms:  Spawned Fetcher step dispatched via MCP tool invocation
t=700ms:  Spawned step completes → raw results (UNTRUSTED)
t=701ms:  Spawned results routed through Step 3 (Tier 1) for sanitization → TRUSTED
t=750ms:  Step 4 continues with enriched data, completes analysis

t=751ms:  Step 5 (Tier 2 Reasoner, trust_level="trusted") generates travel summary
          - Receives clean data from Steps 3 and 4
          - Cannot spawn (can_spawn=false)
t=900ms:  Step 6 (Notifier, api) sends summary via MCP tool invocation (Slack)
```

**Key points**:
- Steps 1-2 are pure **API steps** — identical to deterministic execution
- Step 3 is a **Tier 1 Reasoner** (sanitizes raw API output → structured facts). No tools, strict schema.
- Step 4 is a **Tier 2 Reasoner** (makes adaptive decisions on clean data, can spawn). PolicyEngine-bounded.
- Step 5 is a **Tier 2 Reasoner** (summarizes, cannot spawn)
- **Trust boundary enforced**: API output never reaches Tier 2 directly — always through Tier 1 first
- Spawned step results also go through Tier 1 sanitization before Tier 2 consumes them
- The **original plan_hash** remains valid; the spawned step has a **PolicyAttestation** for audit

### What If the LLM Tries Something Forbidden?

```
Scenario: Step 4 Reasoner tries to spawn a Booker step to auto-book a flight

Python PolicyEngine evaluates:
  ✗ role=Booker → require_approval=true (non-overridable)
  → PolicyDecision: { allowed: true, requires_approval: true }
  → gate_id injected automatically

Result: Spawned Booker step pauses at HITL gate, user must approve before booking
```

---

## 2c) Failure Recovery Example

> **Failure recovery** — When an API step fails, the error object (system-generated, trusted) routes to the nearest Tier 2 Reasoner with `can_spawn=true` for adaptive recovery.

**Scenario**: Step 1 (flights.api search) fails with 503 Service Unavailable — using the same plan from §2b.

```
t=0ms:    Steps 1,2 execute in parallel via asyncio.gather()
t=200ms:  Step 2 (hotels.api) completes successfully
t=201ms:  Step 1 (flights.api) fails after 3 step-level retries (RetryPolicy exhausted)

t=202ms:  Error object generated (system-generated, TRUSTED — not raw API response):
          { "step": 1, "error_type": "api_unavailable", "status_code": 503,
            "retries_exhausted": true, "api": "flights.api" }

t=203ms:  Step 3 (Tier 1 Reasoner) runs on partial data (hotels only):
          - Receives hotel API response → sanitizes → structured hotel data (TRUSTED)
          - Flight data absent (Step 1 failed)

t=300ms:  Step 4 (Tier 2 Reasoner, can_spawn=true) receives:
          - Clean hotel data from Step 3 (TRUSTED)
          - Error object from Step 1 (system-generated, TRUSTED — goes directly to Tier 2)
          Reasoner decides: "Primary flight API down, try alternative provider"
          → Proposes spawned step: Fetcher, alt-flights.api, search(same args as Step 1)

t=301ms:  PolicyEngine evaluates:
          ✓ alt-flights.api is in plan's plugins array
          ✓ Fetcher role (read-only)
          ✓ 1 spawned ≤ max_spawned_steps (3)
          → Approved, PolicyAttestation created, plan_revision → 1

t=302ms:  Spawned Fetcher executes via MCP tool invocation
t=500ms:  Spawned Fetcher returns results (UNTRUSTED — raw API response)

t=501ms:  Results routed through Step 3 (Tier 1 sanitization) → TRUSTED
t=550ms:  Step 4 (Tier 2 Reasoner) continues with enriched data (hotels + alternative flights)
t=600ms:  Step 5 (Tier 2 Reasoner) generates summary from Steps 3 + 4
t=800ms:  Step 6 (Notifier) sends summary via Slack
```

**Key points**:
- Error objects are **system-generated metadata** (step ID, error type, status code) — not raw API responses. They are TRUSTED and go directly to Tier 2 Reasoners.
- Spawned recovery steps still go through **Tier 1 sanitization** before Tier 2 consumes their output.
- **PolicyEngine** evaluates all recovery actions — same deny-by-default rules as normal spawning.
- If recovery is exhausted (`max_recovery_actions` reached or no alternative available) → **plan is terminal** → user notified.

---

## 3) Component Details

Below are the 15 core components organized by layer. Each will have its own `SPEC.md` and `LLD.md` during implementation.

### Memory Layer (4 components)

#### ProfileStore
**What it does**: Stores your stable preferences and consent settings
**Example data**:
- "Work hours: 9 AM - 5 PM CT"
- "Default meeting duration: 30 minutes"
- "Privacy consent: Tier 3 enabled"

**Technology**: PostgreSQL (profiles table, consents table)

#### History
**What it does**: Remembers what you've done (normalized, PII-light facts)
**Example data**:
- "2024-12-01: Booked 30min meeting with Alice at 10 AM"
- "Usually schedules meetings on Tuesdays"

**Technology**: PostgreSQL (history table with user_id index)

**Note**: This stores *structured facts*, not raw emails or messages.

#### VectorIndex
**What it does**: Finds similar past situations via hybrid BM25 + semantic search with Reciprocal Rank Fusion (RRF) score merging
**Example query**: "Find times I've booked meetings with executives"
**Technology**: PostgreSQL with pgvector extension (HNSW index, 384-dim), tsvector (BM25 keyword search), ONNX Runtime (all-MiniLM-L6-v2, ~10ms local inference)

#### PlanLibrary
**What it does**: Stores all past plans and outcomes
**Example data**:
- Plan: "schedule_meeting" → Success (event_id: gcal_123)
- Plan: "book_flight" → Failed (card declined)

**Technology**: PostgreSQL (plans table, indexed by intent type and success)


---

### Domain Layer (5 components)

#### Intake
**What it does**: Understands what you want across multiple messages
**Example conversation**:
```
User: "I need to meet with Alice"
Intake: [collecting info, not ready to plan]

User: "Next week works"
Intake: [still collecting, asks follow-up]

User: "Tuesday at 10 AM"
Intake: [ready! triggers planning]
```

**Output**: Intent JSON with entities and constraints

#### ContextRAG
**What it does**: Gathers relevant context from memory (tiny, typed, budget-limited)
**Input**: Intent: "schedule_meeting with Alice"
**Process**:
1. Vector search for similar past meetings
2. Fetch Alice's contact info
3. Fetch user preferences (meeting duration)
4. Recent history with Alice

**Output**: ≤2KB of typed Evidence items (not raw data!)

**Why small?**: LLM context window is expensive; we only send what's needed.

#### Planner
**What it does**: Creates a deterministic step-by-step plan (may include hybrid LLM reasoning steps)
**Input**: Intent + Evidence + Available tools (from PluginRegistry) + Policy version (from PolicyEngine)
**Process**: Calls Anthropic Claude API (temperature=0) via LLMAdapter protocol to generate plan
**Output**: Plan graph with steps, dependencies, roles, step types (`api`/`llm_reasoning`/`policy_check`), and **credential ID references**

**Key features**:
- **Deterministic**: Same inputs always produce same plan (initial plan)
- **Hybrid plans**: For open-ended tasks, Planner includes `type: "llm_reasoning"` steps with `can_spawn=true` and `policy_ref`
- **PolicyEngine integration**: Planner snapshots `policy_version` at plan creation and assigns `policy_ref` to reasoning steps
- **No access to credentials**: Plans reference credential IDs (e.g., `"gcal_user_123"`), not actual API tokens or secrets
- **Credential resolution deferred**: ExecuteOrchestrator decrypts credentials from vault at execution time
- **LLMAdapter protocol**: Anthropic Claude API for MVP; protocol abstraction allows future provider swaps (Ollama, vLLM)

**Planner LLM vs Runtime LLM**: The Planner generates the initial plan (temperature=0, deterministic). Runtime LLM reasoning steps (temperature=0.1-0.7) execute during plan execution and adapt within PolicyEngine bounds. Both have ZERO credential access.

#### PluginRegistry
**What it does**: Source of truth for available tools and their credential requirements
**Includes**:
- Tool capabilities (operations, scopes, previewable, idempotent)
- **Credential vault ID templates**: Maps user + integration → encrypted credential vault ID
- MCP server bindings (which MCP server and tool to use for each operation)

**Example entry**:
```json
{
  "tool_id": "google.calendar",
  "mcp_server": "google-workspace-mcp",
  "transport": "stdio",
  "credential_template": "gcal_user_{{user_id}}_{{account_name}}",
  "operations": {
    "list_free_busy": {
      "mcp_tool": "calendar_list_free_busy",
      "previewable": true,
      "scopes": ["calendar.read"],
      "idempotent": true
    },
    "create_event": {
      "mcp_tool": "calendar_create_event",
      "previewable": false,
      "scopes": ["calendar.write"],
      "idempotent": true,
      "compensation": "delete_event"
    }
  }
}
```

**Security**: PluginRegistry provides credential vault IDs to Planner, NOT credential values. Actual credentials (OAuth tokens, API keys) are stored in the encrypted credential vault (AES-256-GCM in PostgreSQL) and never exposed to the LLM.

**Three connector sources**:
1. **MCP servers**: Community-maintained protocol connectors (primary)
2. **OpenAPI adapters**: Auto-generated MCP wrappers for REST APIs
3. **Aggregator services**: Multi-provider APIs (e.g., SerpAPI for search)

**Why important**: Adding new capabilities only requires editing the Registry, not the orchestrators.

#### PolicyEngine
**What it does**: Governs all runtime LLM decisions — evaluates whether reasoning steps can spawn new steps, what roles/tools are allowed, and whether human approval is required.

**Key responsibilities**:
1. **Rule evaluation**: Checks proposed actions against policy rules (step-level → role-level → system-level)
2. **Attestation**: Issues PolicyAttestation records for approved runtime modifications
3. **HITL enforcement**: Automatically injects `gate_id` for spawned Booker steps (non-overridable)
4. **Deny-by-default**: Rejects any action without a matching policy rule

**Default policies**:
- LLM Reasoning: Allows Fetcher/Analyzer/Reasoner/Notifier roles, forbids Booker, max 3 spawned steps
- No recursive spawning: Spawned steps cannot have `can_spawn=true`
- Token budget: Max 8192 tokens per reasoning step

**Technology**: PostgreSQL (policy rules), Redis (cached policies for <5ms evaluation)

**Example**: Reasoning step proposes spawning a Fetcher step → PolicyEngine checks: tool in plugins? role allowed? under step limit? → Approves with attestation.

#### PlanWriter
**What it does**: Persists execution results back to memory
**Process**:
1. Receives Execute wrappers (outcomes)
2. Writes to Plan Library (plan + outcome)
3. Writes to History (derived facts)
4. Triggers vector re-indexing

**Example**: "Meeting booked" → History + Plan Library + Vector embedding

---

### Orchestration Layer (4 components)

#### WorkflowBuilder
> **REMOVED in v6.0** — WorkflowBuilder's responsibilities (DAG traversal, parallel grouping, step dispatch) have been absorbed into ExecuteOrchestrator. With n8n removed in favor of direct MCP tool invocations, the intermediate workflow JSON generation step is no longer needed. ExecuteOrchestrator handles DAG resolution, `asyncio.gather()` parallelism, and MCP dispatch natively.

#### PreviewOrchestrator
**What it does**: Shows you what will happen (no side effects!)
**Process**:
1. Verifies plan hash (data integrity)
2. Dispatches read-only MCP tool invocations for previewable steps
3. Returns Preview wrapper with results

**Safety**: Only runs operations marked `previewable: true` in Registry

#### ApprovalGate
**What it does**: Waits for your confirmation and issues approval tokens
**Process**:
1. Shows preview results to user
2. On approve: Creates JWT token (15min TTL)
3. Binds token to: {plan_hash, gate_id, user_id, scopes}
4. **Caches preview state** (user selections, search results)

**Multi-gate support**: Shopping flow can have gate-A (choose item), gate-B (review cart), gate-C (confirm purchase)

**Preview state caching** (NEW):
```python
# Token includes cached preview results
{
  "token": "jwt:eyJ...",
  "plan_hash": "sha256:abc...",
  "preview_state": {
    "selected_product": "sweater-1",
    "search_results": [...],
    "user_choices": {...}
  }
}
```


#### ExecuteOrchestrator
**What it does**: Does the actual work (writes to external systems). Absorbs WorkflowBuilder's DAG traversal and parallel grouping responsibilities.
**Process**:
1. Verifies plan hash + approval token
2. **Retrieves cached preview state** (skip repeated steps!)
3. Resolves plan DAG into execution levels (topological sort)
4. Dispatches steps by type:
   - `type: "api"` → MCP tool invocation with decrypted credentials from vault
   - `type: "llm_reasoning"` → Anthropic API call with two-tier trust model:
     - `trust_level: "untrusted_input"` (Tier 1): No tools, strict output schema, input sanitized
     - `trust_level: "trusted"` (Tier 2): MCP tool access, may spawn steps, PolicyEngine-bounded
   - `type: "policy_check"` → PolicyEngine evaluation
5. Parallel execution via `asyncio.gather()` for independent steps
6. Idempotency checks (plan_id:step:arg_hash)
7. Resource locking (prevent conflicts)
8. Compensation on failure (undo operations)
9. Returns Execute wrappers

**Preview state reuse**:
- Steps marked `execute_mode: "preview_only"` are skipped
- Template args resolved from cached state
- Example: `product_id: "{{preview.cached_state.selected_product}}"`

#### ExecutionMonitor
**What it does**: Monitors asyncio task executions for infrastructure-level failures (hung processes, server crashes, time budget violations)

**Responsibilities**:
1. **Poll execution task registry** every 30 seconds for active executions
2. **Detect stuck executions**: No progress for 5+ minutes → cancel and notify user
3. **Enforce time budgets**: Cancel tasks exceeding max execution time (60 minutes)
4. **Notify users**: Alert on infrastructure failures so they can start a new plan
5. **Track execution state**: Maintain execution_tracker table (plan_id, task_id, status)

**What it does NOT do**:
- ❌ Workflow-level replay (step failures are handled by LLM reasoning steps)
- ❌ Automatic retry of failed plans (failed plans are terminal)

**Why needed**:
- Asyncio tasks may hang indefinitely (waiting for external event that never arrives)
- Time budget enforcement prevents resource leaks from runaway tasks
- User notification ensures awareness of infrastructure failures

**Process**:
```python
async def monitor_loop():
    while True:
        # 1. Query execution task registry for active tasks
        executions = await task_registry.get_active_executions()

        for execution in executions:
            # 2. Check for stuck execution (no progress for 5min)
            if is_stuck(execution, timeout_minutes=5):
                await task_registry.cancel_execution(execution.task_id)
                await notify_user("Execution stuck — please start a new plan")

            # 3. Enforce time budget (cancel if exceeded)
            if is_over_time_budget(execution, max_minutes=60):
                await task_registry.cancel_execution(execution.task_id)
                await notify_user_timeout(execution)

        await asyncio.sleep(30)  # Poll every 30 seconds
```

**Technology**: FastAPI background task + asyncio task registry

---

### Utilities (1 component)

#### Audit & Observability
**What it does**: Tracks everything for debugging and analytics
**Logs**: All steps with plan_id correlation (no secrets/PII)
**Metrics**: Latency (p95, p99), error rates, token usage
**Dashboards**: User-facing (execution status) + System (SLOs)

---

## 4) Runtime Agent Roles (Responsibility Classification)

**IMPORTANT (MVP Scope)**: Runtime roles are **logical plan-step categories**, NOT separate runtime workers or services.

**API steps execute via MCP tool invocations; LLM reasoning steps execute in Python.** Roles serve as metadata for policies and safety rules.

### Purpose of Roles

Roles are assigned to plan steps during planning and used by ExecuteOrchestrator to determine:
- **Idempotency requirement**: Does this step need idempotency keys? (Booker: yes, Fetcher: no)
- **HITL requirement**: Does this step need human approval? (Resolver: yes, Analyzer: no)
- **Retry policy**: How should failures be handled? (Watcher: aggressive retries, Notifier: best-effort)
- **Compensation requirement**: Does this step need undo logic? (Booker: yes, Fetcher: no)
- **Resource locking**: Does this step need locks? (Booker: yes, Analyzer: no)

### The 7 Roles

#### 1. Fetcher (Read Operations)
**Policy Metadata**:
- Side-effecting: **No** (safe to retry without idempotency)
- Requires HITL: No (read-only operations)
- Retry policy: Moderate (3 attempts, linear backoff)
- Compensation: Not applicable
- Resource locking: No

**Examples**:
- Get calendar availability
- Fetch contact info
- Look up product details
- Check flight prices

**Implementation**: MCP tool invocations (HTTP, API connectors)

#### 2. Analyzer (Data Processing)
**Policy Metadata**:
- Side-effecting: **No** (pure computation)
- Requires HITL: No (automated processing)
- Retry policy: None (deterministic, should not fail)
- Compensation: Not applicable
- Resource locking: No

**Examples**:
- Find overlapping calendar slots
- Rank restaurant options by price/rating
- Compare flight routes
- Calculate expense totals

**Implementation**: Python functions or MCP tool invocations

#### 3. Watcher (Long-Running Monitoring)
**Policy Metadata**:
- Side-effecting: **No** (observation only)
- Requires HITL: Yes (notify when condition met)
- Retry policy: Aggressive (10+ attempts, exponential backoff, long duration)
- Compensation: Not applicable
- Resource locking: No

**Examples**:
- Poll visa slots for 2 weeks
- Monitor price drops daily
- Watch for email replies
- Track package delivery

**Implementation**: Python asyncio tasks with APScheduler, Redis-backed state

#### 4. Resolver (User Interaction)
**Policy Metadata**:
- Side-effecting: **No** (captures user input)
- Requires HITL: **Yes** (by definition)
- Retry policy: None (waits indefinitely for user response)
- Compensation: Not applicable
- Resource locking: No

**Examples**:
- "Which John did you mean?"
- "Pick from these 3 options"
- "Confirm this choice"

**Implementation**: Python async approval gates (Redis-backed, webhook resume)

#### 5. Booker (Write Operations)
**Policy Metadata**:
- Side-effecting: **Yes** (REQUIRES IDEMPOTENCY)
- Requires HITL: Yes (preview-first safety)
- Retry policy: Moderate (5 attempts, exponential backoff)
- Compensation: **Required** (must declare undo operation in PluginRegistry)
- Resource locking: **Yes** (prevent concurrent writes)

**Examples**:
- Create calendar events
- Send emails
- Make purchases
- Book appointments

**Implementation**: MCP tool invocations with idempotency wrapper

**Critical Requirement**: ExecuteOrchestrator MUST inject idempotency checks before Booker steps

#### 6. Notifier (Updates and Alerts)
**Policy Metadata**:
- Side-effecting: **Yes** (sends messages/notifications)
- Requires HITL: No (automated notifications)
- Retry policy: Best-effort (3 attempts, linear backoff, fail silently)
- Compensation: Not applicable (can't unsend notifications)
- Resource locking: Optional (rate-limited resources only)

**Examples**:
- "✓ Meeting booked"
- "Visa slot found! Approve to book?"
- Progress updates
- Error notifications

**Implementation**: MCP tool invocations (Slack, email connectors)

#### 7. Reasoner (LLM-Based Adaptive Decisions)
**Policy Metadata**:
- Side-effecting: **No** (LLM reasoning only; spawned Booker steps handle writes)
- Requires HITL: No (PolicyEngine governs; spawned Booker steps get automatic HITL)
- Retry policy: Moderate (3 attempts with circuit breaker)
- Compensation: Not applicable (reasoning itself is not side-effecting)
- Resource locking: No
- **PolicyEngine bounded**: Must declare `policy_ref`, respects token budget (evaluated in Python)
- **May spawn steps**: `can_spawn=true` allows creating new plan steps at runtime (max 3 per step, max 10 absolute); spawned API steps dispatched via MCP tool invocations

**Examples**:
- Analyze flight options and decide if more data is needed
- Rank restaurant options considering user preferences and context
- Generate natural language summary of comparison results
- Decide which dates to check based on initial price analysis

**Implementation**: Python service (ExecuteOrchestrator + Anthropic API). PolicyEngine evaluates spawned steps before execution. Spawned API steps dispatched via MCP tool invocations.

**Spawning constraints**:
1. Spawned steps CANNOT have `can_spawn=true` (no recursive spawning)
2. Spawned steps can only use tools in the plan's `plugins` array
3. Spawned steps with `role=Booker` always get `gate_id` injected (non-overridable HITL)
4. Deny-by-default: if no policy matches, action is denied
5. Total plan steps (original + spawned) cannot exceed 100

### How Steps Execute (Pure Agentic: Python + MCP)

**API steps execute via MCP tool invocations; LLM reasoning steps execute in Python.** ExecuteOrchestrator resolves the plan DAG and dispatches steps directly:
- Each plan step → MCP tool invocation or Anthropic API call
- Dependencies → `asyncio.gather()` waits for prerequisite steps
- Parallel steps → `asyncio.gather()` for concurrent execution
- HITL gates → Redis-backed async approval gates

**Parallel execution** (steps with no dependencies):
```
Plan:
  Step 1 (Fetcher): Get Alice's calendar  [after: []]
  Step 2 (Fetcher): Get Bob's calendar    [after: []]

Execution:
  results = await asyncio.gather(
      mcp_invoke("calendar_list_free_busy", alice_args),
      mcp_invoke("calendar_list_free_busy", bob_args),
  )
```

**Sequential execution** (steps with dependencies):
```
Plan:
  Step 3 (Analyzer): Find overlap  [after: [1, 2]]

Execution:
  # Steps 1,2 completed via asyncio.gather() above
  overlap = await analyze_overlap(results[0], results[1])
```

**Booker with idempotency** (side-effecting steps):
```
Plan:
  Step 4 (Booker): Create calendar event  [after: [3]]

Execution:
  1. Check idempotency key (Redis GET)
  2. Already executed? → Return cached result
  3. Not found → MCP tool invocation: calendar_create_event
  4. Store idempotency result (Redis SET)
```

**Real execution timeline** (meeting booking example):
- t=0ms: ExecuteOrchestrator starts plan execution
- t=0ms: Steps 1 & 2 execute in parallel (asyncio.gather)
- t=200ms: Both Fetcher steps complete
- t=201ms: Results combined
- t=202ms: Step 3 (Analyzer) executes
- t=350ms: Step 3 completes
- t=351ms: Step 4 (Booker) checks idempotency → not found → executes via MCP
- t=580ms: Step 4 completes, stores result
- t=581ms: Plan execution finishes

---

## 5) Safety and Reliability

### Preview-First Safety Model
**Rule**: Never execute anything without showing the user first

**How it works**:
1. **Preview phase**: Read-only operations, no side effects
   - Fetch data from APIs (calendars, contacts, products)
   - Show user what will happen
   - User can cancel at any time

2. **Execute phase**: Only runs after explicit approval
   - Requires valid approval token (JWT, 15min TTL)
   - Checks idempotency (prevents duplicate operations)
   - Supports compensation (undo if something fails)

### Deterministic Planning with Adaptive Execution

**Key distinction**:
- **"Deterministic"** refers to the **initial plan (revision 0)** — same inputs always produce the same DAG topology (same steps, same dependencies, same roles). Revision 0 is immutable, identified by a SHA-256 plan_hash.
- **"Adaptive"** refers to what happens at runtime — Reasoner steps observe step outputs, make judgments, and may spawn new steps within PolicyEngine bounds. Each spawn event creates a **new plan revision** (revision 1, 2, ...) with a PolicyAttestation. The original graph is never mutated; new steps extend it.
- These are not contradictory: the initial plan is deterministic and integrity-checked, runtime extensions are versioned and audited.

**Guarantee**: Same inputs always produce the same **initial plan graph** (revision 0). The plan_hash covers revision 0; runtime spawned steps increment `plan_revision` and get PolicyAttestations (§2.4.1).

**Inputs** (frozen tuple):
- Intent (finalized user request)
- Evidence (context from ContextRAG, ≤2KB)
- Registry (available tools snapshot)
- Policy (GLOBAL_SPEC version)
- PolicyVersion (PolicyEngine rules version snapshot)

**Process**:
1. Planner calls Anthropic Claude API with temperature=0 (via LLMAdapter protocol)
2. Canonicalize plan JSON (sort keys, deterministic serialization)
3. Hash: SHA-256 of canonical plan bytes (plan_hash for data integrity)

**At runtime** (for plans with `type: "llm_reasoning"` steps):
5. LLM reasoning steps execute with per-step ReasoningConfig (temperature 0.1–0.7)
6. Spawned steps are evaluated by PolicyEngine before execution
7. Each spawn event creates a PolicyAttestation as an audit record
8. `plan_revision` increments on each spawn event

**Benefits**:
- Same request tomorrow = same initial plan graph
- Tamper detection (plan_hash verification + policy attestation chain)
- Auditability (reproducible initial plans + audited runtime adaptations)
- Adaptive execution for open-ended tasks (ranking, summarizing, deciding what data to fetch)
- Clear separation: plan graph is reviewable upfront, Reasoner behavior is policy-bounded at runtime

### Policy-Bounded Execution
**Rule**: All runtime LLM decisions are bounded by PolicyEngine rules

**How it works**:
1. **Deny-by-default**: If no policy rule matches a proposed action, it is rejected
2. **Role enforcement**: Spawned Booker steps always require HITL (non-overridable)
3. **Scope inheritance**: Spawned steps can only use tools in the plan's `plugins` array
4. **No recursive spawning**: Spawned steps cannot spawn further steps
5. **Attestation chain**: Every spawn event produces a PolicyAttestation linking to the policy rule, decision, and new steps — forming a complete audit trail alongside the original plan_hash

**Credential isolation**: Runtime LLM reasoning steps (Python service) have the same ZERO credential access as the Planner LLM. Credentials are decrypted from the encrypted vault (AES-256-GCM in PostgreSQL) by ExecuteOrchestrator at execution time for API steps only, held in-memory briefly, zeroed after MCP call, and never exposed to any LLM.

### Retry Strategy (Node-Level + LLM-Adaptive + Infrastructure)

**MVP supports two retry mechanisms:**

#### A) Step-Level Retries (Transient Failures)
For individual step failures (network timeouts, rate limits, temporary API errors):

```python
# Python RetryPolicy (applied by ExecuteOrchestrator per step)
retry_policy = RetryPolicy(
    max_retries=3,
    backoff="exponential",  # 1s, 2s, 4s
    retry_on=[503, 504, "timeout", "connection_reset"],
)
```

**When to use**: Transient failures (503 errors, timeouts, connection resets)

#### B) LLM-Adaptive Recovery (Primary Recovery Mechanism)
For step-level failures in hybrid plans, LLM reasoning handles recovery inline:

**How it works**:
1. A plan step fails (API error, unexpected response, data issue)
2. The failure routes back to the ExecuteOrchestrator, which passes it to the nearest Reasoner step
3. The Reasoner (Python/Anthropic API) analyzes the failure and proposes a fix within PolicyEngine bounds:
   - **Correctable errors**: Spawn a replacement step with adjusted parameters (e.g., different search query, alternative API endpoint)
   - **Transient errors**: Request a retry of the same step (subject to policy retry limits)
   - **Policy rejection**: Adjust the approach (e.g., use a read-only alternative instead of a write)
4. PolicyEngine evaluates the proposed recovery action
5. If approved → fixed step executes → plan continues forward
6. If retries exhausted (per policy) → **plan is terminal** → error returned to user

**Recovery policy** (configured per reasoning step via PolicyEngine):
```python
# PolicyEngine recovery rules
recovery_policy = {
    "max_retry_per_step": 2,         # Max retries for a single failed step
    "max_recovery_actions": 5,        # Max total recovery actions per plan execution
    "allowed_recovery_roles": ["Fetcher", "Analyzer"],  # What roles can be spawned for recovery
    "recovery_timeout_s": 120,        # Max time for recovery attempts
}
```

**Key principle**: **No workflow-level replay.** If the Reasoner exhausts its policy-bounded recovery attempts, the plan fails terminally. The user is notified and must start a new plan for the same task. This avoids:
- Complex partial-execution state management
- "Resume from step N" logic
- Idempotency-dependent full workflow replays

**For pure API plans** (no Reasoner steps): Step-level retries (§A) handle transient failures. If a step fails after step-level retries, the plan fails terminally.

#### C) Infrastructure-Level Recovery (ExecutionMonitor)
For infrastructure failures that are outside the plan's control:

**Scope**: Hung execution tasks, server crashes, network partitions — NOT step-level failures.

**Trigger**: ExecutionMonitor (polls task registry every 30s) detects:
- Stuck executions: No progress for 5+ minutes → cancel and notify user
- Time budget exceeded: >60 minutes → cancel and notify user

**Outcome**: Infrastructure failures are terminal. The user is notified and must start a new plan. There is no automatic workflow-level replay — the LLM reasoning model makes this unnecessary for step-level failures, and infrastructure failures typically indicate systemic issues that replay wouldn't fix.

### Idempotency (Multi-User Safe, No Duplicate Operations)

**Problem**:
1. Network fails after creating a calendar event → Retry would create duplicates
2. Multiple users run similar workflows → Must not collide on idempotency keys
3. LLM reasoning spawns a recovery step that retries the same operation → Must detect duplicate

**Solution**: 3-state idempotency records with multi-user scoping

#### Idempotency Key Structure

**CRITICAL**: Keys MUST include multi-user scope to prevent cross-user collisions:

```
idem:{user_id}:{integration_account_id}:{plan_execution_id}:{step_id}:{operation}:{input_hash}
```

**Example**:
```
idem:user-123:gcal-acct-xyz:plan-01HX:5:create_event:hash-a1b2
```

**Why each component matters**:
- `user_id`: **Prevents User A's retry from returning User B's cached result**
- `integration_account_id`: **Prevents cross-account pollution** (User A's Google ≠ User B's Google)
- `plan_execution_id`: Unique execution instance (ULID)
- `step_id`: Which step in plan (1, 2, 3, ...)
- `operation`: Tool action (create_event, send_email, etc.)
- `input_hash`: SHA256 of canonicalized args (ensures same inputs = same key)

**Input hash generation** (deterministic):
```python
import hashlib
import json

def generate_input_hash(args: dict) -> str:
    """Generate deterministic hash from operation inputs."""
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

#### 3-State Idempotency Record

Each side-effecting step (Booker role) has a state record:

```python
{
  "state": "IN_FLIGHT | SUCCEEDED | FAILED",
  "owner_execution_id": "task-12345",  # Which execution task owns this
  "started_at": "2026-03-03T10:00:00Z",
  "completed_at": "2026-03-03T10:00:15Z",
  "expires_at": "2026-03-04T10:00:00Z",   # 24h TTL
  "result": {"event_id": "gcal_123"},      # Cached result (if SUCCEEDED)
  "error": "API rate limit exceeded",      # Error details (if FAILED)
  "attempt_count": 1
}
```

**State transitions**:
- `IN_FLIGHT`: Execution started, not yet completed
- `SUCCEEDED`: Execution completed successfully, result cached
- `FAILED`: Execution failed, available for retry

#### Atomic Claim Pattern (Prevents Duplicate Execution)

```python
import json
from redis.asyncio import Redis

async def execute_with_idempotency(
    redis: Redis,
    key: str,
    operation: callable,
    timeout_minutes: int = 5
) -> dict:
    """Execute operation with 3-state idempotency check."""

    # 1. Attempt atomic claim (SET NX - set if not exists)
    claim_payload = json.dumps({
        "state": "IN_FLIGHT",
        "owner_execution_id": current_execution_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "attempt_count": 1
    })

    claimed = await redis.set(key, claim_payload, nx=True, ex=86400)

    if not claimed:
        # Key exists - check state
        existing = json.loads(await redis.get(key))

        if existing["state"] == "SUCCEEDED":
            # Already executed successfully - return cached result
            return existing["result"]

        elif existing["state"] == "IN_FLIGHT":
            # Another execution is in progress
            started_at = datetime.fromisoformat(existing["started_at"])
            age_minutes = (datetime.now(timezone.utc) - started_at).total_seconds() / 60

            if age_minutes > timeout_minutes:
                # Stale execution - takeover
                await redis.delete(key)
                return await execute_with_idempotency(redis, key, operation)
            else:
                # Active execution - fail fast
                raise IdempotencyConflict(
                    f"Operation already in progress (started {age_minutes:.1f}m ago)"
                )

        elif existing["state"] == "FAILED":
            # Previous execution failed - retry allowed
            # Delete old record and retry
            await redis.delete(key)
            return await execute_with_idempotency(redis, key, operation)

    # 2. Claim succeeded - execute operation
    try:
        result = await operation()

        # 3. Mark as SUCCEEDED with cached result
        success_payload = json.dumps({
            "state": "SUCCEEDED",
            "owner_execution_id": current_execution_id,
            "started_at": existing["started_at"] if not claimed else claim_payload["started_at"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result": result
        })
        await redis.setex(key, 86400, success_payload)

        return result

    except Exception as e:
        # 4. Mark as FAILED with error details
        failed_payload = json.dumps({
            "state": "FAILED",
            "owner_execution_id": current_execution_id,
            "started_at": claim_payload["started_at"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
            "attempt_count": existing.get("attempt_count", 0) + 1
        })
        await redis.setex(key, 86400, failed_payload)

        raise
```

**How ExecuteOrchestrator injects idempotency** (per Booker step):

```python
# For each Booker step, ExecuteOrchestrator wraps execution:

async def execute_booker_step(step: PlanStep, plan_id: str):
    idem_key = f"idem:{user_id}:{integration_id}:{plan_id}:{step.step}:{step.call}:{input_hash}"

    # 1. Check idempotency state
    existing = await redis.get(idem_key)
    if existing and json.loads(existing)["state"] == "SUCCEEDED":
        return json.loads(existing)["result"]  # Return cached result

    # 2. Claim execution slot (atomic SET NX)
    claimed = await redis.set(idem_key, json.dumps({"state": "IN_FLIGHT"}), nx=True, ex=86400)
    if not claimed:
        raise IdempotencyConflict("Operation already in progress")

    # 3. Execute via MCP tool invocation
    result = await mcp_invoke(step.uses, step.call, step.args, credentials=decrypted_creds)

    # 4. Store result with SUCCEEDED state
    await redis.setex(idem_key, 86400, json.dumps({"state": "SUCCEEDED", "result": result}))
    return result
```

**Benefits**:
- ✅ Safe LLM-adaptive recovery (spawned replacement steps detect prior execution)
- ✅ Multi-user safe (keys scoped by user/integration)
- ✅ Prevents thundering herd (IN_FLIGHT blocks concurrent executions)
- ✅ Stale execution recovery (takeover after timeout)
- ✅ Retry intelligence (FAILED state tracks attempt count)

### Compensation (Undo on Failure)
**Problem**: Step 3 fails after steps 1 and 2 succeeded. Need to undo.

**Solution**: Registry declares compensation operations
```json
{
  "create_event": {
    "compensation": "delete_event"
  },
  "send_email": {
    "compensation": null  // Can't unsend email
  }
}
```

**Process**:
1. Step 1 succeeds → Store undo info
2. Step 2 succeeds → Store undo info
3. Step 3 fails → Execute compensations in reverse order
   - Undo step 2
   - Undo step 1

**Pattern**: Saga pattern for distributed transactions

### Resource Locking (Prevent Conflicts with Multi-User Scoping)
**Problem**: Two users try to book the same calendar slot simultaneously using the same integration account

**Solution**: Fine-grained locks scoped by user and integration account

```python
# Lock structure: lock:resource:{user_id}:{integration_account_id}:{resource_type}:{entity_id}:{operation}

# User A books Alice's calendar (using User A's Google account)
lock_key = "lock:resource:user-123:gcal-acct-xyz:calendar:alice@example.com:write"
await acquire_lock(lock_key)
try:
    create_event(...)
finally:
    release_lock(lock_key)

# User B books Alice's calendar (using User B's Google account)
lock_key = "lock:resource:user-456:gcal-acct-abc:calendar:alice@example.com:write"
# Different integration account → different lock → can run in parallel
```

**Why user + integration scoping is critical**:
- User A's Google account ≠ User B's Google account (different credentials, different calendars)
- Operations using different integration accounts MUST NOT block each other
- Same entity ID (alice@example.com) for different users likely refers to different people

**Lock granularity levels**:

1. **Entity-level** (most common): `lock:resource:{user_id}:{integration_id}:{resource}:{entity}:write`
   - Prevents concurrent writes to same entity by same user/integration
   - Example: User A tries to book Alice's calendar twice simultaneously

2. **Resource-level** (rate-limited): `lock:resource:{user_id}:{integration_id}:{resource}:send:write`
   - Prevents exceeding rate limits (e.g., max 10 emails/minute per account)
   - Example: User's email account has rate limit

3. **Global** (rare, avoid): `lock:global:deployment:migration:write`
   - System-wide operations (database migrations, config updates)
   - Example: Only one migration can run at a time across entire deployment

**Decision tree**:
- Same user + integration + entity → **Serialize** (use lock)
- Different integration accounts → **Parallelize** (no lock conflict)
- Different entities → **Parallelize** (no lock needed)
- Rate-limited resource → **Resource-level lock** (enforce quota)

### Privacy and Consent
**Tier-based context policy**:
- **Tier 1**: Session only (current conversation)
- **Tier 2**: Stable preferences (work hours, duration)
- **Tier 3**: Recent history (past 30 days)
- **Tier 4**: Live signals (free/busy, cross-app data)
- **Tier 5**: Private content (derived facts only, explicit consent)

**Rules**:
- Never store raw PII (emails, messages)
- Store derived facts only ("usually meets Alice on Tuesdays")
- TTL enforcement (Tier 3 expires after 30 days)
- Forget/export on user request

### Observability
**Correlation**: Every log entry includes `plan_id`
```json
{
  "plan_id": "01HX...",
  "step": 5,
  "role": "Booker",
  "operation": "create_event",
  "latency_ms": 234,
  "status": "success"
}
```

**No secrets in logs**: API keys, tokens, passwords never logged

**Metrics**:
- Preview latency: p95 < 800ms
- Execute latency: p95 < 2s
- Error rates by component
- LLM token usage (cost tracking)

---

## 6) Multi-Gate Approvals (Shopping Example)

**Scenario**: "Buy a blue sweater under $50"

### Why Multiple Gates?
Complex tasks need multiple approval points:
- Gate A: Choose which product
- Gate B: Review cart before purchase
- Gate C: Confirm final payment

### How It Works

**Step 1: Preview & Gate A (Product Selection)**
```
Plan step 1 (Fetcher): Search Amazon for blue sweaters
  → Results: 47 products

Plan step 2 (Resolver): User picks one  [gate_id: "gate-A"]
  → User selects: "Cozy Blue Sweater - $45"
```

**ApprovalGate A**:
- Issues token with `gate_id: "gate-A"`
- Caches preview state:
  ```json
  {
    "selected_product": "sweater-1",
    "price": 45,
    "search_results": [...]
  }
  ```

**Step 2: Gate B (Cart Review)**
```
Plan step 3 (Booker): Add to cart  [gate_id: "gate-B"]
  → Preview shows: Cart total $45 + $5 shipping = $50
```

**ApprovalGate B**:
- Requires approval before adding to cart
- Issues new token with `gate_id: "gate-B"`

**Step 3: Gate C (Purchase)**
```
Plan step 4 (Booker): Complete purchase  [gate_id: "gate-C"]
  → Preview shows: Charge $50 to card ending in 1234
```

**ApprovalGate C**:
- Final confirmation before payment
- Issues token with `gate_id: "gate-C"`

### Enforcement
```python
# ExecuteOrchestrator checks gate tokens
if step.gate_id:
    token = get_approval_token(step.gate_id)
    if not token or token.plan_hash != plan_hash:
        raise Unauthorized("Missing approval for gate")
```

**Result**: User approves at 3 checkpoints, safe multi-step purchase

---

## 7) Tech Stack

See [README.md Tech Stack section](../../README.md#tech-stack) for the complete tech stack with rationale.

**Summary**:
- **Backend**: Python 3.11+ (FastAPI, Pydantic, SQLAlchemy async)
- **Orchestration**: Python/FastAPI ExecuteOrchestrator with MCP protocol for tool invocations
- **Credentials**: AES-256-GCM encrypted vault in PostgreSQL (master key from env)
- **Data**: PostgreSQL 16 + pgvector, Redis 7
- **AI**: Anthropic Claude API (plan generation, temperature=0); ONNX Runtime (local embeddings, 384-dim all-MiniLM-L6-v2)
- **Testing**: pytest, ruff, mypy
- **Infra**: Docker, GitHub Actions

**Key architectural decisions**:
- **No LangChain**: Direct API calls for one-shot planning (not iterative agents)
- **Pure agentic runtime**: Python ExecuteOrchestrator dispatches all steps — MCP for APIs, Anthropic for reasoning
- **pgvector**: Single database for relational + vector (upgrade to dedicated vector DB if needed)

### Application Factory & Dependency Injection

All services are wired via a **lifespan-based DI** pattern — no global mutable state, no per-request construction:

```
shared/app.py         → create_app() + lifespan (startup/shutdown)
shared/dependencies.py → Depends() functions pulling from app.state
```

**How it works**:
1. **Startup** (`lifespan` context manager): Constructs all service singletons (adapters, services) and stores them on `app.state`
2. **Request time** (`Depends()`): Thin functions return the pre-built singleton from `app.state` — zero per-request overhead
3. **Shutdown** (`lifespan` exit): Closes database connections and cleans up resources

```python
# shared/app.py — lifespan creates singletons at startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.plan_service = PlanService(db_adapter=..., ...)
    app.state.preference_service = PreferenceService(db_adapter=..., ...)
    yield
    await db.close()

# shared/dependencies.py — thin Depends() wrappers
def get_plan_service(request: Request) -> Any:
    return request.app.state.plan_service

# routes — declare dependencies, get injected
@router.post("")
async def store_plan(service: PlanService = Depends(get_plan_service)):
    ...
```

**Benefits**:
- Services testable via `app.dependency_overrides[get_plan_service] = lambda: mock_service`
- No import-time side effects — lazy imports inside lifespan avoid circular dependencies
- Single source of truth for service wiring
- Adding a new component requires: (1) init in lifespan, (2) add Depends() function, (3) use in routes

---

## 8) Long-Running Tasks (Visa Watcher Example)

**Scenario**: "Monitor German visa appointment slots for the next 2 weeks"

### Why Python Asyncio?
- Native Python async/await for all scheduling
- APScheduler for periodic polling with persistence
- Redis-backed state for approval gates and progress tracking
- Survives server restarts via Redis state persistence
- Simple deployment — no separate runtime to manage

### How It Works

**Plan**:
```json
{
  "graph": [
    {
      "step": 1,
      "mode": "durable",
      "role": "Watcher",
      "uses": "germany.visa",
      "call": "monitor_slots",
      "args": {"location": "Berlin", "duration_days": 14}
    }
  ]
}
```

**Python Implementation**:
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class DurableWatcher:
    """Long-running monitoring via APScheduler + Redis state."""

    def __init__(self, mcp_client, redis, scheduler: AsyncIOScheduler):
        self.mcp = mcp_client
        self.redis = redis
        self.scheduler = scheduler

    async def start_monitoring(self, plan_id: str, step: PlanStep):
        """Start periodic monitoring job."""
        job_id = f"watch:{plan_id}:{step.step}"

        # Store state in Redis (survives restarts)
        await self.redis.hset(f"watcher:{job_id}", mapping={
            "plan_id": plan_id,
            "status": "monitoring",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "max_duration_days": step.args["duration_days"],
        })

        # Schedule periodic checks
        self.scheduler.add_job(
            self._check_slots,
            "interval",
            hours=6,
            id=job_id,
            args=[plan_id, step],
            replace_existing=True,
        )

    async def _check_slots(self, plan_id: str, step: PlanStep):
        """Check for available slots via MCP tool invocation."""
        result = await self.mcp.invoke(
            step.uses, step.call, step.args
        )

        if result.get("available_slots"):
            # Slots found — notify user and pause for approval
            await self._notify_and_wait_approval(plan_id, step, result)

        # Check time budget
        state = await self.redis.hgetall(f"watcher:watch:{plan_id}:{step.step}")
        started = datetime.fromisoformat(state["started_at"])
        max_days = int(state["max_duration_days"])
        if (datetime.now(timezone.utc) - started).days >= max_days:
            self.scheduler.remove_job(f"watch:{plan_id}:{step.step}")
            await self._notify_user(plan_id, "Monitoring ended (duration elapsed)")

    async def _notify_and_wait_approval(self, plan_id, step, result):
        """Redis-backed approval gate for booking."""
        gate_key = f"approval:{plan_id}:{step.step}"
        await self.redis.hset(gate_key, mapping={
            "status": "pending",
            "slots": json.dumps(result["available_slots"]),
        })
        # Webhook notification triggers user approval flow
        await self._notify_user(plan_id, f"Visa slots found! {len(result['available_slots'])} available")
```

**Key Features**:
1. **APScheduler persistence**: Jobs survive server restarts
2. **Redis state**: Monitoring progress and approval gates stored in Redis
3. **MCP tool invocations**: Visa API called via MCP protocol
4. **Approval gates**: Redis-backed async gates for user confirmation
5. **Time budget enforcement**: Automatic cleanup after duration expires
6. **No separate runtime**: Pure Python, same deployment as the rest of the system

### ExecutionMonitor Role

**Why we need ExecutionMonitor** (infrastructure monitoring only):

1. **Detect stuck executions**: Asyncio tasks may hang (e.g., waiting for external event that never arrives)
2. **Enforce time budgets**: Cancel tasks exceeding max execution time (prevent resource leaks)
3. **User notifications**: Alert users of infrastructure failures so they can start a new plan

**What ExecutionMonitor does NOT do**:
- ❌ Replay failed executions (step failures → LLM reasoning recovery; infrastructure failures → terminal)
- ❌ Apply retry policies (retries happen at step-level or via LLM reasoning)

**How it works**:
```
ExecutionMonitor (polls every 30s)
  ↓
Query execution task registry for active tasks
  ↓
Check each execution:
  - Stuck? (no progress for 5min) → Cancel, notify user
  - Timeout? (exceeded 60min) → Cancel, notify user
  ↓
Update execution_tracker table (plan_id, status)
```

**Result**: Monitors long-running tasks (e.g., visa slot watcher) for infrastructure failures. Step-level failures within those tasks are handled by LLM reasoning steps.

---

## 9) Data Schemas (Canonical Contracts)

All components use these schemas. Full definitions in [GLOBAL_SPEC.md](GLOBAL_SPEC.md).

### Intent
```json
{
  "intent": "schedule_meeting",
  "entities": {
    "attendee": "Alice",
    "timeframe": "next week"
  },
  "constraints": {
    "duration_min": 30
  },
  "tz": "America/Chicago",
  "user_id": "user-123"
}
```

### Evidence Item
```json
{
  "type": "preference",
  "key": "meeting_duration_min",
  "value": 30,
  "confidence": 0.95,
  "source_ref": "kv:prefs/duration"
}
```

### Plan (with execute_mode for preview caching)
```json
{
  "plan_id": "01HX...",
  "graph": [
    {
      "step": 1,
      "mode": "interactive",
      "role": "Fetcher",
      "uses": "google.calendar",
      "call": "list_free_busy",
      "args": {"user": "alice@example.com"},
      "after": [],
      "execute_mode": "preview_only",
      "dry_run": true
    },
    {
      "step": 2,
      "mode": "interactive",
      "role": "Booker",
      "uses": "google.calendar",
      "call": "create_event",
      "args": {
        "slot": "{{preview.cached_state.selected_slot}}"
      },
      "after": [1],
      "gate_id": "gate-A",
      "execute_mode": "execute_only"
    }
  ]
}
```

**execute_mode values**:
- `both` (default): Run in preview AND execute
- `preview_only`: Skip during execute (cached in preview state)
- `execute_only`: Skip during preview (use cached args)

---

## 10) Repository Structure

Each component follows the same structure for consistency:

```
shared/
├── app.py              # Application factory (create_app + lifespan DI)
├── dependencies.py     # Depends() functions for all services
├── database/           # Shared database adapter
├── middleware/          # Auth middleware
├── api/                # Auth routes, error handlers
└── schemas/            # Shared Pydantic schemas

components/<ComponentName>/
├── SPEC.md             # Declares conformance to GLOBAL_SPEC
├── LLD.md              # Low-level design details
├── api/                # FastAPI routes (thin wrappers using Depends())
│   └── routes.py
├── domain/             # Domain models and business logic
│   └── models.py
├── service/            # Service layer
├── adapters/           # External integrations (DB, APIs)
└── tests/              # Unit and integration tests

usecases/<UseCase>/
├── SPEC.md             # Use case specification
├── LLD.md              # Implementation design
├── plans/              # Example plans
│   ├── drafts/         # Work-in-progress plans
│   └── approved/       # Validated plans
├── tests/              # End-to-end tests
└── fixtures/           # Test data
```

**15 Active Components**:
1. ProfileStore, History, PlanLibrary, VectorIndex (Memory Layer)
2. Intake, ContextRAG, Planner, PluginRegistry, PlanWriter, **PolicyEngine** (Domain Layer)
3. PreviewOrchestrator, ApprovalGate, ExecuteOrchestrator, ExecutionMonitor (Orchestration Layer)
4. Audit (Utilities)

---

## 11) Performance Targets

### Latency (p95)
- **Preview**: < 800ms (target: 650ms)
- **Execute (short)**: < 2s (target: 1.2s)
- **ContextRAG**: < 150ms (target: 120ms)
- **Plan Retrieval**: < 200ms

### Availability
- **Intake/Preview**: 99.9% (< 43min downtime/month)
- **Execute/Durable**: 99.5% (< 3.6hr downtime/month)

### Scalability
- **Concurrent plans**: 100+ simultaneous executions
- **Plan library**: Unlimited (PostgreSQL partitioned by month)

---

## 12) Architectural Decisions

### VectorIndex Implementation

**Decision**: VectorIndex implements hybrid BM25 + semantic search with Reciprocal Rank Fusion (RRF) score merging, using ONNX Runtime for local embeddings.

**Rationale**:
- **Hybrid search covers both structured and fuzzy queries**: BM25 (tsvector/tsquery) handles keyword matching; semantic search (pgvector HNSW) handles novel intents that don't match existing `intent_type` values exactly.
- **ONNX Runtime solves the latency problem**: Local CPU inference with all-MiniLM-L6-v2 achieves ~10ms embedding generation — well within ContextRAG's <150ms p95 budget (vs ~200-500ms for external API calls).
- **Single database**: pgvector extension keeps everything in PostgreSQL — no separate vector DB infrastructure.
- **RRF score fusion**: Combines BM25 and cosine similarity rankings without requiring score normalization — robust and tuning-light.

**Implementation** (PR #9, merged):
- `plan_embeddings` table: 384-dim vectors + tsvector column + intent_type filter
- HNSW index (m=16, ef_construction=64) for cosine similarity
- GIN index for BM25 keyword search
- ContextRAG uses VectorIndex as optional dependency with graceful degradation

### PlanLibrary and VectorIndex Separation

**Decision**: PlanLibrary stores and retrieves plans via structured queries. VectorIndex indexes plan data separately for hybrid search. PlanWriter triggers re-indexing via VectorIndex.

**Rationale**: Clear separation of concerns — PlanLibrary is a CRUD component (foundation Memory Layer), VectorIndex provides search capabilities. PlanWriter coordinates: after persisting outcomes to PlanLibrary and History, it stores plan embeddings via VectorIndex for future similarity search.

### Hybrid Execution Model

**Decision**: Evolve from a fully deterministic execution model (Planner generates complete static DAG → executor runs mechanically) to a hybrid model where plans include LLM reasoning steps as first-class nodes alongside deterministic API steps.

**Rationale**:
- **Open-ended tasks**: Pure deterministic plans cannot handle tasks requiring judgment (ranking options, deciding what data to fetch, generating contextual summaries)
- **Adaptive execution**: LLM reasoning steps can request additional data at runtime based on initial results, without requiring the user to re-plan
- **Plan continuation**: Failure recovery becomes natural — reasoning steps can propose alternatives within policy bounds

**Trade-offs**:
- **+** Handles open-ended tasks that pure deterministic plans cannot
- **+** Failure recovery is natural: LLM reasoning handles step failures inline, no workflow replay needed
- **+** Eliminates plan continuation complexity: failed plans are terminal, user starts fresh
- **+** Same safety guarantees via PolicyEngine (deny-by-default, HITL for writes)
- **−** Runtime behavior is no longer fully deterministic (same plan may execute differently)
- **−** PolicyEngine is a new component to build, test, and maintain
- **-** Runtime behavior requires PolicyEngine governance for safety

**Backward compatibility**: All new PlanStep fields are optional with defaults. `type` defaults to `"api"` — existing plans work unchanged. Pure deterministic plans (all type=api) skip PolicyEngine entirely.

### Pure Agentic Execution (Drop n8n)

**Decision**: Replace the hybrid n8n + Python execution model with pure Python/FastAPI execution via MCP tool invocations.

**Rationale**:
- **Deployment simplicity**: One runtime (Python) instead of two (Python + n8n)
- **MCP ecosystem**: Community-maintained connectors replace n8n's proprietary node format
- **Credential control**: AES-256-GCM vault with application-level access control replaces n8n's opaque Secrets Vault
- **Testing**: Standard pytest for all execution paths (no n8n environment needed)
- **NemoClaw compatible**: System can run inside NemoClaw for infrastructure-level security (OS sandboxing, network namespaces) on top of application-level security

**Trade-offs**:
- **+** Single runtime reduces operational complexity
- **+** MCP connectors are open-source and community-maintained
- **+** Full control over credential lifecycle
- **+** Simpler testing and debugging
- **-** Lose n8n's visual workflow editor (mitigated by structured logging + plan graph visualization)
- **-** Must implement scheduling (APScheduler) and persistence ourselves
- **-** Fewer built-in connectors initially (MCP ecosystem is growing)

### Encrypted Credential Vault & LLM Isolation

**Decision**: Store credentials in AES-256-GCM encrypted PostgreSQL vault with strict LLM isolation boundary. Two-tier LLM execution for prompt injection defense.

**Architecture**:
- **Storage**: `credential_vault` table with encrypted_value (BYTEA), iv (BYTEA), key_version (INT)
- **Master key**: Loaded from environment variable, never in database
- **Decryption**: Only at step execution time, in-memory only, zeroed after MCP call
- **LLM boundary**: Neither Planner nor runtime reasoning steps can access credential values
- **Key rotation**: `key_version` supports rolling rotation

**Rationale**:
- Prompt injection attacks cannot leak credentials because LLM never sees values
- Application-level encryption gives full audit control (vs n8n's opaque vault)
- Key rotation without downtime via version-based decryption

**Example Plan Step**:
```json
{
  "step": 1,
  "role": "Fetcher",
  "uses": "google.calendar",
  "call": "list_free_busy",
  "credential_ref": "gcal_user_{{user_id}}_primary",
  "args": {
    "calendar_id": "primary",
    "time_min": "2026-03-01T00:00:00Z"
  }
}
```

**Credential Resolution**:
When ExecuteOrchestrator dispatches a step, it decrypts the credential from the vault:
```python
# ExecuteOrchestrator credential resolution
cred_id = resolve_credential_ref(step.credential_ref, user_id)
encrypted = await vault.get(cred_id)
plaintext = decrypt_aes_gcm(encrypted.value, encrypted.iv, master_key)
try:
    result = await mcp_invoke(step.uses, step.call, step.args, credentials=plaintext)
finally:
    plaintext = None  # Zero credential from memory
```

**Deployment Model**:
- **Anthropic Claude API**: Plan generation via LLMAdapter protocol (temperature=0); swappable to local providers (Ollama, vLLM) via protocol
- **Encrypted vault**: AES-256-GCM in PostgreSQL, master key from environment variable
- **No cloud dependencies for secrets**: Credentials never leave the local environment; LLM receives only credential IDs, not values
- **Full isolation**: ExecuteOrchestrator decrypts credentials only at MCP invocation time — LLM reasoning steps never see credential values

### Two-Tier LLM Execution

**Decision**: Split LLM reasoning into two trust tiers declared via `trust_level` on PlanStep.

**Rationale**:
- **Tier 1 (untrusted_input)**: Processes user-provided or external data with no tool access and strict output schema — prevents prompt injection from propagating
- **Tier 2 (trusted)**: Agent reasoning with MCP tool access, PolicyEngine-bounded — only receives clean, validated data from Tier 1 or API steps

**Trade-offs**:
- **+** Structural defense against prompt injection (data/control plane separation)
- **+** Tier 1 sandboxing is cheap (no tools, strict schema)
- **-** Planner must correctly classify trust levels during plan generation
- **-** Two-step processing adds latency for data that needs both tiers

### WorkflowBuilder Absorption

**Decision**: Absorb WorkflowBuilder into ExecuteOrchestrator. WorkflowBuilder was responsible for converting Plan DAGs into n8n workflow JSON. With n8n removed, this intermediate step is unnecessary — ExecuteOrchestrator handles DAG traversal, parallel grouping, and MCP dispatch natively.

**Rationale**:
- WorkflowBuilder's sole purpose was n8n JSON generation
- DAG traversal and parallel grouping are simple enough to inline in ExecuteOrchestrator
- Eliminates an entire component (17 → 15 components, with Signer also removed)

### MCP Connector Model

**Decision**: Replace n8n's proprietary connector nodes with MCP (Model Context Protocol) servers for all external integrations.

**Rationale**:
- **Open protocol**: MCP is an open standard with growing community adoption
- **Three connector sources**: Native MCP servers, auto-generated OpenAPI wrappers, aggregator services
- **Transport flexibility**: stdio, SSE, or HTTP — configurable per connector
- **No vendor lock-in**: Community-maintained connectors vs n8n's proprietary node format

### NemoClaw Deployment Compatibility

**Decision**: The system can optionally run inside NemoClaw for infrastructure-level security on top of application-level security.

**Rationale**:
- **Defense in depth**: Application-level security (PolicyEngine, credential vault, two-tier LLM) + infrastructure-level security (OS sandboxing, network namespaces)
- **Not required**: The system is secure without NemoClaw — NemoClaw adds an additional security layer for high-security deployments

---

## 13) Asynchronous Execution Architecture

**MVP execution model**: ExecuteOrchestrator handles all plan execution via MCP tool invocations and Anthropic API calls. Step-level failures are recovered inline by LLM reasoning steps (PolicyEngine-bounded). ExecutionMonitor provides infrastructure monitoring for stuck/hung tasks.

### ExecutionMonitor Pattern

**Purpose**: Detect stuck execution tasks caused by infrastructure failures (hung processes, server crashes, network partitions). NOT for step-level failures — those are handled by LLM reasoning steps.

**Why needed**:
- ExecuteOrchestrator dispatches tasks asynchronously
- Asyncio tasks may hang indefinitely (waiting for external event that never arrives)
- Time budget enforcement prevents resource leaks from runaway tasks

**Implementation**:

```python
import asyncio
from datetime import datetime, timezone
from typing import List, Dict

class ExecutionMonitor:
    """Monitors execution tasks for infrastructure-level failures.

    Step-level failures are handled inline by LLM reasoning steps.
    This monitor only handles infrastructure issues: stuck processes,
    time budget violations, and server-level failures.
    """

    def __init__(self, task_registry, db_adapter, poll_interval_seconds: int = 30):
        self.task_registry = task_registry
        self.db = db_adapter
        self.poll_interval = poll_interval_seconds

    async def run(self):
        """Background polling loop."""
        while True:
            try:
                await self._check_active_executions()
            except Exception as e:
                logger.error(f"ExecutionMonitor error: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _check_active_executions(self):
        """Poll task registry for active executions and check for infrastructure issues."""
        active_executions = await self.task_registry.get_executions(
            status="running",
            limit=100
        )

        tracked = await self.db.get_tracked_executions(status="running")
        tracked_map = {t.task_id: t for t in tracked}

        for execution in active_executions:
            tracker = tracked_map.get(execution.task_id)

            if not tracker:
                await self.db.create_execution_tracker(
                    plan_id=execution.metadata["plan_id"],
                    task_id=execution.task_id,
                    status="running"
                )
                continue

            # Stuck execution (infrastructure issue) → cancel and notify
            if self._is_stuck(execution, timeout_minutes=5):
                await self._handle_stuck_execution(execution, tracker)

            # Time budget exceeded → cancel and notify
            if self._is_over_time_budget(execution, max_minutes=60):
                await self._handle_timeout(execution, tracker)

    def _is_stuck(self, execution, timeout_minutes: int) -> bool:
        last_update = datetime.fromisoformat(execution.last_progress_at or execution.started_at)
        age = (datetime.now(timezone.utc) - last_update).total_seconds() / 60
        return age > timeout_minutes and execution.status == "running"

    def _is_over_time_budget(self, execution, max_minutes: int) -> bool:
        started_at = datetime.fromisoformat(execution.started_at)
        age = (datetime.now(timezone.utc) - started_at).total_seconds() / 60
        return age > max_minutes

    async def _handle_stuck_execution(self, execution, tracker):
        """Cancel stuck execution — infrastructure failures are terminal."""
        logger.warning(f"Stuck execution detected: {execution.task_id}")
        await self.task_registry.cancel_execution(execution.task_id)
        await self.db.update_tracker(tracker.id, status="infrastructure_failure")
        await self._notify_user(tracker.plan_id, "Execution stuck — please start a new plan")

    async def _handle_timeout(self, execution, tracker):
        """Cancel execution that exceeded time budget — terminal."""
        logger.warning(f"Execution timeout: {execution.task_id}")
        await self.task_registry.cancel_execution(execution.task_id)
        await self.db.update_tracker(tracker.id, status="timeout")
        await self._notify_user(tracker.plan_id, "Execution timed out — please start a new plan")

    async def _notify_user(self, plan_id: str, message: str):
        """Send notification to user about infrastructure failure."""
        # Implementation: webhook/email/Slack notification
        pass
```

**Key features**:
- **Polling interval**: 30 seconds (configurable)
- **Stuck detection**: No progress for 5+ minutes → cancel and notify
- **Timeout enforcement**: Cancel after 60 minutes → notify
- **User notifications**: Alert on infrastructure failures
- **No execution replay**: Infrastructure failures are terminal (user starts a new plan)

### Parallel Step Execution (via asyncio.gather)

Steps with no dependencies execute in parallel via `asyncio.gather()`. ExecuteOrchestrator analyzes the plan graph and groups steps by dependency level:

```python
class ExecuteOrchestrator:
    """Dispatches plan steps via MCP tool invocations and Anthropic API.

    Handles DAG traversal, parallel grouping, and step dispatch natively.
    LLM reasoning steps execute via Anthropic API with two-tier trust model.
    API steps execute via MCP tool invocations.
    """

    async def execute_plan(self, plan: Plan, credentials: dict) -> list[StepResult]:
        """Execute plan by resolving DAG into execution levels."""

        # Group steps by dependency level
        levels = self._group_by_dependency_level(plan.graph)
        results = {}

        for level_steps in levels:
            if len(level_steps) > 1:
                # Multiple steps at same level → parallel execution
                level_results = await asyncio.gather(
                    *[self._dispatch_step(step, results, credentials)
                      for step in level_steps]
                )
            else:
                # Single step → sequential execution
                level_results = [
                    await self._dispatch_step(level_steps[0], results, credentials)
                ]

            for step, result in zip(level_steps, level_results):
                results[step.step] = result

        return list(results.values())

    async def _dispatch_step(self, step, prior_results, credentials):
        """Dispatch step by type: MCP tool invocation or Anthropic API call."""
        if step.type == "api":
            return await self._mcp_invoke(step, credentials)
        elif step.type == "llm_reasoning":
            return await self._anthropic_call(step, prior_results)
        elif step.type == "policy_check":
            return await self._policy_evaluate(step, prior_results)

    def _group_by_dependency_level(self, steps: list) -> list[list]:
        """Group steps by dependency depth for parallel execution."""
        levels = []
        processed = set()

        while len(processed) < len(steps):
            current_level = [
                s for s in steps
                if s.step not in processed
                and all(dep in processed for dep in s.after)
            ]

            if not current_level:
                raise ValueError("Circular dependency detected in plan graph")

            levels.append(current_level)
            processed.update(s.step for s in current_level)

        return levels
```

**Example: Meeting plan with parallel fetches**
```
Plan graph:
  Step 1: Get Alice's calendar [after: []]
  Step 2: Get your calendar    [after: []]
  Step 3: Find overlap         [after: [1, 2]]

Execution:
  asyncio.gather(mcp_invoke(step_1), mcp_invoke(step_2)) → analyze_overlap(results)
```

**Benefits**:
- **Native Python parallelism**: `asyncio.gather()` for concurrent execution
- **Dependency ordering preserved**: Level-by-level execution via topological sort
- **Unified error handling**: Standard Python exception handling and retry policies
- **Simple testing**: Standard pytest with async fixtures, no external runtime needed

### Background Task Monitoring

Long-running execution tasks are monitored via webhook callbacks:

```python
from fastapi import BackgroundTasks

@router.post("/execute")
async def execute_plan(
    request: ExecuteRequest,
    background_tasks: BackgroundTasks,
    service: ExecuteService = Depends(get_execute_service)
):
    """Execute plan and monitor completion asynchronously."""

    # Enqueue execution
    plan_id = await service.enqueue_execution(request.plan, request.token)

    # Monitor completion in background
    background_tasks.add_task(
        monitor_execution,
        plan_id=plan_id,
        callback_url=request.callback_url
    )

    return {"plan_id": plan_id, "status": "queued"}

async def monitor_execution(plan_id: str, callback_url: str):
    """Poll execution status and notify on completion."""
    max_wait = 3600  # 1 hour max
    poll_interval = 5  # 5 seconds

    for _ in range(max_wait // poll_interval):
        status = await task_registry.get_execution_status(plan_id)

        if status.is_complete:
            # Notify caller
            await httpx.post(callback_url, json={
                "plan_id": plan_id,
                "status": status.final_status,
                "result": status.result
            })
            return

        await asyncio.sleep(poll_interval)

    # Timeout - notify caller
    await httpx.post(callback_url, json={
        "plan_id": plan_id,
        "status": "timeout",
        "error": "Execution exceeded 1 hour limit"
    })
```

---

## 14) LLM Guardrails and Structured Interaction

The system uses multiple layers of validation and safety mechanisms when interacting with LLMs for planning.

### Validation Layers

All LLM outputs pass through a 3-layer validation pipeline:

```python
from pydantic import BaseModel, Field, validator
from typing import List, Dict, Any

class PlanSchema(BaseModel):
    """Schema for validating planner LLM output."""

    plan_id: str = Field(..., regex=r'^[0-9A-Z]{26}$')  # ULID format
    intent: Dict[str, Any]
    graph: List[StepSchema]
    constraints: ConstraintsSchema

    @validator('graph')
    def validate_dependencies(cls, steps: List[StepSchema]) -> List[StepSchema]:
        """Ensure all step dependencies are valid."""
        step_ids = {s.step for s in steps}

        for step in steps:
            for dep in step.after:
                if dep not in step_ids:
                    raise ValueError(f"Step {step.step} depends on non-existent step {dep}")
                if dep >= step.step:
                    raise ValueError(f"Step {step.step} has invalid forward/self dependency")

        return steps

    @validator('graph')
    def validate_roles(cls, steps: List[StepSchema]) -> List[StepSchema]:
        """Ensure role assignments are valid."""
        valid_roles = {"Fetcher", "Analyzer", "Watcher", "Resolver", "Booker", "Notifier", "Reasoner"}

        for step in steps:
            if step.role not in valid_roles:
                raise ValueError(f"Step {step.step} has invalid role: {step.role}")

        return steps

class PlanValidator:
    """Multi-layer validation for planner output."""

    async def validate(self, raw_output: str) -> Plan:
        """Validate LLM output through 3 layers."""

        # Layer 1: JSON parsing
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON from planner: {e}")

        # Layer 2: Schema validation (Pydantic)
        try:
            plan = PlanSchema(**data)
        except ValidationError as e:
            raise ValidationError(f"Plan schema validation failed: {e}")

        # Layer 3: Business rules
        await self._validate_business_rules(plan)

        return plan

    async def _validate_business_rules(self, plan: Plan):
        """Enforce business logic constraints."""

        # Check tool availability
        for step in plan.graph:
            if not await plugin_registry.has_tool(step.uses):
                raise ValidationError(f"Tool {step.uses} not available")

        # Check scope requirements
        required_scopes = {scope for step in plan.graph
                          for scope in step.required_scopes}
        if not await user_has_scopes(plan.intent.user_id, required_scopes):
            raise ValidationError(f"Missing required scopes: {required_scopes}")

        # Check plan complexity
        if len(plan.graph) > 50:
            raise ValidationError("Plan exceeds max 50 steps")
```

### Circuit Breaker Pattern

Protect against LLM API failures with circuit breaker:

```python
from enum import Enum
from datetime import datetime, timedelta

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """Circuit breaker for LLM API calls."""

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_seconds: int = 60,
        success_threshold: int = 2
    ):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None

        self.failure_threshold = failure_threshold
        self.timeout = timedelta(seconds=timeout_seconds)
        self.success_threshold = success_threshold

    async def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection."""

        # Check state before calling
        if self.state == CircuitState.OPEN:
            if datetime.now() - self.last_failure_time > self.timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
            else:
                raise CircuitOpenError("LLM service unavailable")

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result

        except Exception as e:
            await self._on_failure()
            raise

    async def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        else:
            self.failure_count = 0

    async def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

# Usage in planner
planner_circuit = CircuitBreaker(failure_threshold=5, timeout_seconds=60)

async def generate_plan(intent: Intent, evidence: List[Evidence]) -> Plan:
    """Generate plan with circuit breaker protection."""
    try:
        raw_plan = await planner_circuit.call(
            claude_api.messages.create,
            model="claude-opus-4-6",
            temperature=0,
            messages=[{"role": "user", "content": build_prompt(intent, evidence)}]
        )
        return await validator.validate(raw_plan)

    except CircuitOpenError:
        # Fallback to cached plan
        return await get_similar_cached_plan(intent)
```

### Fallback Hierarchy

Multi-level fallback strategy for planner failures:

```python
class PlannerService:
    """Planner with fallback hierarchy."""

    async def generate_plan(
        self,
        intent: Intent,
        evidence: List[Evidence]
    ) -> Plan:
        """Generate plan with 4-level fallback."""

        # Level 1: Primary planner (Claude Opus with circuit breaker)
        try:
            return await self._plan_with_claude_opus(intent, evidence)
        except CircuitOpenError:
            logger.warning("Claude Opus circuit open, falling back to Sonnet")
        except Exception as e:
            logger.error(f"Claude Opus failed: {e}")

        # Level 2: Fallback to Claude Sonnet (faster, cheaper)
        try:
            return await self._plan_with_claude_sonnet(intent, evidence)
        except Exception as e:
            logger.error(f"Claude Sonnet failed: {e}")

        # Level 3: Template-based plan from library
        try:
            template = await plan_library.get_template(intent.intent_type)
            return await self._instantiate_template(template, intent, evidence)
        except TemplateNotFoundError:
            logger.warning(f"No template for intent: {intent.intent_type}")

        # Level 4: Minimal safe plan (fetch only, no execution)
        logger.error("All planner fallbacks exhausted, returning minimal plan")
        return self._create_minimal_plan(intent)

    def _create_minimal_plan(self, intent: Intent) -> Plan:
        """Create minimal plan that only fetches data."""
        return Plan(
            plan_id=generate_ulid(),
            intent=intent,
            graph=[
                Step(
                    step=1,
                    mode="interactive",
                    role="Fetcher",
                    uses="system.echo",
                    call="echo",
                    args={"message": "Planner unavailable, manual action required"},
                    dry_run=True
                )
            ],
            constraints={"scopes": [], "ttl_s": 300}
        )
```

### Constraint Enforcement

Hard limits on planner output to prevent abuse:

```python
class PlanConstraints:
    """Enforce hard limits on plan generation."""

    MAX_STEPS = 50
    MAX_PARALLEL_STEPS = 10
    MAX_STEP_ARGS_SIZE = 10_000  # 10KB per step
    MAX_PLAN_SIZE = 100_000  # 100KB total
    ALLOWED_SCOPES = {
        "calendar.read", "calendar.write",
        "contacts.read",
        "email.send",
        "shopping.read", "shopping.write"
    }

    @classmethod
    def enforce(cls, plan: Plan):
        """Enforce all constraints, raise on violation."""

        # Step count (initial plan max 50; total with spawned steps max 100)
        if len(plan.graph) > cls.MAX_STEPS:
            raise ConstraintViolation(f"Plan exceeds {cls.MAX_STEPS} steps")

        # Parallel execution limit
        for step in plan.graph:
            parallel_peers = [s for s in plan.graph if s.after == step.after]
            if len(parallel_peers) > cls.MAX_PARALLEL_STEPS:
                raise ConstraintViolation(f"Too many parallel steps at level {step.after}")

        # Step args size
        for step in plan.graph:
            args_size = len(json.dumps(step.args))
            if args_size > cls.MAX_STEP_ARGS_SIZE:
                raise ConstraintViolation(f"Step {step.step} args exceed 10KB")

        # Total plan size
        plan_size = len(json.dumps(plan.dict()))
        if plan_size > cls.MAX_PLAN_SIZE:
            raise ConstraintViolation(f"Plan size {plan_size} exceeds 100KB")

        # Scope validation
        requested_scopes = set(plan.constraints.get("scopes", []))
        invalid_scopes = requested_scopes - cls.ALLOWED_SCOPES
        if invalid_scopes:
            raise ConstraintViolation(f"Invalid scopes requested: {invalid_scopes}")
```

---

## 15) Advanced Concurrency Patterns

Beyond basic idempotency, the system uses sophisticated concurrency control mechanisms.

### Distributed Locking with Redis

Three lock granularities for different conflict scenarios:

```python
from redis.asyncio import Redis
from contextlib import asynccontextmanager
import uuid

class DistributedLock:
    """Redis-based distributed lock with automatic release."""

    def __init__(self, redis: Redis, lock_key: str, ttl: int = 30):
        self.redis = redis
        self.lock_key = f"lock:{lock_key}"
        self.lock_value = str(uuid.uuid4())  # Unique token for this lock
        self.ttl = ttl

    async def acquire(self, timeout: int = 10) -> bool:
        """Acquire lock with timeout."""
        end_time = time.time() + timeout

        while time.time() < end_time:
            # SET NX (set if not exists) with TTL
            acquired = await self.redis.set(
                self.lock_key,
                self.lock_value,
                nx=True,  # Only set if key doesn't exist
                ex=self.ttl  # Expire after TTL seconds
            )

            if acquired:
                return True

            # Wait before retry
            await asyncio.sleep(0.1)

        return False

    async def release(self):
        """Release lock only if we own it."""
        # Lua script for atomic check-and-delete
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self.redis.eval(script, 1, self.lock_key, self.lock_value)

@asynccontextmanager
async def resource_lock(redis: Redis, resource: str, entity_id: str):
    """Context manager for resource-level locking."""
    lock = DistributedLock(
        redis,
        lock_key=f"{resource}.{entity_id}.write",
        ttl=30
    )

    acquired = await lock.acquire(timeout=10)
    if not acquired:
        raise LockTimeoutError(f"Could not acquire lock for {resource}.{entity_id}")

    try:
        yield
    finally:
        await lock.release()

# Usage examples

# 1. Entity-level lock (finest granularity)
async with resource_lock(redis, "calendar", "alice@example.com"):
    await create_event(attendee="alice@example.com", ...)

# 2. Resource-level lock (coarser granularity)
async with resource_lock(redis, "email", "send"):
    await send_email(...)  # Rate-limited resource

# 3. Global lock (coarsest - avoid when possible)
async with resource_lock(redis, "global", "deployment"):
    await run_migration(...)
```

**Lock granularity decision tree**:
- **Entity-level** (`calendar.alice.write`): Use when operations conflict only for specific entities (most common)
- **Resource-level** (`email.send`): Use for rate-limited resources or global quotas
- **Global** (`global.deployment`): Use only for system-wide operations (migrations, config updates)

### Enhanced Idempotency with TTL

Idempotency keys with automatic expiration and cleanup:

```python
class IdempotencyStore:
    """Redis-based idempotency with automatic cleanup."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.ttl = 86400  # 24 hours

    def _build_key(self, plan_id: str, step: int, args: Dict[str, Any]) -> str:
        """Generate deterministic idempotency key."""
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True).encode()
        ).hexdigest()[:16]

        return f"idempotency:{plan_id}:{step}:{args_hash}"

    async def check_and_store(
        self,
        plan_id: str,
        step: int,
        args: Dict[str, Any],
        result: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Check idempotency and store result atomically."""
        key = self._build_key(plan_id, step, args)

        # Try to get existing result
        cached = await self.redis.get(key)
        if cached:
            return json.loads(cached)

        # Store new result with TTL
        await self.redis.setex(
            key,
            self.ttl,
            json.dumps(result)
        )

        return None

# Usage in executor
async def execute_step(step: Step, plan_id: str) -> Result:
    """Execute step with idempotency."""

    # Check if already executed
    cached_result = await idempotency_store.check_and_store(
        plan_id=plan_id,
        step=step.step,
        args=step.args,
        result=None  # Check only
    )

    if cached_result:
        logger.info(f"Step {step.step} already executed, returning cached result")
        return Result(**cached_result)

    # Execute step with resource lock
    async with resource_lock(redis, step.uses, step.get_entity_id()):
        result = await provider.execute(step)

        # Store result for future idempotency
        await idempotency_store.check_and_store(
            plan_id=plan_id,
            step=step.step,
            args=step.args,
            result=result.dict()
        )

        return result
```

### Optimistic Locking for Read-Modify-Write

Use database versioning for conflict detection:

```python
from sqlalchemy import Column, Integer, select, update
from sqlalchemy.exc import StaleDataError

class ProfileTable(Base):
    __tablename__ = "profiles"

    profile_id = Column(UUID, primary_key=True)
    user_id = Column(UUID, nullable=False)
    preferences = Column(JSONB, nullable=False, default={})
    version = Column(Integer, nullable=False, default=1)  # Optimistic lock

async def update_preference_optimistic(
    db: AsyncSession,
    user_id: UUID,
    key: str,
    value: Any,
    max_retries: int = 3
) -> Profile:
    """Update preference with optimistic locking and retry."""

    for attempt in range(max_retries):
        # Read current version
        result = await db.execute(
            select(ProfileTable)
            .where(ProfileTable.user_id == user_id)
        )
        profile = result.scalar_one()

        # Modify locally
        new_prefs = {**profile.preferences, key: value}
        current_version = profile.version

        # Write with version check
        stmt = (
            update(ProfileTable)
            .where(
                ProfileTable.user_id == user_id,
                ProfileTable.version == current_version  # Check version
            )
            .values(
                preferences=new_prefs,
                version=current_version + 1  # Increment version
            )
            .returning(ProfileTable)
        )

        result = await db.execute(stmt)
        updated = result.scalar_one_or_none()

        if updated:
            await db.commit()
            return Profile.from_orm(updated)

        # Version mismatch - retry
        logger.warning(f"Version conflict on attempt {attempt + 1}, retrying")
        await asyncio.sleep(0.1 * (2 ** attempt))  # Exponential backoff

    raise ConcurrencyError("Failed to update after max retries")
```

**When to use each pattern**:
- **Distributed locks**: External API calls (calendar, email) where conflicts must be prevented
- **Optimistic locking**: Database updates with low contention (user preferences, profile)
- **Pessimistic locking**: High-contention resources (global counters, rate limits)

### Deadlock Prevention

Enforce consistent lock ordering to prevent deadlocks:

```python
class LockManager:
    """Manages multiple locks with consistent ordering."""

    def __init__(self, redis: Redis):
        self.redis = redis

    @asynccontextmanager
    async def acquire_multiple(self, resources: List[str]):
        """Acquire multiple locks in deterministic order."""
        # Sort resources alphabetically to ensure consistent ordering
        sorted_resources = sorted(resources)

        locks = [
            DistributedLock(self.redis, lock_key=res, ttl=30)
            for res in sorted_resources
        ]

        # Acquire in order
        acquired = []
        try:
            for lock in locks:
                if not await lock.acquire(timeout=10):
                    raise LockTimeoutError(f"Could not acquire lock: {lock.lock_key}")
                acquired.append(lock)

            yield

        finally:
            # Release in reverse order
            for lock in reversed(acquired):
                await lock.release()

# Usage: Multi-resource operation
async def book_meeting_with_multiple_attendees(
    attendees: List[str],
    time_slot: str
):
    """Book meeting for multiple attendees (deadlock-safe)."""

    # Acquire locks in consistent order (alphabetical)
    resources = [f"calendar.{email}.write" for email in attendees]

    async with lock_manager.acquire_multiple(resources):
        # All locks acquired - safe to proceed
        for email in attendees:
            await create_event(attendee=email, time_slot=time_slot)
```

---

## 16) What's Next?

After reading this HLD, you should:

1. **Understand the architecture**: Preview-first, pure agentic execution, policy-bounded
2. **Know the 16 components**: Memory, Domain (including PolicyEngine), Orchestration, Utilities layers
3. **See the flow**: Intent → Plan → Preview → Approve → Execute (with LLM reasoning + PolicyEngine + MCP) → Learn
4. **Understand safety**: PolicyEngine governance, credential vault, two-tier LLM execution, idempotency, compensation, resource locking, privacy tiers

### For Developers:
1. Read [GLOBAL_SPEC.md](GLOBAL_SPEC.md) for universal contracts
2. Read [MODULAR_ARCHITECTURE.md](MODULAR_ARCHITECTURE.md) for layer details
3. Pick a component to implement
4. Create `components/<Name>/SPEC.md` declaring conformance
5. Design `components/<Name>/LLD.md` with implementation details
6. Implement with tests until CI passes

### For Product/Stakeholders:
1. See Section 2 for complete end-to-end example
2. See Section 6 for multi-gate approval example
3. See Section 8 for long-running task example
4. Trust that preview-first safety prevents unwanted actions

---

## Related Architecture Documentation

- **[GLOBAL_SPEC.md](GLOBAL_SPEC.md)** - Universal contracts and data envelopes
- **[MODULAR_ARCHITECTURE.md](MODULAR_ARCHITECTURE.md)** - Component patterns and fault isolation
- **[Architecture Decision Records (ADRs)](adr/)** - Documented architectural decisions and their rationale

---

**Document Version**: Project_HLD v6.1
**Last Updated**: 2026-03-31
**Changes from v6.0**: **Clarified execution model.** (1) Reworded Core Idea #2: "deterministic graphs with adaptive execution points" — graph topology never changes at runtime, only Reasoner steps introduce variability. (2) Added "Execution Model: Deterministic Graph, Adaptive Execution" section with three patterns: Pure API, Adaptive with Reasoner, Failure Recovery. (3) Added "Data Trust Boundary" section: default-untrusted rule for API outputs, trust classification table, Tier 1→Tier 2 data flow diagram, plan validator enforcement. (4) Annotated §2a meeting example as pure API plan (all type:api, template resolution, no execution-time LLM). (5) Rewrote §2b travel example with explicit Tier 1 sanitization step between API outputs and Tier 2 Reasoners, trust_level annotations on each step, injection stripping example. (6) Added §2c Failure Recovery Example: step failure → error object (system-generated, trusted) → Tier 2 Reasoner → spawned recovery step → Tier 1 sanitization → continuation. (7) Renamed "Hybrid Planning" → "Deterministic Planning with Adaptive Execution" in §5 with clearer graph-vs-execution distinction. (8) Cross-referenced default-untrusted rule in GLOBAL_SPEC.md §8.2.
**Changes from v5.1**: **Pure Agentic Execution + MCP + Security Model.** (1) Dropped n8n — all execution via Python/FastAPI ExecuteOrchestrator with MCP tool invocations. (2) WorkflowBuilder absorbed into ExecuteOrchestrator (17→16 components). (3) Replaced n8n Secrets Vault with AES-256-GCM encrypted credential vault. (4) Added two-tier LLM execution (trust_level field) for prompt injection defense. (5) MCP connector model replaces n8n proprietary nodes. (6) Long-running tasks use APScheduler + Redis instead of n8n Wait nodes. (7) Parallelism via asyncio.gather() instead of n8n Split/Merge. (8) NemoClaw deployment compatibility for infrastructure-level security.
**Changes from v5.0**: **VectorIndex + Hybrid Execution Split.** (1) VectorIndex un-deferred — now active with hybrid BM25 + semantic search, ONNX Runtime, pgvector (§1, §3, §10, §12). 17 Active Components. (2) Hybrid execution split: n8n handles API steps, Python/FastAPI handles LLM reasoning steps. Removed custom n8n nodes (LLM Reasoning Node, Policy Check Node). Updated Core Idea #3, Orchestration Layer (§1), WorkflowBuilder (§3), Reasoner role (§4), execution timeline (§2b), credential isolation (§5), tech stack (§7), and WorkflowBuilder code (§13). (3) New architectural decision: "Hybrid Execution Split" with rationale and trade-offs (§12). (4) Updated ContextRAG description to include optional VectorIndex hybrid search with graceful degradation.
**Changes from v4.6**: **Hybrid Execution Model** (major version bump — changes fundamental system property). (1) Updated subtitle to "Hybrid planning · Policy-bounded execution". (2) Updated Core Idea #2: plans are deterministic strategies that adapt at runtime via LLM reasoning. (3) Added Core Idea #4: PolicyEngine is the safety moat. (4) Added PolicyEngine to Domain Layer (§1), with description and technology notes. (5) Updated Orchestration Layer (§1) with custom n8n nodes (LLM Reasoning Node, Policy Check Node). (6) Added §2b: Travel Planning Example demonstrating hybrid execution with spawned steps and policy checks. (7) Updated Planner component (§3) with hybrid plans, PolicyEngine integration, Planner vs Runtime LLM comparison. (8) Added PolicyEngine component (§3) with responsibilities, default policies, technology. (9) Updated WorkflowBuilder (§3) with three node types and sub-workflow pattern for spawned steps. (10) Added Reasoner as 7th runtime agent role (§4) with full policy metadata and spawning constraints. (11) Renamed "Deterministic Planning" → "Hybrid Planning" in §5 with runtime adaptation description. (12) Added "Policy-Bounded Execution" subsection (§5). (13) Added "LLM-Aware Recovery (Adaptive Retry)" to retry strategy (§5). (14) Updated component count to 17 (§10). (15) Added "Hybrid Execution Model" and "Policy Attestation vs Re-Signing" architectural decisions (§12). (16) Updated Reasoner role in validation code (§14). (17) Simplified retry/recovery model: LLM reasoning handles step failures inline (no workflow-level replay), ExecutionMonitor reduced to infrastructure monitoring, failed plans are terminal (user starts fresh).
**Changes from v4.5**: (1) Added explicit Deployment Model section: self-hosted, single-tenant, multi-user. (2) Removed `tenant_id` from idempotency key structure and examples — system scopes by `user_id:integration_account_id` only. (3) Aligned with GLOBAL_SPEC v2.2.
**Changes from v4.4**: **MVP scope clarification for multi-user single-tenant deployment.** Major architectural updates: (1) Reframed runtime roles as logical plan-step categories (§4) - all execution happens via MCP tool invocations, not separate services. (2) Multi-user safe idempotency (§5) - 3-state records (IN_FLIGHT/SUCCEEDED/FAILED) with scoping by user/integration account, atomic claim pattern prevents duplicate operations on execution retry. (3) Dual retry strategy (§5) - step-level retries (RetryPolicy) + LLM-adaptive recovery (Reasoner steps). (4) Added ExecutionMonitor component (§3, §8, §10, §13) - polls task registry every 30s, detects stuck executions (5min timeout), enforces time budgets (60min). (5) Removed workspace_id from idempotency and locking keys (no workspace concept in project). (6) Updated §13 - replaced task queue pattern with ExecutionMonitor pattern, clarified parallel execution via asyncio.gather(), single Planner instance uses async/await for I/O concurrency (no threading/multiprocessing needed).
