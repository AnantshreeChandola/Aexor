# Tasks: ProfileStore

**Created**: 2025-12-28
**Branch**: 003-title-profilestore-description
**SPEC**: specs/003-title-profilestore-description/spec.md
**LLD**: components/ProfileStore/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.

---

## Phase 0: Setup & Dependencies

### Global Infrastructure Dependencies (from LLD §11.1)
- [ ] [T000] Set up PostgreSQL 16 and create personal_agent database
- [ ] [T001] Create users table in shared database schema
- [ ] [T002] Implement auth middleware (shared/middleware/auth.py)
- [ ] [T003] Implement encryption service (shared/security/encryption.py)
- [ ] [T004] Create Evidence Item schema (shared/schemas/evidence.py)

---

## Phase 1: Schemas & Domain (Foundation)

### Acceptance Criterion: AC-001 - Store and Retrieve User Preferences (Priority: P1)

- [ ] [T100] Create preferences table migration (components/ProfileStore/migrations/)
- [ ] [T101] Create Pydantic models for preferences (components/ProfileStore/domain/models.py)
- [ ] [T102] Create preference JSON schema registry (components/ProfileStore/schemas/)
- [ ] [T103] Write schema validation tests (components/ProfileStore/tests/test_preferences.py)

---

## Phase 2: Service Layer (Business Logic)

### Acceptance Criterion: AC-002 - Store and Retrieve Sensitive User Data (Priority: P1)

- [ ] [T200] Implement PreferenceService.get_preference() (components/ProfileStore/service/preference_service.py)
  - Reads from PostgreSQL via DatabaseAdapter
  - Enforces consent tier boundaries (context_tier >= 2)
  - Returns preferences in Evidence Item format
  - Handles missing preferences with schema defaults
- [ ] [T201] Implement PreferenceService.set_preference() (components/ProfileStore/service/preference_service.py)
  - Validates against schema registry
  - Encrypts sensitive preferences via EncryptionAdapter
  - Upserts to PostgreSQL (idempotent)
  - Returns preference metadata
- [ ] [T202] Implement PreferenceService.delete_preference() (components/ProfileStore/service/preference_service.py)
  - Soft deletes preferences (sets deleted_at)
  - Returns success confirmation
- [ ] [T203] Write service logic tests (components/ProfileStore/tests/test_preferences.py)

---

## Phase 3: Adapters (External Integrations)

### Acceptance Criterion: AC-003 - Enforce Privacy Consent Tiers (Priority: P2)

- [ ] [T300] Create DatabaseAdapter (components/ProfileStore/adapters/db.py)
  - Async SQLAlchemy 2.0 operations
  - Connection pooling and transaction management
  - Preference CRUD operations with upsert logic
- [ ] [T301] Create EncryptionAdapter (components/ProfileStore/adapters/encryption.py)
  - AES-256-GCM encryption/decryption
  - Base64 encoding for database storage
  - Key management via environment variables
- [ ] [T302] Create SchemaRegistryAdapter (components/ProfileStore/adapters/schema_registry.py)
  - File-based schema loading from schemas/ directory
  - Schema validation for preference values
  - Default value extraction from schemas
- [ ] [T303] Write adapter tests with mocks (components/ProfileStore/tests/test_preferences.py)

---

## Phase 4: API Handlers (Thin Wrappers)

### Acceptance Criterion: AC-004 - Contact Information Storage (Priority: P3)

- [ ] [T400] Create API routes (components/ProfileStore/api/routes.py)
  - GET /preferences/{user_id}/{preference_key}
  - POST /preferences/{user_id}
  - DELETE /preferences/{user_id}/{preference_key}
  - Thin wrappers delegating to PreferenceService
- [ ] [T401] Create API dependencies (components/ProfileStore/api/dependencies.py)
  - Auth dependency to extract context_tier from request
  - User validation dependency
- [ ] [T402] Add request/response models (components/ProfileStore/domain/models.py)
  - Request validation with Pydantic
  - Response formatting for Evidence Items
