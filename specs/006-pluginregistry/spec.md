# Component Specification: PluginRegistry

**Feature Branch**: `feat/pluginregistry`
**Created**: 2026-03-05
**Status**: Draft
**Input**: User description: "PluginRegistry — Source of truth for available tools, their capabilities, credential ID templates, and n8n node bindings"

---

## Scope & Non-Goals

### In Scope

* **Tool catalog**: Maintain a versioned registry of all available tools (Google Calendar, Slack, email, etc.) with their operations and metadata
* **Credential ID templates**: Map user + integration → n8n credential ID pattern (e.g., `gcal_user_{{user_id}}_{{account_name}}`) — templates only, never actual credential values
* **Operation metadata**: For each tool operation, store: n8n node binding, previewable flag, idempotent flag, required scopes, and optional compensation operation
* **Registry snapshots**: Provide a frozen snapshot (vR) to Planner as a deterministic input (GLOBAL_SPEC §2.0)
* **Schema validation**: Validate all registry entries against a strict JSON schema before persistence
* **CRUD operations**: Allow admin to add, update, and deactivate tools and operations
* **Scope resolution**: Given a plan's required scopes, verify that the referenced tools support those scopes

### Out of Scope (Non-Goals)

* **Credential storage or management**: Actual OAuth tokens, API keys, and secrets are stored in n8n Secrets Vault — PluginRegistry never touches them (GLOBAL_SPEC §8)
* **Credential rotation or refresh**: Handled by n8n credential manager
* **Tool execution**: PluginRegistry is a read-heavy catalog; execution is handled by WorkflowBuilder and n8n
* **User-facing Preview/Execute model**: PluginRegistry is an internal component (like ProfileStore) — the safety model applies to plans that reference registry data, not to registry operations themselves
* **Dynamic tool discovery**: MVP uses a static, admin-managed registry — no auto-discovery from n8n
* **Rate limiting or quota tracking**: Per-tool rate limits are enforced by n8n and provider APIs, not the registry

### Assumptions

* Admin user or system bootstrap process populates the initial registry entries
* n8n is self-hosted and available for credential resolution at execution time
* Tool IDs follow a namespace convention: `<provider>.<service>` (e.g., `google.calendar`, `slack.messaging`)
* Each tool has at least one operation
* Registry changes are infrequent (admin-driven, not per-request)
* PostgreSQL 16 is available as the persistence backend

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register and Retrieve Tool Definitions (Priority: P1)

As the Planner component, I need to retrieve the full catalog of available tools and their operations so that I can generate valid plans that reference real tool capabilities.

**Why this priority**: Core functionality — without tool definitions, no plan can be generated. This is the minimum viable PluginRegistry.

**Independent Test**: Can be fully tested by registering a tool (e.g., `google.calendar` with `list_free_busy` and `create_event` operations) and retrieving it. Delivers immediate value to Planner for plan generation.

**Acceptance Scenarios**:

1. **Given** a tool entry `google.calendar` is registered with two operations, **When** Planner requests the tool by `tool_id`, **Then** PluginRegistry returns the full tool definition including all operations, credential template, and n8n credential type

2. **Given** no tools are registered, **When** Planner requests the full catalog, **Then** PluginRegistry returns an empty list (not an error)

3. **Given** a tool entry with an operation marked `previewable: true`, **When** Planner requests that operation's metadata, **Then** the response includes `previewable: true` so Planner can include it in preview-mode plans

4. **Given** a tool entry with `compensation: "delete_event"` on the `create_event` operation, **When** Planner retrieves that operation, **Then** the compensation field is present for ExecuteOrchestrator to use during rollback

---

### User Story 2 - Registry Versioning and Pre-Execution Validation (Priority: P1)

As the Planner component, I need a version counter on the registry so that plans record which registry version they were built against. Before execution, the system validates that all referenced tools are still active.

**Why this priority**: Determinism is a core architectural principle (GLOBAL_SPEC §2.0). The version counter enables plan reproducibility and tamper detection (via Signer). Pre-execution validation prevents executing plans against a changed registry.

