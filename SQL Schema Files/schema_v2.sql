-- =============================================================================
-- Travint.ai — Complete Database Schema v2.0
-- =============================================================================
-- Run this entire file in the Supabase SQL Editor (new project, clean slate).
-- This replaces all previous schema files.
--
-- Architecture:
--   Tier 1 → baseline_versions (immutable, human-approved)
--   Tier 2 → score_history (append-only, AI-maintained)
--   scores  → compatibility VIEW (dashboard.html reads this unchanged)
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- TABLE: countries
-- Master list of all countries in the system.
-- =============================================================================
CREATE TABLE IF NOT EXISTS countries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    iso_code    TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- TABLE: baseline_versions
-- Immutable Tier 1 baseline snapshots. Written once, never overwritten.
-- Every country must have an owner-approved baseline before going live.
-- Tier 3 rebalancing creates a new version_number, keeping history intact.
-- =============================================================================
CREATE TABLE IF NOT EXISTS baseline_versions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id               UUID NOT NULL REFERENCES countries(id),
    identity_layer           TEXT NOT NULL,
    -- 'base' | 'jewish_israeli' | 'solo_women' | 'lgbtq' | 'journalists' | 'aid_workers'
    version_number           INT NOT NULL DEFAULT 1,
    scores                   JSONB NOT NULL,
    -- {armed_conflict, regional_instability, terrorism, civil_strife, crime, health, infrastructure}
    total_score              TEXT NOT NULL,
    -- GREEN | YELLOW | ORANGE | RED | PURPLE
    stability_justifications JSONB,
    -- Per category: why is this the structural baseline? What would need to change?
    confidence_levels        JSONB,
    -- Per category: HIGH | MEDIUM | LOW | INSUFFICIENT
    baseline_narrative       TEXT,
    -- 3-4 paragraph human-readable structural assessment
    sources_used             JSONB,
    -- List of sources consulted for this baseline
    reviewed_by              TEXT DEFAULT 'pending',
    -- 'pending' | 'owner_approved'
    reviewed_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(country_id, identity_layer, version_number)
);

