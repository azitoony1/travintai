-- Add regions JSONB column to score_history and baseline_versions
-- Run once in the Supabase SQL editor.

ALTER TABLE score_history
  ADD COLUMN IF NOT EXISTS regions JSONB;

ALTER TABLE baseline_versions
  ADD COLUMN IF NOT EXISTS regions JSONB;

-- No changes needed to the scores VIEW — the dashboard now fetches
-- score_history directly, so the new column is automatically available.