**Independent Test**: Can be tested by creating a plan with `registry_version: 5`, modifying the registry (incrementing to v6), and verifying that pre-execution validation detects the version mismatch.

**Acceptance Scenarios**:

1. **Given** the registry is at version 5, **When** Planner requests the current version, **Then** PluginRegistry returns `registry_version: 5` (monotonic integer)

2. **Given** a tool is added to the registry (version 5 → 6), **When** Planner requests the current version, **Then** PluginRegistry returns `registry_version: 6`

3. **Given** a plan references `registry_version: 5` and the current registry is at version 7, **When** pre-execution validation runs, **Then** PluginRegistry checks that all tools referenced in the plan are still active and returns a validation result (pass/fail with details)

4. **Given** a plan references `google.calendar` which was deactivated after the plan was created, **When** pre-execution validation runs, **Then** PluginRegistry returns `TOOL_DEACTIVATED` error listing the deactivated tool(s) and the plan is rejected

---

### User Story 3 - Resolve Credential ID Templates (Priority: P1)

As the Planner component, I need to interpolate credential ID templates with user-specific values so that plans reference the correct credential IDs without exposing actual secrets.

**Why this priority**: Credential isolation is a non-negotiable security requirement (GLOBAL_SPEC §8). Plans must reference credential IDs, not values.

**Independent Test**: Can be tested by defining a template `gcal_user_{{user_id}}_{{account_name}}` and resolving it with `user_id=123, account_name=work` to get `gcal_user_123_work`.

**Acceptance Scenarios**:

1. **Given** a tool with credential template `gcal_user_{{user_id}}_{{account_name}}`, **When** Planner requests credential ID resolution with `user_id="u-123"` and `account_name="work"`, **Then** PluginRegistry returns `gcal_user_u-123_work`

2. **Given** a credential template with a missing variable (e.g., `account_name` not provided), **When** resolution is attempted, **Then** PluginRegistry returns a `TEMPLATE_RESOLUTION_ERROR` with the missing variable name

3. **Given** a resolved credential ID, **Then** the ID is a string reference only — PluginRegistry never returns, stores, or logs actual credential values (OAuth tokens, API keys)

---

### User Story 4 - Admin Tool Management (Priority: P2)

As a system administrator, I need to add, update, and deactivate tools in the registry so that the system can support new integrations or disable broken ones.

**Why this priority**: Required for system maintenance, but not on the critical path for plan generation (can be seeded at startup for MVP).

**Independent Test**: Can be tested by adding a new tool, updating its operations, deactivating it, and verifying it no longer appears in active catalog queries.

**Acceptance Scenarios**:

1. **Given** a valid tool definition JSON, **When** an admin submits a create request, **Then** PluginRegistry validates against the tool schema and persists the entry

2. **Given** an existing tool `slack.messaging`, **When** an admin adds a new operation `send_dm`, **Then** the operation is added and subsequent catalog queries include it

3. **Given** an active tool `google.calendar`, **When** an admin deactivates it, **Then** it no longer appears in catalog queries or snapshots (but historical snapshots that included it are preserved)

4. **Given** a tool definition with an invalid schema (e.g., missing `n8n_node` on an operation), **When** the create/update request is submitted, **Then** PluginRegistry returns `SCHEMA_VALIDATION_ERROR` with details

---

### User Story 5 - Scope Verification for Plans (Priority: P3)

As the Planner component, I need to verify that a plan's required scopes are supported by the referenced tools so that plans don't reference capabilities that don't exist.

**Why this priority**: Validation improvement — plans will still work without this if tools are correctly configured, but this catches misconfigurations early.

**Independent Test**: Can be tested by creating a plan referencing `calendar.write` scope and verifying it against a tool that only has `calendar.read`.

**Acceptance Scenarios**:

1. **Given** a plan step referencing `google.calendar.create_event` which requires `calendar.write`, **When** scope verification is requested, **Then** PluginRegistry confirms the tool supports `calendar.write`

