# Quick Reference — Personal Agent

**Purpose**: Fast lookup for common concepts, commands, and patterns.

---

## 6 Runtime Agent Roles

Runtime agents execute individual plan steps as n8n sub-workflows or Temporal activities.

| Role | Purpose | Examples | Implementation |
|------|---------|----------|----------------|
| **Fetcher** | One-time data retrieval | Get calendar availability, fetch contact info, check flight prices | n8n HTTP/connector nodes, Temporal activities |
| **Analyzer** | Data processing | Find overlapping slots, rank options, compare routes, calculate totals | n8n Function nodes, Temporal activities |
| **Watcher** | Long-running monitoring | Poll visa slots (2 weeks), monitor price drops (daily), track package delivery | Temporal workflows with ContinueAsNew |
| **Resolver** | User interaction | "Which John?", "Pick from 3 options", confirm choice | n8n Wait nodes with webhooks |
| **Booker** | Write operations | Create events, send emails, make purchases, book appointments | n8n connector nodes, Temporal activities with idempotency |
| **Notifier** | Updates and alerts | "✓ Meeting booked", "Visa slot found!", progress updates, errors | n8n Slack/email nodes, Temporal activities |

---

## 4 System Layers

| Layer | Components | Purpose |
|-------|-----------|---------|
| **Memory** | ProfileStore, History, VectorIndex, PlanLibrary | Stores everything the system knows |
| **Domain** | Intake, ContextRAG, Planner, Signer, PluginRegistry, PlanWriter | Understands requests and builds plans |
| **Orchestration** | WorkflowBuilder, PreviewOrchestrator, ApprovalGate, ExecuteOrchestrator, DurableOrchestrator | Previews and executes plans safely |
| **Platform** | API Gateway, Audit | Interface and observability |

---

## 5-Phase Flow

```
User Request → Preview → Approval → Execute → Learn
     ↓           ↓          ↓          ↓        ↓
  [Intent]   [Show Me]  [Confirm]  [Do It]  [Remember]
```

1. **Intent**: Intake + ContextRAG understand request
2. **Preview**: Show what will happen (read-only, no side effects)
3. **Approval**: User confirms (issues JWT token)
4. **Execute**: Do the actual work (with idempotency)
5. **Learn**: Save to Plan Library and History

---

## Core Architectural Principles

1. **Preview-first safety**: Never execute without showing user first
2. **Deterministic planning**: Same inputs → same plan → same signature
3. **Dual runtime**: n8n (< 15min) vs Temporal (hours/days)
4. **Idempotency**: Safe retry for all write operations (`plan_id:step:arg_hash`)
5. **Compensation**: Undo failed operations (Saga pattern)
6. **Fine-grained locking**: Prevent conflicts without blocking parallelism
7. **Privacy tiers**: Context access controlled by consent level (Tier 1-5)
8. **No LLM iteration**: One-shot planning, not agentic loops

---

## Common Commands & Skills

### Slash Commands
- `/primer` - Read-only repo overview, propose next step
- `/specify` - Create SPEC using Spec Kit workbench
- `/design` - Generate LLD and flow diagram from SPEC

### Skills (Use as `/skill-name`)
- `/create-component-spec` - SPEC.md template
- `/create-component-lld` - LLD.md template
- `/review-plan-schema` - Validate plan JSON
- `/explain-component` - Explain with examples
- `/add-test-cases` - Generate tests from acceptance criteria
- `/review-architecture` - Architectural review checklist
- `/quick-fix` - Fast bug fixes (< 3 files)
- `/update-component-status` - Update component tracker

### Agents (Use via Task tool or direct reference)
- **planner** - Maps SPEC/LLD to tasks, proposes tests first
- **implementer** - Implements tasks with preview-first safety
- **verifier** - Validates plans, runs tests, checks schemas
- **pr-manager** - Creates PRs with proper templates
- **architect** - Makes architectural decisions, analyzes blast radius

---

## Plan Schema Quick Reference

```json
{
  "plan_id": "01HX...",              // Ed25519-based ID
  "user_id": "user-123",
  "intent": "schedule_meeting",      // Original user request
  "signature": "base64:...",         // Ed25519 signature
  "graph": [
    {
      "step": 1,                     // Sequential number
      "mode": "interactive",         // autonomous/interactive/supervised
      "role": "Fetcher",             // One of 6 runtime roles
      "uses": "google.calendar",     // Connector name
      "call": "list_free_busy",      // Method name
      "args": {...},                 // Parameters
      "after": [],                   // Dependencies [step numbers]
      "execute_mode": "preview_only", // preview_only/execute_only/both
      "dry_run": true                // For preview steps
    },
    {
      "step": 2,
      "role": "Booker",
      "uses": "google.calendar",
      "call": "create_event",
      "args": {
        "slot": "{{preview.cached_state.selected_slot}}"  // Reference cached state
      },
      "after": [1],
      "gate_id": "gate-A",           // Approval gate
      "execute_mode": "execute_only"
    }
  ]
}
```

---

## Data Schemas

