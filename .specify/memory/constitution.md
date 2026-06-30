# Personal Agent Constitution

## Core Principles

### I. Component-First Architecture
Every feature is built as a self-contained component under `components/<Name>/` with:
- **SPEC.md** — Requirements, user stories, acceptance criteria
- **LLD.md** — Low-level design, interfaces, dependencies
- **schemas/** — JSON schemas for normalized responses
- **tests/** — Contract tests, unit tests, integration tests
- **Code** — api/, service/, domain/, adapters/ subdirectories

Components must be independently testable and documented. No cross-component file dependencies.

### II. Preview-First Safety (NON-NEGOTIABLE)
All operations MUST preview before execution:
- **Preview**: Read-only, no external mutations, stubs/mocks only
- **Execute**: Only after explicit human approval with valid token
- **Idempotency**: Required via `plan_id:step:arg_hash`

Preview paths must never call external APIs in write mode. Use cached/mocked data only.

### III. Test-First Development (NON-NEGOTIABLE)
TDD mandatory for all components:
1. Write tests FIRST (schema tests, service tests, contract tests)
2. User approves test plan
3. Tests FAIL (red)
4. Implement code
5. Tests PASS (green)
6. Refactor (maintain green)

No code commits without corresponding tests. CI enforces this.

### IV. Schema Validation
All data contracts MUST have JSON schemas:
- **Shared schemas** in `shared/schemas/` (Intent, Evidence, Plan, Wrappers)
- **Component schemas** in `components/<Name>/schemas/`
- **Tests validate** against schemas (no schema drift)

Breaking schema changes require version bump and migration plan.

### V. Deterministic Planning with Adaptive Execution
Initial plans are pure functions of frozen inputs:
- Intent vN (finalized)
- Evidence vK (typed, budget-limited)
- Registry vR (connector catalog snapshot)
- Policy vP (PolicyEngine rules version)

Same inputs → same canonical plan bytes → same plan hash.

At runtime, LLM reasoning steps may adapt within PolicyEngine bounds. Critical actions (writes, deletes, payments) always require human approval. Runtime modifications receive PolicyEngine attestations.

### VI. Observability & Privacy
- **Structured logging**: Correlated by `plan_id`, `step`, `role`
- **No secrets/PII in logs**: Derived facts only, explicit consent
- **Metrics**: p95 latencies (Preview <800ms, Execute <2s)
- **Audit trail**: All plan executions logged with attestations

### VII. Fault Isolation & Blast Radius
Components must contain failures:
- **Circuit breakers** for external provider calls
- **Fallback behavior** for adapter failures
- **Resource locks** for write conflicts
- **Compensation operations** when declared in registry

If a component fails, it must not cascade to others. See [MODULAR_ARCHITECTURE.md](../../docs/architecture/MODULAR_ARCHITECTURE.md).

---

## Development Workflow

### Branch Strategy
- **main/master**: Protected, no direct pushes
- **Feature branches**: `feat/<area>-<short-desc>`
- All work via Pull Requests

### PR Requirements (CI Gates)
Every PR MUST:
1. Link to `components/<Name>/SPEC.md` and `LLD.md`
2. Pass all tests (pytest with coverage)
3. Pass schema validation (against `shared/schemas/`)
4. Pass linting (ruff) and type checking (mypy)
5. Include acceptance criteria verification
6. Have green CI before merge

### Code Review Checklist
- [ ] SPEC/LLD linked and conformant
- [ ] Tests written first and passing
- [ ] Schemas validated
- [ ] Preview path has no external mutations
- [ ] Idempotency implemented
- [ ] Circuit breakers in place for external calls
- [ ] Structured logging with `plan_id` correlation
- [ ] No secrets/PII in logs
- [ ] Blast radius analyzed

---

## Technology Stack

### Mandatory Stack
- **Python 3.11+** with type hints (mypy strict mode)
- **FastAPI** for async HTTP
- **Pydantic v2** for data validation
- **SQLAlchemy 2.0** with asyncpg
- **PostgreSQL 16** with pgvector extension
- **Redis 7** for caching and coordination
- **MCP protocol** for external tool invocations (community-maintained connectors)
- **AES-256-GCM credential vault** in PostgreSQL (master key from env, LLM never sees plaintext)

### Forbidden
- **No LangChain**: Direct API calls for simplicity and control
- **No global state**: Components must be stateless services
- **No secrets in code**: Environment variables or secret managers only

---

## Quality Gates

### Pre-Commit (Local)
- Ruff auto-format
- Mypy type check
- Unit tests pass

### CI Pipeline (GitHub Actions)
- All unit tests pass
- All contract tests pass
- Schema validation against `shared/schemas/`
- Coverage > 80% for new code
- No mypy errors
- No ruff violations

### Pre-Merge
- Green CI
- At least 1 approval
- SPEC/LLD linked
- Acceptance criteria verified

---

## Conformance to GLOBAL_SPEC

All components MUST conform to [GLOBAL_SPEC.md v2](../../docs/architecture/GLOBAL_SPEC.md):
- Use canonical contracts (Intent, Evidence, Plan, Preview/Execute wrappers)
- Respect safety model (Preview vs Execute vs Durable)
- Meet NFRs (p95 latencies, availability targets)
- Support runtime agent roles (Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier, Reasoner)

Deviations require explicit declaration in component SPEC.md with rationale.

---

## Governance

### Constitution Authority
This constitution supersedes all other practices. In case of conflict:
1. Constitution rules apply
2. GLOBAL_SPEC.md v2 applies
3. Component SPEC.md applies
4. Code conventions apply

### Amendments
Constitution changes require:
1. ADR (Architecture Decision Record) in `docs/architecture/adr/`
2. Team approval
3. Migration plan for existing components
4. Version bump

### Enforcement
- All PRs/reviews MUST verify constitution compliance
- CI enforces automated checks (tests, schemas, linting)
- Manual review verifies design principles (preview-first, fault isolation)

### Working Guidance
For runtime development guidance, see:
- [.claude/CLAUDE.md](.claude/CLAUDE.md) — Agent working instructions
- [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) — Development workflow integration guide

---

**Version**: 1.0.0 | **Ratified**: 2025-12-11 | **Last Amended**: 2025-12-11
