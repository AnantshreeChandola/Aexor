---
description: Orchestrate complete development workflow using specialized agents (planner → implementer → verifier → pr-manager)
---

/system
Act as the flow orchestrator. Execute the complete development workflow by invoking specialized agents in sequence.

## Inputs (ask if missing)
- Target: component/<Name> or usecases/<UseCase>
- Verify SPEC.md exists in specs/ directory and LLD.md exists in the target directory
- Confirm current branch is a feature branch (feat/*)

## Workflow Sequence

Execute the following agents using the Task tool in order:

1) **Planner Agent** (`Task` with custom agent)
   - Invoke the `planner` agent from `.claude/agents/planner.md`
   - Input: SPEC.md from specs/ directory and LLD.md from target directory
   - Output: Creates `components/<Name>/tasks.md` or `usecases/<UseCase>/tasks.md`
   - Maps acceptance criteria to concrete, ordered tasks
   - Includes exact file edits, dependencies, and architectural considerations

2) **Implementer Agent** (`Task` with custom agent)
   - Invoke the `implementer` agent from `.claude/agents/implementer.md`
   - Input: tasks.md from planner output
   - Output: Code, schemas, tests under `components/<Name>/**` or `usecases/**`
   - Enforces preview-first safety, idempotency, and GLOBAL_SPEC compliance
   - Updates component SPEC/LLD if needed (additive changes only)

3) **Verifier Agent** (`Task` with custom agent)
   - Invoke the `verifier` agent from `.claude/agents/verifier.md`
   - Input: Implementer changes
   - Output: Test results, schema validation, preview evidence
   - Runs pytest for use-case scenarios and touched component tests
   - Validates envelopes, schemas, and backward compatibility
   - Checks preview safety (no mutations in preview paths)

4) **PR Manager Agent** (`Task` with custom agent)
   - Invoke the `pr-manager` agent from `.claude/agents/pr-manager.md`
   - Input: Verifier artifacts and all implementer changes
   - Output: Single PR with all changes, conformance checklist, and evidence
   - Populates `.github/pull_request_template.md`
   - Never auto-merges; waits for CI and human approval

## Constraints
- Obey `.specify/memory/constitution.md` (no push to main, PR must link SPEC/LLD)
- Current branch must be `feat/*` (never main/master)
- Preview paths use stubs/mocks only (no external mutations)
- All agents use `model: inherit` (same model as parent conversation)
- Single PR policy: one PR per feature branch containing all changes

## Error Handling
- If SPEC.md missing from specs/ or LLD.md missing from target: suggest `/specify` then `/design` first
- If not on feature branch: suggest creating one with `git checkout -b feat/<area>-<short-desc>`
- If any agent fails: stop workflow and report error to user
- If CI fails on PR: implementer and verifier iterate until green

## Example Invocation
```
User: Implement the PlanLibrary component
Assistant: I'll orchestrate the full workflow:
1. Using planner agent to create tasks.md from SPEC/LLD
2. Using implementer agent to write code/schemas/tests
3. Using verifier agent to validate and test
4. Using pr-manager agent to create PR

[Invokes each agent sequentially using Task tool]
```

/assistant
This orchestrator command executes your custom agents in sequence:
- **planner** - Maps SPEC/LLD → tasks.md
- **implementer** - Implements tasks with safety enforcement
- **verifier** - Validates with tests and schema checks
- **pr-manager** - Creates conformant PR

All agents reference your project-specific architecture docs (GLOBAL_SPEC.md, Project_HLD.md, MODULAR_ARCHITECTURE.md, constitution.md) and enforce your development standards.
