-- Migration: 013_add_plan_outcomes_columns
-- Description: Add plan_revision and final_graph_json columns to plan_outcomes table
-- Date: 2026-04-07
-- Reference: PlanOutcomeTable in shared/database/models.py
-- Reason: These columns were added to the SQLAlchemy model (ExecuteOrchestrator v6.0)
--          but were missing from the original 005 migration.

ALTER TABLE plan_outcomes
    ADD COLUMN IF NOT EXISTS final_graph_json JSONB,
    ADD COLUMN IF NOT EXISTS plan_revision INTEGER NOT NULL DEFAULT 0;
