# Development Workflow Integration Guide

**Purpose**: Complete development workflow using Git Spec Kit, Claude Commands, and Agents for spec-driven development.

**Goal**: Clean separation and integration between Git Spec Kit (validation) and Claude Code (execution) for efficient feature development.

---

## Directory Structure

```
Personal-agent/
├── .claude/                    # Claude Code configuration
│   ├── commands/               # Custom Claude commands (4 files)
│   │   ├── primer.md          # Repo overview
│   │   ├── specify.md         # Create SPEC (component/use case templates)
│   │   ├── design.md          # Create LLD + flowchart
│   │   └── flow_orchestrate.md # Run agents: planner → implementer → verifier → pr-manager
│   ├── agents/                 # Claude agents (5 files)
│   │   ├── planner.md         # Maps SPEC → tasks
│   │   ├── implementer.md     # Writes code/tests/schemas
│   │   ├── verifier.md        # Runs tests, validates
│   │   ├── pr-manager.md      # Creates PR with evidence
│   │   └── architect.md       # Makes architectural decisions (ADRs, trade-offs)
│   ├── skills/                 # Quick helper skills (7 files)
│   │   ├── explain-component.md
│   │   ├── review-plan-schema.md
│   │   ├── review-architecture.md
│   │   ├── quick-fix.md
│   │   ├── update-component-status.md
│   │   ├── add-test-cases.md
│   │   └── async-python-patterns.md
│   ├── CLAUDE.md              # Project rules
│   └── settings.json          # Permissions/MCP config
│
├── .specify/                   # Git Spec Kit workspace
    ├── commands/               # Spec Kit commands (8 files)
    │   ├── speckit.constitution.md
    │   ├── speckit.clarify.md
    │   ├── speckit.plan.md
    │   ├── speckit.analyze.md
    │   ├── speckit.tasks.md
    │   ├── speckit.implement.md
    │   ├── speckit.checklist.md
    │   └── speckit.taskstoissues.md
    ├── templates/              # Spec Kit templates
    │   ├── spec-template-component.md
    │   ├── spec-template-usecase.md
    │   └── tasks-template.md
    ├── scripts/bash/           # Spec Kit bash scripts
    │   ├── create-new-feature.sh
    │   ├── check-prerequisites.sh
    │   └── ...
    ├── memory/                 # Constitution
    │   └── constitution.md
    └── specs/                  # Workbench for specs
        └── 001-feature/
            ├── spec.md
            ├── plan.md
            └── tasks.md
│
├── COMPONENT_STATUS.md         # Implementation tracker (16 components)
├── PROJECT_STRUCTURE.md                # Complete file organization
├── DEVELOPMENT_WORKFLOW.md     # This file
├── QUICK_REFERENCE.md          # Fast lookup (roles, layers, commands)
└── README.md                   # Project overview
```

---

## Three-Tier System

### **Tier 1: Commands** (Orchestrators)

Commands are **standalone prompts** that orchestrate workflows. They either run bash scripts (Spec Kit) or invoke agents (custom).

#### **Spec Kit Commands** (in `.specify/commands/`)
**Location**: `.specify/commands/speckit.*.md`
**Purpose**: Official Spec Kit workflow with validation gates
**How they work**: Run bash scripts in `.specify/scripts/bash/`

| Command | What It Does | Output |
|---------|-------------|--------|
| `/speckit.constitution` | Define project principles | `.specify/memory/constitution.md` |
| `/speckit.clarify` | Address gaps (optional) | Clarifications in spec |
| `/speckit.plan` | Create implementation plan | `.specify/specs/###-name/plan.md`, research.md, data-model.md, contracts/, quickstart.md |
| `/speckit.analyze` | Validate consistency (optional) | Analysis report |
| `/speckit.tasks` | Generate task breakdown | `.specify/specs/###-name/tasks.md` |
| `/speckit.implement` | Execute implementation | Code in `components/` or `usecases/` |
| `/speckit.checklist` | Quality validation (optional) | Quality checklist |
| `/speckit.taskstoissues` | Convert to GitHub issues (optional) | GitHub issues |

