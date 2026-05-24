-- ============================================================
-- schema.sql — Injury Prediction Platform
-- PostgreSQL 16 + TimescaleDB
-- Target: 2026 FIFA World Cup — Group C
-- Authors: Injury Prediction Platform Engineering
-- ============================================================
-- Design principles:
--   • UUID primary keys for distributed-safe ID generation and future microservice split
--   • TIMESTAMPTZ everywhere — store in UTC, display in local time at app layer
--   • CHECK constraints enforce physiological ranges at DB level (last line of defence)
--   • TimescaleDB hypertable partitioned weekly for efficient time-range queries
--   • Schema is intentionally normalized to 3NF to prevent overtraining data conflicts
--     across the multi-coach model
--   • Designed to scale from 4 teams (WC 2026 Group C) to 48 teams without DDL changes
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================
-- ENUMERATED TYPES
-- ============================================================

CREATE TYPE player_position AS ENUM (
    'goalkeeper',
    'center_back',
    'fullback',
    'wingback',
    'defensive_midfielder',
    'central_midfielder',
    'attacking_midfielder',
    'winger',
    'second_striker',
    'striker'
);

CREATE TYPE coach_specialty AS ENUM (
    'physical',
    'tactical',
    'mental',
    'goalkeeper'
);

CREATE TYPE session_type AS ENUM (
    'recovery',
    'technical',
    'tactical',
    'physical',
    'match_prep',
    'strength',
    'match_simulation'
);

CREATE TYPE competition_type AS ENUM (
    'world_cup',
    'friendly',
    'qualifier',
    'continental'
);

CREATE TYPE match_status AS ENUM (
    'scheduled',
    'live',
    'played',
    'postponed',
    'cancelled'
);

CREATE TYPE injury_type AS ENUM (
    'muscle',
    'joint',
    'bone',
    'tendon',
    'ligament',
    'concussion',
    'contusion',
    'other'
);

CREATE TYPE injury_severity AS ENUM (
    'minor',
    'moderate',
    'severe',
    'career_threatening'
);

CREATE TYPE risk_category AS ENUM (
    'low',
    'moderate',
    'high',
    'very_high'
);

CREATE TYPE audit_action AS ENUM (
    'create',
    'update',
    'delete',
    'view',
    'export'
);

CREATE TYPE data_source_type AS ENUM (
    'manual',
    'gps_device',
    'wearable',
    'api',
    'import'
);

-- ============================================================
-- TABLE: teams
-- Represents national football teams
-- Scalable: group_code supports A-L for the 48-team 2026 format
-- ============================================================

