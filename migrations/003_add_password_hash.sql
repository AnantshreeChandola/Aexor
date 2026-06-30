-- Migration: 003_add_password_hash
-- Description: Add password_hash column for JWT-based authentication (Phase 2)
-- Date: 2026-02-11
-- Reference: SHARED_INFRASTRUCTURE.md §2.1 Phase 2

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255) NULL;

-- Nullable: existing seeded users have no password.
-- Only users created via /auth/register will have this populated.
