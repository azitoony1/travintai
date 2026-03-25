-- Manual Score Overrides Table
-- Allows admin to manually adjust scores when AI gets it wrong

CREATE TABLE IF NOT EXISTS score_overrides (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    country_id UUID REFERENCES countries(id),
    identity_layer TEXT NOT NULL,
    category TEXT NOT NULL,  -- 'armed_conflict', 'terrorism', etc. or 'total_score'
    original_value TEXT NOT NULL,  -- What AI said
    override_value TEXT NOT NULL,  -- What admin set
    reason TEXT,  -- Why admin changed it
    created_by TEXT DEFAULT 'admin',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    active BOOLEAN DEFAULT TRUE,
    
    UNIQUE(country_id, identity_layer, category)
);

-- When displaying scores, check for overrides:
-- SELECT 
--   COALESCE(o.override_value, s.terrorism) as terrorism_score
-- FROM scores s
-- LEFT JOIN score_overrides o 
--   ON s.country_id = o.country_id 
--   AND s.identity_layer = o.identity_layer 
--   AND o.category = 'terrorism'
--   AND o.active = TRUE
-- WHERE s.country_id = 'xyz';