CREATE TABLE teams (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(100) NOT NULL,
    fifa_code       CHAR(3)     NOT NULL UNIQUE,
    group_code      CHAR(1)     CHECK (group_code BETWEEN 'A' AND 'L'),
    confederation   VARCHAR(10) CHECK (confederation IN ('UEFA','CONMEBOL','CAF','CONCACAF','AFC','OFC')),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE teams IS
    'National football teams. Designed for 32-team tournaments; group_code A-L supports 48-team 2026 expansion.';
COMMENT ON COLUMN teams.fifa_code IS
    '3-letter FIFA country code (e.g. BRA, MAR, HAI, SCO). Stable external identifier for API integrations.';
COMMENT ON COLUMN teams.group_code IS
    'Tournament group assignment. NULL during draw-pending phase.';
COMMENT ON COLUMN teams.confederation IS
    'Governing confederation. Drives scheduling, travel, and rest-day regulations.';

CREATE INDEX idx_teams_fifa_code   ON teams (fifa_code);
CREATE INDEX idx_teams_group_code  ON teams (group_code) WHERE group_code IS NOT NULL;

-- ============================================================
-- TABLE: players
-- ============================================================

CREATE TABLE players (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    team_id         UUID            NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    first_name      VARCHAR(80)     NOT NULL,
    last_name       VARCHAR(80)     NOT NULL,
    jersey_number   SMALLINT        CHECK (jersey_number BETWEEN 1 AND 99),
    position        player_position NOT NULL,
    date_of_birth   DATE            NOT NULL,
    height_cm       SMALLINT        CHECK (height_cm BETWEEN 150 AND 220),
    weight_kg       NUMERIC(5,2)    CHECK (weight_kg BETWEEN 50.0 AND 130.0),
    dominant_foot   CHAR(5)         CHECK (dominant_foot IN ('left','right','both')),
    is_active       BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE players IS
    'Athletes registered per team. 25 per team (3rd goalkeeper excluded per squad regulations).';
COMMENT ON COLUMN players.jersey_number IS
    'FIFA tournament squad number 1-99. UNIQUE per active roster enforced via partial index.';
COMMENT ON COLUMN players.position IS
    'Granular position used for GPS load normalization. Maps to gps_load_by_position in scientific_tables.json.';
COMMENT ON COLUMN players.date_of_birth IS
    'Used to compute age at prediction time — an XGBoost feature for injury risk modelling.';

CREATE INDEX idx_players_team_id  ON players (team_id);
CREATE INDEX idx_players_position ON players (position);
CREATE UNIQUE INDEX idx_players_team_jersey
    ON players (team_id, jersey_number)
    WHERE is_active = TRUE;

-- ============================================================
-- TABLE: coaches
-- ============================================================

CREATE TABLE coaches (
    id          UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    team_id     UUID            NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    first_name  VARCHAR(80)     NOT NULL,
    last_name   VARCHAR(80)     NOT NULL,
    specialty   coach_specialty NOT NULL,
    email       VARCHAR(255)    NOT NULL UNIQUE,
    is_active   BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE coaches IS
    'Coaching staff per team. Multiple coaches per specialty allowed. Role determines metric entry responsibility.';
COMMENT ON COLUMN coaches.specialty IS
    'Determines which dashboard views and metric-entry forms the coach can access. Physical coaches own sRPE/GPS; mental coaches own stress/sleep data.';

CREATE INDEX idx_coaches_team_id    ON coaches (team_id);
CREATE INDEX idx_coaches_specialty  ON coaches (team_id, specialty);

-- ============================================================
-- TABLE: player_coach_assignments
-- Many-to-many: multiple coaches share responsibility for one athlete
-- Core of the overtraining-prevention model
-- ============================================================

CREATE TABLE player_coach_assignments (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id       UUID        NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    coach_id        UUID        NOT NULL REFERENCES coaches(id) ON DELETE CASCADE,
    assigned_at     DATE        NOT NULL DEFAULT CURRENT_DATE,
    unassigned_at   DATE,
    role_notes      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_assignment_dates
        CHECK (unassigned_at IS NULL OR unassigned_at > assigned_at)
);

COMMENT ON TABLE player_coach_assignments IS
    'Bridges players and coaches. Enables multi-coach shared planning so conflicting sessions are visible before scheduling.';
COMMENT ON COLUMN player_coach_assignments.unassigned_at IS
    'NULL = active assignment. Populated when coach changes role or leaves squad.';
COMMENT ON COLUMN player_coach_assignments.role_notes IS
    'Free-text description of coaching scope, e.g. "primary during hamstring rehab phase".';

CREATE INDEX idx_pca_player_id ON player_coach_assignments (player_id);
CREATE INDEX idx_pca_coach_id  ON player_coach_assignments (coach_id);
CREATE INDEX idx_pca_active
    ON player_coach_assignments (player_id, coach_id)
    WHERE unassigned_at IS NULL;

-- ============================================================
-- TABLE: player_baseline_profiles
-- Physiological reference values — one per player
-- Seeded pre-tournament; used by ACWR algorithm and XGBoost
-- ============================================================

CREATE TABLE player_baseline_profiles (
    id                       UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id                UUID         NOT NULL UNIQUE REFERENCES players(id) ON DELETE CASCADE,
    -- Cardiac parameters
    max_hr_bpm               SMALLINT     CHECK (max_hr_bpm BETWEEN 150 AND 220),
    resting_hr_bpm_baseline  SMALLINT     CHECK (resting_hr_bpm_baseline BETWEEN 30 AND 80),
    hrv_baseline_ms          NUMERIC(5,1) CHECK (hrv_baseline_ms BETWEEN 20.0 AND 150.0),
    -- Aerobic capacity
    vo2max_ml_kg_min         NUMERIC(4,1) CHECK (vo2max_ml_kg_min BETWEEN 40.0 AND 85.0),
    lactate_threshold_kmh    NUMERIC(4,1) CHECK (lactate_threshold_kmh BETWEEN 10.0 AND 22.0),
    -- Load reference (Foster 2001)
    typical_weekly_load_au   INTEGER      CHECK (typical_weekly_load_au BETWEEN 500 AND 8000),
    chronic_load_baseline_au INTEGER,
    measured_at              DATE         NOT NULL,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE player_baseline_profiles IS
    'Physiological reference profile per athlete, assessed pre-tournament. Personalises ACWR and HRV-based alerts.';
COMMENT ON COLUMN player_baseline_profiles.hrv_baseline_ms IS
    'Individual resting RMSSD (ms) measured in standardised conditions. Buchheit 2014: HRV changes evaluated vs individual baseline, not population norms.';
COMMENT ON COLUMN player_baseline_profiles.typical_weekly_load_au IS
    'Pre-tournament typical 7-day sRPE sum (AU = RPE × min). Seeds chronic load for ACWR continuity. Foster 2001.';
COMMENT ON COLUMN player_baseline_profiles.chronic_load_baseline_au IS
    'Pre-camp 28-day sRPE rolling mean × 4. Injected at day 0 so ACWR is valid from day 1 without a 28-day warm-up.';

-- ============================================================
-- TABLE: competitions
-- ============================================================

CREATE TABLE competitions (
    id                  UUID             PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                VARCHAR(150)     NOT NULL,
    competition_type    competition_type NOT NULL,
    edition_year        SMALLINT         NOT NULL,
    start_date          DATE             NOT NULL,
    end_date            DATE             NOT NULL,
    host_country        VARCHAR(100),
    created_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_comp_dates CHECK (end_date > start_date)
);

COMMENT ON TABLE competitions IS
    'Tournament or competition events. Supports World Cup, friendlies, and future competitions without schema changes.';
COMMENT ON COLUMN competitions.edition_year IS
    'Year of competition edition. Combined with competition_type forms a natural business key.';

CREATE INDEX idx_competitions_type_year ON competitions (competition_type, edition_year);

-- ============================================================
-- TABLE: matches
-- ============================================================

CREATE TABLE matches (
    id              UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    competition_id  UUID         NOT NULL REFERENCES competitions(id) ON DELETE RESTRICT,
    home_team_id    UUID         NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    away_team_id    UUID         NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    match_date      TIMESTAMPTZ  NOT NULL,
    venue           VARCHAR(150),
    match_status    match_status NOT NULL DEFAULT 'scheduled',
    home_score      SMALLINT     CHECK (home_score >= 0),
    away_score      SMALLINT     CHECK (away_score >= 0),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_different_teams CHECK (home_team_id <> away_team_id)
);

COMMENT ON TABLE matches IS
    'Individual matches within competitions. Transition to "played" triggers post-match recovery protocol in injury_predictions.';
COMMENT ON COLUMN matches.match_date IS
    'Kick-off in UTC. Frontend converts to local timezone using team home country.';
COMMENT ON COLUMN matches.match_status IS
    'Status lifecycle: scheduled → live → played. ML pipeline re-runs nightly after status = played.';

CREATE INDEX idx_matches_competition  ON matches (competition_id);
CREATE INDEX idx_matches_home_team    ON matches (home_team_id, match_date);
CREATE INDEX idx_matches_away_team    ON matches (away_team_id, match_date);
CREATE INDEX idx_matches_date         ON matches (match_date);

-- ============================================================
-- TABLE: training_sessions
-- Sessions planned and managed by coaches
-- ============================================================

CREATE TABLE training_sessions (
    id                    UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    team_id               UUID         NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
    created_by_coach_id   UUID         NOT NULL REFERENCES coaches(id) ON DELETE RESTRICT,
    session_date          DATE         NOT NULL,
    session_type          session_type NOT NULL,
    planned_duration_min  SMALLINT     NOT NULL CHECK (planned_duration_min BETWEEN 10 AND 300),
    actual_duration_min   SMALLINT     CHECK (actual_duration_min BETWEEN 0 AND 300),
    planned_rpe           NUMERIC(3,1) CHECK (planned_rpe BETWEEN 0 AND 10),
    session_notes         TEXT,
    is_cancelled          BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE training_sessions IS
    'Planned and executed training sessions. Multi-coach model: any authorized coach may create sessions; conflicts surface on shared calendar.';
COMMENT ON COLUMN training_sessions.created_by_coach_id IS
    'Accountability anchor — which coach owns this plan. Enables conflict detection when two coaches schedule high-load sessions on the same day.';
COMMENT ON COLUMN training_sessions.planned_rpe IS
    'Target RPE set by coach before session (Foster CR-10 scale 0-10). Compared post-session to actual sRPE to detect perceived load discrepancies.';

CREATE INDEX idx_ts_team_date  ON training_sessions (team_id, session_date);
CREATE INDEX idx_ts_coach_date ON training_sessions (created_by_coach_id, session_date);
CREATE INDEX idx_ts_type_date  ON training_sessions (session_type, session_date);

-- ============================================================
-- TABLE: daily_metrics  [TIMESCALEDB HYPERTABLE]
-- Core time-series table — 15 metrics per player per day
-- Partitioned weekly (chunk_time_interval = 7 days)
--
-- Partitioning choice: 7 days aligns with the training week
-- reporting cadence and keeps each chunk at ~700 rows
-- (100 players × 7 days), well within TimescaleDB sweet spot.
-- ============================================================

CREATE TABLE daily_metrics (
    id                        UUID                NOT NULL DEFAULT uuid_generate_v4(),
    player_id                 UUID                NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    measured_at               TIMESTAMPTZ         NOT NULL,   -- PARTITION KEY
    session_id                UUID                REFERENCES training_sessions(id) ON DELETE SET NULL,

    -- ── HOOPER WELLNESS ────────────────────────────────────────
    -- Source: Hooper & Mackinnon, Sports Medicine 1995, 20(5):321-327
    -- Scale: 1 (very very good) to 7 (very very bad)
    sleep_duration_h          NUMERIC(4,2)        CHECK (sleep_duration_h  BETWEEN 0.0  AND 24.0),
    sleep_quality             SMALLINT            CHECK (sleep_quality     BETWEEN 1    AND 7),
    fatigue                   SMALLINT            CHECK (fatigue           BETWEEN 1    AND 7),
    soreness                  SMALLINT            CHECK (soreness          BETWEEN 1    AND 7),
    stress                    SMALLINT            CHECK (stress            BETWEEN 1    AND 7),

    -- ── GPS EXTERNAL LOAD ──────────────────────────────────────
    -- Source: Bradley 2009 (JOSS 27:2, EPL data); Dellal 2010 (EJSS 10:1)
    session_distance_km       NUMERIC(5,2)        CHECK (session_distance_km        BETWEEN 0.0 AND 20.0),
    high_intensity_distance_m INTEGER             CHECK (high_intensity_distance_m  BETWEEN 0   AND 10000),  -- threshold: >19.8 km/h
    sprints_count             SMALLINT            CHECK (sprints_count              BETWEEN 0   AND 150),    -- threshold: >25.2 km/h
    accel_decel_count         SMALLINT            CHECK (accel_decel_count          BETWEEN 0   AND 500),    -- threshold: >3 m/s²

    -- ── FOSTER INTERNAL LOAD ───────────────────────────────────
    -- Source: Foster et al., JSCR 2001, 15(1):109-115
    rpe                       NUMERIC(3,1)        CHECK (rpe               BETWEEN 0.0 AND 10.0),  -- Modified Borg CR-10; collected 30 min post-session
    session_duration_min      SMALLINT            CHECK (session_duration_min BETWEEN 0 AND 300),
    srpe                      INTEGER             GENERATED ALWAYS AS               -- sRPE (AU) = RPE × duration. Stored for query performance.
                                  (ROUND(rpe * session_duration_min)::INTEGER) STORED,

    -- ── BUCHHEIT RECOVERY MARKERS ──────────────────────────────
    -- Source: Buchheit, Frontiers in Physiology 2014, 5:73
    hrv_ms                    NUMERIC(5,1)        CHECK (hrv_ms          BETWEEN 20.0  AND 150.0),  -- RMSSD in ms; morning supine measurement
    resting_hr_bpm            SMALLINT            CHECK (resting_hr_bpm  BETWEEN 30    AND 100),
    hydration_usg             NUMERIC(5,3)        CHECK (hydration_usg   BETWEEN 1.001 AND 1.035),  -- urine specific gravity; >1.020 = dehydration alert

    -- ── METADATA ───────────────────────────────────────────────
    recorded_by_coach_id      UUID                REFERENCES coaches(id) ON DELETE SET NULL,
    data_source               data_source_type    NOT NULL DEFAULT 'manual',
    created_at                TIMESTAMPTZ         NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id, measured_at)   -- composite PK required by TimescaleDB
);

COMMENT ON TABLE daily_metrics IS
    'TimescaleDB hypertable. 15 metrics per player per day. ~135,000 rows for 100 players × 90 days. Weekly partitioning (chunk_time_interval=7d).';
COMMENT ON COLUMN daily_metrics.measured_at IS
    'Partition key. Store as day + 06:00 local time in UTC to avoid DST gaps in daily aggregations.';
COMMENT ON COLUMN daily_metrics.srpe IS
    'Session RPE in Arbitrary Units (AU). Foster 2001: sRPE = CR-10 RPE × session duration (min). Generated column, stored for index efficiency.';
COMMENT ON COLUMN daily_metrics.hrv_ms IS
    'RMSSD (root mean square of successive differences) in ms. Morning supine protocol per Buchheit 2014. Interpret vs individual baseline in player_baseline_profiles.';
COMMENT ON COLUMN daily_metrics.hydration_usg IS
    'Urine specific gravity. 1.001-1.010: well-hydrated. >1.020: dehydrated (alert). >1.030: severe. Armstrong et al. 1994, MSSE.';
COMMENT ON COLUMN daily_metrics.high_intensity_distance_m IS
    'Distance covered above 19.8 km/h. Bradley 2009 EPL threshold. Key discriminator between positions and session intensities.';
COMMENT ON COLUMN daily_metrics.accel_decel_count IS
    'Events exceeding ±3 m/s². Dellal 2010 injury risk marker — accumulation correlates with soft-tissue injury risk.';
COMMENT ON COLUMN daily_metrics.sleep_quality IS
    'Hooper scale: 1 = very, very good ; 7 = very, very bad. Counter-intuitive direction maintained for Hooper index fidelity.';

-- Convert to hypertable — partitioned weekly
SELECT create_hypertable(
    'daily_metrics',
    'measured_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

-- Indexes on hypertable (TimescaleDB requires including the time column)
CREATE INDEX idx_dm_player_time ON daily_metrics (player_id, measured_at DESC);
CREATE INDEX idx_dm_session     ON daily_metrics (session_id)               WHERE session_id IS NOT NULL;
CREATE INDEX idx_dm_source_time ON daily_metrics (data_source, measured_at DESC);
CREATE INDEX idx_dm_alert_hrv   ON daily_metrics (player_id, measured_at DESC)
    WHERE hrv_ms IS NOT NULL;                   -- partial index for recovery dashboard queries

-- ============================================================
-- TABLE: wellness_questionnaires
-- Standalone morning Hooper questionnaire
-- Separate from daily_metrics to capture rest-day wellness
-- without requiring a session entry
-- ============================================================

CREATE TABLE wellness_questionnaires (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id           UUID        NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    questionnaire_date  DATE        NOT NULL,
    sleep_duration_h    NUMERIC(4,2) NOT NULL CHECK (sleep_duration_h  BETWEEN 0.0 AND 24.0),
    sleep_quality       SMALLINT    NOT NULL CHECK (sleep_quality BETWEEN 1 AND 7),
    fatigue             SMALLINT    NOT NULL CHECK (fatigue       BETWEEN 1 AND 7),
    soreness            SMALLINT    NOT NULL CHECK (soreness      BETWEEN 1 AND 7),
    stress              SMALLINT    NOT NULL CHECK (stress        BETWEEN 1 AND 7),
    hooper_index        SMALLINT    GENERATED ALWAYS AS            -- composite 4-28; >20 flags overreaching
                            (sleep_quality + fatigue + soreness + stress) STORED,
    notes               TEXT,
    submitted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wellness_player_date UNIQUE (player_id, questionnaire_date)
);

COMMENT ON TABLE wellness_questionnaires IS
    'Daily morning Hooper wellness questionnaire (Hooper & Mackinnon 1995). Decoupled from daily_metrics to allow capture on rest days.';
COMMENT ON COLUMN wellness_questionnaires.hooper_index IS
    'Composite wellness score (4-28). >20 triggers overreaching alert. Higher = worse recovery status.';
COMMENT ON COLUMN wellness_questionnaires.sleep_quality IS
    'Hooper scale: 1 = very very good ; 7 = very very bad.';

CREATE INDEX idx_wq_player_date    ON wellness_questionnaires (player_id, questionnaire_date DESC);
CREATE INDEX idx_wq_hooper_alerts  ON wellness_questionnaires (questionnaire_date, hooper_index)
    WHERE hooper_index > 20;

-- ============================================================
-- TABLE: injury_history
-- ============================================================

CREATE TABLE injury_history (
    id              UUID             PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id       UUID             NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    injury_type     injury_type      NOT NULL,
    body_part       VARCHAR(100)     NOT NULL,
    severity        injury_severity  NOT NULL,
    injury_date     DATE             NOT NULL,
    return_date     DATE,
    days_missed     INTEGER          GENERATED ALWAYS AS
                        (CASE WHEN return_date IS NOT NULL
                         THEN (return_date - injury_date)
                         ELSE NULL END) STORED,
    match_related   BOOLEAN          NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_injury_dates CHECK (return_date IS NULL OR return_date >= injury_date)
);

COMMENT ON TABLE injury_history IS
    'Complete injury record per player. Supplied as feature input to the XGBoost injury prediction model.';
COMMENT ON COLUMN injury_history.days_missed IS
    'Computed (return_date − injury_date). NULL while player is still injured. Used as severity proxy in ML feature engineering.';
COMMENT ON COLUMN injury_history.match_related IS
    'Distinguishes contact/collision injuries (match=TRUE) from overuse injuries (match=FALSE). Different prevention strategies apply.';
COMMENT ON COLUMN injury_history.body_part IS
    'Free-text anatomical location (e.g. "left hamstring", "right ACL"). Future version: replace with SNOMED CT code for interoperability.';

CREATE INDEX idx_ih_player_date ON injury_history (player_id, injury_date DESC);
CREATE INDEX idx_ih_type        ON injury_history (injury_type, severity);

-- ============================================================
-- TABLE: injury_predictions
-- ML model outputs: ACWR (Gabbett 2016) + XGBoost risk scores
-- One row per player per day (upserted nightly by ML pipeline)
-- ============================================================

CREATE TABLE injury_predictions (
    id               UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id        UUID          NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    prediction_date  DATE          NOT NULL,

    -- ACWR components — Gabbett TJ, BJSM 2016, 50(5):273-280
    acute_load_au    NUMERIC(8,2)  NOT NULL,           -- 7-day rolling sum of daily sRPE
    chronic_load_au  NUMERIC(8,2)  NOT NULL,           -- 28-day sRPE mean × 4 (normalised to 4-week cycle)
    acwr             NUMERIC(5,3)  NOT NULL             -- acute_load / chronic_load
                     CHECK (acwr >= 0),

    -- XGBoost output
    risk_score       NUMERIC(5,4)  NOT NULL             -- model probability 0.0-1.0
                     CHECK (risk_score BETWEEN 0 AND 1),
    risk_category    risk_category NOT NULL,
    alert_triggered  BOOLEAN       NOT NULL DEFAULT FALSE,

    -- Model provenance
    model_version    VARCHAR(50)   NOT NULL,            -- semantic version e.g. "1.2.0"
    features_snapshot JSONB,                            -- full feature vector at inference time for explainability

    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_prediction_player_date UNIQUE (player_id, prediction_date)
);

COMMENT ON TABLE injury_predictions IS
    'Daily ML model outputs. Combines ACWR (Gabbett 2016) and XGBoost probability. One row per player per day, upserted by nightly pipeline.';
COMMENT ON COLUMN injury_predictions.acwr IS
    'Acute:Chronic Workload Ratio. Gabbett 2016 danger zone: >1.5. Undertraining zone: <0.8. Sweet spot: 0.8-1.3.';
COMMENT ON COLUMN injury_predictions.acute_load_au IS
    '7-day rolling sum of sRPE in Arbitrary Units. Represents recent training stress stimulus.';
COMMENT ON COLUMN injury_predictions.chronic_load_au IS
    '28-day rolling sRPE average × 4, normalised to compare with 7-day acute. Represents fitness/adaptation base.';
COMMENT ON COLUMN injury_predictions.features_snapshot IS
    'JSONB snapshot of all feature values sent to XGBoost at inference time. Enables post-hoc explainability and model audit without re-computation.';
COMMENT ON COLUMN injury_predictions.model_version IS
    'Semantic version of XGBoost model binary. Tracks model drift across tournament duration and enables A/B comparison.';

CREATE INDEX idx_ip_player_date ON injury_predictions (player_id, prediction_date DESC);
CREATE INDEX idx_ip_alerts      ON injury_predictions (prediction_date, risk_category)
    WHERE alert_triggered = TRUE;
CREATE INDEX idx_ip_acwr_range  ON injury_predictions (acwr, prediction_date DESC);

-- ============================================================
-- TABLE: audit_log
-- Immutable trace of all coach-initiated data mutations
-- Required for sports governance and GDPR Article 30 compliance
-- ============================================================

CREATE TABLE audit_log (
    id          UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    coach_id    UUID         REFERENCES coaches(id) ON DELETE SET NULL,   -- NULL = system action
    action_type audit_action NOT NULL,
    table_name  VARCHAR(60)  NOT NULL,
    record_id   UUID         NOT NULL,
    old_values  JSONB,                          -- NULL for INSERT
    new_values  JSONB,                          -- NULL for DELETE
    ip_address  INET,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE audit_log IS
    'Append-only audit trail of coach-initiated data mutations. Required for sports governance and GDPR Article 30 record-keeping.';
COMMENT ON COLUMN audit_log.coach_id IS
    'NULL indicates a system/automated action (ML pipeline write, scheduled job). Not a FK violation — intentional for system events.';
COMMENT ON COLUMN audit_log.old_values IS
    'Pre-mutation state snapshot in JSONB. NULL for CREATE actions. Enables point-in-time reconstruction.';
COMMENT ON COLUMN audit_log.new_values IS
    'Post-mutation state snapshot in JSONB. NULL for DELETE actions.';
COMMENT ON COLUMN audit_log.ip_address IS
    'Stored as INET type to support both IPv4 and IPv6. Used for anomaly detection (unexpected geolocation).';

-- Audit log is append-only: no UPDATE/DELETE indexes needed, only lookup indexes
CREATE INDEX idx_audit_coach_time    ON audit_log (coach_id, created_at DESC);
CREATE INDEX idx_audit_table_record  ON audit_log (table_name, record_id);
CREATE INDEX idx_audit_time          ON audit_log (created_at DESC);

-- ============================================================
-- TRIGGER: auto-update updated_at on row mutation
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_teams_updated_at
    BEFORE UPDATE ON teams
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_players_updated_at
    BEFORE UPDATE ON players
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_coaches_updated_at
    BEFORE UPDATE ON coaches
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_baseline_updated_at
    BEFORE UPDATE ON player_baseline_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_matches_updated_at
    BEFORE UPDATE ON matches
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_ts_updated_at
    BEFORE UPDATE ON training_sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_ih_updated_at
    BEFORE UPDATE ON injury_history
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