2. **Given** a plan step referencing a scope not supported by the tool, **When** scope verification is requested, **Then** PluginRegistry returns `SCOPE_NOT_SUPPORTED` with the missing scope and tool_id

---

### Edge Cases

* **Empty registry**: System boots with no tools registered — catalog returns empty list, snapshot returns empty with version 0
* **Duplicate tool_id**: Attempting to register a tool with an existing `tool_id` returns `TOOL_ALREADY_EXISTS` error
* **Operation name conflicts**: Two operations on the same tool with the same name — rejected at schema validation
* **Template variable injection**: Credential template variables containing special characters (e.g., `{{user_id}}` where user_id contains `/` or `{{}}`) — sanitize to alphanumeric + hyphen + underscore only
* **Concurrent admin updates**: Two admins updating the same tool simultaneously — last-write-wins with optimistic locking via `updated_at`
* **Deactivated tool in existing plans**: Plans referencing a deactivated tool — pre-execution validation flags this and rejects the plan
* **Large registry**: 100+ tools with 10+ operations each — pagination support for catalog queries

---

## Decision Rules (Deterministic Order)

Explicit, ordered rules evaluated **top to bottom**; first match wins:

1. **IF** `tool_id` is null or empty → Return `TOOL_ID_REQUIRED` error
2. **IF** `tool_id` format is invalid (not `<provider>.<service>`) → Return `INVALID_TOOL_ID_FORMAT` error
3. **IF** operation on create and `tool_id` already exists → Return `TOOL_ALREADY_EXISTS` error
4. **IF** operation on update/get and `tool_id` does not exist → Return `TOOL_NOT_FOUND` error
5. **IF** tool definition fails JSON schema validation → Return `SCHEMA_VALIDATION_ERROR` with details
6. **IF** credential template contains unresolvable variables → Return `TEMPLATE_RESOLUTION_ERROR` with missing vars
7. **IF** tool is deactivated and query is for active catalog → Exclude from results
8. **ELSE** → Proceed with operation (GET, CREATE, UPDATE, DEACTIVATE, RESOLVE, VALIDATE)

---

## Requirements *(mandatory)*

### Functional Requirements

* **FR-001: External Contract**
  * Inputs: `tool_id` (string, format `<provider>.<service>`), `tool_definition` (JSON, validated), `operation` (enum: GET, CREATE, UPDATE, DEACTIVATE, RESOLVE, VALIDATE)
  * Outputs (success): `{"status": "ok", "data": {...}}`
  * Outputs (error): `{"status": "error", "error_code": "...", "message": "...", "details": {...}}`
  * Error codes: `TOOL_ID_REQUIRED`, `INVALID_TOOL_ID_FORMAT`, `TOOL_ALREADY_EXISTS`, `TOOL_NOT_FOUND`, `SCHEMA_VALIDATION_ERROR`, `TEMPLATE_RESOLUTION_ERROR`, `SCOPE_NOT_SUPPORTED`, `TOOL_DEACTIVATED`

* **FR-002: Execution Semantics**
  * All operations execute directly (no Preview/Execute distinction at component level — internal component)
  * Read operations (GET, SNAPSHOT) return current or historical state from database
  * Write operations (CREATE, UPDATE, DEACTIVATE) persist to PostgreSQL via async SQLAlchemy transactions
  * Snapshots are immutable once created — never modified or deleted

* **FR-003: Safety & Security**
  * **Credential isolation (NON-NEGOTIABLE)**: PluginRegistry stores credential ID templates only — NEVER actual credential values, tokens, or secrets
  * Template variables are sanitized to prevent injection (alphanumeric + hyphen + underscore only)
  * Admin operations require admin role in auth context
  * No PII in logs: credential IDs may be logged, but never credential values
  * Registry data is not sensitive — no encryption at rest required (unlike ProfileStore)

