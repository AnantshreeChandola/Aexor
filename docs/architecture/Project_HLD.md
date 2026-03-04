# Personal Agent — High-Level Design (HLD) v4.0
_Preview-first • Human-approved • Deterministic planning • Multi-agent execution_

**Purpose:** System architecture overview with clear component responsibilities and real-world examples.
**Audience:** Developers, architects, and stakeholders.

---

## Architecture Overview

```
User Request → Preview → Approval → Execute → Learn
     ↓           ↓          ↓          ↓        ↓
  [Intent]   [Show Me]  [Confirm]  [Do It]  [Remember]
```

### Core Idea
1. **Never do anything without showing the user first** (Preview-first safety)
2. **Plans are deterministic and signed** (Same inputs → same plan → same signature)
3. **Single execution runtime** optimized for personal agent scale:
   - **n8n**: All workflows (short and long-running) with built-in persistence and retry logic

### Key Innovation
**Preview State Caching**: User choices made during preview are reused during execution—no need to repeat steps.

**Example**: Shopping flow
- Preview: Search 10 sweaters → User picks one
- Execute: Only buy that sweater (skip the search)

---

## 1) System Layers

The system has **4 layers** that work together:

### Layer 1: Memory & Persistence
**What it does**: Stores everything the system knows
- **ProfileStore**: Your stable preferences (work hours, meeting duration)
- **History**: What you've done before ("usually meets Alice on Tuesdays")
- **PlanLibrary**: Reusable successful plans
- **VectorIndex** _(deferred)_: Semantic similarity search — deferred until exact-match queries prove insufficient (see §11 Architectural Decisions)

**Example**: When you say "book a meeting," the system remembers you prefer 30-minute meetings at 10 AM.

### Layer 2: Domain Services
**What it does**: Understands your request and builds a plan
- **Intake**: Figures out what you want across multiple messages
- **ContextRAG**: Assembles relevant context from Memory Layer via structured queries (≤2KB budget, consent tier enforcement) — not embedding-based RAG
- **Planner**: Creates a step-by-step plan (deterministic, signed)
- **PluginRegistry**: Knows what tools are available (Google Calendar, Slack, etc.)

**Example**: "Book meeting with Alice" → Intent + Context → Plan with 4 steps

### Layer 3: Orchestration
**What it does**: Previews and executes plans safely
- **PreviewOrchestrator**: Shows you what will happen (no side effects)
- **ApprovalGate**: Waits for your confirmation
- **ExecuteOrchestrator**: Does the actual work (n8n workflows for all task types)

**Example**: Shows you 3 time slots → You pick one → Creates the calendar event

### Layer 4: API & Frontend
**What it does**: Your interface to the system
- FastAPI endpoints for all interactions
- React/Next.js UI for approvals and previews

---

## 2) How It Works: Complete Example

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

### Step 2: Planning (Planner + Signer)
```
Planner receives:
  - Intent: "schedule_meeting with Alice next week"
  - Evidence: [30min preference, Tuesday pattern, Alice's email]
  - Available tools: Google Calendar, Slack

Creates Plan:
  Step 1 (Fetcher): Get Alice's availability  [parallel]
  Step 2 (Fetcher): Get your availability      [parallel]
  Step 3 (Analyzer): Find overlapping slots   [after 1,2]
  Step 4 (Resolver): User picks slot          [gate-A]
  Step 5 (Booker): Create calendar event      [after 4]
  Step 6 (Notifier): Send confirmation        [after 5]

Signer: Signs plan with Ed25519
  → Plan hash: "sha256:abc123..."
  → Signature: "base64:xyz..."
```

