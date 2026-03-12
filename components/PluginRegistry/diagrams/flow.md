# PluginRegistry — Flow Diagrams

## 1. Planner Requests Catalog (Happy Path)

```mermaid
sequenceDiagram
    participant P as Planner
    participant API as PluginRegistry API
    participant SVC as RegistryService
    participant DB as PostgreSQL

    P->>API: GET /registry/catalog
    API->>SVC: list_catalog()
    SVC->>DB: SELECT * FROM tools WHERE active=TRUE
    DB-->>SVC: tool rows
    SVC->>DB: SELECT * FROM operations WHERE tool_id IN (...)
    DB-->>SVC: operation rows
    SVC->>SVC: assemble catalog
    SVC->>DB: SELECT MAX(version) FROM registry_versions
    DB-->>SVC: 5
    SVC-->>API: CatalogResponse(tools, version=5)
    API-->>P: {status: ok, data: {tools: [...], registry_version: 5}}
```

## 2. Pre-Execution Validation (Tool Deactivated)

```mermaid
sequenceDiagram
    participant EO as ExecuteOrchestrator
    participant API as PluginRegistry API
    participant SVC as RegistryService
    participant DB as PostgreSQL

    EO->>API: POST /registry/validate
    Note over EO,API: {version: 5, tools: ["google.calendar", "slack.messaging"]}
    API->>SVC: validate_plan_tools(5, [...])
    SVC->>DB: SELECT tool_id, active FROM tools WHERE tool_id IN (...)
    DB-->>SVC: google.calendar=active, slack.messaging=inactive
    SVC->>SVC: detect: slack.messaging DEACTIVATED
    SVC->>DB: SELECT MAX(version) FROM registry_versions
    DB-->>SVC: 7
    SVC-->>API: ValidationResult(valid=false, issues=[...])
    API-->>EO: {valid: false, current_version: 7, issues: [...]}
```

## 3. Credential Template Resolution

```mermaid
sequenceDiagram
    participant P as Planner
    participant API as PluginRegistry API
    participant SVC as RegistryService
    participant DB as PostgreSQL

    P->>API: POST /registry/resolve
    Note over P,API: {tool_id: "google.calendar", variables: {user_id: "u-123", account_name: "work"}}
    API->>SVC: resolve_credential_template(...)
    SVC->>DB: SELECT credential_template FROM tools WHERE tool_id=?
    DB-->>SVC: "gcal_user_{{user_id}}_{{account_name}}"
    SVC->>SVC: extract variables: [user_id, account_name]
    SVC->>SVC: validate all provided
    SVC->>SVC: sanitize values (alphanumeric+hyphen+underscore)
    SVC->>SVC: interpolate -> "gcal_user_u-123_work"
    SVC-->>API: ResolvedCredential
    API-->>P: {credential_id: "gcal_user_u-123_work"}
```

## 4. Create Tool (Version Increment)

```mermaid
sequenceDiagram
    participant USER as Authenticated User
    participant API as PluginRegistry API
    participant SVC as RegistryService
    participant DB as PostgreSQL

    USER->>API: POST /registry/tools
    API->>API: Check authenticated user

    API->>SVC: create_tool(tool_def)
    SVC->>SVC: validate tool_id format
    SVC->>DB: SELECT 1 FROM tools WHERE tool_id=?
    DB-->>SVC: null (not exists)
    SVC->>SVC: validate against schema

    SVC->>DB: BEGIN TRANSACTION
    SVC->>DB: INSERT INTO tools (...)
    SVC->>DB: INSERT INTO operations (...)
    SVC->>DB: INSERT INTO registry_versions (version=N+1)
    SVC->>DB: COMMIT
    DB-->>SVC: success

    SVC-->>API: CreateToolResponse(version=N+1)
    API-->>USER: {status: ok, registry_version: N+1}
```

## 5. Template Resolution Failure (Missing Variable)

```mermaid
sequenceDiagram
    participant P as Planner
    participant API as PluginRegistry API
    participant SVC as RegistryService
    participant DB as PostgreSQL

    P->>API: POST /registry/resolve
    Note over P,API: {tool_id: "google.calendar", variables: {user_id: "u-123"}}
    API->>SVC: resolve_credential_template(...)
    SVC->>DB: SELECT credential_template FROM tools WHERE tool_id=?
    DB-->>SVC: "gcal_user_{{user_id}}_{{account_name}}"
    SVC->>SVC: extract variables: [user_id, account_name]
    SVC->>SVC: check provided: {user_id: "u-123"}
    SVC->>SVC: MISSING: account_name
    SVC-->>API: TemplateResolutionError
    API-->>P: {error_code: "TEMPLATE_RESOLUTION_ERROR", missing: ["account_name"]}
```

## 6. End-to-End: Planning with Registry

```mermaid
sequenceDiagram
    participant U as User
    participant I as Intake
    participant CRAG as ContextRAG
    participant PL as Planner
    participant PR as PluginRegistry
    participant SIG as Signer

    U->>I: "Schedule meeting with Alice next week"
    I->>I: Parse Intent (extract account_hint if present)
    I->>CRAG: Get Evidence (prefs, history, integrations)
    CRAG->>CRAG: Query user_integrations for available accounts
    CRAG-->>I: Evidence[] (includes available accounts)

    I->>PL: Plan(Intent, Evidence)
    PL->>PR: GET /registry/catalog
    PR-->>PL: {tools: [...], registry_version: 5}
    PL->>PR: POST /registry/resolve {tool_id: "google.calendar", variables: {...}}
    PR-->>PL: {credential_id: "gcal_user_u-123_work"}

    PL->>PL: Generate plan with registry_version=5
    PL->>SIG: Sign(plan) — includes registry_version
    SIG-->>PL: SignedPlan

    Note over PL: Plan includes registry_version=5,<br/>signed by Signer
```

## 7. End-to-End: Pre-Execution Validation

```mermaid
sequenceDiagram
    participant EO as ExecuteOrchestrator
    participant PR as PluginRegistry
    participant SIG as Signer

    EO->>SIG: Verify plan signature
    SIG-->>EO: Signature valid

    EO->>PR: POST /registry/validate
    Note over EO,PR: {plan_registry_version: 5, tools: ["google.calendar"]}

    alt All tools active
        PR-->>EO: {valid: true, current_version: 7}
        EO->>EO: Proceed with execution
    else Tool deactivated
        PR-->>EO: {valid: false, issues: [{tool_id: "...", reason: "TOOL_DEACTIVATED"}]}
        EO->>EO: Reject plan, notify user
    end
```
