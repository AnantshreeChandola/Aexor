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

#### Long-Running Task Handling
**What it does**: Handles extended monitoring tasks (hours/days/weeks) using n8n
**Examples**:
- Monitor visa appointment slots for 2 weeks
- Watch flight prices for best deal
- Poll API every 6 hours for availability

**Technology**: n8n workflows with persistence
- Built-in workflow state management
- Wait/Schedule nodes for delays
- HTTP retry logic with exponential backoff
- Webhook triggers for external signals
- Visual debugging and monitoring

**Example n8n workflow**:
```yaml
workflow: "visa_slot_monitor"
trigger:
  type: "manual"
  
nodes:
  - name: "check_visa_slots"
    type: "http_request"
    url: "{{embassy_api}}/slots"
    retry_on_fail: true
    max_retries: 3
    
  - name: "slots_available_check"
    type: "if"
    condition: "{{$node.check_visa_slots.json.available_slots.length > 0}}"
    
  - name: "notify_user_slots_found"
    type: "webhook"
    url: "{{approval_gate_url}}/visa-slots-found"
    
  - name: "wait_6_hours"
    type: "wait"
    amount: 6
    unit: "hours"
    
  - name: "continue_monitoring"  # Loop back to check
    type: "set"
    connects_to: "check_visa_slots"
```

---

### Utilities (1 component)

#### Audit & Observability
**What it does**: Tracks everything for debugging and analytics
**Logs**: All steps with plan_id correlation (no secrets/PII)
**Metrics**: Latency (p95, p99), error rates, token usage
**Dashboards**: User-facing (execution status) + System (SLOs)

---

## 4) Runtime Agent Roles (Responsibility Classification)

Runtime agents are **asynchronous workers** that execute individual plan steps. They're not just labels—they're actual n8n sub-workflows and node configurations.

### The 6 Roles

#### 1. Fetcher (Read Operations)
**What it does**: One-time data retrieval
**Examples**:
- Get calendar availability
- Fetch contact info
- Look up product details
- Check flight prices

**Implementation**: n8n HTTP/connector nodes

#### 2. Analyzer (Data Processing)
**What it does**: Compare, rank, research, synthesize
**Examples**:
- Find overlapping calendar slots
- Rank restaurant options by price/rating
- Compare flight routes
- Calculate expense totals

**Implementation**: n8n Function nodes with compute logic

#### 3. Watcher (Long-Running Monitoring)
**What it does**: Continuous observation over time
**Examples**:
- Poll visa slots for 2 weeks
- Monitor price drops daily
- Watch for email replies
- Track package delivery

**Implementation**: n8n workflows with built-in persistence and scheduling

#### 4. Resolver (User Interaction)
**What it does**: Disambiguation and clarification
**Examples**:
- "Which John did you mean?"
- "Pick from these 3 options"
- "Confirm this choice"

**Implementation**: n8n Wait nodes with webhooks, approval flows

#### 5. Booker (Write Operations)
**What it does**: Create, update, or delete with idempotency
**Examples**:
- Create calendar events
- Send emails
- Make purchases
- Book appointments

**Implementation**: n8n connector nodes with idempotency keys

**Key requirement**: Must support compensation (undo) if something fails

#### 6. Notifier (Updates and Alerts)
**What it does**: Keep user informed
**Examples**:
- "✓ Meeting booked"
- "Visa slot found! Approve to book?"
- Progress updates
- Error notifications

**Implementation**: n8n Slack/email nodes

### How They Execute

**Parallel execution** (steps with no dependencies):
```
Step 1 (Fetcher): Get Alice's calendar  [after: []]
Step 2 (Fetcher): Get Bob's calendar    [after: []]
↓
Both execute simultaneously
```

**Sequential execution** (steps with dependencies):
```
Step 3 (Analyzer): Find overlap  [after: [1, 2]]
↓
Waits for steps 1 and 2 to complete first
```

**Real example timeline**:
- t=0ms: Steps 1 & 2 start in parallel
- t=200ms: Both complete
- t=201ms: Step 3 starts (has all required data)
- t=350ms: Step 3 completes

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

### Idempotency (No Duplicate Operations)
**Problem**: What if the network fails after creating a calendar event? Retry would create duplicates.

**Solution**: Idempotency keys
```python
# Before executing step 5
key = f"{plan_id}:5:{hash(args)}"
if redis.exists(key):
    return redis.get(key)  # Return cached result

# Execute operation
result = google_calendar.create_event(...)

# Cache result (1 hour TTL)
redis.setex(key, 3600, result)
```

**Result**: Safe to retry—same operation never executes twice

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

### Resource Locking (Prevent Conflicts)
**Problem**: Two plans try to book the same calendar slot simultaneously

**Solution**: Fine-grained locks
```python
# Plan A wants to book Alice's calendar
await acquire_lock("calendar.alice.write")
try:
    create_event(...)
finally:
    release_lock("calendar.alice.write")

# Plan B waits until Plan A releases the lock
```