### Step 3: Preview (PreviewOrchestrator)
```
PreviewOrchestrator:
  ✓ Verifies plan signature
  ✓ Runs steps 1-3 in READ-ONLY mode

n8n workflow executes:
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
  ✓ Verifies signature
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
    - Plan + signature + outcome (success)
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

## 3) Component Details

Below are the 16 core components organized by layer. Each will have its own `SPEC.md` and `LLD.md` during implementation.

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
**What it does**: Finds similar past situations by semantic meaning
**Example query**: "Find times I've booked meetings with executives"
**Technology**: PostgreSQL with pgvector extension (HNSW index)

#### PlanLibrary
**What it does**: Stores all past plans with signatures and outcomes
**Example data**:
- Plan: "schedule_meeting" → Success (event_id: gcal_123)
- Plan: "book_flight" → Failed (card declined)

**Technology**: PostgreSQL (plans table, indexed by intent type and success)


---

### Domain Layer (6 components)

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
**What it does**: Creates a deterministic step-by-step plan
**Input**: Intent + Evidence + Available tools
**Process**: Calls Claude API (temperature=0) to generate plan
**Output**: Plan graph with steps, dependencies, and roles

**Key feature**: Same inputs always produce same plan (deterministic)

#### Signer
**What it does**: Cryptographically signs plans to prevent tampering
**Process**:
1. Canonicalize plan JSON (sort keys, remove whitespace)
2. Hash with SHA-256
3. Sign with Ed25519 private key

**Verification**: PreviewOrchestrator and ExecuteOrchestrator verify signature before execution

#### PluginRegistry
**What it does**: Source of truth for what tools are available
**Example entry**:
```json
{
  "tool_id": "google.calendar",
  "operations": {
    "list_free_busy": {
      "n8n_node": "Google Calendar",
      "previewable": true,
      "scopes": ["calendar.read"],
      "idempotent": true
    },
    "create_event": {
      "n8n_node": "Google Calendar",
      "previewable": false,
      "scopes": ["calendar.write"],
      "idempotent": true,
      "compensation": "delete_event"
    }
  }
}
```

**Why important**: Adding new capabilities only requires editing the Registry, not the orchestrators.

#### PlanWriter
**What it does**: Persists execution results back to memory
**Process**:
1. Receives Execute wrappers (outcomes)
2. Writes to Plan Library (plan + outcome)
3. Writes to History (derived facts)
4. Triggers vector re-indexing

**Example**: "Meeting booked" → History + Plan Library + Vector embedding

---

### Orchestration Layer (5 components)

#### WorkflowBuilder
**What it does**: Converts plan dependency graph → n8n workflow JSON
**Input**: Plan + mode ("preview" or "execute")
**Output**: n8n workflow with parallel execution structure

**Example**:
```
Plan steps:
  Step 1: Fetch Alice's calendar [after: []]
  Step 2: Fetch your calendar   [after: []]
  Step 3: Find overlap          [after: [1, 2]]

n8n workflow:
  Split → [Step 1 || Step 2] → Merge → Step 3
```

**Modes**:
- `preview`: Only dry_run steps, read-only operations
- `execute`: All steps, with idempotency and compensation

#### PreviewOrchestrator
**What it does**: Shows you what will happen (no side effects!)
**Process**:
1. Verifies plan signature
2. Calls WorkflowBuilder with mode="preview"
3. Executes n8n workflow (read-only)
4. Returns Preview wrapper with results

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
**What it does**: Does the actual work (writes to external systems)
**Process**:
1. Verifies signature + approval token
2. **Retrieves cached preview state** (skip repeated steps!)
3. Calls WorkflowBuilder with mode="execute"
4. Executes n8n workflow with:
   - Idempotency checks (plan_id:step:arg_hash)
   - Resource locking (prevent conflicts)
   - Compensation on failure (undo operations)
5. Returns Execute wrappers

**Preview state reuse**:
- Steps marked `execute_mode: "preview_only"` are skipped
- Template args resolved from cached state
- Example: `product_id: "{{preview.cached_state.selected_product}}"`

#### ExecutionMonitor
**What it does**: Monitors n8n workflow executions and triggers retries for stuck/failed workflows

**Responsibilities**:
1. **Poll n8n API** every 30 seconds for active executions
2. **Detect stuck executions**: No progress for 5+ minutes → mark as stale, trigger retry
3. **Detect failed executions**: Apply retry policy with attempt caps (max 3 attempts)
4. **Enforce time budgets**: Cancel workflows exceeding max execution time
5. **Notify users**: Alert on terminal failures (max retries exhausted)
6. **Track execution state**: Maintain execution_tracker table (plan_id, n8n_execution_id, status, attempt_count)

**Why needed**:
- n8n may not automatically recover stuck executions after restart
- Workflow-level retry requires external trigger (n8n doesn't auto-retry workflows)
- Centralized retry policy enforcement across all workflows

**Process**:
```python
async def monitor_loop():
    while True:
        # 1. Query n8n for active executions
        executions = await n8n_client.get_active_executions()

        for execution in executions:
            # 2. Check for stuck execution (no progress for 5min)
            if is_stuck(execution, timeout_minutes=5):
                await handle_stuck_execution(execution)

            # 3. Check for failed execution needing retry
            if execution.status == "failed":
                tracker = await get_execution_tracker(execution.id)
                if tracker.attempt_count < 3:
                    await retry_workflow(execution, tracker)
                else:
                    await notify_user_terminal_failure(execution)

            # 4. Enforce time budget (cancel if exceeded)
            if is_over_time_budget(execution, max_minutes=60):
                await n8n_client.cancel_execution(execution.id)
                await notify_user_timeout(execution)

        await asyncio.sleep(30)  # Poll every 30 seconds
