-- Migration: Add legal_risk as 8th scoring category
-- Run in Supabase SQL editor
-- 2026-03-27

-- score_history: legal_risk is stored inside the scores JSONB column,
-- so no column addition is needed there — the JSON already accommodates new fields.
-- baseline_versions: same — legal_risk appears inside the scores and
-- stability_justifications JSONB columns automatically.

-- However, if you have a scores view that extracts individual columns,
-- you may need to update it. The default view returns scores as JSONB,
-- so no changes are needed unless you have custom column extractions.

-- Verify the scores view returns the full JSONB (it should by default):
-- SELECT scores FROM score_history LIMIT 1;
-- You should see legal_risk in the JSON once re-runs complete.

-- No DDL changes required. The scores column is JSONB/text and stores
-- all category scores as a JSON object. Adding legal_risk to the object
-- in the Python pipeline is sufficient.

-- If you want to add a check to confirm legal_risk appears in scores:
-- SELECT id, country_id, scores->>'legal_risk' as legal_risk
-- FROM score_history
-- WHERE created_at > '2026-03-27'
-- ORDER BY created_at DESC;
