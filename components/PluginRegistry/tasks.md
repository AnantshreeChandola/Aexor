# Tasks: PluginRegistry

**Created**: 2026-03-11
**Branch**: feat/pluginregistry
**SPEC**: specs/006-pluginregistry/spec.md
**LLD**: components/PluginRegistry/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
PluginRegistry is an **internal component** -- it does NOT use the Preview/Execute model.
All operations execute directly. Admin authorization is deferred for MVP.
Redis caching is deferred for MVP -- all reads go directly to PostgreSQL.

---

## Phase 0: Setup & Dependencies

### Install Dependencies (from LLD.md Section 2.2)

All Python packages are already present in `pyproject.toml`:
- `fastapi>=0.109.0`
- `pydantic>=2.0`
- `sqlalchemy[asyncio]>=2.0`
- `asyncpg>=0.29`
- `alembic>=1.13.0`
- `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`, `pytest-cov>=4.1.0`

No new packages are required for this component.

- [ ] [T000] Verify shared infrastructure is operational
  - **Check**: `shared/database/adapter.py` (SharedDatabaseAdapter, get_database_adapter) -- EXISTS
  - **Check**: `shared/database/models.py` (UserTable, Base) -- EXISTS
  - **Check**: `shared/database/error_handler.py` (with_db_error_handling, DatabaseError) -- EXISTS
  - **Check**: `shared/middleware/auth.py` (AuthMiddleware, JWT-based) -- EXISTS
  - **Check**: `shared/api/auth.py` (get_auth_context, get_user_id) -- EXISTS
  - **Check**: `shared/api/error_handlers.py` (ErrorResponse, APIErrorHandler) -- EXISTS
  - **Check**: `migrations/001_create_users_table.sql` -- EXISTS
  - **Verify**: PostgreSQL is running and `users` table exists
  - **Deliverable**: No file changes -- verification only

- [ ] [T001] Create PluginRegistry package structure with `__init__.py` files
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/__init__.py`
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/api/__init__.py`
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/__init__.py`
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/domain/__init__.py`
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/adapters/__init__.py`
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/schemas/__init__.py` (optional, directory may not need it)
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/__init__.py`
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/conftest.py` (shared fixtures for PluginRegistry tests)
  - All `__init__.py` files should be empty or contain minimal exports
  - `conftest.py` should set up async test fixtures (mock DB adapter, sample tool data, etc.)

- [ ] [T002] Create database migration for PluginRegistry tables
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/migrations/006_create_pluginregistry_tables.sql`
  - **Tables to create** (from LLD Section 2.2):
    - `tools` table: `tool_id VARCHAR(128) PRIMARY KEY`, `display_name VARCHAR(255) NOT NULL`, `credential_template VARCHAR(512) NOT NULL`, `n8n_credential_type VARCHAR(128) NOT NULL`, `active BOOLEAN NOT NULL DEFAULT TRUE`, `created_at TIMESTAMP NOT NULL DEFAULT NOW()`, `updated_at TIMESTAMP NOT NULL DEFAULT NOW()`
    - `operations` table: `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `operation_id VARCHAR(128) NOT NULL`, `tool_id VARCHAR(128) NOT NULL REFERENCES tools(tool_id)`, `n8n_node VARCHAR(255) NOT NULL`, `previewable BOOLEAN NOT NULL DEFAULT FALSE`, `idempotent BOOLEAN NOT NULL DEFAULT FALSE`, `scopes TEXT[] NOT NULL DEFAULT '{}'`, `compensation VARCHAR(128)`, `created_at TIMESTAMP NOT NULL DEFAULT NOW()`, `UNIQUE(tool_id, operation_id)`
    - `registry_versions` table: `version INTEGER PRIMARY KEY`, `created_at TIMESTAMP NOT NULL DEFAULT NOW()`, `change_summary VARCHAR(512) NOT NULL`
  - **Indexes**: `idx_tools_active ON tools(tool_id) WHERE active = TRUE`, `idx_operations_tool ON operations(tool_id)`
  - **Insert initial version**: `INSERT INTO registry_versions (version, change_summary) VALUES (0, 'initial empty registry')`

---

## Phase 1: Schemas & Domain (Foundation)

### Acceptance Criterion: US-1 (Register and Retrieve Tool Definitions), FR-006 (Schemas)