* **FR-004: Registry Versioning & Pre-Execution Validation**
  * Registry maintains a monotonically increasing `version` integer (no gaps)
  * Each write operation (CREATE, UPDATE, DEACTIVATE) auto-increments the version
  * Planner records the `registry_version` in the plan as part of its deterministic input tuple (GLOBAL_SPEC §2.0)
  * Pre-execution validation: Given a plan's referenced `tool_id` list and `registry_version`, verify all tools are still active. Return pass/fail with details of any deactivated or missing tools
  * Tamper detection is handled by Signer (Ed25519 signature covers `registry_version`), not by PluginRegistry

* **FR-005: Credential Template Resolution**
  * Templates use `{{variable}}` syntax (Mustache-like)
  * Supported variables: `user_id`, `account_name`, `integration_id`
  * Resolution is a pure string interpolation — no side effects, no credential fetching
  * Missing variables produce an error, never partial resolution

* **FR-006: Schemas**
  * Tool definition schema: validates `tool_id`, `credential_template`, `n8n_credential_type`, `operations` map
  * Operation schema: validates `n8n_node`, `previewable`, `idempotent`, `scopes`, optional `compensation`
  * Validation result schema: validates `valid`, `current_version`, optional `issues` array
  * All schemas stored in `components/PluginRegistry/schemas/`

* **FR-007: Observability**
  * Structured logging: All operations logged with `tool_id`, `operation`, `admin_user_id` (for writes)
  * No credential values in logs (only credential ID templates and resolved IDs)
  * Correlation: Include `plan_id` if provided by caller
  * Metrics: snapshot creation count, catalog query latency, template resolution latency

* **FR-008: Non-Functional Requirements (NFRs)**
  * Latency budget: p95 < 30ms for GET single tool, p95 < 100ms for full catalog, p95 < 50ms for snapshot retrieval, p95 < 10ms for template resolution
  * Availability: 99.9% aligned with Domain Layer targets
  * Throughput: Read-heavy workload (1000 reads/sec, 10 writes/day)
  * Storage: Support up to 200 tools with 20 operations each

* **FR-009: Backward Compatibility**
  * Tool definitions are versioned via snapshots — no in-place breaking changes
  * Deactivation is soft-delete (tool remains in historical snapshots)
  * Adding new fields to tool/operation schema is additive (non-breaking)
  * Removing fields requires ADR and migration plan

* **FR-010: Registry Impacts**
  * PluginRegistry is itself a component — its own operations (get_tool, list_catalog, create_snapshot) are internal and do not appear in the tool catalog it manages
  * Tools IN the registry are the external integrations (Google Calendar, Slack, etc.)

### Key Entities

* **Tool**: A registered external integration with `tool_id` (string, PK, format `provider.service`), `display_name` (string), `credential_template` (string with `{{var}}` placeholders), `n8n_credential_type` (string), `active` (boolean, default true), `created_at` (timestamp), `updated_at` (timestamp). 1:N with Operations.

* **Operation**: A capability of a tool with `operation_id` (string, unique within tool), `tool_id` (FK), `n8n_node` (string — n8n node type), `previewable` (boolean), `idempotent` (boolean), `scopes` (string array), `compensation` (optional string — operation_id of the undo operation), `created_at` (timestamp). N:1 with Tool.

* **RegistryVersion**: A version counter with `version` (integer, PK, monotonic), `created_at` (timestamp), `change_summary` (string — brief description of what changed, e.g., "added slack.messaging"). Auto-created on each write operation.

---

## Invariants & Guarantees

Statements that must **always** hold true:

1. **Credential isolation**: PluginRegistry NEVER stores, returns, or logs actual credential values — only templates and resolved IDs
2. **Version monotonicity**: Registry versions are strictly increasing integers with no gaps
3. **Tool ID uniqueness**: Each `tool_id` exists at most once in the active registry
4. **Operation uniqueness**: Each `operation_id` is unique within its parent tool
5. **Schema conformance**: All tool definitions MUST validate against the tool definition JSON schema before persistence
6. **Template completeness**: Credential template resolution either succeeds completely or fails entirely — no partial interpolation
7. **Deactivation is soft-delete**: Deactivated tools are excluded from active queries but remain in the database for audit
8. **Referential integrity**: Every operation references an existing tool_id
9. **Deterministic version**: The `registry_version` recorded in a plan is signed by Signer — any tampering is detected via signature verification
10. **Pre-execution safety**: No plan executes without validating that all referenced tools are still active in the current registry

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

