# Component Specification: ⟨Name⟩

**Feature Branch**: `⟨NNN-name⟩`
**Created**: ⟨DATE⟩
**Status**: Draft
**Input**: User description: "⟨component: Name⟩"

---

## ⚡ Quick Guidelines

* Focus on **WHAT & WHY** (no implementation details)
* Written for stakeholders and implementers
* Must be **testable, deterministic, and unambiguous**
* This document is the **single source of truth** for behavior

### Section Rules

* All **mandatory** sections must be present
* Remove optional sections only if clearly not applicable

---

## Scope & Non-Goals *(mandatory)*

### In Scope

* What this component explicitly owns and guarantees

### Out of Scope (Non-Goals)

* What this component explicitly does **not** handle

### Assumptions

* Preconditions that must already be satisfied by upstream systems

---

## User Scenarios & Testing *(mandatory)*

### Primary User Story

Plain-language description of how a user or system interacts with this component and what value it provides.

### Acceptance Scenarios

At least one **success** and one **failure** scenario are required.

1. **Given** … **When** … **Then** …
2. **Given** … **When** … **Then** …

### Edge Cases

* Boundary conditions
* Invalid inputs
* Concurrency or retry behavior

---

## Decision Rules (Deterministic Order) *(mandatory)*

Explicit, ordered rules that define how decisions are made.
Rules are evaluated **top to bottom**; first match wins unless stated otherwise.

1. …
2. …
3. …

---

## Requirements *(mandatory)*

### Functional Requirements

* **FR-001: External Contract**
  Define all inputs and outputs, including:

  * Required vs optional fields
  * Valid ranges or constraints
  * Success and error response shapes

* **FR-002: Execution Semantics**

  * Preview vs Execute behavior
  * Preview must have **no external side effects**
  * Execute must occur only through approved adapters

* **FR-003: Safety & Security**

  * Least-privilege scopes
  * Explicit authorization expectations
  * Sensitive data handling guarantees

* **FR-004: Idempotency & Compensation**

  * Idempotency key definition and scope
  * Expected behavior on retries
  * Compensation or rollback operations (if applicable)

* **FR-005: Schemas**

  * JSON Schemas for all payloads
  * Must conform to `GLOBAL_SPEC` Preview/Execute wrappers

* **FR-006: Observability**

  * Structured logging (no PII)
  * Correlation identifiers (e.g., plan_id, step_id, role)
  * Stable error classes and codes

* **FR-007: Determinism**

  * Same inputs must produce the same preview outputs
  * No hidden or implicit state

* **FR-008: Non-Functional Requirements (NFRs)**

  * Latency budgets
  * Availability expectations
  * Resource limits

* **FR-009: Backward Compatibility**

  * Versioning strategy
  * Breaking-change policy
  * ADR required for incompatible changes

* **FR-010: Registry Impacts**
  For each affected registry entry, specify:

  * `uses / calls → node / op / params`
  * Previewability
  * Required scopes
  * Idempotency behavior
  * Compensation support

---

## Invariants & Guarantees *(mandatory)*

Statements that must **always** hold true, regardless of input or execution path.

* …
* …
* …

---

## Key Entities *(include if data is involved)*

For each entity, describe **behavioral meaning**, not storage details.

* **⟨Entity⟩**

  * Purpose
  * Key fields (conceptual)
  * Cardinality
  * Ownership
  * Lifecycle (create/update/delete semantics)

---

## Review & Acceptance Checklist *(mandatory)*

* [ ] No implementation details present
* [ ] Scope, non-goals, and assumptions clearly defined
* [ ] Acceptance scenarios include failure paths
* [ ] Decision rules are explicit and ordered
* [ ] Invariants and guarantees listed
* [ ] GLOBAL_SPEC Preview/Execute wrappers respected
* [ ] Idempotency and compensation behavior defined
* [ ] Registry impacts fully documented

---

## Conformance

This work conforms to:

* `docs/architecture/GLOBAL_SPEC.md` v2
* `docs/architecture/Project_HLD.md`