- [ ] [T100] Create tool definition JSON schema
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/schemas/tool_definition.schema.json`
  - Must validate: `tool_id` (string, pattern `^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$`), `display_name` (string, 1-255), `credential_template` (string, max 512), `n8n_credential_type` (string, max 128), `active` (boolean), `operations` (object map)
  - Required fields: `tool_id`, `display_name`, `credential_template`, `n8n_credential_type`, `operations`
  - Reference: SPEC "Interfaces & Contracts" section and LLD Section 5.1

- [ ] [T101] Create operation JSON schema
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/schemas/operation.schema.json`
  - Must validate: `operation_id` (string, pattern `^[a-z][a-z0-9_]{1,126}$`), `n8n_node` (string), `previewable` (boolean), `idempotent` (boolean), `scopes` (array of strings), `compensation` (string or null)
  - Required fields: `n8n_node`
  - Reference: SPEC Key Entities, LLD Section 5.4

- [ ] [T102] Create validation result JSON schema
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/schemas/validation_result.schema.json`
  - Must validate: `valid` (boolean, required), `current_version` (integer, required), `issues` (array of objects with `tool_id` and `reason`)
  - Reference: SPEC "Pre-Execution Validation" contract, LLD Section 5.3

- [ ] [T103] Create Pydantic domain models
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/domain/models.py`
  - **Models to implement** (from LLD Section 5.4):
    - `OperationModel(BaseModel)`: `operation_id`, `n8n_node`, `previewable`, `idempotent`, `scopes`, `compensation`
    - `ToolModel(BaseModel)`: `tool_id`, `display_name`, `credential_template`, `n8n_credential_type`, `active`, `operations` (dict of OperationModel), `created_at`, `updated_at`
    - `RegistryVersionModel(BaseModel)`: `version`, `created_at`, `change_summary`
    - `CreateToolRequest(BaseModel)`: `tool_id`, `display_name`, `credential_template`, `n8n_credential_type`, `operations`
    - `UpdateToolRequest(BaseModel)`: all fields optional
    - `ResolveCredentialRequest(BaseModel)`: `tool_id`, `variables`
    - `ValidatePlanToolsRequest(BaseModel)`: `plan_registry_version`, `referenced_tool_ids`
    - `CatalogResponse(BaseModel)`: `tools`, `registry_version`, `total`, `page`, `page_size`
    - `ResolvedCredential(BaseModel)`: `credential_id`, `tool_id`, `n8n_credential_type`
    - `ValidationResult(BaseModel)`: `valid`, `current_version`, `issues`
    - `ValidationIssue(BaseModel)`: `tool_id`, `reason`
    - `CreateToolResponse(BaseModel)`: `tool_id`, `registry_version`, `created_at`
    - `UpdateToolResponse(BaseModel)`: `tool_id`, `registry_version`, `updated_at`
    - `DeactivateToolResponse(BaseModel)`: `tool_id`, `active`, `registry_version`, `deactivated_at`
    - `ScopeVerificationResult(BaseModel)`: `supported`, `missing_scopes`
  - **Custom exceptions** (create in same file or separate `domain/exceptions.py`):
    - `ToolNotFoundError(Exception)`: `tool_id` attribute
    - `ToolAlreadyExistsError(Exception)`: `tool_id` attribute
    - `SchemaValidationError(Exception)`: `details` attribute
    - `TemplateResolutionError(Exception)`: `tool_id`, `template`, `missing_variables` attributes
    - `ScopeNotSupportedError(Exception)`: `tool_id`, `operation_id`, `missing_scopes`
    - `InvalidToolIdFormatError(Exception)`: `tool_id`
  - Use Pydantic v2 Field validators with pattern constraints
  - Follow PYTHON_GUIDE.md: snake_case, type hints, max 500 lines per file

- [ ] [T104] Create SQLAlchemy table models for PluginRegistry
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/shared/database/models.py`
  - **Add** (below existing tables):
    - `ToolTable(Base)`: maps to `tools` table with all columns from LLD Section 2.2
    - `OperationTable(Base)`: maps to `operations` table with all columns
    - `RegistryVersionTable(Base)`: maps to `registry_versions` table
  - Follow existing patterns in the file (SQLAlchemy_UUID, text for defaults, Index objects)
  - Include partial index: `idx_tools_active ON tools(tool_id) WHERE active = TRUE`
  - Include unique constraint: `UNIQUE(tool_id, operation_id)` on operations table

- [ ] [T105] Write schema validation tests
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_schemas.py`
  - Test cases:
    - Valid tool definition JSON validates against `tool_definition.schema.json`
    - Invalid tool_id format (missing dot separator) is rejected
    - Missing required fields are rejected
    - Valid operation JSON validates against `operation.schema.json`
    - Valid validation result validates against `validation_result.schema.json`
    - Pydantic models serialize/deserialize correctly
    - Tool_id pattern `^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$` rejects invalid formats
    - Operation_id pattern `^[a-z][a-z0-9_]{1,126}$` rejects invalid formats
  - Use `jsonschema` library for JSON schema validation (add to pyproject.toml dev deps if needed)
  - TDD: Write these tests FIRST, they should FAIL before implementation