* **SC-001**: Single tool retrieval completes in p95 < 30ms (measured via distributed tracing)
* **SC-002**: Full catalog retrieval completes in p95 < 100ms for up to 200 tools (measured via load testing)
* **SC-003**: Pre-execution validation completes in p95 < 50ms (measured via distributed tracing)
* **SC-004**: Template resolution completes in p95 < 10ms (measured via distributed tracing)
* **SC-005**: 99.9% availability for all PluginRegistry read operations (measured via uptime monitoring)
* **SC-006**: Zero credential value leakage in logs or API responses (verified via log audits and contract tests)
* **SC-007**: 100% schema validation compliance — all persisted tool definitions validate against schemas (enforced by tests)

---

## Interfaces & Contracts

### Internal Component — No Preview/Execute Model

PluginRegistry is an internal backend component invoked directly by other components (Planner, WorkflowBuilder, PreviewOrchestrator, ExecuteOrchestrator). It does **not** use the Preview/Execute model from GLOBAL_SPEC — that model applies to user-facing **plans**, not internal component operations.

### Tool Definition (Registry Entry)

```json
{
  "tool_id": "google.calendar",
  "display_name": "Google Calendar",
  "credential_template": "gcal_user_{{user_id}}_{{account_name}}",
  "n8n_credential_type": "googleCalendarOAuth2Api",
  "active": true,
  "operations": {
    "list_free_busy": {
      "n8n_node": "Google Calendar",
      "previewable": true,
      "idempotent": true,
      "scopes": ["calendar.read"],
      "compensation": null
    },
    "create_event": {
      "n8n_node": "Google Calendar",
      "previewable": false,
      "idempotent": true,
      "scopes": ["calendar.write"],
      "compensation": "delete_event"
    },
    "delete_event": {
      "n8n_node": "Google Calendar",
      "previewable": false,
      "idempotent": true,
      "scopes": ["calendar.write"],
      "compensation": null
    }
  }
}
```

### Registry Version (Deterministic Input for Planner)

```json
{
  "registry_version": 5
}
```

Planner records this in the plan. Signer signs it along with the plan. Before execution, PreviewOrchestrator/ExecuteOrchestrator calls PluginRegistry's pre-execution validation endpoint.

### Pre-Execution Validation (Request/Response)

```json
// Request
{
  "plan_registry_version": 5,
  "referenced_tool_ids": ["google.calendar", "slack.messaging"]
}

// Response (pass)
{ "valid": true, "current_version": 7 }

// Response (fail)
{
  "valid": false,
  "current_version": 7,
  "issues": [
    { "tool_id": "slack.messaging", "reason": "TOOL_DEACTIVATED" }
  ]
}
```

### Resolved Credential ID (Output of Template Resolution)

```json
{
  "credential_id": "gcal_user_u-123_work",
  "tool_id": "google.calendar",
  "n8n_credential_type": "googleCalendarOAuth2Api"
}
```

### Standard Response (Success)

```json
{
  "status": "ok",
  "data": { "...tool, catalog, or snapshot..." }
}
```

### Standard Response (Error)

```json
{
  "status": "error",
  "error_code": "TOOL_NOT_FOUND",
  "message": "Tool with id 'google.calendar' not found",
  "details": { "tool_id": "google.calendar" }
}
```

Reference: Credential isolation model from `docs/architecture/GLOBAL_SPEC.md` v2.2 §8

---

## Component Mapping