```

**Retry policy** (workflow-level):
```python
retry_backoff = [60, 300, 900]  # 1min, 5min, 15min (exponential)

async def retry_workflow(execution, tracker):
    attempt = tracker.attempt_count

    if attempt < 3:
        # Wait with exponential backoff
        await asyncio.sleep(retry_backoff[attempt])

        # Trigger new execution with same input
        await n8n_client.execute_workflow(
            workflow_id=execution.workflow_id,
            input_data=execution.input_data
        )

        # Update tracker
        await update_tracker(execution.id, attempt_count=attempt + 1)
```

**Technology**: FastAPI background task + n8n REST API

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

**All execution happens inside n8n.** Roles serve as metadata for policies and safety rules.

### Purpose of Roles

Roles are assigned to plan steps during planning and used by WorkflowBuilder to determine:
- **Idempotency requirement**: Does this step need idempotency keys? (Booker: yes, Fetcher: no)
- **HITL requirement**: Does this step need human approval? (Resolver: yes, Analyzer: no)
- **Retry policy**: How should failures be handled? (Watcher: aggressive retries, Notifier: best-effort)
- **Compensation requirement**: Does this step need undo logic? (Booker: yes, Fetcher: no)
- **Resource locking**: Does this step need locks? (Booker: yes, Analyzer: no)

### The 6 Roles

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

**n8n Implementation**: HTTP Request nodes, connector nodes in read mode

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

**n8n Implementation**: Function nodes, Code nodes (JavaScript/Python)

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

**n8n Implementation**: Loop workflows with Wait nodes, scheduled triggers, webhook listeners

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

**n8n Implementation**: Wait nodes with webhook resume, approval flows

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

**n8n Implementation**: Connector nodes (Google Calendar, Slack, etc.) with idempotency wrapper

**Critical Requirement**: WorkflowBuilder MUST inject idempotency checks before Booker nodes

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

**n8n Implementation**: Slack nodes, email nodes, webhook notifications

### How n8n Executes Steps

**All steps execute as n8n workflow nodes.** WorkflowBuilder generates n8n workflow JSON where:
- Each plan step → one or more n8n nodes
- Dependencies → n8n connections between nodes
- Parallel steps → Split/Merge nodes
- HITL gates → Wait nodes with webhooks

**Parallel execution** (steps with no dependencies):
```
Plan:
  Step 1 (Fetcher): Get Alice's calendar  [after: []]
  Step 2 (Fetcher): Get Bob's calendar    [after: []]

n8n Workflow:
  Split → [HTTP Request: Alice || HTTP Request: Bob] → Merge
```

**Sequential execution** (steps with dependencies):
```
Plan:
  Step 3 (Analyzer): Find overlap  [after: [1, 2]]

n8n Workflow:
  Merge (from 1 & 2) → Function: Find Overlap
```

**Booker with idempotency** (side-effecting steps):
```
Plan:
  Step 4 (Booker): Create calendar event  [after: [3]]

n8n Workflow:
  1. HTTP Request: Check idempotency key (Redis GET)
  2. IF: Already executed?
     - Yes → Return cached result
     - No → Continue
  3. Google Calendar: Create Event
  4. HTTP Request: Store idempotency result (Redis SET)
```

**Real execution timeline** (meeting booking example):
- t=0ms: n8n starts workflow execution
- t=0ms: Steps 1 & 2 execute in parallel (Split node)
- t=200ms: Both Fetcher steps complete
- t=201ms: Merge node combines results
- t=202ms: Step 3 (Analyzer) executes
- t=350ms: Step 3 completes
- t=351ms: Step 4 (Booker) checks idempotency → not found → executes
- t=580ms: Step 4 completes, stores result
- t=581ms: Workflow finishes

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

### Deterministic Planning
**Guarantee**: Same inputs always produce the same plan

**Inputs** (frozen tuple):
- Intent (finalized user request)
- Evidence (context from ContextRAG, ≤2KB)
- Registry (available tools snapshot)
- Policy (GLOBAL_SPEC version)

**Process**:
1. Planner calls Claude API with temperature=0
2. Canonicalize plan JSON (sort keys, deterministic serialization)
3. Sign with Ed25519 (cryptographic signature)
4. Hash: SHA-256 of canonical plan bytes

**Benefits**:
- Same request tomorrow = same plan
- Tamper detection (signature verification)
- Auditability (reproducible plans)

### Retry Strategy (Node-Level + Workflow-Level)

**MVP supports two retry mechanisms:**

#### A) Node-Level Retries (Transient Failures)
For individual step failures (network timeouts, rate limits, temporary API errors):

```yaml
# n8n node configuration (generated by WorkflowBuilder)
node:
  retry_on_fail: true
  max_retries: 3
  wait_between: 1000  # Linear backoff (1 second)