---

## Phase 2: Service Layer (Business Logic)

### Acceptance Criterion: US-1 (Tool Retrieval), US-2 (Versioning & Validation), US-3 (Template Resolution), US-4 (Admin CRUD)

- [ ] [T200] Implement RegistryService core read operations
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/registry_service.py`
  - **Methods to implement**:
    - `__init__(self, db_adapter: RegistryDatabaseAdapter)` -- dependency injection
    - `async def get_tool(self, tool_id: str, plan_id: str | None = None) -> ToolModel` -- US-1 scenario 1
    - `async def list_catalog(self, page: int = 1, page_size: int = 50, plan_id: str | None = None) -> CatalogResponse` -- US-1 scenario 2
    - `async def get_version(self) -> int` -- US-2 scenario 1
  - **Validation logic**:
    - Validate `tool_id` format before DB query (SPEC Decision Rule 2)
    - Raise `ToolNotFoundError` if tool not found or inactive (SPEC Decision Rule 4)
    - Return empty list for empty catalog (US-1 scenario 2)
  - **Logging**: structured JSON logs with `tool_id`, `plan_id` (if provided), `operation`, `latency_ms` (LLD Section 7.3)
  - **No external mutations**: all read operations

- [ ] [T201] Implement RegistryService write operations (Admin CRUD)
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/registry_service.py`
  - **Methods to implement**:
    - `async def create_tool(self, tool_def: CreateToolRequest) -> CreateToolResponse` -- US-4 scenario 1
    - `async def update_tool(self, tool_id: str, updates: UpdateToolRequest) -> UpdateToolResponse` -- US-4 scenario 2
    - `async def deactivate_tool(self, tool_id: str) -> DeactivateToolResponse` -- US-4 scenario 3
  - **Validation logic**:
    - Validate `tool_id` format (SPEC Decision Rule 2)
    - Check tool_id uniqueness on create (SPEC Decision Rule 3) -- raise `ToolAlreadyExistsError`
    - Check tool exists on update/deactivate (SPEC Decision Rule 4) -- raise `ToolNotFoundError`
    - Validate tool definition against JSON schema (SPEC Decision Rule 5) -- raise `SchemaValidationError`
    - Validate compensation referential integrity: if `compensation` field references an operation, that operation must exist on the same tool (LLD Section 11.3)
  - **Version increment**: each write operation increments registry version atomically via DB adapter (LLD Section 4.4)
  - **Logging**: include `admin_user_id` (from auth context) for write operations

- [ ] [T202] Implement credential template resolution
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/registry_service.py`
  - **Method to implement**:
    - `async def resolve_credential_template(self, tool_id: str, variables: dict[str, str]) -> ResolvedCredential` -- US-3
  - **Implementation details** (from LLD Section 7.2):
    - Extract template variables from tool's `credential_template` (regex: `\{\{(\w+)\}\}`)
    - Check all required variables are provided -- raise `TemplateResolutionError` with missing variable names
    - Sanitize each variable value: only `[a-zA-Z0-9_-]` allowed (SPEC FR-005, LLD Section 7.2)
    - Perform pure string interpolation: replace `{{var}}` with sanitized value
    - Return `ResolvedCredential(credential_id=resolved_string, tool_id=tool_id, n8n_credential_type=...)`
  - **Security**: NEVER return, store, or log actual credential values
  - **Pure computation**: no network calls, no file I/O for the resolution itself (DB lookup for template is the only I/O)

- [ ] [T203] Implement pre-execution validation
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/registry_service.py`
  - **Method to implement**:
    - `async def validate_plan_tools(self, plan_registry_version: int, referenced_tool_ids: list[str]) -> ValidationResult` -- US-2 scenarios 3-4
  - **Implementation details** (from LLD Section 4.2):
    - Query all referenced tools by their IDs
    - For each tool: check if it exists and if it is active
    - Build issues list: `TOOL_NOT_FOUND` for missing tools, `TOOL_DEACTIVATED` for inactive tools
    - Get current registry version
    - Return `ValidationResult(valid=len(issues)==0, current_version=current_ver, issues=issues)`
  - **No version mismatch error**: version differences are informational only; the check is about tool activity