-- =============================================================================
-- TABLE: score_history
-- Append-only record of every score update. Never deleted or overwritten.
-- Replaces the old `scores` table which used destructive upsert.
-- Every pipeline run appends a new row; the view shows only the latest.
-- =============================================================================
CREATE TABLE IF NOT EXISTS score_history (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id           UUID NOT NULL REFERENCES countries(id),
    identity_layer       TEXT NOT NULL,
    total_score          TEXT NOT NULL,
    -- GREEN | YELLOW | ORANGE | RED | PURPLE
    scores               JSONB NOT NULL,
    -- {armed_conflict, regional_instability, terrorism, civil_strife, crime, health, infrastructure}
    ai_summary           TEXT,
    veto_explanation     TEXT,
    recommendations      JSONB,
    watch_factors        TEXT,
    sources              JSONB,
    confidence           JSONB,
    -- Confidence levels per category (HIGH|MEDIUM|LOW|INSUFFICIENT)
    logistics_score      TEXT,
    -- Future Travel Disruption dimension: NONE|MINOR|MODERATE|SEVERE|CRITICAL
    baseline_version_id  UUID REFERENCES baseline_versions(id),
    -- Which Tier 1 baseline this update was compared against
    tier                 INT DEFAULT 2,
    -- 1=Tier1 Baseline, 2=Tier2 Daily, 3=Tier3 Rebalancing
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- TABLE: change_events
-- Immutable record of every validated score change with its evidence.
-- The paper trail. Required: source quote for every change.
-- =============================================================================
CREATE TABLE IF NOT EXISTS change_events (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id           UUID NOT NULL REFERENCES countries(id),
    identity_layer       TEXT NOT NULL,
    category             TEXT NOT NULL,
    -- Which of the 7 categories changed
    old_score            TEXT NOT NULL,
    new_score            TEXT NOT NULL,
    source_quote         TEXT,
    -- Verbatim quote from the source that triggered this change
    source_name          TEXT,
    source_url           TEXT,
    source_date          DATE,
    change_type          TEXT NOT NULL,
    -- 'EVENT' | 'TREND' | 'POSITIVE' | 'SPILLOVER'
    trigger_country_id   UUID REFERENCES countries(id),
    -- For SPILLOVER changes: which country triggered this
    event_elevated       BOOLEAN DEFAULT FALSE,
    -- True = temporary event elevation, will auto-expire
    event_expiry         DATE,
    -- For EVENT type: 30 days from now; auto-propose return to baseline
    score_history_id     UUID REFERENCES score_history(id),
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- TABLE: trend_signals
-- Accumulating sub-threshold negative signals per country/layer.
-- When signal_count hits threshold (default 5), flag for human review.
-- Catches slow-building threats that no single event triggers.
-- Reset when human reviews or when score is officially elevated.
-- =============================================================================
CREATE TABLE IF NOT EXISTS trend_signals (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id       UUID NOT NULL REFERENCES countries(id),
    identity_layer   TEXT NOT NULL,
    signal_count     INT DEFAULT 0,
    last_signal_date DATE,
    threshold        INT DEFAULT 5,
    flagged          BOOLEAN DEFAULT FALSE,
    flagged_at       TIMESTAMPTZ,
    reset_at         TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(country_id, identity_layer)
);

-- =============================================================================
-- TABLE: review_queue
-- Items awaiting human review. Core of the human-in-the-loop system.
-- Priority: URGENT (RED/PURPLE changes) | STANDARD | WEEKLY
-- =============================================================================
CREATE TABLE IF NOT EXISTS review_queue (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id       UUID NOT NULL REFERENCES countries(id),
    identity_layer   TEXT NOT NULL,
    proposal         JSONB NOT NULL,
    -- Full proposed change: scores, quotes, reasoning, source articles
    priority         TEXT NOT NULL DEFAULT 'STANDARD',
    -- 'URGENT' | 'STANDARD' | 'WEEKLY'
    triggered_by     TEXT,
    -- 'sentinel' | 'trend_threshold' | 'second_llm_flag' | 'score_jump' | 'manual'
    failure_category TEXT,
    -- For tracking systematic prompt issues: 'hallucination' | 'overscoring' | etc.
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at      TIMESTAMPTZ,
    reviewer_action  TEXT,
    -- 'APPROVED' | 'REJECTED' | 'MODIFIED'
    reviewer_note    TEXT
);

-- =============================================================================
-- TABLE: annual_data
-- Annual dataset snapshots: RSF, ILGA, UNODC, Georgetown GIWPS, etc.
-- Loaded once per year. Used to inform Tier 1 baselines.
-- =============================================================================
CREATE TABLE IF NOT EXISTS annual_data (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id   UUID NOT NULL REFERENCES countries(id),
    data_type    TEXT NOT NULL,
    -- 'rsf_score' | 'ilga_criminalization' | 'unodc_homicide_rate' | 'giwps_score' | etc.
    value        TEXT NOT NULL,
    year         INT NOT NULL,
    source       TEXT NOT NULL,
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(country_id, data_type, year)
);

-- =============================================================================
-- TABLE: regional_dependencies
-- Owner-curated map: when country A has a sentinel event, re-analyze country B.
-- Configured by owner in admin panel — not hardcoded or algorithmic.
-- =============================================================================
CREATE TABLE IF NOT EXISTS regional_dependencies (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_country_id   UUID NOT NULL REFERENCES countries(id),
    affected_country_id  UUID NOT NULL REFERENCES countries(id),
    relationship_type    TEXT,
    -- 'border' | 'conflict_spillover' | 'diaspora' | 'trade' | 'alliance'
    notes                TEXT,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(trigger_country_id, affected_country_id)
);

-- =============================================================================
-- TABLE: country_resources
-- Practical resources shown in country detail panel.
-- Embassy contacts, emergency numbers, identity-specific community resources.
-- =============================================================================
CREATE TABLE IF NOT EXISTS country_resources (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id     UUID NOT NULL REFERENCES countries(id),
    resource_type  TEXT NOT NULL,
    -- 'embassy' | 'emergency' | 'community' | 'hospital' | 'consulate'
    identity_layer TEXT,
    -- NULL = all travelers; 'jewish_israeli' = only shown to that layer; etc.
    name           TEXT NOT NULL,
    contact        TEXT,
    url            TEXT,
    notes          TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- TABLE: notifications
-- Internal alert feed for score changes, sentinel alerts, and events.
-- =============================================================================
CREATE TABLE IF NOT EXISTS notifications (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id          UUID REFERENCES countries(id),
    identity_layer      TEXT NOT NULL,
    notification_type   TEXT NOT NULL,
    -- 'level_change' | 'sentinel_alert' | 'trend_flagged' | 'baseline_due'
    old_value           TEXT,
    new_value           TEXT,
    message             TEXT NOT NULL,
    severity            TEXT NOT NULL,
    -- 'info' | 'warning' | 'critical'
    read                BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- TABLE: score_overrides
-- Manual admin overrides applied at display time.
-- When AI gets something wrong: log the correct value + reason here.
-- The system reads score_history, then applies any active overrides on top.
-- Every rejected AI proposal → logged here as a learning signal.
-- =============================================================================
CREATE TABLE IF NOT EXISTS score_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_id      UUID NOT NULL REFERENCES countries(id),
    identity_layer  TEXT NOT NULL,
    category        TEXT NOT NULL,
    -- Category name ('armed_conflict', etc.) or 'total_score'
    original_value  TEXT NOT NULL,
    override_value  TEXT NOT NULL,
    reason          TEXT,
    created_by      TEXT DEFAULT 'admin',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    active          BOOLEAN DEFAULT TRUE,
    UNIQUE(country_id, identity_layer, category)
);

-- =============================================================================
-- INDEXES — performance for common query patterns
-- =============================================================================

-- Most common: get latest scores for a country/layer
CREATE INDEX IF NOT EXISTS idx_score_history_country_layer
    ON score_history(country_id, identity_layer, created_at DESC);

-- Dashboard: get all latest scores across all countries
CREATE INDEX IF NOT EXISTS idx_score_history_created
    ON score_history(created_at DESC);

-- Change event lookups
CREATE INDEX IF NOT EXISTS idx_change_events_country
    ON change_events(country_id, created_at DESC);

-- Review queue: unreviewed items by priority
CREATE INDEX IF NOT EXISTS idx_review_queue_pending
    ON review_queue(priority, created_at) WHERE reviewed_at IS NULL;

-- Notifications: unread items
CREATE INDEX IF NOT EXISTS idx_notifications_unread
    ON notifications(read, created_at DESC);

-- Baseline lookups
CREATE INDEX IF NOT EXISTS idx_baseline_versions_country_layer
    ON baseline_versions(country_id, identity_layer, version_number DESC);

-- Annual data lookups
CREATE INDEX IF NOT EXISTS idx_annual_data_country_type
    ON annual_data(country_id, data_type, year DESC);

-- =============================================================================
-- VIEW: scores (backward compatibility)
-- The current dashboard.html queries a table called `scores`.
-- This view returns the latest score_history entry per country/layer,
-- exposing the exact same columns the dashboard expects.
-- No changes needed to dashboard.html.
-- When Next.js replaces the dashboard, drop this view.
-- =============================================================================
CREATE OR REPLACE VIEW scores AS
SELECT DISTINCT ON (sh.country_id, sh.identity_layer)
    sh.id,
    sh.country_id,
    sh.identity_layer,
    sh.total_score,
    (sh.scores->>'armed_conflict')::text        AS armed_conflict,
    (sh.scores->>'regional_instability')::text  AS regional_instability,
    (sh.scores->>'terrorism')::text             AS terrorism,
    (sh.scores->>'civil_strife')::text          AS civil_strife,
    (sh.scores->>'crime')::text                 AS crime,
    (sh.scores->>'health')::text                AS health,
    (sh.scores->>'infrastructure')::text        AS infrastructure,
    sh.ai_summary,
    sh.veto_explanation,
    sh.recommendations,
    sh.watch_factors,
    sh.sources,
    sh.created_at AS scored_at
FROM score_history sh
ORDER BY sh.country_id, sh.identity_layer, sh.created_at DESC;

-- =============================================================================
-- ROW LEVEL SECURITY
-- =============================================================================

ALTER TABLE countries             ENABLE ROW LEVEL SECURITY;
ALTER TABLE baseline_versions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE score_history         ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE trend_signals         ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_queue          ENABLE ROW LEVEL SECURITY;
ALTER TABLE annual_data           ENABLE ROW LEVEL SECURITY;
ALTER TABLE regional_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE country_resources     ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications         ENABLE ROW LEVEL SECURITY;
ALTER TABLE score_overrides       ENABLE ROW LEVEL SECURITY;

-- countries: anyone reads, service_role writes
CREATE POLICY "countries_read"  ON countries FOR SELECT USING (true);
CREATE POLICY "countries_write" ON countries FOR ALL TO service_role USING (true);

-- score_history: anyone reads; only service_role inserts (pipeline writes)
CREATE POLICY "score_history_read"  ON score_history FOR SELECT USING (true);
CREATE POLICY "score_history_write" ON score_history FOR INSERT TO service_role WITH CHECK (true);

-- baseline_versions: anyone reads; service_role writes
CREATE POLICY "baselines_read"  ON baseline_versions FOR SELECT USING (true);
CREATE POLICY "baselines_write" ON baseline_versions FOR ALL TO service_role USING (true);

-- change_events: anyone reads; service_role writes
CREATE POLICY "change_events_read"  ON change_events FOR SELECT USING (true);
CREATE POLICY "change_events_write" ON change_events FOR INSERT TO service_role WITH CHECK (true);

-- trend_signals: service_role only (internal pipeline state)
CREATE POLICY "trend_signals_all" ON trend_signals FOR ALL TO service_role USING (true);

-- review_queue: service_role full access (admin uses service_role)
CREATE POLICY "review_queue_all" ON review_queue FOR ALL TO service_role USING (true);

-- annual_data: anyone reads; service_role writes
CREATE POLICY "annual_data_read"  ON annual_data FOR SELECT USING (true);
CREATE POLICY "annual_data_write" ON annual_data FOR INSERT TO service_role WITH CHECK (true);

-- regional_dependencies: anyone reads; service_role writes
CREATE POLICY "regional_deps_read"  ON regional_dependencies FOR SELECT USING (true);
CREATE POLICY "regional_deps_write" ON regional_dependencies FOR ALL TO service_role USING (true);

-- country_resources: anyone reads; service_role writes
CREATE POLICY "resources_read"  ON country_resources FOR SELECT USING (true);
CREATE POLICY "resources_write" ON country_resources FOR ALL TO service_role USING (true);

-- notifications: service_role only
CREATE POLICY "notifications_all" ON notifications FOR ALL TO service_role USING (true);

-- score_overrides: anyone reads (applied at display time); service_role writes
CREATE POLICY "overrides_read"  ON score_overrides FOR SELECT USING (true);
CREATE POLICY "overrides_write" ON score_overrides FOR ALL TO service_role USING (true);

-- =============================================================================
-- SEED DATA: 22 Countries
-- =============================================================================
INSERT INTO countries (name, iso_code) VALUES
    ('Israel',                              'IL'),
    ('Netherlands',                         'NL'),
    ('USA',                                 'US'),
    ('France',                              'FR'),
    ('United Kingdom',                      'GB'),
    ('Turkey',                              'TR'),
    ('Thailand',                            'TH'),
    ('Saudi Arabia',                        'SA'),
    ('Russia',                              'RU'),
    ('Democratic Republic of the Congo',    'CD'),
    ('Nigeria',                             'NG'),
    ('Ukraine',                             'UA'),
    ('Brazil',                              'BR'),
    ('Australia',                           'AU'),
    ('China',                               'CN'),
    ('Egypt',                               'EG'),
    ('India',                               'IN'),
    ('Mexico',                              'MX'),
    ('South Africa',                        'ZA'),
    ('Poland',                              'PL'),
    ('Iran',                                'IR'),
    ('Libya',                               'LY')
ON CONFLICT (iso_code) DO NOTHING;

-- =============================================================================
-- VERIFY
-- =============================================================================
SELECT name, iso_code FROM countries ORDER BY name;