**Granularity**:
- Fine-grained: `calendar.alice.write` vs `calendar.bob.write` (can run parallel)
- Read operations: No locks needed
- Coarse locks: Only for rate-limited resources (`email.send`)

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
5. **Automatic retries**: Configurable retry strategies with backoff
6. **Loop handling**: Workflow can loop back to previous nodes

**Result**: Monitors visa slots 24/7 for 2 weeks, survives restarts, handles approval flow with visual monitoring

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
1. ProfileStore, History, PlanLibrary (Memory Layer) — VectorIndex deferred (§11)
2. Intake, ContextRAG, Planner, Signer, PluginRegistry, PlanWriter (Domain Layer)
3. WorkflowBuilder, PreviewOrchestrator, ApprovalGate, ExecuteOrchestrator, DurableOrchestrator (Orchestration Layer)
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

The system uses FastAPI's async capabilities with bounded concurrency and task queue patterns to ensure reliable execution at scale.

### Task Queue Pattern

All execution requests go through an in-memory task queue with bounded concurrency to prevent resource exhaustion:

```python
from asyncio import Queue, Semaphore
from typing import Dict, Any
import asyncio

class ExecutionQueue:
    """Bounded task queue for plan execution with concurrency control."""

    def __init__(self, max_concurrent: int = 10):
        self.queue: Queue = Queue(maxsize=100)
        self.semaphore = Semaphore(max_concurrent)
        self.active_tasks: Dict[str, asyncio.Task] = {}

    async def enqueue(self, plan_id: str, plan: Dict[str, Any], token: str) -> str:
        """Add execution request to queue with backpressure."""
        if self.queue.full():
            raise QueueFullError("Execution queue at capacity")

        await self.queue.put({
            "plan_id": plan_id,
            "plan": plan,
            "token": token,
            "enqueued_at": datetime.now(timezone.utc)
        })
        return plan_id

    async def worker(self, worker_id: int):
        """Process execution requests with bounded concurrency."""
        while True:
            # Wait for semaphore slot (max_concurrent enforcement)
            async with self.semaphore:
                item = await self.queue.get()
                plan_id = item["plan_id"]

                try:
                    # Execute plan with timeout
                    result = await asyncio.wait_for(
                        self._execute_plan(item["plan"], item["token"]),
                        timeout=300  # 5 minute max execution
                    )
                    await self._store_result(plan_id, result)

                except asyncio.TimeoutError:
                    await self._handle_timeout(plan_id)
                except Exception as e:
                    await self._handle_error(plan_id, e)
                finally:
                    self.queue.task_done()

    async def _execute_plan(self, plan: Dict[str, Any], token: str) -> Dict[str, Any]:
        """Execute plan with n8n orchestration."""
        # Verify signature and token
        verify_signature(plan)
        verify_approval_token(token, plan["plan_hash"])

        # Build n8n workflow
        workflow = await workflow_builder.build(plan, mode="execute")

        # Execute with n8n
        result = await n8n_client.execute_workflow(workflow)
        return result
```

**Key features**:
- **Bounded concurrency**: Semaphore limits parallel executions (default: 10)
- **Backpressure**: Queue size limit (100) prevents memory exhaustion
- **Timeout protection**: 5-minute max per plan prevents hangs
- **Graceful degradation**: Returns 503 when queue is full

### Parallel Step Execution

Steps with no dependencies execute in parallel using `asyncio.gather` with bounded parallelism:

```python
async def execute_parallel_steps(steps: List[Step], max_parallel: int = 5) -> List[Result]:
    """Execute independent steps in parallel with bounded concurrency."""

    # Group steps by dependency level
    levels = group_by_dependency_level(steps)

    all_results = []
    for level_steps in levels:
        # Execute each level in parallel (bounded by max_parallel)
        semaphore = Semaphore(max_parallel)

        async def execute_with_limit(step: Step) -> Result:
            async with semaphore:
                return await execute_step(step)

        # Gather results for this level
        results = await asyncio.gather(
            *[execute_with_limit(s) for s in level_steps],
            return_exceptions=True
        )

        # Check for failures before proceeding to next level
        for step, result in zip(level_steps, results):
            if isinstance(result, Exception):
                raise ExecutionError(f"Step {step.step} failed", cause=result)

        all_results.extend(results)

    return all_results
```

**Benefits**:
- Parallel execution for independent steps (Steps 1 & 2 in meeting example)
- Dependency ordering preserved (Step 3 waits for 1 & 2)
- Bounded parallelism prevents resource exhaustion
- Fail-fast on errors (halt execution immediately)

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

**Document Version**: HLD v4.4
**Last Updated**: 2026-02-28
**Changes from v4.3**: Added §13 Asynchronous Execution Architecture (task queues, bounded concurrency, parallel execution patterns), §14 LLM Guardrails and Structured Interaction (validation layers, circuit breakers, fallback hierarchy, constraint enforcement), §15 Advanced Concurrency Patterns (distributed locking, enhanced idempotency, optimistic locking, deadlock prevention). Renumbered "What's Next?" from §13 to §16.