```

**When to use**: Transient failures (503 errors, timeouts, connection resets)

**Limitations**: n8n only supports linear backoff. For exponential backoff, WorkflowBuilder generates custom retry loops with IF/Wait nodes.

#### B) Workflow-Level Retries (Execution Failures)
For entire workflow failures (unhandled errors, node crashes, n8n restarts):

**Trigger mechanisms**:
1. **n8n error workflow**: Catches workflow failures and logs to execution_tracker table
2. **ExecutionMonitor**: Background service polls n8n API every 30 seconds, detects stuck/failed executions

**Retry policy**:
```python
max_attempts = 3
backoff_strategy = [60, 300, 900]  # seconds (1min, 5min, 15min)

if attempt_count < max_attempts:
    await asyncio.sleep(backoff_strategy[attempt_count - 1])
    await n8n_client.trigger_workflow(workflow_id, input_data)
else:
    await notify_user("Workflow failed after 3 attempts")
```

**Critical requirement**: Workflow-level retry starts from the beginning → **MUST have idempotency** to prevent duplicate side effects.

### Idempotency (Multi-User Safe, No Duplicate Operations)

**Problem**:
1. Network fails after creating a calendar event → Retry would create duplicates
2. Multiple users run similar workflows → Must not collide on idempotency keys
3. Workflow retry starts from beginning → Must skip already-executed side effects

**Solution**: 3-state idempotency records with multi-user scoping

#### Idempotency Key Structure

**CRITICAL**: Keys MUST include multi-user scope to prevent cross-user collisions:

```
idem:{tenant_id}:{user_id}:{integration_account_id}:{plan_execution_id}:{step_id}:{operation}:{input_hash}
```

**Example**:
```
idem:tenant-1:user-123:gcal-acct-xyz:plan-01HX:5:create_event:hash-a1b2
```

**Why each component matters**:
- `tenant_id`: Deployment isolation (MVP: single tenant, but architecture supports multi-tenant)
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
  "owner_execution_id": "n8n-exec-12345",  # Which n8n execution owns this
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

**How WorkflowBuilder injects idempotency** (n8n workflow nodes):

```yaml
# For each Booker step, generate 4 nodes:

nodes:
  # 1. Check idempotency state
  - id: "idem_check_step_5"
    type: "HTTP Request"
    url: "{{$env.REDIS_API}}/idempotency/{{$json.idem_key}}"
    method: "GET"

  # 2. Conditional execution based on state
  - id: "should_execute_step_5"
    type: "IF"
    conditions:
      - "={{$node.idem_check_step_5.json.state !== 'SUCCEEDED'}}"

  # 3. Main operation (only if not already succeeded)
  - id: "step_5_create_event"
    type: "Google Calendar"
    operation: "createEvent"
    # ... parameters

  # 4. Store result with SUCCEEDED state
  - id: "idem_store_step_5"
    type: "HTTP Request"
    url: "{{$env.REDIS_API}}/idempotency/{{$json.idem_key}}"
    method: "POST"
    body:
      state: "SUCCEEDED"
      result: "={{$node.step_5_create_event.json}}"
```

**Benefits**:
- ✅ Safe workflow-level retry (skips already-executed steps)
- ✅ Multi-user safe (keys scoped by workspace/user/integration)
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
- **Orchestration**: n8n (all workflows with built-in persistence)
- **Data**: PostgreSQL 16 + pgvector, Redis 7
- **AI**: Anthropic Claude (planning), OpenAI (embeddings only)
- **Testing**: pytest, ruff, mypy
- **Infra**: Docker, GitHub Actions

**Key architectural decisions**:
- **No LangChain**: Direct API calls for one-shot planning (not iterative agents)
- **Single runtime**: n8n for all workflows with built-in persistence and scheduling
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

### Why n8n?
- Built-in persistence and state management
- Survives server restarts
- Native scheduling and retry capabilities
- Visual workflow management and debugging
- Webhook triggers for user interactions

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

**n8n Workflow**:
```yaml
workflow: "visa_slot_monitor"
trigger:
  type: "manual"
  