- [ ] [T204] Implement scope verification
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/registry_service.py`
  - **Method to implement**:
    - `async def verify_scopes(self, tool_id: str, operation_id: str, required_scopes: list[str]) -> ScopeVerificationResult` -- US-5
  - **Implementation details**:
    - Retrieve tool and specific operation
    - Compare required scopes against operation's `scopes` list
    - Return `ScopeVerificationResult(supported=all_present, missing_scopes=[...])`

- [ ] [T205] Write unit tests for registry service (tool CRUD and versioning)
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_unit_registry.py`
  - **Test cases** (from LLD Section 8.5, item 1):
    - `test_get_tool_happy_path` -- US-1 scenario 1
    - `test_get_tool_not_found` -- returns ToolNotFoundError
    - `test_list_catalog_with_tools` -- US-1 scenario 3 (previewable field present)
    - `test_list_catalog_empty_registry` -- US-1 scenario 2 (empty list, no error)
    - `test_list_catalog_excludes_inactive_tools` -- SPEC Decision Rule 7
    - `test_list_catalog_pagination` -- SPEC edge case (100+ tools)
    - `test_get_version_empty_registry` -- returns 0
    - `test_get_version_after_writes` -- US-2 scenario 2
    - `test_create_tool_happy_path` -- US-4 scenario 1
    - `test_create_tool_duplicate_id` -- SPEC edge case (TOOL_ALREADY_EXISTS)
    - `test_create_tool_invalid_id_format` -- SPEC Decision Rule 2
    - `test_create_tool_schema_validation_failure` -- US-4 scenario 4
    - `test_create_tool_increments_version` -- US-2 scenario 2
    - `test_create_tool_compensation_referential_integrity` -- SPEC Q3
    - `test_update_tool_happy_path` -- US-4 scenario 2
    - `test_update_tool_not_found` -- SPEC Decision Rule 4
    - `test_update_tool_increments_version` -- US-2 scenario 2
    - `test_deactivate_tool_happy_path` -- US-4 scenario 3
    - `test_deactivate_tool_not_found` -- SPEC Decision Rule 4
    - `test_deactivate_tool_increments_version` -- US-2 scenario 2
    - `test_deactivate_tool_idempotent` -- LLD Section 6.1 (deactivating already-inactive is no-op)
    - `test_compensation_field_present` -- US-1 scenario 4
  - Mock the `RegistryDatabaseAdapter` for unit isolation
  - TDD: Write tests FIRST

- [ ] [T206] Write unit tests for credential template resolution
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_unit_template.py`
  - **Test cases** (from LLD Section 8.5, item 2):
    - `test_resolve_template_happy_path` -- US-3 scenario 1: `gcal_user_{{user_id}}_{{account_name}}` + `{user_id: "u-123", account_name: "work"}` -> `gcal_user_u-123_work`
    - `test_resolve_template_missing_variable` -- US-3 scenario 2: returns TEMPLATE_RESOLUTION_ERROR with missing var name
    - `test_resolve_template_extra_variables_ignored` -- extra vars are harmless
    - `test_resolve_template_sanitization_rejects_slashes` -- SPEC edge case: `../` rejected
    - `test_resolve_template_sanitization_rejects_braces` -- SPEC edge case: `{{}}` rejected
    - `test_resolve_template_sanitization_rejects_spaces` -- SPEC edge case
    - `test_resolve_template_sanitization_rejects_semicolon` -- SPEC edge case: `;DROP TABLE` rejected
    - `test_resolve_template_sanitization_allows_hyphen_underscore` -- alphanumeric + `-` + `_` allowed
    - `test_resolve_template_empty_variable_value` -- edge case
    - `test_resolve_template_long_variable_value` -- edge case
    - `test_resolve_template_tool_not_found` -- raises ToolNotFoundError
    - `test_credential_id_never_contains_secrets` -- US-3 scenario 3: verify output is opaque string, no credential values
  - Mock the DB adapter
  - TDD: Write tests FIRST

- [ ] [T207] Write unit tests for pre-execution validation
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_unit_validation.py`
  - **Test cases** (from LLD Section 8.5, item 3):
    - `test_validate_all_tools_active` -- US-2 scenario 3: valid=True
    - `test_validate_deactivated_tool` -- US-2 scenario 4: valid=False, TOOL_DEACTIVATED
    - `test_validate_missing_tool` -- valid=False, TOOL_NOT_FOUND
    - `test_validate_mixed_results` -- some active, some deactivated, some missing
    - `test_validate_empty_tool_list` -- edge case: valid=True (nothing to validate)
    - `test_validate_returns_current_version` -- current_version field populated correctly
    - `test_verify_scopes_all_present` -- US-5 scenario 1: supported=True
    - `test_verify_scopes_missing_scope` -- US-5 scenario 2: supported=False, missing_scopes populated
  - Mock the DB adapter
  - TDD: Write tests FIRST

---

## Phase 3: Adapters (Database Integration)

### Acceptance Criterion: FR-002 (Execution Semantics), FR-004 (Versioning)

