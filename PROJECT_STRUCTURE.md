# Project Structure (Source of Truth)

```
Personal-agent/
├── .claude/                    # Claude Code configuration
│   ├── commands/               # Custom Claude commands (4 files)
│   ├── agents/                 # Specialized agents (5 files)
│   ├── skills/                 # Quick helper skills (8 files)
│   ├── CLAUDE.md               # Project rules
│   └── settings.json           # Permissions/MCP config
├── .specify/                   # Git Spec Kit workspace
│   ├── commands/               # Spec Kit commands
│   ├── templates/              # Spec templates
│   ├── scripts/bash/           # Spec Kit bash scripts
│   └── memory/                 # Constitution
├── components/                 # Component implementations (16 components)
│   └── <Name>/                 # Each component is self-contained
│       ├── LLD.md              # Low-level design
│       ├── schemas/            # Pydantic models
│       ├── tests/              # Unit and integration tests
│       ├── api/                # API handlers
│       ├── service/            # Business logic
│       ├── domain/             # Domain models
│       └── adapters/           # External integrations
├── usecases/                   # End-to-end use case implementations
│   └── <UseCase>/              # Each use case is self-contained
│       ├── LLD.md              # Low-level design
│       ├── plans/              # Execution plans
│       ├── tests/              # Use case tests
│       └── fixtures/           # Test data
├── docs/                       # Architecture and development documentation
│   ├── architecture/           # System design documents
│   │   ├── Project_HLD.md      # High-level design
│   │   ├── GLOBAL_SPEC.md      # Universal contracts
│   │   ├── MODULAR_ARCHITECTURE.md # Blast radius isolation
│   │   └── adr/                # Architecture Decision Records
│   └── dev/                    # Development guides
│       └── PYTHON_GUIDE.md     # Python standards
├── shared/                     # Cross-component utilities
│   └── schemas/                # Shared schemas (Intent, Evidence, Plan, Signature)
├── tests/                      # Cross-component/system-level tests
├── specs/                      # Component and use case SPEC files
│   └── <spec-id>/              # Spec workbench (contains spec.md)
├── .github/workflows/          # CI/CD pipelines
├── migrations/                 # Database migrations
├── .gitignore                  # Git ignore patterns
├── COMPONENT_STATUS.md         # Implementation tracker (16 components)
├── DEVELOPMENT_WORKFLOW.md     # Development workflow guide
├── PROJECT_STRUCTURE.md        # This file
├── README.md                   # Project overview
└── pyproject.toml              # Python project configuration
```

## Component-first Architecture

**Note**: Component SPEC files are stored in `/specs/<spec-id>/spec.md`, not within component directories.

Each component under `/components/<Name>/` is a self-contained packet with:

- **LLD.md** - Low-level design and implementation details  
- **schemas/** - Pydantic models and validation schemas
- **tests/** - Unit and integration tests
- **api/** - API handlers (thin wrappers)
- **service/** - Business logic layer
- **domain/** - Domain models and core logic
- **adapters/** - External service integrations

## Use Case Architecture

**Note**: Use case SPEC files are stored in `/specs/<spec-id>/spec.md`, not within use case directories.

Each use case under `/usecases/<UseCase>/` implements end-to-end workflows with:

- **LLD.md** - Low-level design for the workflow
- **plans/** - Execution plan templates and drafts
- **tests/** - End-to-end scenario tests
- **fixtures/** - Test data and mock responses

## Claude Code Structure

The `.claude/` directory contains Claude Code configuration:

### Commands (4 files)
- **primer.md** - Repository overview and next step suggestions
- **specify.md** - Create SPEC using Git Spec Kit templates
- **design.md** - Generate LLD and flow diagrams from SPEC
- **flow_orchestrate.md** - Execute full agent workflow

### Agents (5 files)
- **planner.md** - Maps SPEC/LLD acceptance criteria to ordered tasks
- **implementer.md** - Writes code, tests, and schemas with safety enforcement
- **verifier.md** - Runs tests, validates schemas and backward compatibility
- **pr-manager.md** - Creates PRs with proper templates and evidence
- **architect.md** - Makes architectural decisions and analyzes trade-offs

### Skills (8 files)
- **create-component-spec.md** - Generate SPEC.md templates
- **create-component-lld.md** - Generate LLD.md templates  
- **review-architecture.md** - Architectural review for components
- **review-plan-schema.md** - Validate plan JSON against GLOBAL_SPEC
- **explain-component.md** - Explain components with examples
- **add-test-cases.md** - Generate tests from acceptance criteria
- **quick-fix.md** - Fast bug fixes for small issues
- **update-component-status.md** - Update implementation tracker

## Key Documentation

### Root Level
- **README.md** - Project overview and getting started
- **COMPONENT_STATUS.md** - Implementation tracker for all 16 components
- **DEVELOPMENT_WORKFLOW.md** - Complete development workflow guide
- **PROJECT_STRUCTURE.md** - This file (directory structure reference)

### Architecture Documentation  
- **docs/architecture/Project_HLD.md** - High-level system design
- **docs/architecture/GLOBAL_SPEC.md** - Universal contracts and patterns
- **docs/architecture/MODULAR_ARCHITECTURE.md** - Blast radius isolation patterns

### Development Guides
- **docs/dev/PYTHON_GUIDE.md** - Python coding standards and practices