**Note**: `/speckit.specify` was removed as duplicate - use `/specify` instead (custom wrapper with component/use case templates)

**Characteristics**:
- ✅ Full validation at each step
- ✅ Consistency checks
- ✅ Creates artifacts in `.specify/` workbench
- ❌ More steps (8-9 commands)

---

#### **Custom Claude Commands** (in `.claude/commands/`)
**Location**: `.claude/commands/*.md`
**Purpose**: Streamlined workflows tailored to this repo
**How they work**: Give Claude detailed instructions OR invoke agents

| Command | What It Does | How It Works |
|---------|-------------|--------------|
| `/primer` | Repo overview + propose next step | Reads docs, proposes command |
| `/specify` | Create SPEC (component OR use case) | Wrapper for Spec Kit - runs `create-new-feature.sh` with custom templates |
| `/design` | Create comprehensive LLD + dependencies + architectural analysis | Wrapper for `/speckit.plan` - generates LLD.md, flow.mmd, dependencies.md with blast radius analysis |
| `/flow_orchestrate` | Run full agent workflow with 6-phase tasks | Invokes: planner → implementer → verifier → pr-manager (includes Phase 5: Safety) |

**Characteristics**:
- ✅ Fast, tailored to your repo
- ✅ Direct to `components/` (no workbench)
- ❌ No validation gates

---

### **Tier 2: Agents** (Specialists)

Agents are **execution roles** that can be invoked by commands OR manually.

**Location**: `.claude/agents/*.md`
**Purpose**: Execute specific roles with specialized expertise

| Agent | Role | Invoked By | Creates |
|-------|------|------------|---------|
| **planner** | Maps SPEC → 6-phase tasks (includes dependencies, safety) | `/flow_orchestrate` or manual | `tasks.md` with Phase 0-6, architectural considerations |
| **implementer** | Writes code/tests/schemas following tasks | `/flow_orchestrate` or manual | Code, tests, schemas, adapters |
| **verifier** | Runs tests, validates BC/schemas/envelopes | `/flow_orchestrate` or manual | Test results, validation reports, preview evidence |
| **pr-manager** | Creates PR with SPEC/LLD links and evidence | `/flow_orchestrate` or manual | PR with conformance checks and test results |
| **architect** | Makes architectural decisions (NOT spec/LLD creation) | Manual (standalone) | ADRs, trade-off analysis, blast radius analysis |

**Key insight**: Agents are **NOT linked to Spec Kit commands**. Spec Kit commands run bash scripts; they don't invoke agents.

**Characteristics**:
- ✅ Specialized expertise
- ✅ Can be invoked standalone OR by commands
- ✅ Reusable across workflows

---

### **Tier 3: Skills** (Quick Helpers)

Skills are **fast shortcuts** that bypass workflows entirely.

**Location**: `.claude/skills/*.md`
**Purpose**: Fast, focused tasks without workflow overhead

| Skill | Purpose | When to Use |
|-------|---------|-------------|
| `quick-fix` | Fast bug fixes (< 3 files, < 10 lines) | Typos, small bugs |
| `explain-component` | Explain with examples | Understand a component |
| `review-architecture` | Quick architectural review | Before major changes |
| `review-plan-schema` | Validate plan JSON | Check plan structure |
| `update-component-status` | Update tracker | After any workflow completes |
| `add-test-cases` | Generate test templates | Quick test scaffolding |
| `async-python-patterns` | Asyncio/concurrent patterns | Building async services |

**Characteristics**:
- ✅ Fastest (no validation, no branching)
- ✅ Standalone, no dependencies
- ❌ No quality gates

---

## Enhanced Workflow Features (v2)

### **`/design` Command Enhancements**

The `/design` command is a comprehensive wrapper for `/speckit.plan` that adds architectural analysis:

