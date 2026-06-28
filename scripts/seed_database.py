"""
seed_database.py
================
Loads players_baseline.json + daily_metrics.csv into PostgreSQL.

Run from repo root:
    python scripts/seed_database.py

Requires DATABASE_URL in backend/.env or as environment variable.
Uses uuid5 to produce stable, deterministic UUIDs from string IDs —
re-running is idempotent (ON CONFLICT DO NOTHING / DO UPDATE).
"""

import asyncio
import csv
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

# ── resolve paths ─────────────────────────────────────────────────────────────

ROOT        = Path(__file__).resolve().parent.parent
DATA_DIR    = ROOT / "data"
BACKEND_DIR = ROOT / "backend"

# Load .env from backend/ before importing settings
from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# ── deterministic UUID namespace ──────────────────────────────────────────────

NS = uuid.UUID("b4a3f8c1-2d5e-4f7a-9b0c-1d2e3f4a5b6c")  # project-specific namespace

def pid(s: str) -> uuid.UUID:
    """Stable UUID from any string (player_id, team code, etc.)."""
    return uuid.uuid5(NS, s)

# ── static team definitions ───────────────────────────────────────────────────

TEAMS = [
    # Group C
    {"name": "Brazil",   "fifa_code": "BRA", "group_code": "C", "confederation": "CONMEBOL"},
    {"name": "Morocco",  "fifa_code": "MAR", "group_code": "C", "confederation": "CAF"},
    {"name": "Haiti",    "fifa_code": "HAI", "group_code": "C", "confederation": "CONCACAF"},
    {"name": "Scotland", "fifa_code": "SCO", "group_code": "C", "confederation": "UEFA"},
    # Group I
    {"name": "France",   "fifa_code": "FRA", "group_code": "I", "confederation": "UEFA"},
    {"name": "Senegal",  "fifa_code": "SEN", "group_code": "I", "confederation": "CAF"},
    {"name": "Iraq",     "fifa_code": "IRQ", "group_code": "I", "confederation": "AFC"},
    {"name": "Norway",   "fifa_code": "NOR", "group_code": "I", "confederation": "UEFA"},
]

# position_detail → DB enum value
POSITION_MAP = {
    "Goalkeeper":      "goalkeeper",
    "Center Back":     "center_back",
    "Full Back":       "fullback",
    "Wing Back":       "wingback",
    "Defensive Mid":   "defensive_midfielder",
    "Central Mid":     "central_midfielder",
    "Attacking Mid":   "attacking_midfielder",
    "Winger":          "winger",
    "Second Striker":  "second_striker",
    "Striker":         "striker",
}

# ── helpers ───────────────────────────────────────────────────────────────────

def split_name(full: str) -> tuple[str, str]:
    """'Achraf Hakimi' → ('Achraf', 'Hakimi').  Single-name players get '' first."""
    parts = full.strip().rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])


def _v(val):
    """Convert empty string / 'None' → None for DB insertion."""
    if val in ("", "None", "nan", "NaN", None):
        return None
    return val


def _f(val, decimals: int = 4):
    v = _v(val)
    if v is None:
        return None
    try:
        rounded = round(float(v), decimals)
        return rounded
    except (ValueError, TypeError):
        return None


def _i(val):
    v = _v(val)
    if v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None

# ── main seed logic ───────────────────────────────────────────────────────────

