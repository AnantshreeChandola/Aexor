<p align="center">
  <img src="static/aexor-logo-top-cropped.png" alt="Aexor Logo" width="200">
</p>

<h1 align="center">Aexor</h1>

<p align="center">
  <strong>Preview-first personal assistant with deterministic planning and adaptive execution</strong>
</p>

**Status:** All 15 components implemented
**Architecture:** HLD v6.1 / GLOBAL_SPEC v3.0
**Deployment:** Self-hosted, single-tenant, multi-user

Aexor produces immutable plan DAGs (revision 0) where LLM Reasoner steps can spawn bounded new steps at runtime, creating versioned revisions with PolicyEngine attestations. All execution runs through a pure Python/FastAPI runtime with MCP tool invocations — no external workflow engines.

---

## How It Works

```
User Request → Understand → Plan → Preview → Approve → Execute → Learn
     ↓            ↓          ↓        ↓          ↓         ↓        ↓
  [Message]   [Intent]   [Plan    [Show    [Confirm]  [Do It]  [Remember]
                          DAG]     Me]
```

**Core flow:**
1. **Intake** collects user intent across multiple messages (LLM parsing, Redis sessions)
2. **ContextRAG** gathers relevant context from 4 Memory Layer sources (≤2KB budget)
3. **Planner** generates a deterministic plan DAG (same inputs → same graph)
4. **PreviewOrchestrator** shows what will happen (no side effects)
5. **ApprovalGate** waits for user confirmation (JWT token, 15min TTL)
6. **ExecuteOrchestrator** dispatches steps — API steps via MCP, reasoning via Anthropic API
7. **PlanWriter** persists outcomes back to Memory Layer for future context

---

## Architecture

### 4 Layers, 15 Components

**Layer 1 — Memory & Persistence** (all implemented)
| Component | Purpose |
|-----------|---------|
| ProfileStore | Stable user preferences and consent settings |
| History | Normalized, PII-light facts about past actions (30-day TTL) |
| PlanLibrary | All past plans with outcomes |
| VectorIndex | Hybrid BM25 + semantic search (pgvector + ONNX Runtime) |

**Layer 2 — Domain Services** (all implemented)
| Component | Purpose |
|-----------|---------|
| Intake | Multi-turn intent collection with LLM parsing and Redis sessions |
| ContextRAG | Tiered evidence gathering from Memory Layer (≤2KB) |
| Planner | Deterministic plan generation via Anthropic Claude API with 4-level fallback |
| PolicyEngine | Policy rule evaluation, attestations, HITL enforcement |
| PluginRegistry | Tool catalog with scope verification and credential resolution |
| PlanWriter | Outcome persistence with fact derivation |

**Layer 3 — Orchestration** (all implemented)
| Component | Purpose |
|-----------|---------|
| PreviewOrchestrator | Side-effect-free plan preview with dry-run MCP and Redis cache |
| ApprovalGate | HITL approval workflow with JWT tokens, single-use, multi-gate support |
| ExecuteOrchestrator | Pure agentic DAG execution — MCP dispatch, two-tier LLM, spawning |
| ExecutionMonitor | Stuck execution detection and timeout enforcement |

**Layer 4 — Platform** (all implemented)
| Component | Purpose |
|-----------|---------|
| Audit | Append-only audit trail for all plan/execution/approval events |
| IntegrationManager | User-provider connection status, Composio OAuth, per-user MCP tools |

### Key Architectural Principles

1. **Preview-first safety** — never execute without showing the user first
2. **Deterministic planning with adaptive execution** — initial plan (revision 0) is immutable; Reasoner steps may spawn bounded new steps at runtime, creating new revisions with PolicyAttestations
3. **Pure agentic runtime** — Python ExecuteOrchestrator dispatches all steps via MCP (APIs) and Anthropic API (reasoning)
4. **Two-tier LLM execution** — sandboxed Tier 1 (untrusted external data, no tools) + capable Tier 2 (agent reasoning, MCP tools)
5. **Default-untrusted rule** — all external API data must pass through Tier 1 sanitization before reaching Tier 2 Reasoners
6. **PolicyEngine governance** — deny-by-default; actions are only allowed when an explicit policy rule matches
7. **Seven runtime roles** — Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier, Reasoner

### Key Documents
- [GLOBAL_SPEC.md](docs/architecture/GLOBAL_SPEC.md) — Universal operating contract (v3.0)
- [Project_HLD.md](docs/architecture/Project_HLD.md) — High-level design with examples (v6.1)
- [QUICK_REFERENCE.md](docs/architecture/QUICK_REFERENCE.md) — Tech stack, roles, and troubleshooting
- [COMPONENT_STATUS.md](COMPONENT_STATUS.md) — Implementation progress per component

