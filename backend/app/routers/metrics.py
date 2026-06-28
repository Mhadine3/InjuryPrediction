import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DailyMetric, Player, PlayerBaselineProfile
from app.schemas import DailyMetricOut, DailyMetricCreate, SubmitAndPredictOut
from app.services.ml_inference import InjuryModel
from app.services.wellness_cache import refresh_wellness_cache

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/player/{player_id}", response_model=list[DailyMetricOut])
async def get_player_metrics(
    player_id: uuid.UUID,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    limit: int = Query(90, le=365),
    db: AsyncSession = Depends(get_db),
):
    q = select(DailyMetric).where(DailyMetric.player_id == player_id)
    if start_date:
        q = q.where(DailyMetric.metric_date >= start_date)
    if end_date:
        q = q.where(DailyMetric.metric_date <= end_date)
    q = q.order_by(DailyMetric.metric_date.desc()).limit(limit)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=DailyMetricOut, status_code=201)
async def submit_metric(payload: DailyMetricCreate, db: AsyncSession = Depends(get_db)):
    # Verify player exists
    player = await db.get(Player, payload.player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Prevent duplicate entry for same player + date
    existing = await db.execute(
        select(DailyMetric).where(
            DailyMetric.player_id == payload.player_id,
            DailyMetric.metric_date == payload.metric_date,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Metric already exists for player {payload.player_id} on {payload.metric_date}",
        )

    metric = DailyMetric(
        player_id=payload.player_id,
        metric_date=payload.metric_date,
        session_type=payload.session_type,
        sleep_quality=payload.sleep_quality,
        fatigue=payload.fatigue,
        soreness=payload.soreness,
        stress=payload.stress,
        sleep_duration_h=payload.sleep_duration_h,
        rpe=payload.rpe,
        session_duration_min=payload.session_duration_min,
        srpe=payload.srpe,
        session_distance_km=payload.session_distance_km,
        high_intensity_distance_m=payload.high_intensity_distance_m,
        sprints_count=payload.sprints_count,
        accel_decel_count=payload.accel_decel_count,
        hrv_ms=payload.hrv_ms,
        resting_hr_bpm=payload.resting_hr_bpm,
        hydration_usg=payload.hydration_usg,
        data_source=payload.data_source,
    )
    db.add(metric)
    await db.flush()
    await refresh_wellness_cache(db)
    await db.refresh(metric)
    return metric


@router.post("/submit-and-predict", response_model=SubmitAndPredictOut, status_code=201)
async def submit_and_predict(payload: DailyMetricCreate, db: AsyncSession = Depends(get_db)):
    """Submit today's metric, compute ACWR, run XGBoost, persist + return prediction."""
    player = await db.get(Player, payload.player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    existing = await db.execute(
        select(DailyMetric).where(
            DailyMetric.player_id == payload.player_id,
            DailyMetric.metric_date == payload.metric_date,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Metric already exists for player {payload.player_id} on {payload.metric_date}",
        )

    # ── Fetch last 27 rows so we have 28 total (including today) ──────────────
    history_result = await db.execute(
        select(DailyMetric)
        .where(DailyMetric.player_id == payload.player_id)
        .where(DailyMetric.metric_date < payload.metric_date)
        .order_by(DailyMetric.metric_date.desc())
        .limit(27)
    )
    history = list(reversed(history_result.scalars().all()))

    # ── Compute ACWR from sRPE history + today ─────────────────────────────────
    srpe_today = int(payload.srpe or 0)
    srpe_series = [int(r.srpe or 0) for r in history] + [srpe_today]

    acute_7d  = sum(srpe_series[-7:])
    chronic_28d = sum(srpe_series[-28:])
    # ACWR = weekly mean / 4-week mean (Gabbett 2016)
    acwr: float | None = round(acute_7d / (chronic_28d / 4), 3) if chronic_28d > 0 and len(srpe_series) >= 7 else None

    # ── Save metric ────────────────────────────────────────────────────────────
    metric = DailyMetric(
        player_id=payload.player_id,
        metric_date=payload.metric_date,
        session_type=payload.session_type,
        sleep_quality=payload.sleep_quality,
        fatigue=payload.fatigue,
        soreness=payload.soreness,
        stress=payload.stress,
        sleep_duration_h=payload.sleep_duration_h,
        rpe=payload.rpe,
        session_duration_min=payload.session_duration_min,
        srpe=srpe_today or None,
        session_distance_km=payload.session_distance_km,
        high_intensity_distance_m=payload.high_intensity_distance_m,
        sprints_count=payload.sprints_count,
        accel_decel_count=payload.accel_decel_count,
        hrv_ms=payload.hrv_ms,
        resting_hr_bpm=payload.resting_hr_bpm,
        hydration_usg=payload.hydration_usg,
        acute_load_7d=acute_7d or None,
        chronic_load_28d=chronic_28d or None,
        acwr=acwr,
        data_source=payload.data_source,
    )
    db.add(metric)
    await db.flush()
    await refresh_wellness_cache(db)

    # ── Baseline ───────────────────────────────────────────────────────────────
    bl_result = await db.execute(
        select(PlayerBaselineProfile).where(PlayerBaselineProfile.player_id == payload.player_id)
    )
    baseline_row = bl_result.scalar_one_or_none()
    baseline = {}
    if baseline_row:
        baseline = {
            "hrv_baseline_ms":      float(baseline_row.hrv_baseline_ms or 65),
            "sprint_speed_max_kmh": 30.0,
            "vo2_max_ml_kg_min":    float(baseline_row.vo2max_ml_kg_min or 55),
            "injury_proneness":     "medium",
            "recovery_speed":       "medium",
            "age_category":         "prime",
        }

    # ── Build metric dicts for ML (chronological, last = today) ───────────────
    def _row_dict(r: DailyMetric) -> dict:
        return {
            "metric_date":               str(r.metric_date),
            "session_type":              r.session_type,
            "rpe":                       float(r.rpe or 0),
            "session_duration_min":      float(r.session_duration_min or 0),
            "srpe":                      float(r.srpe or 0),
            "session_distance_km":       float(r.session_distance_km or 0),
            "high_intensity_distance_m": float(r.high_intensity_distance_m or 0),
            "sprints_count":             float(r.sprints_count or 0),
            "accel_decel_count":         float(r.accel_decel_count or 0),
            "sleep_duration_h":          float(r.sleep_duration_h or 7),
            "sleep_quality":             float(r.sleep_quality or 4),
            "fatigue":                   float(r.fatigue or 3),
            "soreness":                  float(r.soreness or 3),
            "stress":                    float(r.stress or 3),
            "hrv_ms":                    float(r.hrv_ms or 65),
            "resting_hr_bpm":            float(r.resting_hr_bpm or 60),
            "hydration_usg":             float(r.hydration_usg or 1.010),
            "acute_load_7d":             float(r.acute_load_7d or 0) or None,
            "chronic_load_28d":          float(r.chronic_load_28d or 0) or None,
            "acwr":                      float(r.acwr or 0) or None,
        }

    metrics_dicts = [_row_dict(r) for r in history] + [_row_dict(metric)]

    # ── Run prediction ─────────────────────────────────────────────────────────
    age = (payload.metric_date - player.date_of_birth).days // 365
    from datetime import date as _date
    day_number = max(1, (payload.metric_date - _date(2026, 4, 1)).days + 1)

    model = InjuryModel.get()
    pred = model.predict(
        metrics=metrics_dicts,
        baseline=baseline,
        day_number=day_number,
        caps=player.caps,
        age=age,
        position_detail=player.position,
    )

    # ── Persist prediction back onto the metric row ────────────────────────────
    metric.injury_risk_score = round(pred["risk_score"], 4)
    metric.risk_category = pred["risk_category"]
    await db.flush()

    return SubmitAndPredictOut(
        metric_id=metric.id,
        player_id=metric.player_id,
        metric_date=metric.metric_date,
        acwr=acwr,
        acute_load_7d=acute_7d or None,
        chronic_load_28d=chronic_28d or None,
        risk_score=pred["risk_score"],
        risk_category=pred["risk_category"],
        confidence=pred["confidence"],
        top_features=pred["top_features"],
        model_version=pred["model_version"],
    )


@router.get("/team/{fifa_code}/latest", response_model=list[DailyMetricOut])
async def get_team_latest_metrics(
    fifa_code: str,
    as_of: date | None = Query(None, description="Date to use as 'today' (default: today)"),
    db: AsyncSession = Depends(get_db),
):
    """Most recent metric row per player for a team — used for the dashboard heat-map."""
    from app.models import Team
    from sqlalchemy import func

    target_date = as_of or date.today()

    # Subquery: latest metric_date per player
    sub = (
        select(
            DailyMetric.player_id,
            func.max(DailyMetric.metric_date).label("latest_date"),
        )
        .join(Player, Player.id == DailyMetric.player_id)
        .join(Team, Team.id == Player.team_id)
        .where(Team.fifa_code == fifa_code.upper())
        .where(DailyMetric.metric_date <= target_date)
        .group_by(DailyMetric.player_id)
        .subquery()
    )

    q = select(DailyMetric).join(
        sub,
        (DailyMetric.player_id == sub.c.player_id)
        & (DailyMetric.metric_date == sub.c.latest_date),
    )
    result = await db.execute(q)
    return result.scalars().all()