- [ ] [T300] Implement RegistryDatabaseAdapter
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/adapters/db.py`
  - **Class**: `RegistryDatabaseAdapter`
  - **Constructor**: `__init__(self)` -- uses `get_database_adapter()` from `shared/database/adapter.py`
  - **Methods to implement** (from LLD Section 6.1):
    - `async def get_tool(self, tool_id: str) -> ToolModel | None` -- SELECT tool + JOIN operations
    - `async def list_active_tools(self, page: int, page_size: int) -> tuple[list[ToolModel], int]` -- SELECT WHERE active=TRUE with OFFSET/LIMIT
    - `async def get_tools_by_ids(self, tool_ids: list[str]) -> list[ToolRow]` -- SELECT WHERE tool_id IN (...) for validation
    - `async def create_tool(self, tool: CreateToolRequest) -> None` -- INSERT tool + INSERT operations in transaction
    - `async def update_tool(self, tool_id: str, updates: UpdateToolRequest) -> None` -- UPDATE tool, upsert operations
    - `async def deactivate_tool(self, tool_id: str) -> None` -- UPDATE active=FALSE, updated_at=NOW()
    - `async def tool_exists(self, tool_id: str) -> bool` -- SELECT 1 FROM tools
    - `async def get_current_version(self) -> int` -- SELECT MAX(version) FROM registry_versions, default 0
    - `async def increment_version(self, change_summary: str) -> int` -- INSERT new version row, return new version
  - **Transaction management**: write operations (create, update, deactivate) wrap tool mutation + version increment in a single async transaction
  - **Use**: `shared/database/adapter.py` (SharedDatabaseAdapter / get_database_adapter)
  - **Use**: `shared/database/error_handler.py` (with_db_error_handling decorator)
  - **Use**: SQLAlchemy table models from `shared/database/models.py` (ToolTable, OperationTable, RegistryVersionTable)
  - Follow PYTHON_GUIDE: use `async with self.shared_db.get_session() as session:` pattern

- [ ] [T301] Write adapter integration tests
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_integration.py`
  - **Test cases** (from LLD Section 8.5, item 4):
    - `test_create_and_retrieve_tool` -- round-trip via PostgreSQL
    - `test_create_tool_with_operations` -- operations persisted and retrieved
    - `test_list_active_tools_excludes_inactive` -- only active tools returned
    - `test_list_active_tools_pagination` -- page/page_size work correctly
    - `test_deactivate_tool_persists` -- active=FALSE in DB after deactivate
    - `test_version_increment_atomic` -- version incremented within same transaction as tool write
    - `test_version_monotonic` -- each write produces version = previous + 1
    - `test_create_duplicate_tool_id_fails` -- IntegrityError on duplicate PK
    - `test_operation_unique_within_tool` -- UNIQUE(tool_id, operation_id) enforced
    - `test_transaction_rollback_on_failure` -- if version insert fails, tool insert also rolled back
    - `test_get_tools_by_ids_returns_correct_subset` -- only requested tools returned
  - **Requires**: PostgreSQL test database (use environment variable `TEST_DATABASE_URL`)
  - Mark with `@pytest.mark.integration` for CI isolation
  - TDD: Write tests FIRST

---

## Phase 4: API Handlers (Thin Wrappers)

### Acceptance Criterion: FR-001 (External Contract), all User Stories

