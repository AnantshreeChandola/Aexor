# Quick Reference — Personal Agent

**Purpose**: Fast lookup for common concepts, commands, and patterns.

---

## 7 Runtime Agent Roles

Runtime agents are **logical plan-step categories** (not separate services). API steps execute via MCP tool invocations; LLM reasoning steps execute in Python.

| Role | Purpose | Examples | Implementation |
|------|---------|----------|----------------|
| **Fetcher** | One-time data retrieval | Get calendar availability, fetch contact info, check flight prices | MCP tool invocations (HTTP, API connectors) |
| **Analyzer** | Data processing | Find overlapping slots, rank options, compare routes, calculate totals | Python functions or MCP tool invocations |
| **Watcher** | Long-running monitoring | Poll visa slots (2 weeks), monitor price drops (daily), track package delivery | Python asyncio tasks with APScheduler, Redis-backed state |
| **Resolver** | User interaction | "Which John?", "Pick from 3 options", confirm choice | Python async approval gates (Redis-backed, webhook resume) |
| **Booker** | Write operations | Create events, send emails, make purchases, book appointments | MCP tool invocations with idempotency wrapper |
| **Notifier** | Updates and alerts | "✓ Meeting booked", "Visa slot found!", progress updates, errors | MCP tool invocations (Slack, email connectors) |
| **Reasoner** | LLM-based adaptive decisions | Analyze flight options, rank with judgment, decide if more data needed, generate summaries | Python service (Anthropic API, two-tier trust model, PolicyEngine-bounded, may spawn steps) |

---

## 4 System Layers

| Layer | Components | Purpose |
|-------|-----------|---------|
| **Memory** | ProfileStore, History, VectorIndex, PlanLibrary | Stores everything the system knows |
| **Domain** | Intake, ContextRAG, Planner, PluginRegistry, PlanWriter, PolicyEngine | Understands requests, builds plans, enforces policies |
| **Orchestration** | PreviewOrchestrator, ApprovalGate, ExecuteOrchestrator, ExecutionMonitor | Previews and executes plans safely |
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
2. **Deterministic planning with adaptive execution**: Initial plan (revision 0) is a fixed, immutable DAG (same inputs → same graph → same plan_hash). At runtime, Reasoner steps may spawn new steps within PolicyEngine bounds — each spawn creates a new plan revision with a PolicyAttestation. The original graph is never mutated.
3. **Pure agentic runtime**: Python ExecuteOrchestrator dispatches all steps — MCP for APIs, Anthropic for reasoning
4. **Idempotency**: Multi-user safe retry (`user:integration:plan:step:op:hash`)
5. **Compensation**: Undo failed operations (Saga pattern)
6. **Fine-grained locking**: Prevent conflicts without blocking parallelism
7. **Privacy tiers**: Context access controlled by consent level (Tier 1-5)
8. **Policy-bounded execution**: All LLM runtime decisions governed by PolicyEngine; critical actions require HITL
9. **Two-tier LLM execution**: Sandboxed Tier 1 (untrusted data, no tools) + capable Tier 2 (agent reasoning, MCP tools)
10. **Default-untrusted API outputs**: All external API responses must pass through Tier 1 sanitization before reaching Tier 2 Reasoners. Plan validator enforces this at creation time.

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
  "plan_id": "01HX...",              // ULID-based ID
  "user_id": "user-123",
  "intent": "schedule_meeting",      // Original user request
  "plan_hash": "sha256:...",         // Data integrity checksum
  "graph": [
    {
      "step": 1,                     // Sequential number
      "mode": "interactive",         // autonomous/interactive/supervised
      "role": "Fetcher",             // One of 7 runtime roles
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
| Orchestration | Python/FastAPI + MCP protocol | ExecuteOrchestrator dispatches all steps via MCP tool invocations |
| Credentials | AES-256-GCM vault in PostgreSQL | Master key from env; LLM never sees plaintext values |
| Database | PostgreSQL 16 + pgvector | Relational + vector + credential vault in one DB |
| Cache | Redis 7 | Sessions, tokens, idempotency (3-state), preview state, approval gates |
| AI | Anthropic Claude (**only paid external dependency**) | Planning, intent parsing, two-tier runtime reasoning |
| Embeddings | ONNX Runtime (local, all-MiniLM-L6-v2) | Vector search, 384-dim, ~10ms inference, zero API cost |
| Testing | pytest, ruff, mypy | Type safety + fast tests |

### LLM Model Configuration

| Purpose | Default Model | Env Var | Notes |
|---------|--------------|---------|-------|
| Planning (primary) | `claude-sonnet-4-5-20250929` | `PLANNER_PRIMARY_MODEL` | temperature=0, deterministic plan generation |
| Planning (fallback) | `claude-haiku-4-5-20251001` | `PLANNER_FALLBACK_MODEL` | Used when primary circuit-breaks |
| Intent parsing | `claude-haiku-4-5-20251001` | `INTAKE_PARSER_MODEL` | Fast/cheap for multi-turn message parsing |
| Runtime reasoning | Per-step via `reasoning_config.model` | N/A (set in plan) | Planner chooses model per Reasoner step |
| Embeddings | `all-MiniLM-L6-v2` (384-dim) | N/A (bundled ONNX) | Fully local CPU inference, no external API |

**External dependencies summary**: Anthropic API is the sole paid external service. PostgreSQL, Redis, and ONNX Runtime are self-hosted. User integration APIs (Google, Slack, etc.) use the user's own accounts.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Preview mutates data | Add `mock=True` to adapter calls |
| Duplicate operations | Add idempotency key check (3-state: IN_FLIGHT/SUCCEEDED/FAILED) |
| Circular dependencies | Extract shared logic to new component |
| Tests failing | Check acceptance criteria mapping |
| CI failing | Run `pytest` + `ruff check` locally |
| Slow preview | Check ContextRAG budget (≤2KB) |
| Stuck execution | ExecutionMonitor detects stuck tasks (5min timeout) and cancels; step failures handled by LLM reasoning |

---

**See Also**:
- [Project_HLD.md](Project_HLD.md) - Complete architecture overview
- [GLOBAL_SPEC.md](GLOBAL_SPEC.md) - Universal contracts
- [COMPONENT_STATUS.md](../../COMPONENT_STATUS.md) - Implementation progress
- [DEVELOPMENT_WORKFLOW.md](../../DEVELOPMENT_WORKFLOW.md) - Full workflow guide
- [PROJECT_STRUCTURE.md](../../PROJECT_STRUCTURE.md) - Complete directory structure
