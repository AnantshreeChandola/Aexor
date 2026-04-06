-- Migration: 010_create_credential_vault_table
-- Description: Create credential_vault table for encrypted tool credentials
-- Date: 2026-04-05
-- Reference: shared/database/models.py (CredentialVaultTable)
-- Component: ExecuteOrchestrator (credential storage)

-- =============================================================================
-- Table: credential_vault
-- Purpose: AES-256-GCM encrypted credentials for external tool integrations.
--          LLM never sees plaintext values. Decrypted at execution time by
--          ExecuteOrchestrator only.
-- =============================================================================

CREATE TABLE IF NOT EXISTS credential_vault (
    credential_id   UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID            NOT NULL
                    REFERENCES users(user_id) ON DELETE CASCADE,
    tool_id         VARCHAR(128)    NOT NULL,
    encrypted_value BYTEA           NOT NULL,
    iv              BYTEA           NOT NULL,
    key_version     INTEGER         NOT NULL DEFAULT 1,
    metadata        JSONB,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Indexes
-- =============================================================================

-- Composite index for lookups by user + tool
CREATE INDEX IF NOT EXISTS idx_credential_vault_user_tool
    ON credential_vault (user_id, tool_id);

-- Index for user-scoped queries
CREATE INDEX IF NOT EXISTS idx_credential_vault_user_id
    ON credential_vault (user_id);

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE credential_vault IS
    'AES-256-GCM encrypted credentials for external tools. Owned by ExecuteOrchestrator.';

COMMENT ON COLUMN credential_vault.encrypted_value IS
    'AES-256-GCM ciphertext of the credential value. NEVER expose to LLM.';

COMMENT ON COLUMN credential_vault.iv IS
    'Initialization vector for AES-256-GCM decryption.';

COMMENT ON COLUMN credential_vault.key_version IS
    'Encryption key version for key rotation support.';
