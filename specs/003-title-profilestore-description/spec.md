# Component Specification: ProfileStore

**Feature Branch**: `003-title-profilestore-description`
**Created**: 2025-12-16
**Status**: Draft
**Input**: User description: "ProfileStore — Store stable user preferences and consent settings"

---

## Scope & Non-Goals

### In Scope

* **Tier 2 Data Source**: ProfileStore provides stable user preferences for ContextRAG (as defined in GLOBAL_SPEC §7)
* Store and retrieve stable user preferences (meeting duration, work hours, notification settings)
* Store and retrieve sensitive user data with encryption (passport numbers, health info, emergency contacts)
* Store and manage user consent flags for all context tiers (Tiers 1-4 as defined in GLOBAL_SPEC §7)
* Provide CRUD operations for user profile metadata (timezone, display preferences)
* Validate preference values against registered schemas
* Enforce Tier 2+ consent requirement (deny access to ProfileStore data without Tier 2 or higher consent)
* Flag sensitive preferences with `sensitive: true` and encrypt at rest (application-level encryption)
* Return preference data in Evidence Item format for ContextRAG integration

### Out of Scope (Non-Goals)

* Session-only data / Tier 1 data (owned by Intake via Redis; includes temporary context extracted from any source)
* Historical interaction data / Tier 3 data (owned by History component; 30-day TTL)
* Live signals and real-time data / Tier 4 data (fetched from external APIs on-demand during planning; never stored)
* Plan storage and retrieval (owned by PlanLibrary component)
* Vector embeddings for semantic search (owned by VectorIndex component)
* User authentication and password management (handled by separate auth service)
* User registration and account creation flows (API layer responsibility)

### Assumptions

* User accounts already exist in the system (created via registration service)
* Database schema is migrated and tables exist (`users`, `preferences`)
* Auth middleware provides `context_tier` in authenticated request context (from JWT token or session)
* Caller provides valid `user_id` for all operations
* Privacy tier definitions (1-4) are established in GLOBAL_SPEC v2 §7
* Consent is cumulative: `context_tier = N` grants access to Tiers 1 through N
* Users table (owned by Auth/Registration) includes `context_tier` column (integer 0-4, default 0)
* Preference schema registry exists and defines valid keys with validation rules
* Application-level encryption service is available for sensitive data (AES-256 or equivalent)

---

## User Scenarios & Testing *(mandatory)*

###  User Story 1 - Store and Retrieve User Preferences (Priority: P1)

As a system component (ContextRAG, Planner), I need to retrieve stable user preferences so that I can personalize behavior without accessing historical data or making real-time API calls.

**Why this priority**: Core functionality - all other components depend on preferences for personalization. This is the minimum viable ProfileStore.

**Independent Test**: Can be fully tested by setting a preference (e.g., "meeting_duration_min": 30) and retrieving it. Delivers immediate value to ContextRAG for tier 2 context.

**Acceptance Scenarios**:

1. **Given** a valid user_id exists in the system, **When** ContextRAG requests all preferences for that user, **Then** ProfileStore returns key-value pairs in Evidence Item format

2. **Given** a new user with no preferences set, **When** a component requests a preference (e.g., "meeting_duration_min"), **Then** ProfileStore returns the default value from the preference schema

3. **Given** a user updates their "work_hours" preference from "9-5" to "10-6", **When** the update operation commits, **Then** subsequent read operations return "10-6" for "work_hours"

4. **Given** a preference update with value that fails schema validation, **When** the update is attempted, **Then** ProfileStore returns `VALIDATION_ERROR` and does not persist the invalid value

---

### User Story 2 - Store and Retrieve Sensitive User Data (Priority: P1)

As a system component, I need to securely store and retrieve sensitive user information (passport numbers, health info, emergency contacts) so that I can use this data for travel booking and safety features.

**Why this priority**: Critical for core use cases like travel booking automation. Must be encrypted for security compliance.

**Independent Test**: Can be fully tested by storing a sensitive preference (e.g., "passport_number": "ABC123456") with encryption, retrieving it, and verifying decryption works correctly.

**Acceptance Scenarios**:

1. **Given** a user stores a sensitive preference (e.g., passport number) with `sensitive: true`, **When** the preference is persisted, **Then** ProfileStore encrypts the value before storing in the database

2. **Given** a user has a stored encrypted preference, **When** a component requests that preference, **Then** ProfileStore decrypts and returns the original value

3. **Given** a user deletes a sensitive preference, **When** the delete operation commits, **Then** the encrypted data is permanently removed from the database

---

### User Story 3 - Enforce Privacy Consent Tiers (Priority: P2)

As ProfileStore, I need to enforce that Tier 2 data is only accessible when the user has granted `context_tier >= 2` consent.