- [ ] [T403] Write API handler tests (components/ProfileStore/tests/test_preferences.py)

---

## Phase 5: Fault Isolation & Safety (Architectural)

### From LLD Architectural Considerations and SPEC Safety Requirements

- [ ] [T500] Implement consent tier enforcement (service layer)
  - Check context_tier >= 2 before any preference access
  - Return CONSENT_DENIED error with tier details
  - Integration with auth middleware
- [ ] [T501] Add structured logging with PII protection
  - Log operations with user_id, preference_key, plan_id
  - NEVER log preference values or ciphertext
  - JSON format with correlation IDs
- [ ] [T502] Implement idempotency for SET operations
  - Use ON CONFLICT for upsert behavior
  - Safe retry logic with same parameters
- [ ] [T503] Add error handling with stable error codes
  - USER_NOT_FOUND, CONSENT_DENIED, VALIDATION_ERROR
  - Unknown_PREFERENCE, USER_ID_REQUIRED
  - Consistent error response format
- [ ] [T504] Add validation determinism checks
  - Same inputs → same validation results
  - Schema registry consistency

---

## Phase 6: Contract Tests & Integration

### Acceptance Criterion: AC-005 - GLOBAL_SPEC Compliance and Integration

- [ ] [T600] Write contract tests (components/ProfileStore/tests/test_contract.py)
  - Evidence Item format compliance (GLOBAL_SPEC §2.2)
  - Context tier boundary enforcement
  - Auth middleware integration
  - Error response format validation
- [ ] [T601] Integration test with database
  - End-to-end GET/SET/DELETE flows
  - Transaction rollback on errors
  - Encryption/decryption roundtrip
- [ ] [T602] Validate preference schema compliance
  - All stored preferences validate against schemas
  - Default value behavior
  - Sensitive data encryption enforcement

---

## Task Summary

- **Total Tasks**: 25
- **Setup**: T000-T004 (5 tasks)
- **Schemas**: T100-T103 (4 tasks)
- **Service**: T200-T203 (4 tasks)
- **Adapters**: T300-T303 (4 tasks)
- **API**: T400-T403 (4 tasks)
- **Safety**: T500-T504 (5 tasks)
- **Tests**: T600-T602 (3 tasks)

## Dependencies

**Global Infrastructure** (Must be implemented first):
- PostgreSQL 16 with users table
- Auth middleware providing context_tier
- Encryption service (AES-256-GCM)
- Evidence Item schema (GLOBAL_SPEC §2.2)

**Python Packages**:
- FastAPI (async HTTP)
- SQLAlchemy 2.0 with asyncpg
- Pydantic v2 (data validation)
- Cryptography (AES encryption)
- Alembic (database migrations)

**External Services**: None (ProfileStore is internal component)

**Internal Dependencies**: None (ProfileStore provides Tier 2 data to ContextRAG)

## Architectural Considerations

**Blast Radius** (from LLD):
- If ProfileStore fails: ContextRAG cannot access user preferences, PlanWriter cannot personalize plans
- Containment: Other components degrade gracefully without preferences, use sensible defaults
- Fallback: Return empty preferences or cached data if database unavailable

**Safety Model** (from LLD):
- ProfileStore is internal component - no Preview/Execute model
- All operations execute directly (GET/SET/DELETE)
- Consent enforcement at API layer prevents unauthorized access
- Encryption protects sensitive data at rest

**Determinism** (from SPEC):
- GET operations: Same user_id + key → same preference value
- SET operations: Idempotent via upsert (user_id + key uniqueness)
- Schema validation: Same value + schema → same validation result
- Default values: Consistent from schema registry

**Context Tier Enforcement** (from GLOBAL_SPEC §7):
- ProfileStore is Tier 2 data source
- All operations require context_tier >= 2 from auth context
- Cumulative consent model: Tier 3 grants access to Tier 2 data
- Consent checked before any database access