nodes:
  - name: "start_monitoring"
    type: "function"
    code: |
      const startTime = new Date();
      const durationDays = {{$json.duration_days}};
      const maxDuration = durationDays * 24 * 60 * 60 * 1000;
      
      return {
        startTime,
        maxDuration,
        location: {{$json.location}},
        userId: {{$json.user_id}},
        planId: {{$json.plan_id}}
      };
    
  - name: "check_visa_slots"
    type: "http_request"
    url: "{{embassy_api}}/slots"
    retry_on_fail: true
    max_retries: 3
    backoff_strategy: "exponential"
    
  - name: "slots_available_check"
    type: "if"
    condition: "{{$node.check_visa_slots.json.available_slots.length > 0}}"
    
  - name: "notify_user_slots_found"
    type: "webhook"
    url: "{{approval_gate_url}}/visa-slots-found"
    method: "POST"
    body: |
      {
        "user_id": "{{$node.start_monitoring.json.userId}}",
        "plan_id": "{{$node.start_monitoring.json.planId}}",
        "slot_date": "{{$node.check_visa_slots.json.available_slots[0].date}}",
        "slot_id": "{{$node.check_visa_slots.json.available_slots[0].id}}"
      }
    
  - name: "wait_for_approval"
    type: "wait_for_webhook"
    webhook_path: "/visa-approval/{{$node.start_monitoring.json.planId}}"
    timeout: 86400  # 24 hours
    
  - name: "book_approved_slot"
    type: "http_request"
    condition: "{{$node.wait_for_approval.json.approved === true}}"
    url: "{{embassy_api}}/book"
    method: "POST"
    body: |
      {
        "slot_id": "{{$node.notify_user_slots_found.json.slot_id}}"
      }
    
  - name: "notify_booking_success"
    type: "webhook"
    url: "{{notification_service}}/send"
    method: "POST"
    body: |
      {
        "user_id": "{{$node.start_monitoring.json.userId}}",
        "message": "✓ Visa appointment booked successfully!"
      }
    
  - name: "check_time_elapsed"
    type: "function"
    code: |
      const startTime = new Date({{$node.start_monitoring.json.startTime}});
      const now = new Date();
      const elapsed = now - startTime;
      const maxDuration = {{$node.start_monitoring.json.maxDuration}};
      
      return {
        shouldContinue: elapsed < maxDuration,
        elapsed,
        remaining: maxDuration - elapsed
      };
    
  - name: "wait_6_hours"
    type: "wait"
    amount: 6
    unit: "hours"
    condition: "{{$node.check_time_elapsed.json.shouldContinue === true}}"
    
  - name: "continue_monitoring"
    type: "set"
    connects_to: "check_visa_slots"
    condition: "{{$node.check_time_elapsed.json.shouldContinue === true}}"
    
  - name: "notify_monitoring_ended"
    type: "webhook"
    url: "{{notification_service}}/send"
    method: "POST"
    condition: "{{$node.check_time_elapsed.json.shouldContinue === false}}"
    body: |
      {
        "user_id": "{{$node.start_monitoring.json.userId}}",
        "message": "Visa slot monitoring ended (14 days elapsed)"
      }
```

**Key Features**:
1. **Built-in persistence**: n8n automatically manages workflow state
2. **Visual debugging**: Monitor workflow execution in real-time
3. **Native scheduling**: Built-in wait nodes and cron triggers
4. **Webhook integration**: Seamless user approval flows
5. **Node-level retries**: Configurable retry with linear backoff
6. **Loop handling**: Workflow can loop back to previous nodes

### n8n Persistence & Recovery

**What n8n provides**:
- Stores workflow execution state in PostgreSQL database
- Waiting executions (Wait nodes) can survive n8n server restarts IF persistence is configured
- Each node execution result is saved to database (enables debugging and resume)

**What n8n does NOT provide**:
- ❌ Automatic workflow-level retry after failure
- ❌ Exponential backoff (only linear: 1s, 2s, 3s...)
- ❌ Stuck execution detection (workflow may hang indefinitely)
- ❌ Time budget enforcement (no automatic timeout/cancellation)

### ExecutionMonitor Role

**Why we need ExecutionMonitor** (even with n8n persistence):

1. **Detect stuck executions**: n8n may not detect workflows that hang (e.g., waiting for external webhook that never arrives)
2. **Trigger workflow-level retries**: n8n only retries individual nodes, not entire workflows
3. **Enforce time budgets**: Cancel workflows exceeding max execution time (prevent resource leaks)
4. **Apply retry policy**: Exponential backoff for workflow retries (1min, 5min, 15min)
5. **User notifications**: Alert users when workflows fail terminally

**How it works**:
```
ExecutionMonitor (polls every 30s)
  ↓