**What it generates**:
1. **LLD.md** - Complete low-level design with:
   - Architectural considerations (blast radius, fault isolation, determinism)
   - Layer placement (Memory/Domain/Orchestration/Platform)
   - Preview/Execute flows with safety guarantees
   - Library dependencies with justifications
   - Cross-component interactions

2. **dependencies.md** - Complete dependency manifest:
   ```markdown
   ## Python Packages
   | Package | Version | Purpose | Justification |

   ## External Services
   | Service | Provider | Scopes Required | Purpose |

   ## Internal Dependencies
   | Component | Purpose |
   ```

3. **diagrams/flow.mmd** - Mermaid flowchart with:
   - Preview flow (read-only)
   - Execute flow (with idempotency)
   - Error handling and circuit breakers

**Architectural docs consulted**:
- [docs/architecture/GLOBAL_SPEC.md](docs/architecture/GLOBAL_SPEC.md) - Universal contracts
- [docs/architecture/Project_HLD.md](docs/architecture/Project_HLD.md) - 4 layers, 16 components
- [docs/architecture/MODULAR_ARCHITECTURE.md](docs/architecture/MODULAR_ARCHITECTURE.md) - Blast radius isolation

---

### **Planner Agent Enhancements**

The planner agent now generates **6-phase structured tasks** instead of basic lists:

**Phase 0: Setup & Dependencies** (from dependencies.md)
- Install Python packages
- Verify external service access
- Set up internal component dependencies

**Phase 1: Schemas & Domain** (Foundation)
- Create response.normalized.json schema
- Create domain models
- Write schema validation tests

**Phase 2: Service Layer** (Business Logic)
- Implement service.preview() (NO external mutations)
- Implement service.execute() (with idempotency)
- Write service tests

**Phase 3: Adapters** (External Integrations)
- Create provider adapters with required scopes
- Add circuit breakers and retry logic
- Write adapter tests with mocks

**Phase 4: API Handlers** (Thin Wrappers)
- Create API handler (validates Intent, returns Preview/Execute)
- Write handler tests

**Phase 5: Fault Isolation & Safety** (NEW - Architectural)
- Implement circuit breakers for provider calls
- Add fallback behavior for adapter failures
- Validate determinism (same inputs → same preview outputs)
- Add structured logging (correlation: plan_id/step/role)
- Verify no PII in logs

**Phase 6: Contract Tests & Integration**
- Write contract tests (Intent → Preview → Execute flow)
- Integration tests with ProfileStore, ContextRAG
- Validate GLOBAL_SPEC envelope conformance

**Reads architectural docs**:
- [.specify/memory/constitution.md](.specify/memory/constitution.md) - PR rules, CI gates
- [docs/architecture/MODULAR_ARCHITECTURE.md](docs/architecture/MODULAR_ARCHITECTURE.md) - Blast radius, fault isolation
- [components/<Name>/dependencies.md](components/) - Library deps, scopes, internal deps

---

## How They Work Together

### **Example 1: Full Feature (Enhanced Workflow)**

```bash
# 1. COMMAND: Create spec with component template
/specify
# → Runs .specify/scripts/bash/create-new-feature.sh
# → Prompts: component or use case?
# → Uses appropriate template
# → Writes components/ProfileStore/SPEC.md (or usecases/<UseCase>/SPEC.md)

# 2. COMMAND: Create comprehensive LLD with architectural analysis
/design
# → Wraps /speckit.plan (runs full research)
# → Reads GLOBAL_SPEC.md, Project_HLD.md, MODULAR_ARCHITECTURE.md
# → Generates:
#   - components/ProfileStore/LLD.md (with blast radius, dependencies section)
#   - components/ProfileStore/dependencies.md (Python packages, external services, internal deps)
#   - components/ProfileStore/diagrams/flow.mmd (preview + execute flows)

# 3. COMMAND: Run full agent workflow with 6-phase tasks
/flow_orchestrate
# → Invokes planner agent:
#   - Reads SPEC + LLD + dependencies.md + MODULAR_ARCHITECTURE.md
#   - Writes tasks.md with Phase 0-6 (includes Phase 5: Safety)
# → Invokes implementer agent:
#   - Follows tasks.md phases
#   - Implements circuit breakers, fallbacks, determinism validation
# → Invokes verifier agent:
#   - Runs pytest on all phases
#   - Validates schemas match response.normalized.json
#   - Validates GLOBAL_SPEC envelope conformance
#   - Attaches preview evidence
# → Invokes pr-manager agent:
#   - Reads .github/pull_request_template.md
#   - Opens PR linking SPEC.md and LLD.md
#   - Includes test results and preview evidence

# 4. SKILL: Update component status tracker
/update-component-status
# → Scans components/ directory
# → Updates COMPONENT_STATUS.md with new component
```