### Intent (Input)
```json
{
  "intent": "schedule_meeting",
  "entities": {"attendee": "Alice", "timeframe": "next week"},
  "constraints": {"duration_min": 30},
  "tz": "America/Chicago",
  "user_id": "user-123"
}
```

### Preview Wrapper (Output)
```json
{
  "normalized": {
    "available_slots": ["Tue 10 AM", "Thu 2 PM", "Fri 11 AM"]
  },
  "source": "preview",
  "can_execute": true,
  "evidence": [...]
}
```

### Execute Wrapper (Output)
```json
{
  "provider": "google.calendar",
  "result": {"id": "gcal_123456"},
  "status": "created"
}
```

---

## Performance Targets

| Metric | Target | p95 Threshold |
|--------|--------|---------------|
| Preview latency | 650ms | < 800ms |
| Execute latency (short) | 1.2s | < 2s |
| ContextRAG | 120ms | < 150ms |
| Vector search | 80ms | < 100ms |
| Concurrent plans | 100+ simultaneous | - |

---

## File Structure (Component-First)

```
components/<ComponentName>/
├── SPEC.md           # Declares conformance to GLOBAL_SPEC
├── LLD.md            # Low-level design details
├── api/
│   └── handlers.py   # FastAPI routes
├── service/
│   └── service.py    # preview() and execute() logic
├── domain/
│   └── models.py     # Domain entities
├── adapters/
│   └── provider.py   # External API/DB calls
├── schemas/
│   ├── input.py      # Pydantic models for input
│   ├── output.py     # Preview/Execute wrappers
│   └── response.normalized.json
└── tests/
    ├── test_service.py      # Unit tests
    ├── test_contract.py     # Contract tests
    └── test_integration.py  # Integration tests
```

---

## Git Workflow

```bash
# 1. Create feature branch
git checkout -b feat/component-name

# 2. Create SPEC
/specify

# 3. Design LLD
/design

# 4. Plan implementation
# Use planner agent

# 5. Implement
# Use implementer agent

# 6. Verify
# Use verifier agent

# 7. Create PR (never push to main!)
# Use pr-manager agent

# 8. Wait for CI + human approval
# Never auto-merge
```

---

## Common Patterns

### Idempotency
```python
key = f"{plan_id}:{step}:{hash(args)}"
if redis.exists(key):
    return redis.get(key)  # Return cached result

result = provider.execute(...)
redis.setex(key, 3600, result)  # Cache for 1 hour
```

### Preview vs Execute
```python
async def preview(intent: Intent) -> PreviewWrapper:
    # READ-ONLY: Use mocks
    slots = await calendar.get_free_slots(mock=True)
    return PreviewWrapper(
        normalized={"slots": slots},
        source="preview",
        can_execute=True
    )

async def execute(approved: ApprovedPreview) -> ExecuteWrapper:
    # WRITE: Use real API
    event_id = await calendar.create_event(...)
    return ExecuteWrapper(
        provider="google.calendar",
        result={"id": event_id},
        status="created"
    )
```

### Compensation (Saga)
```json
{
  "create_event": {
    "compensation": "delete_event"
  },
  "send_email": {
    "compensation": null  // Can't unsend
  }
}
```

---

## Privacy Tiers

| Tier | Data | TTL | Example |
|------|------|-----|---------|
| Tier 1 | Session only | Until session ends | Current conversation |
| Tier 2 | Stable preferences | Forever (until user changes) | Work hours, meeting duration |
| Tier 3 | Recent history | 30 days | Past meetings |
| Tier 4 | Live signals | Real-time | Free/busy status |
| Tier 5 | Private content (derived facts only) | Explicit consent | "Usually meets Alice on Tuesdays" |

---

## Tech Stack

| Category | Technology | Rationale |
|----------|-----------|-----------|
| Backend | Python 3.11+, FastAPI | Async, type hints, Pydantic |
| Orchestration | n8n + Temporal | Short jobs (n8n) + long jobs (Temporal) |
| Database | PostgreSQL 16 + pgvector | Relational + vector in one DB |
| Cache | Redis 7 | Sessions, tokens, idempotency, preview state |
| AI | Anthropic Claude | Planning (temperature=0) |
| Embeddings | OpenAI | Vector search only |
| Testing | pytest, ruff, mypy | Type safety + fast tests |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Preview mutates data | Add `mock=True` to adapter calls |
| Duplicate operations | Add idempotency key check |
| Circular dependencies | Extract shared logic to new component |
| Tests failing | Check acceptance criteria mapping |
| CI failing | Run `pytest` + `ruff check` locally |
| Slow preview | Check ContextRAG budget (≤2KB) |
| Long execution | Consider Temporal instead of n8n |

---

**See Also**:
- [Project_HLD.md](Project_HLD.md) - Complete architecture overview
- [GLOBAL_SPEC.md](GLOBAL_SPEC.md) - Universal contracts
- [COMPONENT_STATUS.md](../../COMPONENT_STATUS.md) - Implementation progress
- [DEVELOPMENT_WORKFLOW.md](../../DEVELOPMENT_WORKFLOW.md) - Full workflow guide
- [PROJECT_STRUCTURE.md](../../PROJECT_STRUCTURE.md) - Complete directory structure
