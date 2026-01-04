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
- **VectorIndex**: Finds similar situations by meaning
- **PlanLibrary**: Reusable successful plans

**Example**: When you say "book a meeting," the system remembers you prefer 30-minute meetings at 10 AM.

### Layer 2: Domain Services
**What it does**: Understands your request and builds a plan
- **Intake**: Figures out what you want across multiple messages
- **ContextRAG**: Gathers relevant context (≤2KB, not your entire history)
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
components/<ComponentName>/
├── SPEC.md           # Declares conformance to GLOBAL_SPEC
├── LLD.md            # Low-level design details
├── schemas/          # Pydantic models
│   ├── input.py
│   ├── output.py
│   └── internal.py
├── tests/            # Unit and integration tests
│   ├── test_unit.py
│   └── test_integration.py
└── <code files>      # Implementation

usecases/<UseCase>/
├── SPEC.md           # Use case specification
├── LLD.md            # Implementation design
├── plans/            # Example plans
│   ├── drafts/       # Work-in-progress plans
│   └── approved/     # Validated plans
├── tests/            # End-to-end tests
└── fixtures/         # Test data
```

**16 Core Components**:
1. ProfileStore, History, VectorIndex, PlanLibrary (Memory Layer)
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
- **Vector search**: < 100ms

### Availability
- **Intake/Preview**: 99.9% (< 43min downtime/month)
- **Execute/Durable**: 99.5% (< 3.6hr downtime/month)

### Scalability
- **Concurrent plans**: 100+ simultaneous executions
- **Vector index**: < 1M embeddings (upgrade to dedicated DB if exceeded)
- **Plan library**: Unlimited (PostgreSQL partitioned by month)

---

## 12) What's Next?

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

**Document Version**: HLD v4.2 (Simplified n8n-only Architecture)
**Last Updated**: 2025-01-02
**Changes from v4.1**: Removed Temporal in favor of n8n-only architecture for all workflows, removed ADR-003 architectural improvements as no longer needed