- [ ] [T400] Implement FastAPI routes
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/api/routes.py`
  - **Router**: `APIRouter(prefix="/registry", tags=["registry"])`
  - **Endpoints to implement** (from LLD Section 3.1):
    - `GET /registry/tools/{tool_id}` -- delegates to `service.get_tool()`, returns standard response wrapper
    - `GET /registry/catalog` -- query params: `page`, `page_size`; delegates to `service.list_catalog()`
    - `GET /registry/version` -- delegates to `service.get_version()`
    - `POST /registry/validate` -- body: `ValidatePlanToolsRequest`; delegates to `service.validate_plan_tools()`
    - `POST /registry/resolve` -- body: `ResolveCredentialRequest`; delegates to `service.resolve_credential_template()`
    - `POST /registry/tools` -- body: `CreateToolRequest`; delegates to `service.create_tool()`
    - `PUT /registry/tools/{tool_id}` -- body: `UpdateToolRequest`; delegates to `service.update_tool()`
    - `DELETE /registry/tools/{tool_id}` -- delegates to `service.deactivate_tool()` (soft delete)
    - `GET /registry/health` -- health check endpoint
  - **Response format**: All endpoints return `{"status": "ok", "data": {...}}` on success, `{"status": "error", "error_code": "...", "message": "...", "details": {...}}` on error (SPEC FR-001)
  - **Error handling**: Catch domain exceptions (`ToolNotFoundError`, `ToolAlreadyExistsError`, `SchemaValidationError`, `TemplateResolutionError`, `InvalidToolIdFormatError`) and map to appropriate HTTP status codes (404, 409, 400, 422)
  - **Auth**: Use `Depends(get_auth_context)` from `shared/api/auth.py` for all endpoints; MVP does not enforce admin role
  - **Correlation**: Read `X-Plan-ID` header for log correlation on read endpoints
  - **Handlers are thin**: validate input, delegate to service, format response

- [ ] [T401] Register PluginRegistry service in app lifespan and router
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`
  - **Add** to `lifespan()` function:
    - Import `RegistryDatabaseAdapter` from `components.PluginRegistry.adapters.db`
    - Import `RegistryService` from `components.PluginRegistry.service.registry_service`
    - Create `registry_db = RegistryDatabaseAdapter()`
    - Create `app.state.registry_service = RegistryService(db_adapter=registry_db)`
  - **Add** router registration:
    - Import `router as registry_router` from `components.PluginRegistry.api.routes`
    - `app.include_router(registry_router)`
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py`
  - **Add**: `def get_registry_service(request: Request) -> Any: return request.app.state.registry_service`

- [ ] [T402] Write API handler tests
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_api.py`
  - **Test cases**:
    - `test_get_tool_returns_200_with_data` -- US-1 scenario 1
    - `test_get_tool_returns_404_when_not_found` -- TOOL_NOT_FOUND error format
    - `test_get_tool_invalid_id_format_returns_400` -- INVALID_TOOL_ID_FORMAT
    - `test_list_catalog_returns_200_with_tools` -- US-1 scenario 3
    - `test_list_catalog_returns_empty_list` -- US-1 scenario 2
    - `test_list_catalog_pagination_params` -- page and page_size honored
    - `test_get_version_returns_200` -- US-2 scenario 1
    - `test_validate_returns_200_valid` -- US-2 scenario 3
    - `test_validate_returns_200_invalid_with_issues` -- US-2 scenario 4
    - `test_resolve_returns_200_with_credential_id` -- US-3 scenario 1
    - `test_resolve_returns_error_for_missing_var` -- US-3 scenario 2
    - `test_create_tool_returns_200` -- US-4 scenario 1
    - `test_create_tool_returns_409_duplicate` -- TOOL_ALREADY_EXISTS
    - `test_create_tool_returns_400_schema_error` -- US-4 scenario 4
    - `test_update_tool_returns_200` -- US-4 scenario 2
    - `test_deactivate_tool_returns_200` -- US-4 scenario 3
    - `test_health_endpoint_returns_ok`
    - `test_unauthenticated_request_returns_401` -- auth middleware enforced
    - `test_response_format_matches_spec` -- verify `{"status": "ok", "data": {...}}` envelope
    - `test_error_response_format_matches_spec` -- verify `{"status": "error", "error_code": "...", ...}` envelope
  - Use `httpx.AsyncClient` with `TestClient` or `ASGITransport` for async testing
  - Mock `RegistryService` to isolate API layer

---

## Phase 5: Fault Isolation & Safety (Architectural)

### From MODULAR_ARCHITECTURE.md, LLD Section 7, constitution.md

- [ ] [T500] Implement credential isolation enforcement
  - **Scope**: Cross-cutting across all layers
  - **In service layer** (`registry_service.py`): Verify `resolve_credential_template` never returns actual credential values
  - **In adapter layer** (`db.py`): Verify no credential value columns exist in DB schema
  - **In API layer** (`routes.py`): Verify no credential values leak in response bodies
  - **Deliverable**: This is primarily verified through contract tests (T600), but ensure code never has a code path that could return credential values
  - Reference: LLD Section 7.1 (NON-NEGOTIABLE)

- [ ] [T501] Implement structured logging throughout component
  - **Modify**: All component files
  - **Requirements** (from LLD Section 7.3):
    - JSON structured log format with: `timestamp`, `level`, `service` ("pluginregistry"), `operation`, `tool_id`, `plan_id` (optional), `latency_ms`, `status`
    - Include `admin_user_id` for write operations (audit trail)
    - NEVER log credential values (only credential ID templates and resolved IDs)
    - NEVER log n8n vault contents
    - User IDs logged as UUIDs (not emails/names)
    - Use Python `logging` with `structlog` (already in pyproject.toml)
  - **Log at each layer**:
    - API layer: request received, response sent, latency
    - Service layer: operation start, validation results, version increments
    - Adapter layer: DB query execution, transaction commit/rollback
  - Reference: constitution.md Section VI (Observability & Privacy)

- [ ] [T502] Implement input validation and sanitization
  - **Scope**: Service layer (`registry_service.py`)
  - **tool_id format validation**: regex `^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$` -- already in Pydantic model, but also validate in service before DB calls
  - **Template variable sanitization**: `SAFE_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")` -- reject all other characters (LLD Section 7.2)
  - **Operation ID validation**: regex `^[a-z][a-z0-9_]{1,126}$`
  - **Decision rules enforcement**: implement the 8 deterministic rules from SPEC "Decision Rules" section in order (first match wins)