**Why this priority**: Critical for privacy compliance. ProfileStore must never return preference data without proper consent.

**Independent Test**: Can be tested by mocking authenticated requests with different `context_tier` values and verifying access is denied when `context_tier < 2`.

**Acceptance Scenarios**:

1. **Given** an authenticated request with `context_tier = 1`, **When** ProfileStore receives GET preference request, **Then** it returns `CONSENT_DENIED` error without querying database

2. **Given** an authenticated request with `context_tier = 3`, **When** ProfileStore receives GET preference request, **Then** it allows access (3 >= 2) and returns preference data

3. **Given** an unauthenticated request (no `context_tier` in context), **When** ProfileStore receives any request, **Then** it returns authentication error before checking consent

---

### User Story 4 - Contact Information Storage (Priority: P3)

As a user, I want to store frequently used contact information so that I don't have to re-enter it for common tasks.

**Why this priority**: Convenience feature, not critical for core functionality. Can be added post-MVP.

**Independent Test**: Can be tested by storing a contact ("Alice", "alice@example.com") and retrieving it.

**Acceptance Scenarios**:

1. **Given** a user stores a contact with name and email, **When** they request contacts later, **Then** ProfileStore returns the stored contact information

2. **Given** a user updates a contact's email address, **When** the update commits, **Then** subsequent retrievals return the new email

---

### Edge Cases

* **Empty preferences**: New user with no custom preferences (return schema defaults)
* **Partial preferences**: User has some preferences set but not all (mix of custom and default values)
* **Consent tier boundaries**: Requesting exactly the tier boundary value (Tier 3 with Tier 3 consent = allowed)
* **Concurrent updates**: Two components updating the same preference simultaneously (last-write-wins with timestamp)
* **Preference deletion**: Setting preference to null vs deleting the key (null = reset to default)
* **Invalid sensitive flag changes**: Attempting to change existing non-sensitive preference to sensitive (or vice versa) after data is stored

---

## Decision Rules (Deterministic Order)

Explicit, ordered rules evaluated **top to bottom**; first match wins:

1. **IF** `user_id` is null or empty → Return `USER_ID_REQUIRED` error
2. **IF** `user_id` does not exist in `users` table → Return `USER_NOT_FOUND` error
3. **IF** operation requires consent tier N AND user has not granted tier N → Return `CONSENT_DENIED` error with tier details
4. **IF** `preference_key` does not exist in schema registry → Return `UNKNOWN_PREFERENCE` error
5. **IF** `preference_value` fails schema validation for the key → Return `VALIDATION_ERROR` with schema violation details
6. **IF** read operation for valid preference with no custom value set → Return default value from preference schema
7. **ELSE** → Proceed with operation (GET, SET, DELETE, or consent operation)

---

## Requirements *(mandatory)*

### Functional Requirements

* **FR-001: External Contract**
  * Inputs: `user_id` (required, UUID), `preference_key` (string, max 64 chars), `preference_value` (JSON-serializable), `sensitive` (boolean, optional), `operation` (enum)
  * Auth Context: `context_tier` (integer 0-4, provided by auth middleware in request context)
  * Outputs (success): `{"status": "ok", "data": {...}, "tier": 2, "sensitive": false}`
  * Outputs (error): `{"status": "error", "error_code": "...", "message": "...", "details": {...}}`
  * Valid ranges: Preference keys alphanumeric + underscore
  * Error codes: `USER_NOT_FOUND`, `CONSENT_DENIED`, `VALIDATION_ERROR`, `UNKNOWN_PREFERENCE`, `USER_ID_REQUIRED`

* **FR-002: Execution Semantics**
  * All operations execute directly (no Preview/Execute distinction at component level)
  * Read operations (`GET_PREFERENCE`) return current state from database
  * Write operations (`SET_PREFERENCE`, `DELETE_PREFERENCE`) persist immediately to PostgreSQL via async SQLAlchemy transactions
  * Idempotency: Write operations are safe to retry with same parameters

* **FR-003: Safety & Security**
  * Authorization: Caller must provide valid `user_id` matching authenticated user context
  * Sensitive data: Preferences with `sensitive: true` flagged in schema and encrypted at rest (AES-256)
  * Consent enforcement: Tier 2 data NEVER returned without `context_tier >= 2` (read from auth context)
  * Access control: Users can only access their own preferences
  * No PII in logs: Preference values NEVER logged, only keys

* **FR-004: Idempotency & Compensation**
  * Idempotency key: `SET_PREFERENCE` operations idempotent per user_id + preference_key + value
  * Retry safety: All GET and SET operations safe to retry
  * Compensation for SET: Store previous value before update; restore on compensation request
  * No compensation for DELETE: Cannot restore without external backup

