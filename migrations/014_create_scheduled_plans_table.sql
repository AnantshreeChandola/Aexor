-- Migration 014: Create scheduled_plans table
-- Owned by: Scheduler component
-- Stores scheduled plan definitions, recurrence config, and execution state.
-- APScheduler uses in-memory job store; this table is the source of truth for
-- recovery on restart.

CREATE TABLE IF NOT EXISTS scheduled_plans (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID         NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name              VARCHAR(255) NOT NULL,
    intent_type       VARCHAR(64)  NOT NULL,
    skeleton_json     JSONB        NOT NULL,
    entities_json     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    constraints_json  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    schedule_type     VARCHAR(16)  NOT NULL,          -- "once" or "recurring"
    scheduled_at      TIMESTAMPTZ,                    -- For one-time schedules
    cron_expression   VARCHAR(128),                   -- Human-readable display
    recurrence_config JSONB,                          -- UI-friendly descriptor
    timezone          VARCHAR(64)  NOT NULL DEFAULT 'UTC',
    status            VARCHAR(16)  NOT NULL DEFAULT 'active',
    approval_mode     VARCHAR(16)  NOT NULL DEFAULT 'auto_approve',
    last_run_at       TIMESTAMPTZ,
    next_run_at       TIMESTAMPTZ,
    run_count         INTEGER      NOT NULL DEFAULT 0,
    max_runs          INTEGER,
    last_error        JSONB,
    source_plan_id    VARCHAR(26),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Indexes matching the SQLAlchemy model's __table_args__
CREATE INDEX IF NOT EXISTS idx_scheduled_plans_user_id
    ON scheduled_plans (user_id);

CREATE INDEX IF NOT EXISTS idx_scheduled_plans_user_active
    ON scheduled_plans (user_id, status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_scheduled_plans_next_run
    ON scheduled_plans (next_run_at);

CREATE INDEX IF NOT EXISTS idx_scheduled_plans_status
    ON scheduled_plans (status);
