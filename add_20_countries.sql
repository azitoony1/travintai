-- Travint.ai: Add 20 Countries for Global Coverage
-- Run this in your Supabase SQL editor

INSERT INTO countries (name, iso_code, created_at)
VALUES 
    ('USA', 'US', NOW()),
    ('France', 'FR', NOW()),
    ('United Kingdom', 'GB', NOW()),
    ('Turkey', 'TR', NOW()),
    ('Thailand', 'TH', NOW()),
    ('Saudi Arabia', 'SA', NOW()),
    ('Russia', 'RU', NOW()),
    ('Democratic Republic of the Congo', 'CD', NOW()),
    ('Nigeria', 'NG', NOW()),
    ('Ukraine', 'UA', NOW()),
    ('Brazil', 'BR', NOW()),
    ('Australia', 'AU', NOW()),
    ('China', 'CN', NOW()),
    ('Egypt', 'EG', NOW()),
    ('India', 'IN', NOW()),
    ('Mexico', 'MX', NOW()),
    ('South Africa', 'ZA', NOW()),
    ('Poland', 'PL', NOW()),
    ('Iran', 'IR', NOW()),
    ('Libya', 'LY', NOW())
ON CONFLICT (iso_code) DO NOTHING;

-- Verify
SELECT name, iso_code FROM countries 
WHERE iso_code IN ('US','FR','GB','TR','TH','SA','RU','CD','NG','UA','BR','AU','CN','EG','IN','MX','ZA','PL','IR','LY')
ORDER BY name;