* **FR-005: Schemas**
  * Input schema for SET_PREFERENCE: `{"user_id": "uuid", "preference_key": "string", "preference_value": "any", "sensitive": "boolean"}`
  * Output schema as Evidence Item (GLOBAL_SPEC §2.2): `{"type": "preference", "key": "...", "value": ..., "confidence": 1.0, "source_ref": "profilestore:...", "tier": 2}`
  * All stored preference values MUST validate against registered JSON schemas in preference schema registry

* **FR-006: Observability**
  * Structured logging: All operations logged with `user_id`, `operation`, `preference_key` (value never logged)
  * No PII in logs: Preference values NEVER logged
  * Correlation: Include `plan_id` if provided by caller
  * Stable error codes: See FR-001

* **FR-007: Determinism**
  * Same inputs → same outputs: GET operations return identical data for same user_id + key
  * No hidden state: All values explicitly stored in database
  * Default values: Defined in schema registry and returned consistently

* **FR-008: Non-Functional Requirements (NFRs)**
  * Latency budget: p95 < 50ms for GET_PREFERENCE, < 100ms for SET_PREFERENCE, < 10ms for GET_CONSENT (Redis cached)
  * Availability: 99.9% aligned with Memory Layer targets
  * Throughput: 1000 req/sec per user_id
  * Resource limits: Max 100 preference keys per user

* **FR-009: Backward Compatibility**
  * Versioning: Preference schemas versioned (v1, v2, v3)
  * Breaking changes: Require migration plan + ADR
  * Deprecation policy: 3-month notice before removing keys
  * Schema evolution: Additive changes only (new keys allowed; removing keys = breaking)

* **FR-010: Registry Impacts**
  * Operations in Plugin Registry: `get_preference`, `set_preference`, `delete_preference`
  * All operations specify: idempotent (true/false), compensation (operation name or null)
  * Note: ProfileStore is an internal component; Preview/Execute model applies to plans that USE ProfileStore, not ProfileStore itself

### Key Entities *(include if data is involved)*

* **User**: Account identity with `user_id` (UUID), `created_at` (timestamp), `timezone` (IANA string), `context_tier` (integer 0-4, defaults to 0). Owned by Auth/Registration component. ProfileStore reads `context_tier` from authenticated request context to enforce Tier 2 boundary. Cumulative consent: `context_tier = N` grants access to Tiers 1 through N.

* **Preference**: Key-value pairs with `preference_id` (UUID), `user_id` (FK), `key` (string, e.g., "work_hours"), `value` (JSON, validated), `tier` (always 2 for ProfileStore), `sensitive` (boolean, true for encrypted data), `updated_at` (timestamp). N:1 cardinality with User. Lifecycle: Create on first SET, Update on subsequent SET (upsert), Delete on DELETE_PREFERENCE.

---

## Invariants & Guarantees

Statements that must **always** hold true:

1. **User uniqueness**: Every `user_id` exists in exactly one row in the `users` table
2. **Consent monotonicity**: Consent tiers are cumulative (granting Tier 3 implicitly grants Tier 1 and Tier 2; granting Tier 4 grants all)
3. **Preference ownership**: Each preference key belongs to exactly one user (no shared preferences)
4. **Tier enforcement**: ProfileStore data (Tier 2) is NEVER accessible without `context_tier >= 2` (read from auth context)
5. **Schema conformance**: All stored preference values MUST validate against their registered JSON schema
6. **Atomicity**: SET operations are atomic (preference value + metadata updated together or not at all)
7. **Default stability**: Default values defined in schema NEVER change without schema version bump
8. **Null safety**: Absent preference returns default value from schema (never null unless schema explicitly allows null)
9. **Sensitive data encryption**: All preferences with `sensitive = true` MUST be encrypted at rest before storage
10. **Auth context availability**: Every authenticated request MUST include `context_tier` in request context (enforced by auth middleware)

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

* **SC-001**: Preference GET operations complete in p95 < 50ms (measured via distributed tracing)
* **SC-002**: Preference SET operations complete in p95 < 100ms (measured via distributed tracing)
* **SC-003**: 99.9% availability for all ProfileStore operations (measured via uptime monitoring)
* **SC-004**: Zero PII leakage in logs (verified via log audits and automated PII detection)
* **SC-005**: 100% schema validation compliance (all stored preferences validate against schemas, enforced by tests)
* **SC-006**: Support 1000 req/sec per user_id without degradation (verified via load testing)

---

## Interfaces & Contracts

### Internal Component - No Preview/Execute Model

ProfileStore is an internal backend component invoked directly by other components (ContextRAG, PlanWriter). It does **not** use the Preview/Execute model from GLOBAL_SPEC - that model applies to user-facing **plans**, not internal component operations.

