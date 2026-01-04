---
description: Generate comprehensive LLD using Spec Kit plan workflow. Creates LLD.md, Mermaid diagrams, and lists library dependencies.
---

/system
Act as a design orchestrator. Use Spec Kit's `/speckit.plan` workflow to generate comprehensive design artifacts, then extract and organize them for the component/use case.

/user
## Inputs (ask if missing)
- Target: component/<Name> or usecases/<UseCase>
- SPEC path: absolute path to SPEC.md (or will be found in `.specify/specs/` if in workbench)

## Steps

1) **Run Spec Kit plan workflow**
   Execute `/speckit.plan` to generate comprehensive design artifacts:
   - Research phase (resolve NEEDS CLARIFICATION)
   - Data model (entities, relationships)
   - API contracts
   - Implementation plan
   - Quickstart scenarios

   This creates artifacts in `.specify/specs/###-name/`:
   - plan.md (implementation plan)
   - research.md (technical decisions)
   - data-model.md (entities, schemas)
   - contracts/ (API specifications)
   - quickstart.md (integration scenarios)

2) **Read canonical repo rules and architecture**
   - .specify/memory/constitution.md (PR rules, CI gates, no push to main)
   - docs/dev/PYTHON_GUIDE.md (DRY principles, shared infrastructure, error handling patterns)
   - PROJECT_STRUCTURE.md (component-first structure, directory layout)
   - docs/architecture/GLOBAL_SPEC.md (Intent + Preview/Execute envelopes, NFRs)
   - docs/architecture/Project_HLD.md (system context, 4 layers, 16 components, dual runtime)
   - docs/architecture/MODULAR_ARCHITECTURE.md (modular design principles, blast radius isolation)
   - docs/architecture/adr/*.md (Architecture Decision Records - decision context and rationale)

3) **Generate LLD.md from plan artifacts and architecture docs**
   Read all generated artifacts from step 1 AND canonical architecture docs from step 2, then create comprehensive LLD.md with:

   **Sections (required)**:
   - **Purpose & Scope** - What this component does, boundaries, which layer (Memory/Domain/Orchestration/Platform)
   - **Conformance** - Reference to GLOBAL_SPEC.md v2 and MODULAR_ARCHITECTURE.md
   - **Architecture Overview** - High-level structure from data-model.md
     - Layer placement (from Project_HLD.md)
     - Blast radius analysis (from MODULAR_ARCHITECTURE.md)
     - Component boundaries and isolation strategy
   - **Interfaces** - From contracts/ directory
     - API handlers (thin wrappers)
     - service.preview() signature (inputs → normalized output)
     - service.execute() signature (inputs → provider result)
   - **Data Model** - From data-model.md
     - Domain entities
     - Schema references (response.normalized.json)
   - **Adapters** - External integrations from plan.md
     - Provider integrations
     - Required scopes (read-only for preview, write for execute)
     - Idempotency strategy (plan_id:step:arg_hash)
     - Compensation operations (if declared)
     - **Shared Infrastructure Usage** - From PYTHON_GUIDE.md
       - Database: Use shared/database/adapter.py (never duplicate connection setup)
       - Error handling: Use decorators from shared/database/error_handler.py
       - API errors: Use shared/api/error_handlers.py (ErrorHandlerMixin)
       - Models: Import shared tables from shared/database/models.py
       - Schemas: Use universal schema approach where applicable
   - **Sequences** - Flow diagrams
     - Happy path (preview → execute)
     - Error paths
     - Retry/backoff strategies
   - **Dependencies & External Integrations** - **NEW SECTION**
     - Python packages (from plan.md tech stack) with version constraints and justifications
     - External APIs/services with SLA requirements
     - Internal infrastructure dependencies (shared utilities)
     - Component dependencies (upstream/downstream relationships)
     - Development and testing dependencies
   - **Observability & Safety** - From plan.md
     - Structured logging (correlation: plan_id/step/role)
     - No PII in logs
     - Error classes
     - HITL gates (if applicable)
   - **Non-Functional Requirements** - From SPEC.md
     - Performance tables (Local vs Cloud expected latency)
     - Availability targets (99.9% cloud, best-effort local)
     - Throughput requirements (single-user vs multi-user scenarios)
     - Scalability targets (deployment scenarios: local/cloud/enterprise)
     - Testing strategy (simplified for single-user, contract tests separate)
   - **Architectural Considerations** - **NEW SECTION** from MODULAR_ARCHITECTURE.md
     - Blast radius containment (what fails if this component fails?)
     - Fault isolation strategy (circuit breakers, fallbacks)
     - Cross-component interactions (which components does this depend on?)
     - Determinism guarantees (same inputs → same outputs for preview)
     - State management (stateless vs stateful, persistence needs)
   - **Architecture Decision Records** - **NEW SECTION** from docs/architecture/adr/*.md
     - Reference relevant ADRs that impact this component design
     - Document how component adheres to established architectural decisions
     - Note any new decisions that may require ADR creation
     - Cross-reference decision rationale for design choices
   - **Risks & Open Questions** - From research.md and plan.md

4) **Generate Mermaid flowchart**
   Create comprehensive flow diagram from sequences:
   - Preview flow (data fetching, normalization)
   - Execute flow (provider calls, result handling)
   - Error handling
   - HITL gates (if applicable)

   Save as markdown file with mermaid code block:
   - `components/<Name>/diagrams/flow.md` (for components)
   - `.specify/specs/###-name/diagrams/flow.md` (for use cases, then copy to usecases/<UseCase>/diagrams/)

5) **Write to target directory**
   - **For components**: Write LLD.md and diagrams to `components/<Name>/`
   - **For use cases**: Write LLD.md and diagrams to `usecases/<UseCase>/`
   - Create `diagrams/` directory if missing

6) **Report results**
   Print:
   - LLD path: `components/<Name>/LLD.md` or `usecases/<UseCase>/LLD.md` (includes integrated dependencies section)
   - Flowchart path: `components/<Name>/diagrams/flow.md`
   - Summary: Key architectural decisions and design rationale
   - Next step: `/flow_orchestrate` or `/speckit.tasks`

7) **Cleanup temporary artifacts**
   After successful LLD creation, automatically remove the temporary `.specify/specs/###-name/` directory and all its contents:
   - Delete plan.md, research.md, data-model.md
   - Delete contracts/ directory
   - Delete quickstart.md  
   - Delete diagrams/ directory (if created by speckit.plan)
   - Remove the entire spec directory to keep workspace clean

## Constraints
- Use `/speckit.plan` for comprehensive research and planning
- Extract and organize artifacts into clean LLD.md
- **Must integrate dependencies section in LLD.md** with justifications (no separate dependencies.md file)
- Only write LLD.md and diagrams/flow.md - no code/schemas/tests
- Use existing directories; create diagrams/ if missing
- Prefer canonical paths (`components/<Name>/`) over workbench (`.specify/specs/`)
- **Automatically cleanup temporary artifacts** - remove `.specify/specs/###-name/` directory after successful LLD creation

/assistant
This command leverages Spec Kit's thorough planning workflow to generate a comprehensive LLD with:
1. Research-backed design decisions
2. Complete data modeling
3. API contract specifications
4. Detailed flow diagrams
5. **Integrated dependencies section** with Python packages, external services, and internal component relationships
6. **Architectural considerations** (blast radius, fault isolation, determinism) 
7. Layer placement and modular architecture compliance

**Key architectural docs consulted**:
- GLOBAL_SPEC.md (universal contracts)
- Project_HLD.md (4 layers, 16 components, dual runtime)
- MODULAR_ARCHITECTURE.md (blast radius isolation, fault tolerance)

The output is organized in the component/use case directory for easy reference during implementation.