- [ ] [T503] Add health check endpoint
  - **Already included in T400** (GET /registry/health)
  - **Implementation**: check database connectivity via `SharedDatabaseAdapter.health_check()`
  - Return `{"status": "ok", "service": "pluginregistry"}` on success
  - Return `{"status": "degraded", "service": "pluginregistry", "reason": "database unavailable"}` on DB failure

---

## Phase 6: Contract Tests & Integration

### Acceptance Criterion: FR-003 (Safety), FR-006 (Schemas), SC-006 (Zero credential leakage), SC-007 (100% schema compliance)

- [ ] [T600] Write contract tests for schema compliance and credential isolation
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_contract.py`
  - **Test cases** (from LLD Section 8.5, item 5):
    - **Schema compliance**:
      - `test_tool_definition_conforms_to_schema` -- ToolModel output validates against `tool_definition.schema.json`
      - `test_operation_conforms_to_schema` -- OperationModel output validates against `operation.schema.json`
      - `test_validation_result_conforms_to_schema` -- ValidationResult output validates against `validation_result.schema.json`
      - `test_all_api_responses_match_spec_envelope` -- `{"status": "ok", "data": {...}}` format
      - `test_all_error_responses_match_spec_envelope` -- `{"status": "error", "error_code": "...", ...}` format
    - **Credential isolation (SC-006)**:
      - `test_no_credential_values_in_tool_response` -- API response contains template, not values
      - `test_no_credential_values_in_resolve_response` -- resolve returns ID, not secret
      - `test_no_credential_values_in_logs` -- capture log output, verify no credential values
      - `test_resolved_credential_is_opaque_string` -- credential_id is a plain string reference
    - **Invariant tests**:
      - `test_version_monotonicity` -- version never decreases
      - `test_tool_id_uniqueness` -- duplicate tool_id rejected
      - `test_operation_uniqueness_within_tool` -- duplicate operation_id within tool rejected
      - `test_template_resolution_all_or_nothing` -- no partial interpolation
      - `test_deactivation_is_soft_delete` -- deactivated tools remain in DB
  - Reference: SPEC Invariants & Guarantees (10 items)

- [ ] [T601] Write end-to-end flow tests
  - **Create**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_e2e_flow.py`
  - **Test cases**:
    - `test_planner_flow_catalog_to_plan_validation`:
      1. Create tool `google.calendar` with operations
      2. Retrieve catalog (verify tool present, version > 0)
      3. Resolve credential template with variables
      4. Validate plan tools (all active -> valid)
      5. Deactivate tool
      6. Validate same plan tools (deactivated -> invalid)
    - `test_admin_flow_create_update_deactivate`:
      1. Create tool
      2. Update tool (add operation)
      3. Verify version incremented twice
      4. Deactivate tool
      5. Verify catalog excludes deactivated tool
      6. Verify version incremented a third time
    - `test_scope_verification_flow`:
      1. Create tool with specific scopes
      2. Verify scopes present -> supported=True
      3. Verify scope not present -> supported=False, missing_scopes listed
  - These tests run against an actual PostgreSQL instance
  - Mark with `@pytest.mark.integration`

- [ ] [T602] Update conftest.py with shared test fixtures
  - **Modify**: `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/conftest.py`
  - **Fixtures to create**:
    - `sample_tool_def` -- a complete `CreateToolRequest` for `google.calendar` with 3 operations (list_free_busy, create_event, delete_event)
    - `sample_slack_tool_def` -- a `CreateToolRequest` for `slack.messaging` with 1 operation
    - `mock_db_adapter` -- a mocked `RegistryDatabaseAdapter` with configurable return values
    - `registry_service` -- a `RegistryService` instance with mock DB adapter injected
    - `sample_tool_model` -- a `ToolModel` instance for assertion comparisons
    - `sample_validation_request` -- a `ValidatePlanToolsRequest` with typical values
    - `sample_resolve_request` -- a `ResolveCredentialRequest` with typical variables
    - `async_db_adapter` -- (integration only) real `RegistryDatabaseAdapter` connected to test DB

---

## Task Summary

- **Total Tasks**: 25
- **Setup (Phase 0)**: T000-T002 (3 tasks)
- **Schemas & Domain (Phase 1)**: T100-T105 (6 tasks)
- **Service Layer (Phase 2)**: T200-T207 (8 tasks)
- **Adapters (Phase 3)**: T300-T301 (2 tasks)
- **API Handlers (Phase 4)**: T400-T402 (3 tasks)
- **Safety (Phase 5)**: T500-T503 (4 tasks -- T500 and T503 overlap with other phases but are tracked separately for verification)
- **Contract Tests (Phase 6)**: T600-T602 (3 tasks -- T602 is conftest setup used by all test phases)