---

## Tech Stack

| Category | Technology | Notes |
|----------|-----------|-------|
| Backend | Python 3.11+, FastAPI, Pydantic v2 | Async, type hints |
| Database | PostgreSQL 16 + pgvector | Relational + vector + credential vault |
| Cache | Redis 7 with hiredis | Sessions, idempotency, approval gates |
| AI/LLM | Anthropic Claude + OpenAI (optional fallback) | Planning + intent parsing + runtime reasoning |
| Embeddings | ONNX Runtime (all-MiniLM-L6-v2, 384-dim) | Fully local CPU inference, zero API cost |
| Credentials | AES-256-GCM vault in PostgreSQL | Master key from env; LLM never sees plaintext |
| Tools | MCP protocol (Composio, custom servers) | All external tool invocations |
| Testing | pytest, ruff, mypy | Async support, strict type checking |
| CI/CD | GitHub Actions | Lint, test, type-check |

### LLM Models

| Purpose | Default Model | Env Var |
|---------|--------------|---------|
| Planning (primary) | `claude-sonnet-4-5-20250929` | `PLANNER_PRIMARY_MODEL` |
| Planning (fallback) | `claude-sonnet-4-5-20250929` | `PLANNER_FALLBACK_MODEL` |
| Intent parsing | `claude-sonnet-4-5-20250929` | `INTAKE_PARSER_MODEL` |
| Runtime reasoning | Per-step via `reasoning_config.model` | Set in plan by Planner |
| Embeddings | `all-MiniLM-L6-v2` (ONNX, local) | Bundled, no API |

---

## Project Structure

```
Aexor/
├── components/           # Self-contained component packages
│   ├── Intake/           # Multi-turn intent collection
│   ├── ContextRAG/       # Evidence gathering
│   ├── Planner/          # Plan generation + validation
│   ├── PolicyEngine/     # Policy rules + attestations
│   ├── PluginRegistry/   # Tool catalog
│   ├── PlanWriter/       # Outcome persistence
│   ├── ProfileStore/     # User preferences
│   ├── History/          # Past action facts
│   ├── PlanLibrary/      # Plan storage
│   ├── VectorIndex/      # Hybrid search
│   ├── ExecuteOrchestrator/  # Agentic DAG execution
│   ├── PreviewOrchestrator/  # Dry-run preview
│   ├── ApprovalGate/     # HITL approval workflow
│   ├── ExecutionMonitor/ # Timeout and stuck detection
│   ├── Audit/            # Append-only audit log
│   └── IntegrationManager/  # Provider connections
├── shared/               # Cross-component infrastructure
│   ├── schemas/          # Pydantic models (Intent, Plan, Evidence, Policy)
│   ├── database/         # SQLAlchemy models, session management
│   ├── mcp/              # MCP client, tool catalog, session manager
│   ├── middleware/        # Auth middleware
│   ├── security/         # Encryption utilities
│   ├── api/              # Shared error handlers, orchestration routes
│   ├── app.py            # FastAPI app factory with DI wiring
│   └── dependencies.py   # Dependency injection (get_*_service)
├── docs/
│   └── architecture/     # GLOBAL_SPEC, Project_HLD, QUICK_REFERENCE
├── tests/                # Shared acceptance and contract tests
├── docker-compose.yml    # Full local stack (PostgreSQL, Redis, app)
├── .github/workflows/    # CI pipeline
└── pyproject.toml        # Dependencies, ruff, mypy, pytest config
```

---

## Development

### Prerequisites
- Python 3.11+
- Docker & Docker Compose (recommended)
- Anthropic API key (`ANTHROPIC_API_KEY`)
- OpenAI API key (`OPENAI_API_KEY`, optional — for fallback)

### Getting Started

```bash
# Clone and setup
git clone <repo-url>
cd Aexor

# Option 1: Docker (recommended)
cp .env.example .env  # Add ANTHROPIC_API_KEY
docker compose up --build -d

# Option 2: Local
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
pytest components/ tests/
ruff check .
```

### Creating a Feature Branch
```bash
git checkout -b feat/<short-name>
# Implement with tests → iterate until CI green
# Open PR linking relevant spec
```

---

## Contributing

1. Read [GLOBAL_SPEC.md](docs/architecture/GLOBAL_SPEC.md) for universal contracts
2. Read [Project_HLD.md](docs/architecture/Project_HLD.md) for system design and examples
3. Each component follows the structure: `SPEC.md`, `LLD.md`, `schemas/`, `tests/`, code
4. Create branch `feat/<short-name>`, ensure CI passes, open PR


