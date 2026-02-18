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
   - docs/architecture/Project_HLD.md (system context, 4 layers, 16 components)
   - docs/architecture/MODULAR_ARCHITECTURE.md (modular design principles, blast radius isolation, **table ownership map**, **dependency graph**)
   - docs/architecture/SHARED_INFRASTRUCTURE.md (shared DB, users table, shared schemas)
   - docs/architecture/adr/*.md (Architecture Decision Records - decision context and rationale)

3) **Cross-validate against GLOBAL_SPEC and MODULAR_ARCHITECTURE (MANDATORY)**

   **GLOBAL_SPEC §2 contract alignment:**
   - If the component stores, consumes, or produces any canonical contract (Intent, Evidence Item, Plan, Signature, Wrappers, Approval Token), the data model MUST use the exact field names from GLOBAL_SPEC §2. Do NOT invent local field names.
   - Check `shared/schemas/` for existing implementations. If a shared schema exists, import it. If missing, note it as a prerequisite and design the local model to match GLOBAL_SPEC exactly.

   **MODULAR_ARCHITECTURE cross-validation:**
   - **Table ownership**: Tables claimed in the LLD must match the Table Ownership Map. Flag new tables as requiring a MODULAR_ARCHITECTURE update.
   - **Component dependencies**: If MODULAR_ARCHITECTURE shows a dependency (e.g., `→ VectorIndex`), the LLD must either route through that component's API or document the deviation with rationale and ADR reference.
   - **Upstream consumers**: Identify all components that depend on this one. The Interfaces section must cover every consumer's query/write patterns.
   - Flag any contradictions as explicit risks requiring resolution.

4) **Generate LLD.md from plan artifacts and architecture docs**
   Read all generated artifacts from step 1 AND canonical architecture docs from step 2, then create comprehensive LLD.md with:

   **Sections (required)**:
   - **Purpose & Scope** - What this component does, boundaries, which layer (Memory/Domain/Orchestration/Platform)
   - **Conformance** - Reference to GLOBAL_SPEC.md, MODULAR_ARCHITECTURE.md, and Project_HLD.md with **exact current version numbers** (check the document footers — do not use stale references)
   - **Architecture Overview** - High-level structure from data-model.md
     - Layer placement (from Project_HLD.md)
     - Blast radius analysis (from MODULAR_ARCHITECTURE.md)
     - Component boundaries and isolation strategy
   - **Interfaces** - From contracts/ directory
     - API handlers (thin wrappers)
     - service.preview() signature — if applicable for this layer
     - service.execute() signature — if applicable for this layer
     - **Consumer contracts**: For each upstream consumer (from MODULAR_ARCHITECTURE), define what it calls, what input it provides, what output it expects, and error responses it must handle
   - **Data Model** - From data-model.md
     - Domain entities — field names MUST match GLOBAL_SPEC §2 (see step 3)
     - **`user_id` on all owned entities** (FK to `users.user_id` from SHARED_INFRASTRUCTURE.md §1.2) — required for multi-user isolation, privacy tier enforcement, and user data deletion
     - Schema references
     - Use Pydantic v2 syntax (`min_length` not `min_items`, `datetime.now(timezone.utc)` not `datetime.utcnow()`)
   - **Database Schema** (REQUIRED for Memory Layer components)
     - Complete DDL for all owned tables (CREATE TABLE with types, constraints, defaults)
     - Index definitions (including pgvector HNSW with explicit `m`/`ef_construction` params if applicable)
     - Foreign keys (especially `user_id` FK to `users`)
     - Migration reference (Alembic)
   - **Adapters** - External integrations from plan.md
     - Provider integrations
     - Required scopes (read-only for preview, write for execute)
     - **Idempotency**: Both for provider calls (plan_id:step:arg_hash) AND for the component's own storage APIs (per GLOBAL_SPEC §8 — duplicate writes should return original result, not error)
     - Compensation operations (if declared)
     - **Shared Infrastructure Usage** - From PYTHON_GUIDE.md and Project_HLD.md §7
       - **Dependency injection**: Services are wired via lifespan-based DI (see Project_HLD.md §7 "Application Factory & Dependency Injection"). New components must:
         1. Initialize their service in `shared/app.py` lifespan function
         2. Add a `Depends()` function in `shared/dependencies.py`
         3. Use `Depends(get_<service>)` in route handlers — never construct services in routes
       - Database: Use shared/database/adapter.py (never duplicate connection setup)
       - Error handling: Use decorators from shared/database/error_handler.py
       - API errors: Use shared/api/error_handlers.py (ErrorHandlerMixin)
       - Models: Import shared tables from shared/database/models.py
       - Schemas: Import from shared/schemas/ where they exist
     - **Signature verification**: Prefer calling Signer component's API over local Ed25519 implementation. Document deviation if Signer is unavailable.
   - **Sequences** - Flow diagrams
     - Happy path
     - Error paths (signature failure, DB errors, external API failures)
     - **Retry/idempotency path**: What happens when the caller retries after a network failure?
     - **Consumer query paths**: Sequence for each upstream consumer's primary flow
     - **Graceful degradation**: What response/error does the component return when dependencies are down?
     - Retry/backoff strategies
   - **Dependencies & External Integrations**
     - Python packages with version constraints and justifications — verify versions are current (not deprecated)
     - Include `alembic` for any component that owns database tables
     - External APIs/services with SLA requirements
     - Internal infrastructure dependencies (shared utilities)
     - Component dependencies — **must match MODULAR_ARCHITECTURE**
     - Development and testing dependencies
   - **Observability & Safety** - From plan.md
     - Structured logging (correlation: plan_id/step/role)
     - No PII in logs
     - Error classes
     - **Prometheus metrics**: Define specific metric names, types (histogram/counter/gauge), and labels. At minimum: operation duration, error counter, queue depth (if queues exist), circuit breaker state (if applicable).
     - HITL gates (if applicable)
   - **Caching Strategy** (REQUIRED if component uses Redis)
     - Which queries/data are cached, cache key structure, TTL values
     - Cache invalidation rules (when does a write invalidate cached reads?)
     - Graceful degradation when Redis is unavailable
   - **Non-Functional Requirements** - From SPEC.md
     - Performance tables — local targets should be relaxed vs cloud; include p99 for hot-path operations
     - Availability targets (99.9% cloud, best-effort local)
     - Throughput requirements (single-user vs multi-user)
     - Scalability targets (local/cloud/enterprise)
     - Testing strategy
   - **Architectural Considerations** from MODULAR_ARCHITECTURE.md
     - Blast radius containment
     - Fault isolation strategy (circuit breakers, fallbacks)
     - Cross-component interactions
     - Determinism guarantees
     - State management (stateless vs stateful, persistence needs)
     - **Background task durability**: If using in-process queues (asyncio.Queue, etc.), document data loss risk on restart and whether a persistent queue is needed for production
   - **Architecture Decision Records** from docs/architecture/adr/*.md
     - Reference relevant ADRs
     - Document adherence to established decisions
     - Note new decisions requiring ADR creation
   - **Risks & Open Questions** - From research.md and plan.md

5) **Post-generation validation checklist (MANDATORY)**
   Before writing the final LLD.md, verify:
   - [ ] Data model fields match GLOBAL_SPEC §2 contracts (no invented field names)
   - [ ] `user_id` present on all owned entities
   - [ ] Conformance header references current document versions
   - [ ] Table ownership matches MODULAR_ARCHITECTURE Table Ownership Map
   - [ ] Component dependencies match MODULAR_ARCHITECTURE dependency graph
   - [ ] Every upstream consumer has a documented interface contract
   - [ ] Storage APIs are idempotent (duplicate ID returns original, not error)
   - [ ] DDL included for owned tables with indexes (Memory Layer)
   - [ ] Prometheus metrics defined with names and types
   - [ ] No deprecated library versions or API models
   - [ ] Evidence Item keys use deterministic generation (no Python `hash()`)

   Fix any failures before writing. Document intentional deviations in Risks with rationale.

6) **Generate Mermaid flowchart**
   Create comprehensive flow diagram from sequences:
   - Preview flow (data fetching, normalization)
   - Execute flow (provider calls, result handling)
   - Error handling
   - HITL gates (if applicable)

   Save as markdown file with mermaid code block:
   - `components/<Name>/diagrams/flow.md` (for components)
   - `.specify/specs/###-name/diagrams/flow.md` (for use cases, then copy to usecases/<UseCase>/diagrams/)

7) **Write to target directory**
   - **For components**: Write LLD.md and diagrams to `components/<Name>/`
   - **For use cases**: Write LLD.md and diagrams to `usecases/<UseCase>/`
   - Create `diagrams/` directory if missing

8) **Report results**
   Print:
   - LLD path
   - Flowchart path
   - Summary: Key architectural decisions and design rationale
   - Validation checklist results (all passed / deviations noted)
   - Next step: `/flow_orchestrate` or `/speckit.tasks`

9) **Cleanup temporary artifacts**
   After successful LLD creation, automatically remove the temporary `.specify/specs/###-name/` directory and all its contents.

## Constraints
- Use `/speckit.plan` for comprehensive research and planning
- Extract and organize artifacts into clean LLD.md
- **Must integrate dependencies section in LLD.md** with justifications (no separate dependencies.md file)
- Only write LLD.md and diagrams/flow.md - no code/schemas/tests
- Use existing directories; create diagrams/ if missing
- Prefer canonical paths (`components/<Name>/`) over workbench (`.specify/specs/`)
- **Automatically cleanup temporary artifacts** - remove `.specify/specs/###-name/` directory after successful LLD creation
- **GLOBAL_SPEC §2 field alignment is non-negotiable** — reference the spec, match it exactly
- **user_id is mandatory** on all owned entities
- **Post-generation validation checklist must pass** before writing final LLD

/assistant
This command leverages Spec Kit's thorough planning workflow to generate a comprehensive LLD with:
1. Research-backed design decisions
2. Complete data modeling — validated against GLOBAL_SPEC §2 canonical contracts
3. API contract specifications — including consumer-driven contracts for all upstream callers
4. Detailed flow diagrams — including consumer query paths and graceful degradation
5. **Integrated dependencies section** with Python packages, external services, and internal component relationships
6. **Architectural considerations** (blast radius, fault isolation, determinism)
7. Layer placement and modular architecture compliance — cross-validated against MODULAR_ARCHITECTURE dependency graph and table ownership
8. **Database DDL** with indexes for Memory Layer components
9. **Prometheus metrics** with specific names and types
10. **Caching strategy** with invalidation rules (if Redis is used)
11. **Post-generation validation checklist** preventing common LLD defects

**Key architectural docs consulted**:
- GLOBAL_SPEC.md (universal contracts — field-by-field validation)
- Project_HLD.md (4 layers, 16 components)
- MODULAR_ARCHITECTURE.md (blast radius, dependency graph, table ownership)
- SHARED_INFRASTRUCTURE.md (shared DB, users table, shared schemas)

The output is organized in the component/use case directory for easy reference during implementation.
