import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DailyMetric, InjuryPrediction, Player, PlayerBaselineProfile, Team
from app.schemas import PredictionOut, TeamRiskSummary, HighRiskAlert, PlayerRiskRow
from app.services.ml_inference import InjuryModel

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/wc-team/{fifa_code}")
async def wc_team_risk(fifa_code: str):
    """
    Real WC 2026 injury risk based on BSD Bzzoiro lineup data.
    Uses actual match participation (starter / bench / injured / not selected)
    instead of synthetic training-camp metrics.
    """
    from app.services.wc_injury_service import compute_wc_team_risk
    return compute_wc_team_risk(fifa_code.upper())


@router.get("/compare/{match_id}")
async def compare_match_risk(match_id: str):
    """
    Side-by-side injury risk comparison for both teams in a scheduled match.
    Returns risk data for home + away team, win probabilities, and an injury
    edge indicator (which team carries lower aggregate risk into the fixture).
    """
    from app.services.wc_injury_service import compute_wc_team_risk
    from app.services.match_predictor import SCHEDULED_MATCHES
    from app.services.prematch_service import compute_prematch

    fixture = next((m for m in SCHEDULED_MATCHES if m["match_id"] == match_id), None)
    if not fixture:
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not in schedule")

    home_tla, away_tla = fixture["home"], fixture["away"]

    home_data = compute_wc_team_risk(home_tla)
    away_data  = compute_wc_team_risk(away_tla)

    outcome: dict = {}
    mls = None
    try:
        pm      = compute_prematch(home_tla, away_tla, match_id)
        outcome = pm.get("outcome_probabilities") or {}
        mls     = pm.get("most_likely_score")
    except Exception:
        pass

    home_mean = home_data.get("mean_risk_score") or 0.0
    away_mean = away_data.get("mean_risk_score") or 0.0

    return {
        "match_id":              match_id,
        "date":                  fixture["date"],
        "group":                 fixture["group"],
        "home":                  home_data,
        "away":                  away_data,
        "outcome_probabilities": outcome,
        "most_likely_score":     mls,
        "risk_delta":            round(home_mean - away_mean, 4),
    }


MODEL_VERSION = "v1"


async def _run_prediction_for_player(
    player: Player,
    baseline: PlayerBaselineProfile | None,
    db: AsyncSession,
    as_of: date,
    day_number: int,
) -> dict | None:
    """Fetch last 28 metric rows, run ML, return prediction dict."""
    rows_result = await db.execute(
        select(DailyMetric)
        .where(DailyMetric.player_id == player.id)
        .where(DailyMetric.metric_date <= as_of)
        .order_by(DailyMetric.metric_date.desc())
        .limit(28)
    )
    rows = rows_result.scalars().all()
    if len(rows) < 1:
        return None

    metrics = [
        {
            "metric_date":              str(r.metric_date),
            "session_type":             r.session_type,
            "rpe":                      float(r.rpe or 0),
            "session_duration_min":     float(r.session_duration_min or 0),
            "srpe":                     float(r.srpe or 0),
            "session_distance_km":      float(r.session_distance_km or 0),
            "high_intensity_distance_m": float(r.high_intensity_distance_m or 0),
            "sprints_count":            float(r.sprints_count or 0),
            "accel_decel_count":        float(r.accel_decel_count or 0),
            "sleep_duration_h":         float(r.sleep_duration_h or 7),
            "sleep_quality":            float(r.sleep_quality or 4),
            "fatigue":                  float(r.fatigue or 3),
            "soreness":                 float(r.soreness or 3),
            "stress":                   float(r.stress or 3),
            "hrv_ms":                   float(r.hrv_ms or 65),
            "resting_hr_bpm":           float(r.resting_hr_bpm or 60),
            "hydration_usg":            float(r.hydration_usg or 1.010),
            "acute_load_7d":            float(r.acute_load_7d or 0) or None,
            "chronic_load_28d":         float(r.chronic_load_28d or 0) or None,
            "acwr":                     float(r.acwr or 0) or None,
        }
        for r in reversed(rows)   # chronological order
    ]

    bl = {}
    if baseline:
        bl = {
            "hrv_baseline_ms":      float(baseline.hrv_baseline_ms or 65),
            "sprint_speed_max_kmh": 30.0,
            "vo2_max_ml_kg_min":    float(baseline.vo2max_ml_kg_min or 55),
            "injury_proneness":     "medium",
            "recovery_speed":       "medium",
            "age_category":         "prime",
        }

    age = (as_of - player.date_of_birth).days // 365
    model = InjuryModel.get()
    return model.predict(
        metrics=metrics,
        baseline=bl,
        day_number=day_number,
        caps=player.caps,
        age=age,
        position_detail=player.position,
    )


