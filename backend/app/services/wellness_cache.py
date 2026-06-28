"""
wellness_cache.py

Bridges the async DB (DailyMetric, UUID-keyed) with the synchronous
risk_adjustment layer (string player_id keyed).

Called after every POST /metrics to keep data/latest_wellness.json fresh.
risk_adjustment.py loads that file at startup and re-loads it on each
prediction run so coaches see their logged data reflected immediately.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyMetric, Player, Team

logger = logging.getLogger(__name__)

# Day-1 of the WC preparation cycle (must match ml_inference._get_phase)
_TOURNAMENT_EPOCH = date(2026, 4, 1)

_BASELINE_FILE = Path(__file__).resolve().parents[3] / "data" / "players_baseline.json"
_CACHE_FILE    = Path(__file__).resolve().parents[3] / "data" / "latest_wellness.json"
_LOOKBACK_DAYS = 7          # only use metrics logged in the last 7 days


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


def _build_name_index() -> dict[str, str]:
    """Return {normalised_full_name -> player_id} from players_baseline.json."""
    if not _BASELINE_FILE.exists():
        return {}
    try:
        players = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))["players"]
        return {_norm(p["name"]): p["player_id"] for p in players}
    except Exception:
        return {}


async def refresh_wellness_cache(db: AsyncSession) -> None:
    """
    Pull the most recent DailyMetric per player (within last 7 days),
    compute deviations from baseline, and write data/latest_wellness.json.
    """
    cutoff = date.today() - timedelta(days=_LOOKBACK_DAYS)

    # Subquery: latest metric_date per player within lookback window
    sub = (
        select(
            DailyMetric.player_id,
            func.max(DailyMetric.metric_date).label("latest_date"),
        )
        .where(DailyMetric.metric_date >= cutoff)
        .group_by(DailyMetric.player_id)
        .subquery()
    )

    # Join to get the actual metric rows + player + team
    q = (
        select(DailyMetric, Player.first_name, Player.last_name, Team.fifa_code)
        .join(sub, (DailyMetric.player_id == sub.c.player_id) &
                   (DailyMetric.metric_date == sub.c.latest_date))
        .join(Player, Player.id == DailyMetric.player_id)
        .join(Team, Team.id == Player.team_id)
    )

    rows = (await db.execute(q)).all()
    if not rows:
        logger.info("wellness_cache: no recent metrics found, cache unchanged")
        return

    name_index = _build_name_index()
    baseline_map: dict[str, dict] = {}
    if _BASELINE_FILE.exists():
        for p in json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))["players"]:
            baseline_map[p["player_id"]] = p

    cache: dict[str, dict] = {}

    for metric, first_name, last_name, fifa_code in rows:
        full_name = f"{first_name} {last_name}"
        player_id = name_index.get(_norm(full_name))
        if not player_id:
            # Try last name only
            player_id = name_index.get(_norm(last_name))
        if not player_id:
            logger.debug("wellness_cache: no baseline match for %s (%s)", full_name, fifa_code)
            continue

        bl = baseline_map.get(player_id, {})
        bl_w  = bl.get("wellness",   {}) or {}
        bl_ph = bl.get("physiology", {}) or {}

        # Raw logged values (float-safe)
        def fv(val):
            return float(val) if val is not None else None

        hrv_logged   = fv(metric.hrv_ms)
        hr_logged    = fv(metric.resting_hr_bpm)
        fat_logged   = fv(metric.fatigue)
        sor_logged   = fv(metric.soreness)
        str_logged   = fv(metric.stress)
        slq_logged   = fv(metric.sleep_quality)
        slh_logged   = fv(metric.sleep_duration_h)
        acwr_logged  = fv(metric.acwr)

        # Baseline references
        hrv_bl  = bl_ph.get("hrv_baseline_ms")
        hr_bl   = bl_ph.get("resting_hr_bpm")
        fat_bl  = bl_w.get("fatigue_baseline")
        sor_bl  = bl_w.get("soreness_baseline")
        str_bl  = bl_w.get("stress_baseline")
        slq_bl  = bl_w.get("sleep_quality_baseline")
        slh_bl  = bl_w.get("sleep_duration_baseline_h")

        entry: dict = {
            "metric_date":    str(metric.metric_date),
            "days_ago":       (date.today() - metric.metric_date).days,
            # Raw values
            "hrv_ms":         hrv_logged,
            "resting_hr_bpm": hr_logged,
            "fatigue":        fat_logged,
            "soreness":       sor_logged,
            "stress":         str_logged,
            "sleep_quality":  slq_logged,
            "sleep_duration_h": slh_logged,
            "acwr":           acwr_logged,
            # Deviations from baseline (positive = worse than normal)
            "hrv_pct_drop":    round((hrv_bl - hrv_logged) / hrv_bl * 100, 1) if hrv_bl and hrv_logged else None,
            "hr_delta":        round(hr_logged - hr_bl, 1)  if hr_bl and hr_logged else None,
            "fatigue_delta":   round(fat_logged - fat_bl, 1) if fat_bl and fat_logged else None,
            "soreness_delta":  round(sor_logged - sor_bl, 1) if sor_bl and sor_logged else None,
            "stress_delta":    round(str_logged - str_bl, 1) if str_bl and str_logged else None,
            "slq_delta":       round(slq_logged - slq_bl, 1) if slq_bl and slq_logged else None,
            "slh_delta":       round(slh_logged - slh_bl, 1) if slh_bl and slh_logged else None,
        }

        # ── XGBoost ML risk score ──────────────────────────────────────────────
        # Skip when ACWR is absent — regressor and classifier disagree badly on
        # NaN inputs (NaN routes to the high-risk branch in XGBoost, producing
        # ~0.555 regressor score with "very_high" classifier output).
        if acwr_logged is None:
            logger.debug("wellness_cache: skipping ML for %s — no ACWR", player_id)
        else:
            try:
                from app.services.ml_inference import InjuryModel  # lazy import — avoids circular at module load

                metric_row = {
                    "metric_date":               str(metric.metric_date),
                    "session_type":              metric.session_type or "match_prep",
                    "rpe":                       float(metric.rpe or 7),
                    "session_duration_min":      float(metric.session_duration_min or 90),
                    "srpe":                      float(metric.srpe or 630),
                    "session_distance_km":       float(metric.session_distance_km or 10),
                    "high_intensity_distance_m": float(metric.high_intensity_distance_m or 1500),
                    "sprints_count":             float(metric.sprints_count or 25),
                    "accel_decel_count":         float(metric.accel_decel_count or 120),
                    "sleep_duration_h":          float(metric.sleep_duration_h or 7.5),
                    "sleep_quality":             float(metric.sleep_quality or 4),
                    "fatigue":                   float(metric.fatigue or 3),
                    "soreness":                  float(metric.soreness or 3),
                    "stress":                    float(metric.stress or 3),
                    "hrv_ms":                    float(metric.hrv_ms or hrv_bl or 65),
                    "resting_hr_bpm":            float(metric.resting_hr_bpm or 60),
                    "hydration_usg":             float(metric.hydration_usg or 1.010),
                    "acute_load_7d":             float(metric.acute_load_7d) if metric.acute_load_7d else None,
                    "chronic_load_28d":          float(metric.chronic_load_28d) if metric.chronic_load_28d else None,
                    "acwr":                      float(metric.acwr) if metric.acwr else None,
                }
                traits = bl.get("traits") or {}
                age = int(bl.get("age") or 25)
                bl_for_ml = {
                    "hrv_baseline_ms":      float(hrv_bl or 65),
                    "sprint_speed_max_kmh": float(bl_ph.get("sprint_speed_max_kmh") or 30),
                    "vo2_max_ml_kg_min":    float(bl_ph.get("vo2_max_ml_kg_min") or 55),
                    "injury_proneness":     traits.get("injury_proneness", "medium"),
                    "recovery_speed":       traits.get("recovery_speed", "medium"),
                    "age_category":         "young" if age < 24 else ("veteran" if age >= 32 else "prime"),
                }
                day_number = max(1, (metric.metric_date - _TOURNAMENT_EPOCH).days + 1)
                model = InjuryModel.get()
                ml_pred = model.predict(
                    metrics=[metric_row],
                    baseline=bl_for_ml,
                    day_number=day_number,
                    caps=int(bl.get("caps") or 0),
                    age=age,
                    position_detail=bl.get("position_detail") or bl.get("position") or "",
                )
                entry["ml_risk_score"]    = round(ml_pred["risk_score"], 4)
                entry["ml_risk_category"] = ml_pred["risk_category"]
                entry["ml_top_features"]  = ml_pred["top_features"]
            except Exception as exc:
                logger.debug("wellness_cache: ML inference skipped for %s: %s", player_id, exc)

        cache[player_id] = entry

    try:
        _CACHE_FILE.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("wellness_cache: wrote %d player entries", len(cache))
    except Exception as exc:
        logger.warning("wellness_cache: could not write cache: %s", exc)
