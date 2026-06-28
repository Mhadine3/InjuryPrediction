from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DailyMetric, Player, PlayerBaselineProfile, Team
from app.schemas.readiness import PlayerReadiness, TeamReadinessSummary
from app.services.readiness import compute_readiness

router = APIRouter(prefix="/readiness", tags=["readiness"])

# ── WC competition-mode readiness ─────────────────────────────────────────────

_PART_MAP = {
    "starter":      {"score": 88, "category": "peak",     "session": "match_prep", "load_min": 350, "load_max": 550},
    "benched":      {"score": 74, "category": "ready",    "session": "recovery",   "load_min": 200, "load_max": 400},
    "no_match_yet": {"score": 75, "category": "ready",    "session": "technical",  "load_min": 380, "load_max": 560},
    "not_in_squad": {"score": 52, "category": "moderate", "session": "recovery",   "load_min": 150, "load_max": 300},
    "unavailable":  {"score": 35, "category": "low",      "session": "rest",       "load_min": 0,   "load_max": 0},
    "injured":      {"score": 22, "category": "low",      "session": "rest",       "load_min": 0,   "load_max": 0},
}


@router.get("/wc-team/{fifa_code}")
async def wc_team_readiness(fifa_code: str):
    """
    Competition-mode readiness for WC 2026 teams.
    Maps last-match participation → readiness score/category/recommended load.
    No training DB required — uses BSD Bzzoiro lineup data.
    """
    from app.services.wc_injury_service import compute_wc_team_risk

    tla = fifa_code.upper()
    risk_data = compute_wc_team_risk(tla)
    if "error" in risk_data:
        raise HTTPException(status_code=404, detail=risk_data["error"])

    players: list[dict] = []
    scores:  list[float] = []
    cats:    list[str]   = []

    for p in risk_data["players"]:
        part = p["participation"]
        m = _PART_MAP.get(part, _PART_MAP["no_match_yet"])

        score = float(m["score"])
        age = p.get("age", 25)
        if age >= 35:   score -= 8
        elif age >= 33: score -= 5
        elif age >= 30: score -= 3
        score = max(10.0, min(98.0, score))

        cat = "peak" if score >= 85 else "ready" if score >= 70 else "moderate" if score >= 50 else "low"

        flags: list[str] = []
        if part == "starter":
            flags.append("Played full match — monitor recovery")
        elif part == "benched":
            flags.append("Named substitute — maintain fitness")
        elif part in ("injured", "unavailable"):
            reason = p.get("injury_reason") or ""
            if reason and reason not in ("Started match", "Named substitute", "Not selected"):
                flags.append(f"Injury: {reason}")
            flags.append("Injured — rest & treatment only")
        elif part == "not_in_squad":
            flags.append("Not selected for last squad")

        dtm = p.get("days_to_next_match")
        if dtm is not None and dtm <= 3:
            flags.append(f"Match in {dtm} day{'s' if dtm != 1 else ''} — reduce volume")

        players.append({
            "player_id":          p["player_id"],
            "full_name":          p["full_name"],
            "position":           p["position"],
            "caps":               p["caps"],
            "readiness_score":    round(score, 1),
            "readiness_category": cat,
            "acwr_score":         None,
            "hrv_score":          None,
            "wellness_score":     None,
            "fitness_trend_score": None,
            "recommended_session": m["session"],
            "recommended_load_min": m["load_min"],
            "recommended_load_max": m["load_max"],
            "days_to_next_match": dtm,
            "acwr":               None,
            "flags":              flags,
            "participation":      part,
            "age":                age,
        })
        scores.append(score)
        cats.append(cat)

    players.sort(key=lambda x: -x["readiness_score"])
    overall = round(sum(scores) / len(scores), 1) if scores else 0.0

    return {
        "team_fifa_code":    tla,
        "team_name":         risk_data["team_name"],
        "as_of_date":        risk_data["as_of_date"],
        "day_number":        None,
        "competition_mode":  True,
        "overall_readiness": overall,
        "peak_count":        cats.count("peak"),
        "ready_count":       cats.count("ready"),
        "moderate_count":    cats.count("moderate"),
        "low_count":         cats.count("low"),
        "rest_count":        0,
        "players":           players,
        "days_to_next_match": risk_data.get("days_to_next_match"),
    }


