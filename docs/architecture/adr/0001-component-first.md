# ADR 0001 — Component-first folder layout (no global /spec)
Date: 2025-09-16  
Status: Accepted

## Context
We want self-contained components that include spec, design, schemas, and tests. This improves discoverability, makes PRs/CI clearer, and helps agents follow predictable rules.

## Decision
Adopt `/components/<Name>/` packets containing:
- `SPEC.md` — requirements & acceptance
- `LLD.md` — implementation details (APIs, sequences, data, failure modes)
- `api/` — routes/controllers/handlers (thin I/O)
- `service/` — component services (implements `preview()` and `execute()`); no cross‑component orchestration or connector bindings
- `domain/` — pure domain models & invariants (no external deps)
- `adapters/` — external integrations (HTTP, DB, queues)
- `schemas/` — component-specific JSON Schemas
- `tests/` — acceptance/contract tests for the component
- `diagrams/` — mermaid/plantuml
- `notes/` — component-local ADRs/decisions (optional)

## Consequences
- **Pros:** modular reviews, clear contracts, agent/CI friendly, easy navigation.
- **Cons:** possible duplication vs shared schemas.
- **Mitigation:** keep truly shared schemas in `/shared/schemas/`.

## Alternatives considered
Keep a global `/spec` tree — rejected for discoverability and coupling.