### Evidence Item Output (for GET_PREFERENCE)

When ProfileStore returns preference data to ContextRAG, it uses the Evidence Item format from GLOBAL_SPEC §2.2:

```json
{
  "type": "preference",
  "key": "meeting_duration_min",
  "value": 30,
  "confidence": 1.0,
  "source_ref": "profilestore:prefs/meeting_duration_min",
  "ttl_days": null,
  "tier": 2
}
```

### Standard Response (for SET_PREFERENCE)

Write operations return a simple success/error response:

```json
{
  "status": "ok",
  "data": {
    "preference_id": "pref-abc123",
    "user_id": "user-xyz",
    "preference_key": "work_hours",
    "preference_value": "10-6",
    "updated_at": "2025-12-17T10:30:00Z"
  },
  "tier": 2,
  "sensitive": false
}
```

Reference: Evidence Item schema from `docs/architecture/GLOBAL_SPEC.md` v2 §2.2

---

## Component Mapping

* **Target**: `components/ProfileStore/`
* **Files expected to change**:
  * `api/routes.py` - FastAPI endpoints for preference CRUD operations
  * `api/dependencies.py` - Auth dependency to extract `context_tier` from request
  * `service/preference_service.py` - Business logic for preferences
  * `domain/models.py` - Pydantic models for validation
  * `adapters/db.py` - SQLAlchemy database adapter (preferences table only)
  * `schemas/preference.schema.json` - JSON schema for preferences
  * `tests/test_unit_preference.py` - Unit tests for preference logic
  * `tests/test_consent_enforcement.py` - Unit tests for consent boundary enforcement
  * `tests/test_integration.py` - Integration tests with database
  * `tests/test_contract.py` - Contract tests for GLOBAL_SPEC compliance

---

## Dependencies & Risks

### Dependencies

* **Auth Middleware**: Provides `context_tier` in authenticated request context (from JWT/session)
* **PostgreSQL 16**: Database for storing preferences table
* **Pydantic v2**: Data validation for preference values
* **SQLAlchemy 2.0**: Async ORM for database access
* **Preference Schema Registry**: Centralized registry defining valid keys and validation rules

### Risks

* **Risk 1: Schema drift** - Preferences stored without schema validation could violate constraints
  * *Mitigation*: Enforce schema validation at API layer before persistence; contract tests verify compliance

* **Risk 2: Consent enforcement bypass** - Component bug could leak higher-tier data without consent
  * *Mitigation*: Enforce consent checks in service layer (not just API); integration tests verify tier enforcement

* **Risk 3: Performance degradation** - Large numbers of preferences per user could slow queries
  * *Mitigation*: Max 100 keys per user (enforced); indexed queries on user_id; Redis caching for consent

* **Risk 4: Concurrent update conflicts** - Two components updating same preference simultaneously
  * *Mitigation*: Last-write-wins with timestamp; optimistic locking via `updated_at` field

---

## Non-Functional Requirements

* **Inherit baseline** (from constitution.md):
  * Preview p95 < 800ms (ProfileStore targets <50ms for GET)
  * Execute p95 < 2s (ProfileStore targets <100ms for SET)
  * Structured logs with no secrets/PII
  * 99.9% availability

* **Deltas** (ProfileStore-specific):
  * Lower latency targets: GET <50ms, SET <100ms, consent <10ms (stricter than baseline)
  * Resource limits: Max 100 preference keys per user
  * Redis caching: 5-min TTL for consent flags (not in baseline)

---

## Open Questions

* **Q1**: Should preference schema registry be a separate component or part of ProfileStore?
  * **Proposed answer**: Separate lightweight registry component (simpler, reusable by other components)

* **Q2**: What is the exact format for timezone-aware preferences (e.g., "work_hours")?
  * **Proposed answer**: Store as ISO 8601 time ranges with timezone from user profile (e.g., "09:00-17:00 America/Chicago")

* **Q3**: Should we support preference history/audit trail (tracking all changes)?
  * **Proposed answer**: Not in MVP; if needed, implement via database triggers to separate audit table

* **Q4**: How should we handle preference conflicts across devices (e.g., mobile vs desktop)?
  * **Proposed answer**: Last-write-wins with timestamp; eventual consistency model

* **Q5**: Should consent tier grants be all-or-nothing or allow partial grants within a tier?
  * **Proposed answer**: All-or-nothing per tier (simpler model); fine-grained permissions deferred to v2

---

## Conformance

This work conforms to:

* `docs/architecture/GLOBAL_SPEC.md` v2
* `docs/architecture/Project_HLD.md` v4.0
* `.specify/memory/constitution.md` v1.0.0
