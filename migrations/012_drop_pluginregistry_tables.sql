-- Migration: 012_drop_pluginregistry_tables
-- Description: Drop PluginRegistry tables and FK constraint from credential_vault.
--              PluginRegistry replaced by ToolCatalog (shared/mcp/catalog.py).
-- Date: 2026-04-06

-- =============================================================================
-- Step 1: Drop FK constraint from credential_vault → tools
-- =============================================================================

ALTER TABLE credential_vault
    DROP CONSTRAINT IF EXISTS credential_vault_tool_id_fkey;

-- =============================================================================
-- Step 2: Drop PluginRegistry tables
-- =============================================================================

DROP TABLE IF EXISTS operations CASCADE;
DROP TABLE IF EXISTS tools CASCADE;
DROP TABLE IF EXISTS registry_versions CASCADE;
