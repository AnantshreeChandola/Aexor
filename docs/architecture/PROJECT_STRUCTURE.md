# Project Structure (Source of Truth)

## Global
- **.specify/memory/constitution.md** — repo laws (PRs only, green CI, no secrets). Top-level `CONSTITUTION.md` is a pointer.
- **.claude/** — working instructions/playbooks for Claude (no PRPs here).
- **docs/architecture/Project_HLD.md** — system HLD (diagram, components, safety).
- **docs/architecture/adr/** — Architecture Decision Records (tiny, numbered).
- **docs/planning/** — PRP docs (Plan/PR Proposals) and index.
- **plans/** — optional: plan drafts/signed/records for provenance.
- **shared/** — shared tool registry + JSON Schemas used across components.
- **tests/** — cross-component/system-level tests.
- **.github/workflows/** — CI pipelines.
- **usecases/** — use‑case packets (SPEC.md, LLD.md, plans/, tests/, fixtures/)

## Component-first layout (no global /spec)
Each component is a self-contained packet under `/components/<Name>/`:
