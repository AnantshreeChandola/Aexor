-- Migration 009: Create audit_events table
-- Owned by: Audit component (platform layer)
-- Immutable append-only audit log for plan executions, approvals, and infrastructure events

CREATE TABLE IF NOT EXISTS audit_events (
    event_id      VARCHAR(26)   PRIMARY KEY,
    event_type    VARCHAR(32)   NOT NULL,
    plan_id       VARCHAR(26),
    user_id       VARCHAR(255),
    trace_id      VARCHAR(255),
    step_number   INTEGER,
    event_data    JSONB         NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Partial indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_audit_events_plan_id
    ON audit_events (plan_id) WHERE plan_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_audit_events_user_id
    ON audit_events (user_id) WHERE user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_audit_events_trace_id
    ON audit_events (trace_id) WHERE trace_id IS NOT NULL;

-- Full indexes for type and time filtering
CREATE INDEX IF NOT EXISTS idx_audit_events_event_type
    ON audit_events (event_type);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events (created_at);

-- Composite index for plan timeline queries
CREATE INDEX IF NOT EXISTS idx_audit_events_plan_created
    ON audit_events (plan_id, created_at) WHERE plan_id IS NOT NULL;
