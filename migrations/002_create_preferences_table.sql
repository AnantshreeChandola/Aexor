-- Migration: 002_create_preferences_table
-- Description: Create preferences table for ProfileStore component
-- Date: 2025-12-28
-- Reference: specs/003-title-profilestore-description/LLD.md §2.2

CREATE TABLE preferences (
  preference_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  key VARCHAR(64) NOT NULL,
  value JSONB NOT NULL,
  sensitive BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  deleted_at TIMESTAMP NULL
);

-- Partial unique index: user_id + key must be unique for active (non-deleted) preferences
CREATE UNIQUE INDEX idx_preferences_user_key_active
  ON preferences(user_id, key) WHERE deleted_at IS NULL;

-- Index for efficient user preference queries (excluding soft-deleted)
CREATE INDEX idx_preferences_user_id ON preferences(user_id) WHERE deleted_at IS NULL;

-- Index for soft delete queries
CREATE INDEX idx_preferences_deleted_at ON preferences(deleted_at);