## Implementation Order (Critical Path)

```
T000 (verify infra) -> T001 (package structure) -> T002 (migration)
    |
    v
T104 (SQLAlchemy models) -> T103 (Pydantic models) -> T100-T102 (JSON schemas) -> T105 (schema tests)
    |
    v
T602 (conftest fixtures)
    |
    v
T205-T207 (unit tests FIRST -- TDD) -> T200-T204 (service implementation)
    |
    v
T301 (adapter tests FIRST -- TDD) -> T300 (adapter implementation)
    |
    v
T402 (API tests FIRST -- TDD) -> T400 (routes implementation) -> T401 (app registration)
    |
    v
T500-T503 (safety & observability)
    |
    v
T600-T601 (contract & e2e tests)
```

## Dependencies

**External (from LLD.md Section 2.2 -- all already in pyproject.toml)**:
- `fastapi>=0.109.0` -- HTTP endpoints
- `pydantic>=2.0` -- data validation
- `sqlalchemy[asyncio]>=2.0` -- async ORM
- `asyncpg>=0.29` -- PostgreSQL async driver
- `structlog>=24.1.0` -- structured logging
- `pytest>=8.0.0`, `pytest-asyncio>=0.23.0` -- testing
- PostgreSQL 16 -- database

**Internal (from LLD.md Section 2.2 -- all already exist)**:
- `shared/database/adapter.py` -- SharedDatabaseAdapter, get_database_adapter()
- `shared/database/models.py` -- Base, UserTable (will be extended with PluginRegistry tables in T104)
- `shared/database/error_handler.py` -- with_db_error_handling, DatabaseError
- `shared/middleware/auth.py` -- AuthMiddleware (JWT-based)
- `shared/api/auth.py` -- get_auth_context, get_user_id
- `shared/api/error_handlers.py` -- ErrorResponse, APIErrorHandler
- `shared/app.py` -- create_app, lifespan (will be extended in T401)
- `shared/dependencies.py` -- FastAPI Depends functions (will be extended in T401)
- `migrations/001_create_users_table.sql` -- users table (already exists)

## Architectural Considerations

**Blast Radius** (from MODULAR_ARCHITECTURE.md Section 4):
- PluginRegistry has `Deps: None` (no component dependencies) -- it is a leaf/foundation component
- If PluginRegistry fails: Planner cannot generate plans, WorkflowBuilder cannot resolve n8n bindings, PreviewOrchestrator/ExecuteOrchestrator cannot perform pre-execution validation
- Containment: Database circuit breaker (via `with_db_error_handling`), health check endpoint for upstream monitoring
- Post-MVP: Redis caching for catalog to serve stale data during DB outages

**Determinism** (from LLD Section 1.3):
- PluginRegistry provides the `Registry vR` input to Planner's deterministic tuple
- The `registry_version` integer is monotonically increasing and included in signed plans
- Tamper detection is handled by Signer (Ed25519 signature covers registry_version), not by PluginRegistry
- Catalog queries are deterministic: same registry state -> same catalog output

**Credential Isolation** (NON-NEGOTIABLE, from LLD Section 7.1):
- PluginRegistry stores credential ID templates only -- NEVER actual credential values
- Template resolution is pure string interpolation -- no secret fetching, no network calls for credentials
- Resolved credential IDs are opaque string references -- actual secrets resolved by n8n at execution time
- Contract tests (T600) verify zero credential value leakage in API responses and logs

**Preview/Execute Model** (from LLD Section 1.3):
- DOES NOT APPLY to PluginRegistry -- it is an internal component
- All operations execute directly; no Preview/Execute wrappers
- The Preview/Execute model applies to plans that reference registry data, not to registry operations themselves

## Files Created/Modified Summary

**New files (18)**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/api/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/api/routes.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/service/registry_service.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/domain/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/domain/models.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/adapters/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/adapters/db.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/schemas/tool_definition.schema.json`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/schemas/operation.schema.json`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/schemas/validation_result.schema.json`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/conftest.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_schemas.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_unit_registry.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_unit_template.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_unit_validation.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_integration.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_contract.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_e2e_flow.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/PluginRegistry/tests/test_api.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/migrations/006_create_pluginregistry_tables.sql`

**Modified files (3)**:
- `/Users/anantshreechandola/Desktop/Personal-agent/shared/database/models.py` (add ToolTable, OperationTable, RegistryVersionTable)
- `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py` (add PluginRegistry service init and router)
- `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py` (add get_registry_service)
