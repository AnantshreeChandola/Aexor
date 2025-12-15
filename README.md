# Personal Agent

**Status:** Architecture & Scaffolding Phase
**Default Timezone:** America/Chicago

A preview-first personal assistant system with deterministic planning, multi-agent orchestration, and component-first architecture. The system uses asynchronous runtime agents for responsibility isolation, enabling parallel task execution across calendars, shopping, travel, and more.

---

## Tech Stack

### Core Backend
- **Python 3.11+** with type hints
- **FastAPI** for async HTTP
- **Pydantic v2** for data validation
- **SQLAlchemy 2.0** with asyncpg
- **aioredis** for async Redis

### Orchestration
- **n8n** (self-hosted) for short interactive workflows
- **Temporal** (Python SDK) for long-running durable tasks

### Data Storage
- **PostgreSQL 16** with pgvector extension
- **Redis 7** for caching and coordination

### AI/LLM
- **Anthropic Claude API** (Sonnet 4/Opus) for planning and reasoning
- **OpenAI API** for embeddings only
- **No LangChain** - direct API calls for simplicity and control

### Testing & Quality
- **pytest** with async support
- **ruff** for linting/formatting
- **mypy** for static type checking

### Infrastructure
- **Docker** for local development and deployment
- **GitHub Actions** for CI/CD

> See [docs/architecture/Project_HLD.md](docs/architecture/Project_HLD.md#11-tech-stack) for detailed rationale and integration patterns.

---

## Architecture

The system follows a **component-first architecture** with:

- **Preview-first safety model** - all operations preview before execution
- **Deterministic planning** - same inputs always produce same plan
- **Six runtime agent roles** for responsibility isolation:
  - Fetcher (one-time reads)
  - Analyzer (data processing)
  - Watcher (long-running monitoring)
  - Resolver (disambiguation)
  - Booker (writes with idempotency)
  - Notifier (updates and alerts)
- **Dual orchestration runtime** - n8n for short jobs, Temporal for durable workflows
- **Ed25519 signatures** for plan integrity
- **Human-in-the-loop gates** for approval workflows

### Key Documents
- [GLOBAL_SPEC.md](docs/architecture/GLOBAL_SPEC.md) - Universal operating contract
- [Project_HLD.md](docs/architecture/Project_HLD.md) - High-level design and components
- [PROJECT_STRUCTURE.md](docs/architecture/PROJECT_STRUCTURE.md) - Repository layout

---

## Project Structure

```
/Users/anantshreechandola/Desktop/Personal-agent/
├── components/          # Self-contained component packets
│   └── <Name>/          # SPEC.md, LLD.md, schemas/, tests/, code
├── usecases/            # End-to-end use case definitions
│   └── <UseCase>/       # SPEC.md, LLD.md, plans/, tests/, fixtures/
├── shared/             # Cross-component utilities
│   └── schemas/         # Shared schemas (Intent, Evidence, Plan, Signature)
├── docs/
│   ├── architecture/    # GLOBAL_SPEC, HLD, ADRs
│   └── dev/             # Development guides (PYTHON_GUIDE.md)
├── tests/               # Acceptance and contract tests
├── .claude/             # Claude Code agents and commands
└── .github/workflows/   # CI/CD pipelines
```

---

## Development

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- n8n instance (local or cloud)
- Temporal server (local dev)
- PostgreSQL 16 with pgvector
- Redis 7

### Getting Started

1. **Clone the repository**
   ```bash
   git clone <repo-url>
   cd Personal-agent
   ```

2. **Follow Python setup**
   See [docs/dev/PYTHON_GUIDE.md](docs/dev/PYTHON_GUIDE.md) for environment setup, dependencies, and coding standards.

3. **Review architecture**
   Read [docs/architecture/Project_HLD.md](docs/architecture/Project_HLD.md) to understand the system design.

4. **Create a feature branch**
   ```bash
   git checkout -b feat/<short-name>
   ```

5. **Run tests**
   ```bash
   pytest tests/
   ```

---

## Contributing

Before making changes:
1. Read [.claude/CLAUDE.md](.claude/CLAUDE.md) for project rules and working context
2. Read [docs/architecture/GLOBAL_SPEC.md](docs/architecture/GLOBAL_SPEC.md) for universal contracts
3. Create a branch `feat/<short-name>` and link relevant specs
4. Ensure CI passes before merging

### Component Development
Each component must include:
- `SPEC.md` - Declares conformance to GLOBAL_SPEC
- `LLD.md` - Low-level design and implementation details
- `schemas/` - Pydantic models and validation
- `tests/` - Unit and integration tests
- Code implementing the component

### Workflow
1. Create/update component SPEC → get approval
2. Design LLD → review
3. Implement with tests → iterate until CI green
4. Open PR linking spec and (if applicable) draft plan in `/plans/drafts/`

---

## License

[License details to be added]

---

## Contact

[Contact information to be added]
