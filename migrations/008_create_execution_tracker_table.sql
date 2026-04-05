-- ExecutionMonitor: execution_tracker table
-- Tracks running plan executions for background watchdog monitoring.
-- Reference: Project_HLD.md §2.14 ExecutionMonitor

CREATE TABLE IF NOT EXISTS execution_tracker (
    tracker_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id      VARCHAR(26)  NOT NULL,
    user_id      VARCHAR(255) NOT NULL,
    trace_id     VARCHAR(255) NOT NULL,
    status       VARCHAR(32)  NOT NULL DEFAULT 'running',
    total_steps  INTEGER      NOT NULL DEFAULT 0,
    completed_steps INTEGER   NOT NULL DEFAULT 0,
    error_type   VARCHAR(64),
    error_details JSONB,
    notification_sent BOOLEAN NOT NULL DEFAULT FALSE,
    started_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_progress_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Partial index for active (running) executions — primary query target
CREATE INDEX IF NOT EXISTS idx_execution_tracker_active
    ON execution_tracker (status, started_at)
    WHERE status = 'running';

-- Lookup by plan_id (execution status queries)
CREATE INDEX IF NOT EXISTS idx_execution_tracker_plan_id
    ON execution_tracker (plan_id);

-- Lookup by user_id (user execution history)
CREATE INDEX IF NOT EXISTS idx_execution_tracker_user_id
    ON execution_tracker (user_id);
