-- 011: Create user_connections table
-- Tracks which providers each user has connected via hosted MCP services
-- (e.g. Composio). This is NOT a credential store — just boolean status
-- used by the intake layer to validate tool availability.

CREATE TABLE IF NOT EXISTS user_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    provider_name VARCHAR(64) NOT NULL,
    is_connected BOOLEAN NOT NULL DEFAULT false,
    connected_at TIMESTAMP WITH TIME ZONE,
    disconnected_at TIMESTAMP WITH TIME ZONE,
    composio_entity_id VARCHAR(128) NOT NULL,
    UNIQUE(user_id, provider_name)
);

CREATE INDEX IF NOT EXISTS idx_user_connections_user_id
    ON user_connections (user_id);

CREATE INDEX IF NOT EXISTS idx_user_connections_user_provider
    ON user_connections (user_id, provider_name)
    WHERE is_connected = TRUE;