---

### **Example 2: Architectural Decision (Manual Agent)**

```bash
# User asks: "Should we merge PreviewOrchestrator and ExecuteOrchestrator?"

# Manually invoke architect agent
# → Agent analyzes blast radius
# → Creates ADR with trade-offs (FOR/AGAINST arguments)
# → Recommends decision with rationale
```

---

### **Example 3: Quick Template (Skill)**

```bash
# User: "I need a quick SPEC template for ScheduleStore"

# Use skill for speed
/create-component-spec
# → Fast template generation
# → No branching, no validation
# → Direct write to components/ScheduleStore/SPEC.md
```

---

## Decision Matrix: What to Use When

| Task | Best Tool | Why |
|------|-----------|-----|
| **New feature (full validation)** | `/specify` → `/speckit.plan` → `/speckit.tasks` → `/speckit.implement` | Need validation gates, team collaboration |
| **New feature (quick)** | `/specify` → `/design` → `/flow_orchestrate` | Solo dev, fast iteration |
| **Quick SPEC template** | `/specify` command | Fast SPEC creation with component/use case templates |
| **Quick LLD template** | `/design` command | Fast LLD creation with architectural analysis |
| **Architectural decision** | `architect` agent (manual) | Need trade-off analysis, ADR |
| **Bug fix (tiny < 3 files)** | `quick-fix` skill | Typos, small logic errors |
| **Bug fix (medium)** | `verifier` agent → `implementer` agent | Need test validation |
| **Understand repo** | `/primer` command | First-time orientation |
| **Explain component** | `explain-component` skill | Understand specific component |
| **Validate consistency** | `/speckit.analyze` command | Check spec ↔ plan consistency |
| **Track progress** | `update-component-status` skill | After workflow completes |

---

## Three Workflow Options

### **Option 1: Spec Kit Full** (Rigorous, Team Collaboration)

```bash
/speckit.constitution  # One-time setup
/specify               # Create spec with validation (custom wrapper with templates)
/speckit.plan          # Generate technical plan
/speckit.tasks         # Break down to tasks
/speckit.implement     # Execute implementation
/speckit.checklist     # Quality validation
```

**Pros**: Full validation, consistency checks, constitution enforcement
**Cons**: More steps (8-9 commands), workbench in `.specify/`

---

### **Option 2: Custom Streamlined** (Fast, Solo Dev)

```bash
/primer               # Understand repo
/specify              # Create SPEC (component/use case template)
/design               # Create LLD + flowchart
/flow_orchestrate     # Run: planner → implementer → verifier → pr-manager
/update-component-status
```

**Pros**: Faster (4 commands), direct to `components/`, integrated workflow
**Cons**: No validation gates, manual constitution compliance

---

### **Option 3: Hybrid** (Recommended - Best of Both)

```bash
# Use custom /specify + Spec Kit for planning
/specify              # Create spec with validation (custom wrapper with templates)
/speckit.plan         # Generate technical plan
/speckit.tasks        # Break down to tasks (optional - planner agent can also do this)

# Use custom agents for execution (faster than /speckit.implement)
/flow_orchestrate     # Run: planner → implementer → verifier → pr-manager

# Update status
/update-component-status
```

