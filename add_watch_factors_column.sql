-- Add watch_factors column to scores table
-- Run this in Supabase SQL Editor

ALTER TABLE scores 
ADD COLUMN IF NOT EXISTS watch_factors TEXT;

-- Also add sources column if missing
ALTER TABLE scores 
ADD COLUMN IF NOT EXISTS sources JSONB DEFAULT '[]'::jsonb;

-- Verify columns exist
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'scores' 
AND column_name IN ('watch_factors', 'sources', 'recommendations');
