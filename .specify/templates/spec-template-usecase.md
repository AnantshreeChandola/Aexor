# Use Case Specification: ⟨UseCase⟩

**Feature Branch**: `⟨NNN-usecase⟩`
**Created**: ⟨DATE⟩
**Status**: Draft
**Input**: User description: "⟨use case⟩"

---

## Execution Flow (main)

```
1. Parse user description from Input
2. Extract actors, actions, data, constraints
3. Mark ambiguities with [NEEDS CLARIFICATION: …]
4. Define Scope & Assumptions
5. Fill User Scenarios & Testing
6. Define Decision Rules (ordered)
7. Generate Acceptance Criteria (testable)
8. Map to impacted components
9. Run Review Checklist
10. SUCCESS (spec ready for design)
```

---

## ⚡ Quick Guidelines

* Focus on **WHAT & WHY** (avoid implementation details)
* Describes **orchestration intent**, not component internals
* Must be **testable, deterministic, and safe**
* This document defines **end-to-end behavior** for the use case

---

## Scope & Assumptions *(mandatory)*

### In Scope

* What this use case orchestrates end-to-end

### Out of Scope (Non-Goals)

* Explicit non-goals or responsibilities delegated elsewhere

### Assumptions

* Preconditions expected to hold before execution

---

## User Scenarios & Testing *(mandatory)*

### Primary User Story

Plain-language journey describing the intent and expected outcome of this use case.

### Acceptance Scenarios

At least one **success** and one **failure** scenario are required.

1. **Given** … **When** … **Then** …
2. **Given** … **When** … **Then** …

### Edge Cases

* Missing or ambiguous user intent
* Conflicting constraints
* Partial execution, retries, or timeouts

---

## Decision Rules (Deterministic Order) *(mandatory)*

Explicit, ordered rules governing orchestration and branching behavior.
Rules are evaluated **top to bottom** unless stated otherwise.

1. …
2. …
3. …

---

## Requirements / Acceptance Criteria *(mandatory)*

* **AC-001**: Observable outcome expected on success
* **AC-002**: Behavior on invalid, unsafe, or ambiguous input
* **AC-003**: Retry and idempotency expectations (if applicable)

---

## Invariants & Success Guarantees *(mandatory)*

Conditions that must **always** hold true for this use case.

* No irreversible side effects without explicit Execute approval
* All writes pass through HITL gates (if defined)
* Use case completion is observable and auditable

---

## Intent & Gates

* **Intent shape (GLOBAL_SPEC 2.1)**:

  * `action`
  * `entities`
  * `constraints`
  * `timezone`
  * `context_budget`

* **HITL Gates**:

  * `gate_id`
  * Trigger condition
  * Approval requirement before any writes

---

## Evidence Needs (ContextRAG)

* Typed context keys to request (e.g., preferences, history, contacts)
* Context budget limits
* Explicit exclusions (what must **not** be fetched)

---

## Scopes & Safety

* Minimal provider scopes by phase:

  * **Preview** → read-only
  * **Execute** → write (explicit)
* Idempotency expectations for writes
* Compensation or rollback expectations (if declared)

---

## Component Mapping *(behavioral)*

List impacted components and **why** they are involved.
Avoid file-level or implementation details.

* `components/⟨Name⟩`: behavior impacted
* …

---

## Dependencies & Risks

* External systems or services
* Known ambiguity or risk areas
* Safety, compliance, or data sensitivity concerns

---

## Review Checklist *(mandatory)*

* [ ] Scope, non-goals, and assumptions defined
* [ ] Acceptance scenarios include failure paths
* [ ] Decision rules are explicit and ordered
* [ ] Invariants and success guarantees listed
* [ ] Components mapped by behavior
* [ ] Safety, scopes, and gates clearly defined

---

## Conformance

This work conforms to:

* `docs/architecture/GLOBAL_SPEC.md` v2
* `docs/architecture/Project_HLD.md`