async def _player_readiness(
    player: Player,
    baseline: PlayerBaselineProfile | None,
    db: AsyncSession,
    as_of: date,
    day_number: int,
    team_code: str,
) -> PlayerReadiness | None:
    rows_result = await db.execute(
        select(DailyMetric)
        .where(DailyMetric.player_id == player.id)
        .where(DailyMetric.metric_date <= as_of)
        .order_by(DailyMetric.metric_date.desc())
        .limit(28)
    )
    rows = rows_result.scalars().all()
    if not rows:
        return None

    metrics = [
        {
            "metric_date":   str(r.metric_date),
            "srpe":          float(r.srpe or 0),
            "hrv_ms":        float(r.hrv_ms or 0) or None,
            "fatigue":       float(r.fatigue or 3),
            "soreness":      float(r.soreness or 3),
            "stress":        float(r.stress or 3),
            "sleep_quality": float(r.sleep_quality or 3),
        }
        for r in reversed(rows)
    ]

    hrv_baseline = float(baseline.hrv_baseline_ms) if baseline and baseline.hrv_baseline_ms else 65.0

    latest_acwr_row = await db.execute(
        select(DailyMetric.acwr)
        .where(DailyMetric.player_id == player.id)
        .where(DailyMetric.metric_date <= as_of)
        .order_by(DailyMetric.metric_date.desc())
        .limit(1)
    )
    acwr_val = latest_acwr_row.scalar_one_or_none()
    acwr = float(acwr_val) if acwr_val else None

    result = compute_readiness(
        metrics=metrics,
        hrv_baseline=hrv_baseline,
        acwr=acwr,
        team_code=team_code,
        day_number=day_number,
    )

    return PlayerReadiness(
        player_id=player.id,
        full_name=f"{player.first_name} {player.last_name}",
        position=player.position,
        caps=player.caps,
        readiness_score=result.readiness_score,
        readiness_category=result.readiness_category,
        acwr_score=result.acwr_score,
        hrv_score=result.hrv_score,
        wellness_score=result.wellness_score,
        fitness_trend_score=result.fitness_trend_score,
        recommended_session=result.recommended_session,
        recommended_load_min=result.recommended_load_min,
        recommended_load_max=result.recommended_load_max,
        days_to_next_match=result.days_to_next_match,
        acwr=result.acwr,
        flags=result.flags,
    )


@router.get("/team/{fifa_code}", response_model=TeamReadinessSummary)
async def team_readiness(
    fifa_code: str,
    as_of: date | None = Query(None),
    day_number: int = Query(1, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    target_date = as_of or date.today()
    code = fifa_code.upper()

    team_result = await db.execute(select(Team).where(Team.fifa_code == code))
    team = team_result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{code}' not found")

    players_result = await db.execute(
        select(Player).where(Player.team_id == team.id, Player.is_active == True)
    )
    players = players_result.scalars().all()

    readiness_rows: list[PlayerReadiness] = []
    scores: list[float] = []

    for p in players:
        bl_result = await db.execute(
            select(PlayerBaselineProfile).where(PlayerBaselineProfile.player_id == p.id)
        )
        baseline = bl_result.scalar_one_or_none()

        row = await _player_readiness(p, baseline, db, target_date, day_number, code)
        if row is None:
            continue
        readiness_rows.append(row)
        scores.append(row.readiness_score)

    readiness_rows.sort(key=lambda r: -r.readiness_score)

    cats = [r.readiness_category for r in readiness_rows]
    overall = round(sum(scores) / len(scores), 1) if scores else 0.0

    return TeamReadinessSummary(
        team_fifa_code=code,
        team_name=team.name,
        as_of_date=target_date,
        day_number=day_number,
        overall_readiness=overall,
        peak_count=cats.count("peak"),
        ready_count=cats.count("ready"),
        moderate_count=cats.count("moderate"),
        low_count=cats.count("low"),
        rest_count=cats.count("rest"),
        players=readiness_rows,
    )