Query n8n API: /api/v1/executions?status=running
  ↓
Check each execution:
  - Stuck? (no progress for 5min) → Mark stale, trigger retry
  - Failed? (error status) → Apply retry policy (attempt 1/3)
  - Timeout? (exceeded 60min) → Cancel execution, notify user
  ↓
Update execution_tracker table (plan_id, attempt_count, status)
```

**Result**: Monitors visa slots 24/7 for 2 weeks with automatic recovery from stuck/failed executions

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

**15 Active Components** (VectorIndex deferred):
1. ProfileStore, History, PlanLibrary (Memory Layer) — VectorIndex deferred (§12)
2. Intake, ContextRAG, Planner, Signer, PluginRegistry, PlanWriter (Domain Layer)
3. WorkflowBuilder, PreviewOrchestrator, ApprovalGate, ExecuteOrchestrator, ExecutionMonitor (Orchestration Layer)
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

### VectorIndex Deferred

**Decision**: VectorIndex (semantic embedding search) is deferred until exact-match queries prove insufficient with real usage data.

**Rationale**:
- **All current queries are structured**: ContextRAG queries Memory Layer components by `intent_type`, `preference_key`, `user_id`, and `created_at` — all indexed PostgreSQL columns. No query requires fuzzy semantic matching.
- **ContextRAG is a context assembler, not RAG**: Despite the name, ContextRAG makes structured API calls to ProfileStore/History/PlanLibrary and assembles Evidence Items with budget management (≤2KB) and consent tier enforcement. It does not perform embedding-based retrieval.
- **Embedding latency violates NFRs**: An OpenAI embedding call (~200-500ms) would blow the ContextRAG <150ms p95 latency budget.
- **Scale doesn't justify it**: A personal agent has ~20-50 intent types and hundreds of plans. At this scale, exact-match by `intent_type` covers the real need. Semantic search adds value at thousands of diverse records.
- **Architecture slot preserved**: pgvector is in the stack. When exact-match returns empty for novel intents frequently enough to hurt plan quality, VectorIndex can be added as a small lift — not a rewrite.

**Trigger to revisit**: ContextRAG returns empty results for >10% of novel intent queries in production.

### PlanLibrary Has No Embedding Dependencies

**Decision**: PlanLibrary stores and retrieves plans via structured queries only. Embedding generation, storage, and similarity search are removed from PlanLibrary scope.

**Rationale**: Embedding is VectorIndex's responsibility per the MODULAR_ARCHITECTURE separation. PlanLibrary is a foundation Memory Layer component — it provides CRUD operations for plan data. If semantic search is needed later, VectorIndex indexes PlanLibrary data externally (PlanWriter triggers re-indexing).

---

## 13) Asynchronous Execution Architecture

**MVP execution model**: n8n handles all workflow execution. ExecutionMonitor provides reliability layer for stuck/failed workflow detection and retry triggering.

### ExecutionMonitor Pattern

**Purpose**: Detect stuck/failed n8n workflow executions and trigger workflow-level retries with exponential backoff.

**Why needed**:
- n8n executes workflows asynchronously (no blocking wait)
- n8n may not detect stuck executions (waiting for external event that never arrives)
- Workflow-level retry requires external trigger (n8n doesn't auto-retry workflows)

**Implementation**:

```python
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict

class ExecutionMonitor:
    """Monitors n8n executions and triggers retries for stuck/failed workflows."""

    def __init__(self, n8n_client, db_adapter, poll_interval_seconds: int = 30):
        self.n8n_client = n8n_client
        self.db = db_adapter
        self.poll_interval = poll_interval_seconds
        self.max_attempts = 3
        self.retry_backoff = [60, 300, 900]  # 1min, 5min, 15min (exponential)

    async def run(self):
        """Background polling loop."""
        while True:
            try:
                await self._check_active_executions()
            except Exception as e:
                logger.error(f"ExecutionMonitor error: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _check_active_executions(self):
        """Poll n8n API for active/recent executions."""
        # 1. Query n8n for active executions
        active_executions = await self.n8n_client.get_executions(
            status="running",
            limit=100
        )

        # 2. Query our execution_tracker for known executions
        tracked = await self.db.get_tracked_executions(status="running")
        tracked_map = {t.n8n_execution_id: t for t in tracked}

        # 3. Check each n8n execution
        for execution in active_executions:
            tracker = tracked_map.get(execution.id)

            if not tracker:
                # New execution - track it
                await self.db.create_execution_tracker(
                    plan_id=execution.metadata["plan_id"],
                    n8n_execution_id=execution.id,
                    status="running",
                    attempt_count=1
                )
                continue

            # Check for stuck execution (no progress for 5min)
            if self._is_stuck(execution, timeout_minutes=5):
                await self._handle_stuck_execution(execution, tracker)

            # Check for timeout (exceeded max execution time)
            if self._is_over_time_budget(execution, max_minutes=60):
                await self._handle_timeout(execution, tracker)

        # 4. Query n8n for recently failed executions
        failed_executions = await self.n8n_client.get_executions(
            status="error",
            limit=50
        )

        for execution in failed_executions:
            tracker = await self.db.get_tracker_by_n8n_id(execution.id)
            if tracker and tracker.status != "failed":
                await self._handle_failed_execution(execution, tracker)

    def _is_stuck(self, execution, timeout_minutes: int) -> bool:
        """Check if execution has made no progress for timeout period."""
        last_update = datetime.fromisoformat(execution.stoppedAt or execution.startedAt)
        age = (datetime.now(timezone.utc) - last_update).total_seconds() / 60
        return age > timeout_minutes and execution.status == "running"

    def _is_over_time_budget(self, execution, max_minutes: int) -> bool:
        """Check if execution exceeded max allowed time."""
        started_at = datetime.fromisoformat(execution.startedAt)
        age = (datetime.now(timezone.utc) - started_at).total_seconds() / 60
        return age > max_minutes

    async def _handle_stuck_execution(self, execution, tracker):
        """Mark stuck execution as stale and trigger retry."""
        logger.warning(f"Stuck execution detected: {execution.id}")

        # Cancel stuck execution in n8n
        await self.n8n_client.cancel_execution(execution.id)

        # Mark as failed in tracker
        await self.db.update_tracker(tracker.id, status="failed")

        # Trigger retry if under attempt cap
        await self._try_retry_workflow(tracker)

    async def _handle_timeout(self, execution, tracker):
        """Cancel execution that exceeded time budget."""
        logger.warning(f"Execution timeout: {execution.id} (>{tracker.time_budget_minutes}min)")

        await self.n8n_client.cancel_execution(execution.id)
        await self.db.update_tracker(tracker.id, status="timeout")
        await self._notify_user(tracker.plan_id, "Execution timed out")

    async def _handle_failed_execution(self, execution, tracker):
        """Handle workflow execution failure."""
        logger.error(f"Execution failed: {execution.id}")

        await self.db.update_tracker(tracker.id, status="failed")
        await self._try_retry_workflow(tracker)

    async def _try_retry_workflow(self, tracker):
        """Apply retry policy with exponential backoff."""
        if tracker.attempt_count >= self.max_attempts:
            # Max retries exhausted - notify user
            logger.error(f"Max retries ({self.max_attempts}) exhausted for plan {tracker.plan_id}")
            await self.db.update_tracker(tracker.id, status="terminal_failure")
            await self._notify_user(tracker.plan_id, "Workflow failed after max retries")
            return

        # Apply exponential backoff
        backoff_seconds = self.retry_backoff[tracker.attempt_count - 1]
        logger.info(f"Retrying plan {tracker.plan_id} in {backoff_seconds}s (attempt {tracker.attempt_count + 1}/{self.max_attempts})")

        await asyncio.sleep(backoff_seconds)

        # Trigger new n8n workflow execution
        plan = await self.db.get_plan(tracker.plan_id)
        new_execution = await self.n8n_client.execute_workflow(
            workflow_id=tracker.workflow_id,
            input_data=plan.input_data
        )

        # Update tracker
        await self.db.update_tracker(
            tracker.id,
            n8n_execution_id=new_execution.id,
            status="running",
            attempt_count=tracker.attempt_count + 1
        )

    async def _notify_user(self, plan_id: str, message: str):
        """Send notification to user about execution status."""
        # Implementation: webhook/email/Slack notification
        pass
```

**Key features**:
- **Polling interval**: 30 seconds (configurable)
- **Stuck detection**: No progress for 5+ minutes
- **Timeout enforcement**: Cancel after 60 minutes
- **Workflow-level retry**: Exponential backoff (1min, 5min, 15min)
- **Attempt tracking**: Max 3 retries before terminal failure
- **User notifications**: Alert on timeout/terminal failure

### Parallel Step Execution (via n8n)

Steps with no dependencies execute in parallel **within n8n workflows**. The WorkflowBuilder analyzes the plan graph and generates n8n workflow JSON with parallel branches:

```python
class WorkflowBuilder:
    """Converts plan graph to n8n workflow with parallel execution."""

    def build(self, plan: Plan, mode: str) -> Dict[str, Any]:
        """Build n8n workflow from plan graph."""

        # Group steps by dependency level
        levels = self._group_by_dependency_level(plan.graph)

        nodes = []
        for level_idx, level_steps in enumerate(levels):
            if len(level_steps) > 1:
                # Multiple steps at same level → parallel branches
                nodes.append({
                    "name": f"split_level_{level_idx}",
                    "type": "SplitInBatches",
                    "parameters": {"batchSize": 1},
                    "typeVersion": 1
                })

                # Create parallel branches for each step
                for step in level_steps:
                    nodes.append(self._step_to_n8n_node(step, mode))

                # Merge results
                nodes.append({
                    "name": f"merge_level_{level_idx}",
                    "type": "Merge",
                    "parameters": {},
                    "typeVersion": 1
                })
            else:
                # Single step at this level → sequential
                nodes.append(self._step_to_n8n_node(level_steps[0], mode))

        return {
            "name": f"plan_{plan.plan_id}",
            "nodes": nodes,
            "connections": self._build_connections(nodes)
        }

    def _group_by_dependency_level(self, steps: List[Step]) -> List[List[Step]]:
        """Group steps by dependency depth for parallel execution."""
        levels = []
        processed = set()

        while len(processed) < len(steps):
            # Find steps whose dependencies are all processed
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

Generated n8n workflow:
  Split → [Fetch Alice || Fetch You] → Merge → Find Overlap
```

**Benefits**:
- **n8n handles parallelism**: Native parallel execution with visual monitoring
- **Dependency ordering preserved**: Level-by-level execution
- **Fault isolation**: n8n's built-in error handling and retry logic
- **No custom concurrency code**: Leverage n8n's mature orchestration

### Background Task Monitoring

Long-running n8n workflows are monitored via webhook callbacks:

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
        status = await n8n_client.get_execution_status(plan_id)

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
        valid_roles = {"Fetcher", "Analyzer", "Watcher", "Resolver", "Booker", "Notifier"}

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

        # Step count
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

1. **Understand the architecture**: Preview-first, deterministic planning, dual runtime
2. **Know the 16 components**: Memory, Domain, Orchestration, Utilities layers
3. **See the flow**: Intent → Plan → Preview → Approve → Execute → Learn
4. **Understand safety**: Idempotency, compensation, resource locking, privacy tiers

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

**Document Version**: HLD v4.5
**Last Updated**: 2026-03-03
**Changes from v4.4**: **MVP scope clarification for multi-user single-tenant deployment.** Major architectural updates: (1) Reframed runtime roles as logical plan-step categories (§4) - all execution happens in n8n, not separate services. (2) Multi-user safe idempotency (§5) - 3-state records (IN_FLIGHT/SUCCEEDED/FAILED) with scoping by tenant/user/integration account, atomic claim pattern prevents duplicate operations on workflow retry. (3) Dual retry strategy (§5) - node-level retries (n8n config) + workflow-level retries (ExecutionMonitor with exponential backoff: 60s, 300s, 900s). (4) Added ExecutionMonitor component (§3, §8, §10, §13) - polls n8n API every 30s, detects stuck executions (5min timeout), triggers workflow retries (max 3 attempts), enforces time budgets (60min). Replaced DurableOrchestrator with ExecutionMonitor. (5) Removed workspace_id from idempotency and locking keys (no workspace concept in project). (6) Clarified n8n persistence capabilities (§8) - stores execution state but doesn't auto-retry workflows or detect stuck executions. (7) Updated §13 - replaced task queue pattern with ExecutionMonitor pattern, clarified parallel execution happens in n8n (WorkflowBuilder generates Split/Merge nodes), single Planner instance uses async/await for I/O concurrency (no threading/multiprocessing needed).