**Pros**: Template customization + Spec Kit validation + fast execution
**Cons**: Slightly more manual than pure Spec Kit workflow

---

## Workflow Comparison

| Feature | Spec Kit Full | Custom Streamlined | Hybrid |
|---------|--------------|-------------------|--------|
| **Steps** | 8-9 commands | 4 commands | 5-6 commands |
| **Validation** | ✅ Full | ❌ Manual | ✅ Partial |
| **Speed** | Slower | Fastest | Medium |
| **Artifacts** | All (spec, plan, tasks, checklist) | Minimal (SPEC, LLD) | Core (spec, plan, tasks) |
| **Workbench** | ✅ `.specify/` | ❌ Direct | ✅ `.specify/` |
| **Constitution** | ✅ Enforced | ❌ Manual | ✅ Enforced |
| **Team Use** | ✅ Excellent | ❌ Solo only | ✅ Good |

---

## Key Design Principles

### 1. **Clean Separation**
- **Spec Kit** = `.specify/` directory (commands, templates, scripts, workbench)
- **Claude Code** = `.claude/` directory (commands, agents, skills, docs)
- **No overlap** between systems

### 2. **Commands ≠ Agents**
- **Spec Kit commands** run bash scripts (`.specify/scripts/bash/`)
- **Custom commands** either give Claude instructions OR invoke agents
- **Agents** are invoked BY commands or manually

### 3. **Three Tiers Serve Different Purposes**
- **Commands** = Orchestrators (run workflows)
- **Agents** = Specialists (execute roles)
- **Skills** = Shortcuts (bypass workflows)

### 4. **No Duplication**
- `/specify` (custom) keeps both templates (component + use case)
- `/design` (custom) is simpler alternative to `/speckit.plan`
- Skills serve quick needs; Spec Kit serves rigorous needs
- Architect agent for DECISIONS; not SPEC/LLD creation

---

## Summary

**Perfect balance achieved**:
1. **Git Spec Kit** lives in `.specify/` - full validation workflow
2. **Claude Commands** live in `.claude/commands/` - custom orchestration
3. **Claude Agents** live in `.claude/agents/` - specialized execution
4. **Skills** live in `.claude/skills/` - quick helpers

**They complement each other**:
- Need validation? → Spec Kit
- Need speed? → Custom commands + agents
- Need template? → Skills
- Need decision? → Architect agent

**No duplication, clear responsibilities, maximum flexibility.**

---

## Key Files Reference

| Need | File |
|------|------|
| Fast lookup (roles, layers, patterns) | `docs/architecture/QUICK_REFERENCE.md` |
| Component implementation status | `COMPONENT_STATUS.md` |
| Development workflows | `DEVELOPMENT_WORKFLOW.md` (this file) |
| Directory structure | `PROJECT_STRUCTURE.md` |
| System architecture | `docs/architecture/Project_HLD.md` |
| Universal contracts | `docs/architecture/GLOBAL_SPEC.md` |
| Project rules | `.claude/CLAUDE.md` |

---

## Next Steps

1. Check **COMPONENT_STATUS.md** to see what to build first
2. Use **/primer** to understand the repo
3. Pick a component from the critical path:
   - PluginRegistry (source of truth for tools)
   - Signer (plan signing with Ed25519)
   - ProfileStore (user preferences)
   - PlanLibrary (store past plans)
4. Run **/specify** to create its SPEC
5. Follow the full workflow above

---

## Tips for Efficient Usage

1. **Always start with /primer** before making changes
2. **Use QUICK_REFERENCE.md** for fast lookups
3. **Check COMPONENT_STATUS.md** to see progress
4. **Use architect agent** for decisions, not SPEC/LLD creation
5. **Use quick-fix skill** only for tiny bugs (< 10 lines, < 3 files)
6. **Update COMPONENT_STATUS.md** after completing components
