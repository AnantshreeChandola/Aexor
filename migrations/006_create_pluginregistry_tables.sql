-- Migration: 006_create_pluginregistry_tables
-- Description: Create tools, operations, and registry_versions tables
-- Date: 2026-03-11
-- Reference: components/PluginRegistry/LLD.md (Section 2.2)
-- Component: PluginRegistry (Domain Layer - tool catalog)

-- =============================================================================
-- Table: tools
-- Purpose: Registered external integrations (Google Calendar, Slack, etc.)
-- =============================================================================

CREATE TABLE tools (
    tool_id             VARCHAR(128) PRIMARY KEY,
    display_name        VARCHAR(255) NOT NULL,
    credential_template VARCHAR(512) NOT NULL,
    n8n_credential_type VARCHAR(128) NOT NULL,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Partial index for fast lookups of active tools only
CREATE INDEX idx_tools_active
    ON tools (tool_id) WHERE active = TRUE;

-- =============================================================================
-- Table: operations
-- Purpose: Capabilities of a tool (e.g., create_event, list_free_busy)
-- =============================================================================

CREATE TABLE operations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id    VARCHAR(128) NOT NULL,
    tool_id         VARCHAR(128) NOT NULL
                    REFERENCES tools(tool_id) ON DELETE CASCADE,
    n8n_node        VARCHAR(255) NOT NULL,
    previewable     BOOLEAN NOT NULL DEFAULT FALSE,
    idempotent      BOOLEAN NOT NULL DEFAULT FALSE,
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    compensation    VARCHAR(128),
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    UNIQUE (tool_id, operation_id)
);

-- Fast lookup of operations by tool
CREATE INDEX idx_operations_tool
    ON operations (tool_id);

-- =============================================================================
-- Table: registry_versions
-- Purpose: Monotonically increasing version counter for deterministic planning
-- =============================================================================

CREATE TABLE registry_versions (
    version         INTEGER PRIMARY KEY,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    change_summary  VARCHAR(512) NOT NULL
);

-- Seed initial version (empty registry)
INSERT INTO registry_versions (version, change_summary)
VALUES (0, 'initial empty registry');

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE tools IS
'PluginRegistry: source of truth for available external integrations.';

COMMENT ON COLUMN tools.credential_template IS
'Mustache-style template for credential IDs, e.g. gcal_user_{{user_id}}_{{account_name}}. NEVER stores actual secrets.';

COMMENT ON TABLE operations IS
'Capabilities of a registered tool with n8n node bindings and policy metadata.';

COMMENT ON COLUMN operations.compensation IS
'Operation ID of the undo operation on the same tool (nullable).';

COMMENT ON TABLE registry_versions IS
'Monotonically increasing version for deterministic planning (GLOBAL_SPEC 2.0).';
