<!--
Title format: feat(<component>): <short description>
Examples:
  feat(Planner): add default-untrusted validation rule
  feat(shared): add ts and nonce fields to Signature schema
  fix(Signer): handle missing plan_hash in verification

Fill every section below. Write "N/A" for sections that don't apply.
-->

## Summary
<!-- 1-3 sentences: what changed and why -->


## Spec / Design References
- Spec: <!-- Link to specs/<NNN>/spec.md or "N/A" -->
- LLD: <!-- Link to components/<Name>/LLD.md or "N/A" -->
- GLOBAL_SPEC section: <!-- e.g., "§2.3.2 Plan validation rules" or "N/A" -->

## Components Touched
<!-- Check the layer and list affected components -->
- [ ] **Memory Layer**: <!-- ProfileStore, History, PlanLibrary, VectorIndex -->
- [ ] **Domain Layer**: <!-- Intake, ContextRAG, Planner, PolicyEngine, Signer, PluginRegistry, PlanWriter -->
- [ ] **Orchestration Layer**: <!-- PreviewOrchestrator, ApprovalGate, ExecuteOrchestrator, ExecutionMonitor -->
- [ ] **Shared Infrastructure**: <!-- shared/schemas, shared/database, shared/app.py, shared/dependencies.py -->

## Schema Changes
- [ ] Pydantic models updated (`shared/schemas/*.py`)
- [ ] JSON schemas updated (`shared/schemas/*.schema.json` or `components/<Name>/schemas/`)
- [ ] Database models updated (`shared/database/models.py`)
- [ ] No schema changes

## Test Plan
- [ ] Unit tests added/updated: `pytest components/<Name>/tests/`
- [ ] Shared tests pass: `pytest tests/shared/`
- [ ] Lint passes: `ruff check .`
- Test summary: <!-- e.g., "12 new tests for trust boundary validation, all 67 existing tests still pass" -->

## Evidence
<!-- Links to test output, screenshots, logs, or "N/A" -->


## Risks / Rollback
- Breaking changes: <!-- e.g., "PlanStep schema adds required field" or "None" -->
- Rollback plan: <!-- e.g., "Revert commit" or "Feature-flagged" -->

---

### Checklist
- [ ] Branch is `feat/<short-name>` based off `master`
- [ ] SPEC/LLD links included (or "N/A" for infra changes)
- [ ] Tests added/updated and passing
- [ ] Schemas validated (Pydantic + JSON schema consistent)
- [ ] No secrets, credentials, or PII committed
- [ ] `ruff check .` passes
- [ ] Reviewer(s) assigned

### Safety (GLOBAL_SPEC v3.0)
- [ ] Preview uses stubs/mocks only — no side effects
- [ ] Plan signature covers all planning-time fields (runtime fields excluded from hash)
- [ ] Booker steps require gate_id (HITL enforcement)
- [ ] Trust boundary respected: no direct API → Tier 2 Reasoner references without Tier 1 sanitization
- [ ] PolicyEngine attestations for any runtime plan modifications
- [ ] N/A — this PR does not affect plan execution or safety model