@router.get("/player/{player_id}", response_model=PredictionOut)
async def predict_player(
    player_id: uuid.UUID,
    as_of: date | None = Query(None),
    day_number: int = Query(1, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    target_date = as_of or date.today()

    player = await db.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    bl_result = await db.execute(
        select(PlayerBaselineProfile).where(PlayerBaselineProfile.player_id == player_id)
    )
    baseline = bl_result.scalar_one_or_none()

    pred = await _run_prediction_for_player(player, baseline, db, target_date, day_number)
    if not pred:
        raise HTTPException(status_code=422, detail="Not enough metric data to run prediction")

    latest_acwr = await db.execute(
        select(DailyMetric.acwr)
        .where(DailyMetric.player_id == player_id)
        .where(DailyMetric.metric_date <= target_date)
        .order_by(DailyMetric.metric_date.desc())
        .limit(1)
    )
    acwr_val = latest_acwr.scalar_one_or_none()

    return PredictionOut(
        player_id=player_id,
        prediction_date=target_date,
        model_version=pred["model_version"],
        risk_score=pred["risk_score"],
        risk_category=pred["risk_category"],
        acwr_at_prediction=float(acwr_val) if acwr_val else None,
        top_features=pred["top_features"],
    )


@router.get("/team/{fifa_code}", response_model=TeamRiskSummary)
async def team_risk_summary(
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

    rows: list[PlayerRiskRow] = []
    scores: list[float] = []

    for p in players:
        bl_result = await db.execute(
            select(PlayerBaselineProfile).where(PlayerBaselineProfile.player_id == p.id)
        )
        baseline = bl_result.scalar_one_or_none()

        pred = await _run_prediction_for_player(p, baseline, db, target_date, day_number)
        if not pred:
            continue

        acwr_row = await db.execute(
            select(DailyMetric.acwr)
            .where(DailyMetric.player_id == p.id)
            .where(DailyMetric.metric_date <= target_date)
            .order_by(DailyMetric.metric_date.desc())
            .limit(1)
        )
        acwr_val = acwr_row.scalar_one_or_none()

        risk_score = pred["risk_score"]
        scores.append(risk_score)
        rows.append(PlayerRiskRow(
            player_id=p.id,
            full_name=f"{p.first_name} {p.last_name}",
            position=p.position,
            caps=p.caps,
            risk_score=round(risk_score, 4),
            risk_category=pred["risk_category"],
            acwr=float(acwr_val) if acwr_val else None,
            alert=pred["risk_category"] in ("high", "very_high"),
        ))

    rows.sort(key=lambda r: -r.risk_score)
    cats = [r.risk_category for r in rows]

    return TeamRiskSummary(
        team_fifa_code=code,
        team_name=team.name,
        as_of_date=target_date,
        total_players=len(rows),
        low_count=cats.count("low"),
        moderate_count=cats.count("moderate"),
        high_count=cats.count("high"),
        very_high_count=cats.count("very_high"),
        mean_risk_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
        players=rows,
    )


@router.get("/high-risk", response_model=list[HighRiskAlert])
async def high_risk_alerts(
    as_of: date | None = Query(None),
    day_number: int = Query(1, ge=1, le=90),
    threshold: str = Query("moderate", description="Minimum category: moderate | high | very_high"),
    db: AsyncSession = Depends(get_db),
):
    target_date = as_of or date.today()
    threshold_map = {"moderate": 1, "high": 2, "very_high": 3}
    threshold_level = threshold_map.get(threshold, 1)
    cat_order = {"low": 0, "moderate": 1, "high": 2, "very_high": 3}

    players_result = await db.execute(
        select(Player, Team)
        .join(Team, Team.id == Player.team_id)
        .where(Player.is_active == True)
    )
    rows_raw = players_result.all()

    alerts: list[HighRiskAlert] = []
    for player, team in rows_raw:
        bl_result = await db.execute(
            select(PlayerBaselineProfile).where(PlayerBaselineProfile.player_id == player.id)
        )
        baseline = bl_result.scalar_one_or_none()
        pred = await _run_prediction_for_player(player, baseline, db, target_date, day_number)
        if not pred:
            continue
        if cat_order.get(pred["risk_category"], 0) < threshold_level:
            continue

        acwr_row = await db.execute(
            select(DailyMetric.acwr)
            .where(DailyMetric.player_id == player.id)
            .where(DailyMetric.metric_date <= target_date)
            .order_by(DailyMetric.metric_date.desc())
            .limit(1)
        )
        acwr_val = acwr_row.scalar_one_or_none()

        top_features = pred.get("top_features") or {}
        top_driver = next(iter(top_features), None)

        alerts.append(HighRiskAlert(
            player_id=player.id,
            full_name=f"{player.first_name} {player.last_name}",
            team_fifa_code=team.fifa_code,
            position=player.position,
            risk_score=round(pred["risk_score"], 4),
            risk_category=pred["risk_category"],
            acwr=float(acwr_val) if acwr_val else None,
            top_driver=top_driver,
            as_of_date=target_date,
        ))

    alerts.sort(key=lambda a: -a.risk_score)
    return alerts
