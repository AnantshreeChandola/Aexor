-- Migration: 001_create_users_table
-- Description: Create users table for global identity foundation
-- Date: 2025-12-28
-- Reference: SHARED_INFRASTRUCTURE.md §1.2

CREATE TABLE users (
  user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email VARCHAR(255) UNIQUE NOT NULL,
  full_name VARCHAR(255),
  context_tier INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  deleted_at TIMESTAMP NULL
);

-- Indexes for performance
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_context_tier ON users(context_tier);
CREATE INDEX idx_users_active ON users(user_id) WHERE deleted_at IS NULL;

-- Insert test user for development
INSERT INTO users (email, full_name, context_tier)
VALUES ('test@example.com', 'Test User', 3);