* **Target**: `components/PluginRegistry/`
* **Files expected to change**:
  * `api/routes.py` — FastAPI endpoints for tool CRUD, catalog queries, version retrieval, template resolution, pre-execution validation
  * `api/dependencies.py` — Admin auth dependency for write operations
  * `service/registry_service.py` — Business logic for tool management, versioning, template resolution, pre-execution validation
  * `domain/models.py` — Pydantic models for Tool, Operation, RegistryVersion
  * `adapters/db.py` — SQLAlchemy database adapter (tools, operations, registry_versions tables)
  * `schemas/tool_definition.schema.json` — JSON schema for tool entry validation
  * `schemas/operation.schema.json` — JSON schema for operation metadata
  * `schemas/validation_result.schema.json` — JSON schema for pre-execution validation response
  * `tests/test_unit_registry.py` — Unit tests for tool CRUD and validation
  * `tests/test_unit_template.py` — Unit tests for credential template resolution
  * `tests/test_unit_validation.py` — Unit tests for pre-execution validation logic
  * `tests/test_integration.py` — Integration tests with database
  * `tests/test_contract.py` — Contract tests for schema compliance and credential isolation

---

## Dependencies & Risks

### Dependencies

* **PostgreSQL 16**: Database for tool definitions, operations, and version history
* **Pydantic v2**: Data validation for tool definitions and API contracts
* **SQLAlchemy 2.0**: Async ORM for database access
* **FastAPI**: HTTP endpoints for internal service communication
* **Auth Middleware**: Provides admin role verification for write operations
* **UserIntegrations table** (shared infrastructure): Maps users to their integration accounts and resolved credential IDs. PluginRegistry's pre-execution validation queries this table to verify the user has active integrations for all tools in a plan. See `SHARED_INFRASTRUCTURE.md` §1.3

### Risks

* **Risk 1: Plan executes against stale registry** — Registry changes between plan creation and execution
  * *Mitigation*: Pre-execution validation checks all referenced tools are still active; plans are short-lived (TTL 900s per GLOBAL_SPEC §2.3)

* **Risk 2: Template injection** — Malicious variable values could produce unexpected credential IDs
  * *Mitigation*: Strict sanitization (alphanumeric + hyphen + underscore only); reject all other characters

* **Risk 3: Registry unavailability blocks planning** — Planner cannot generate plans without registry access
  * *Mitigation*: Circuit breaker on database failures; post-MVP add Redis caching if latency becomes an issue

* **Risk 4: Schema evolution breaks existing tools** — Updating the tool definition schema could invalidate existing entries
  * *Mitigation*: Schema changes are additive only; breaking changes require ADR and migration plan (FR-009)

---

## Non-Functional Requirements

* **Inherit baseline** (from constitution.md):
  * Structured logs with no secrets/PII
  * 99.9% availability
  * Observability via plan_id correlation

* **Deltas** (PluginRegistry-specific):
  * Lower latency targets: GET single tool <30ms, catalog <100ms, pre-execution validation <50ms, template resolution <10ms
  * Read-heavy workload profile: 1000 reads/sec, ~10 writes/day
  * Post-MVP: Redis caching for catalog if query latency becomes an issue under load
  * Storage capacity: up to 200 tools, 4000 operations

---

## Open Questions

* **Q1**: Should the registry support tool "categories" or "tags" for grouping (e.g., "communication", "productivity")?
  * **Proposed answer**: Not in MVP; add as metadata field in v2 if Planner needs filtering by category

* **Q2**: How should we handle n8n node type changes (e.g., n8n renames "Google Calendar" node)?
  * **Proposed answer**: Admin updates the `n8n_node` field; WorkflowBuilder uses the current registry value

* **Q3**: Should PluginRegistry validate that `compensation` operation names reference existing operations on the same tool?
  * **Proposed answer**: Yes — enforce referential integrity within tool at schema validation time

* **Q4**: Should pre-execution validation also check that required scopes haven't changed?
  * **Proposed answer**: MVP validates tool existence and active status only; scope drift detection deferred to v2

* **Q5**: Where does the UserIntegrations table (user → integration account mapping) live?
  * **Proposed answer**: Separate lightweight component or table — PluginRegistry owns tool definitions, UserIntegrations owns which users have which accounts for each tool

---

## Conformance

This work conforms to:

* `docs/architecture/GLOBAL_SPEC.md` v2.2
* `docs/architecture/Project_HLD.md` v4.6
* `.specify/memory/constitution.md` v1.0.0
