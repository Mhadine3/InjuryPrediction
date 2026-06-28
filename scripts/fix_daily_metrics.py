"""Recreate daily_metrics table to match the SQLAlchemy model (metric_date, no generated srpe)."""
import asyncio
import asyncpg

DB_URL = "postgresql://postgres:postgres123@localhost:5432/injury_prediction"

SQL = """
DROP TABLE IF EXISTS daily_metrics CASCADE;

CREATE TABLE daily_metrics (
    id                        UUID          NOT NULL DEFAULT uuid_generate_v4(),
    player_id                 UUID          NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    metric_date               DATE          NOT NULL,
    session_type              VARCHAR(20),

    -- Hooper 1995 wellness (1-7 Likert)
    sleep_quality             SMALLINT      CHECK (sleep_quality  BETWEEN 1 AND 7),
    fatigue                   SMALLINT      CHECK (fatigue        BETWEEN 1 AND 7),
    soreness                  SMALLINT      CHECK (soreness       BETWEEN 1 AND 7),
    stress                    SMALLINT      CHECK (stress         BETWEEN 1 AND 7),
    sleep_duration_h          NUMERIC(4,2)  CHECK (sleep_duration_h BETWEEN 0 AND 24),

    -- Foster 2001 sRPE
    rpe                       SMALLINT      CHECK (rpe BETWEEN 0 AND 10),
    session_duration_min      SMALLINT      CHECK (session_duration_min BETWEEN 0 AND 300),
    srpe                      INTEGER,

    -- GPS — Bradley 2009 / Dellal 2010
    session_distance_km       NUMERIC(5,2)  CHECK (session_distance_km BETWEEN 0 AND 20),
    high_intensity_distance_m INTEGER       CHECK (high_intensity_distance_m BETWEEN 0 AND 10000),
    sprints_count             SMALLINT      CHECK (sprints_count BETWEEN 0 AND 150),
    accel_decel_count         SMALLINT      CHECK (accel_decel_count BETWEEN 0 AND 500),

    -- Buchheit 2014 recovery markers
    hrv_ms                    NUMERIC(6,2)  CHECK (hrv_ms BETWEEN 20 AND 150),
    resting_hr_bpm            SMALLINT      CHECK (resting_hr_bpm BETWEEN 30 AND 100),

    -- Armstrong 1994 hydration
    hydration_usg             NUMERIC(6,4)  CHECK (hydration_usg BETWEEN 1.001 AND 1.035),

    -- Gabbett 2016 ACWR (stored for fast queries)
    acute_load_7d             INTEGER,
    chronic_load_28d          INTEGER,
    acwr                      NUMERIC(5,3)  CHECK (acwr >= 0),

    -- ML output
    injury_risk_score         NUMERIC(5,4)  CHECK (injury_risk_score BETWEEN 0 AND 1),
    risk_category             VARCHAR(20),

    data_source               VARCHAR(20)   DEFAULT 'manual',
    created_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id)
);

CREATE INDEX idx_dm_player_date ON daily_metrics (player_id, metric_date DESC);
CREATE INDEX idx_dm_acwr        ON daily_metrics (acwr, metric_date DESC) WHERE acwr IS NOT NULL;
"""

async def fix():
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute(SQL)
        print("daily_metrics recreated successfully.")
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        await conn.close()

asyncio.run(fix())