async def seed(db: AsyncSession) -> None:

    # ── 1. Teams ──────────────────────────────────────────────────────────────
    print("Seeding teams …")
    for t in TEAMS:
        team_id = pid(t["fifa_code"])
        await db.execute(text("""
            INSERT INTO teams (id, name, fifa_code, group_code, confederation)
            VALUES (:id, :name, :fifa_code, :group_code, :confederation)
            ON CONFLICT (fifa_code) DO UPDATE
                SET name          = EXCLUDED.name,
                    group_code    = EXCLUDED.group_code,
                    confederation = EXCLUDED.confederation,
                    updated_at    = NOW()
        """), {**t, "id": str(team_id)})
    print(f"  {len(TEAMS)} teams upserted")

    # ── 2. Players + baselines ────────────────────────────────────────────────
    print("Seeding players …")
    with open(DATA_DIR / "players_baseline.json", encoding="utf-8") as f:
        data = json.load(f)

    players = data["players"]
    # build string_id → team_uuid map
    team_uuid = {t["fifa_code"]: pid(t["fifa_code"]) for t in TEAMS}

    for p in players:
        player_uuid = pid(p["player_id"])
        team_id     = team_uuid[p["team_code"]]
        first, last = split_name(p["name"])
        pos         = POSITION_MAP.get(p["position_detail"], "striker")
        dob         = date.fromisoformat(p["date_of_birth"])

        await db.execute(text("""
            INSERT INTO players (
                id, team_id, first_name, last_name,
                position, date_of_birth, caps, is_active
            ) VALUES (
                :id, :team_id, :first_name, :last_name,
                :position, :date_of_birth, :caps, TRUE
            )
            ON CONFLICT (id) DO UPDATE
                SET first_name   = EXCLUDED.first_name,
                    last_name    = EXCLUDED.last_name,
                    position     = EXCLUDED.position,
                    date_of_birth= EXCLUDED.date_of_birth,
                    caps         = EXCLUDED.caps,
                    updated_at   = NOW()
        """), {
            "id":            str(player_uuid),
            "team_id":       str(team_id),
            "first_name":    first,
            "last_name":     last,
            "position":      pos,
            "date_of_birth": dob,
            "caps":          int(p.get("caps") or 0),
        })

        # baseline profile
        phys = p.get("physiology", {})
        await db.execute(text("""
            INSERT INTO player_baseline_profiles (
                id, player_id,
                resting_hr_bpm_baseline, hrv_baseline_ms,
                vo2max_ml_kg_min,
                typical_weekly_load_au,
                measured_at
            ) VALUES (
                :id, :player_id,
                :resting_hr, :hrv,
                :vo2max,
                :weekly_load,
                :measured_at
            )
            ON CONFLICT (player_id) DO UPDATE
                SET resting_hr_bpm_baseline = EXCLUDED.resting_hr_bpm_baseline,
                    hrv_baseline_ms         = EXCLUDED.hrv_baseline_ms,
                    vo2max_ml_kg_min        = EXCLUDED.vo2max_ml_kg_min,
                    typical_weekly_load_au  = EXCLUDED.typical_weekly_load_au,
                    updated_at              = NOW()
        """), {
            "id":           str(pid(p["player_id"] + "_baseline")),
            "player_id":    str(player_uuid),
            "resting_hr":   phys.get("resting_hr_bpm"),
            "hrv":          phys.get("hrv_baseline_ms"),
            "vo2max":       phys.get("vo2_max_ml_kg_min"),
            "weekly_load":  min(8000, max(500, _i(phys.get("distance_per_match_km", 10) * 700))),
            "measured_at":  date(2026, 3, 1),
        })

    print(f"  {len(players)} players + baselines upserted")

    # ── 3. Daily metrics ─────────────────────────────────────────────────────
    print("Seeding daily metrics …")

    # Build string player_id → UUID map
    player_uuid_map = {p["player_id"]: str(pid(p["player_id"])) for p in players}

    csv_path = DATA_DIR / "daily_metrics.csv"
    inserted = 0
    skipped  = 0

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # batch in chunks of 500 for performance
    CHUNK = 500
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        for row in chunk:
            player_uuid_str = player_uuid_map.get(row["player_id"])
            if not player_uuid_str:
                skipped += 1
                continue

            metric_id = pid(f"{row['player_id']}_{row['date']}")

            await db.execute(text("""
                INSERT INTO daily_metrics (
                    id, player_id, metric_date, session_type,
                    sleep_quality, fatigue, soreness, stress, sleep_duration_h,
                    rpe, session_duration_min, srpe,
                    session_distance_km, high_intensity_distance_m,
                    sprints_count, accel_decel_count,
                    hrv_ms, resting_hr_bpm, hydration_usg,
                    acute_load_7d, chronic_load_28d, acwr,
                    injury_risk_score, risk_category,
                    data_source
                ) VALUES (
                    :id, :player_id, :metric_date, :session_type,
                    :sleep_quality, :fatigue, :soreness, :stress, :sleep_duration_h,
                    :rpe, :session_duration_min, :srpe,
                    :session_distance_km, :hi_distance,
                    :sprints, :accel_decel,
                    :hrv_ms, :resting_hr, :hydration_usg,
                    :acute_load, :chronic_load, :acwr,
                    :risk_score, :risk_cat,
                    'import'
                )
                ON CONFLICT (id) DO NOTHING
            """), {
                "id":                str(metric_id),
                "player_id":         player_uuid_str,
                "metric_date":       date.fromisoformat(row["date"]),
                "session_type":      _v(row.get("session_type")),
                "sleep_quality":     _i(row.get("sleep_quality")),
                "fatigue":           _i(row.get("fatigue")),
                "soreness":          _i(row.get("soreness")),
                "stress":            _i(row.get("stress")),
                "sleep_duration_h":  _f(row.get("sleep_duration_h"), 2),
                "rpe":               _i(row.get("rpe")),
                "session_duration_min": _i(row.get("session_duration_min")),
                "srpe":              _i(row.get("srpe")),
                "session_distance_km": _f(row.get("session_distance_km"), 2),
                "hi_distance":       _i(row.get("high_intensity_distance_m")),
                "sprints":           _i(row.get("sprints_count")),
                "accel_decel":       _i(row.get("accel_decel_count")),
                "hrv_ms":            _f(row.get("hrv_ms"), 2),
                "resting_hr":        _i(row.get("resting_hr_bpm")),
                "hydration_usg":     _f(row.get("hydration_usg"), 4),
                "acute_load":        _i(row.get("acute_load_7d")),
                "chronic_load":      _i(row.get("chronic_load_28d")),
                "acwr":              _f(row.get("acwr"), 3),
                "risk_score":        _f(row.get("injury_risk_score"), 4),
                "risk_cat":          _v(row.get("risk_category")),
            })
            inserted += 1

        await db.commit()
        print(f"  … {min(i + CHUNK, len(rows)):,} / {len(rows):,} rows", end="\r")

    print(f"\n  {inserted:,} metric rows inserted  ({skipped} skipped — unknown player_id)")


# ── schema check helper ───────────────────────────────────────────────────────

async def check_schema(db: AsyncSession) -> None:
    """Abort early with a helpful message if the schema hasn't been applied."""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='teams'"
    ))
    count = result.scalar()
    if count == 0:
        print("ERROR: 'teams' table not found.")
        print("Apply the schema first:")
        print("  psql -U postgres -d injury_prediction -f schema.sql")
        sys.exit(1)


# ── entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg", "postgresql+asyncpg")
    if not db_url:
        db_url = "postgresql+asyncpg://postgres:password@localhost:5432/injury_prediction"
        print(f"DATABASE_URL not set — using default: {db_url}")

    engine = create_async_engine(db_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        await check_schema(db)
        t0 = datetime.now()
        await seed(db)
        elapsed = (datetime.now() - t0).total_seconds()
        print(f"\nDone in {elapsed:.1f}s")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
