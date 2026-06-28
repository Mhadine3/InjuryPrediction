"""
ML inference wrapper.

Loads both XGBoost models once at startup (singleton pattern).
Accepts a list of DailyMetric ORM rows (≥28 days for a player) and
returns a prediction dict with risk_score, risk_category, and top_features.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from app.config import settings

# ── Encoding maps (must match train_injury_model.py) ──────────────────────────

SESSION_ORD   = {"rest": 0, "recovery": 1, "technical": 2, "match_prep": 3,
                 "tactical": 4, "match_simulation": 5, "physical": 6}
PRONENESS_ORD = {"low": 0, "medium": 1, "high": 2}
RECOVERY_ORD  = {"fast": 0, "medium": 1, "slow": 2}
AGE_CAT_ORD   = {"young": 0, "prime": 1, "veteran": 2}
PHASE_ORD     = {"foundation": 0, "build": 1, "intensification": 2, "taper": 3}

FEATURE_COLS = [
    "session_type_enc", "day_of_week_enc", "is_rest",
    "rpe", "session_duration_min", "srpe",
    "session_distance_km", "high_intensity_distance_m",
    "sprints_count", "accel_decel_count",
    "sleep_duration_h", "sleep_quality", "fatigue", "soreness", "stress",
    "hooper_index",
    "hrv_ms", "resting_hr_bpm", "hydration_usg",
    "hrv_vs_baseline", "hrv_7d_mean", "hrv_7d_std", "hrv_7d_delta",
    "acute_load_7d", "chronic_load_28d", "acwr",
    "fatigue_7d_mean", "soreness_7d_mean", "hooper_7d_mean", "srpe_14d_mean",
    "age", "caps", "hrv_baseline_ms",
    "sprint_speed_baseline", "vo2_max_baseline",
    "injury_proneness_enc", "recovery_speed_enc",
    "age_category_enc", "position_enc",
    "day_number", "training_phase_enc", "is_taper",
]


def _get_phase(day_number: int) -> str:
    week = (day_number - 1) // 7
    if week < 4:   return "foundation"
    if week < 8:   return "build"
    if week < 11:  return "intensification"
    return "taper"


class InjuryModel:
    """Singleton that owns both XGBoost models and the class mapping."""

    _instance: "InjuryModel | None" = None

    def __init__(self) -> None:
        models_dir = settings.ML_MODELS_DIR
        self.reg = xgb.XGBRegressor()
        self.reg.load_model(str(models_dir / "injury_risk_regressor.json"))
        self.clf = xgb.XGBClassifier()
        self.clf.load_model(str(models_dir / "injury_risk_classifier.json"))
        meta = json.loads((models_dir / "model_meta.json").read_text(encoding="utf-8"))
        # risk_cat_map: {"0": "low", "1": "moderate", ...}
        self.class_names: list[str] = meta["class_names"]
        self.model_version: str = meta.get("generated_at", "unknown")

    @classmethod
    def get(cls) -> "InjuryModel":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Feature engineering ────────────────────────────────────────────────────

    def _build_feature_row(
        self,
        metrics: list[dict],       # chronological rows for one player, last = today
        baseline: dict,            # player baseline profile dict
        day_number: int,           # 1-90
        caps: int,
        age: int,
        position_detail: str,
    ) -> pd.DataFrame:
        df = pd.DataFrame(metrics).sort_values("metric_date")

        # Rolling stats
        df["hrv_7d_mean"]     = df["hrv_ms"].rolling(7,  min_periods=1).mean()
        df["hrv_7d_std"]      = df["hrv_ms"].rolling(7,  min_periods=2).std().fillna(0)
        df["hrv_7d_delta"]    = df["hrv_ms"] - df["hrv_7d_mean"]
        df["fatigue_7d_mean"] = df["fatigue"].rolling(7,  min_periods=1).mean()
        df["soreness_7d_mean"]= df["soreness"].rolling(7, min_periods=1).mean()
        df["hooper_index"]    = df["fatigue"] + df["soreness"] + df["stress"] + df["sleep_quality"]
        df["hooper_7d_mean"]  = df["hooper_index"].rolling(7, min_periods=1).mean()
        df["srpe_14d_mean"]   = df["srpe"].rolling(14, min_periods=1).mean()

        row = df.iloc[-1].copy()

        hrv_baseline = float(baseline.get("hrv_baseline_ms", 65))

        feature = {
            "session_type_enc":     SESSION_ORD.get(str(row.get("session_type", "rest")), 0),
            "day_of_week_enc":      pd.to_datetime(row["metric_date"]).dayofweek,
            "is_rest":              int(str(row.get("session_type", "")) == "rest"),
            "rpe":                  float(row.get("rpe") or 0),
            "session_duration_min": float(row.get("session_duration_min") or 0),
            "srpe":                 float(row.get("srpe") or 0),
            "session_distance_km":  float(row.get("session_distance_km") or 0),
            "high_intensity_distance_m": float(row.get("high_intensity_distance_m") or 0),
            "sprints_count":        float(row.get("sprints_count") or 0),
            "accel_decel_count":    float(row.get("accel_decel_count") or 0),
            "sleep_duration_h":     float(row.get("sleep_duration_h") or 7),
            "sleep_quality":        float(row.get("sleep_quality") or 4),
            "fatigue":              float(row.get("fatigue") or 3),
            "soreness":             float(row.get("soreness") or 3),
            "stress":               float(row.get("stress") or 3),
            "hooper_index":         float(row.get("hooper_index") or 12),
            "hrv_ms":               float(row.get("hrv_ms") or hrv_baseline),
            "resting_hr_bpm":       float(row.get("resting_hr_bpm") or 60),
            "hydration_usg":        float(row.get("hydration_usg") or 1.010),
            "hrv_vs_baseline":      float(row.get("hrv_ms") or hrv_baseline) / hrv_baseline,
            "hrv_7d_mean":          float(row["hrv_7d_mean"]),
            "hrv_7d_std":           float(row["hrv_7d_std"]),
            "hrv_7d_delta":         float(row["hrv_7d_delta"]),
            "acute_load_7d":        float(row.get("acute_load_7d") or 0) or np.nan,
            "chronic_load_28d":     float(row.get("chronic_load_28d") or 0) or np.nan,
            "acwr":                 float(row.get("acwr") or 0) or np.nan,
            "fatigue_7d_mean":      float(row["fatigue_7d_mean"]),
            "soreness_7d_mean":     float(row["soreness_7d_mean"]),
            "hooper_7d_mean":       float(row["hooper_7d_mean"]),
            "srpe_14d_mean":        float(row["srpe_14d_mean"]),
            "age":                  age,
            "caps":                 caps,
            "hrv_baseline_ms":      hrv_baseline,
            "sprint_speed_baseline": float(baseline.get("sprint_speed_max_kmh", 30)),
            "vo2_max_baseline":     float(baseline.get("vo2_max_ml_kg_min", 55)),
            "injury_proneness_enc": PRONENESS_ORD.get(baseline.get("injury_proneness", "medium"), 1),
            "recovery_speed_enc":   RECOVERY_ORD.get(baseline.get("recovery_speed", "medium"), 1),
            "age_category_enc":     AGE_CAT_ORD.get(baseline.get("age_category", "prime"), 1),
            "position_enc":         hash(position_detail) % 10,  # stable ordinal for inference
            "day_number":           day_number,
            "training_phase_enc":   PHASE_ORD.get(_get_phase(day_number), 0),
            "is_taper":             int(_get_phase(day_number) == "taper"),
        }

        return pd.DataFrame([feature])[FEATURE_COLS]

    # ── Predict ────────────────────────────────────────────────────────────────

    def predict(
        self,
        metrics: list[dict],
        baseline: dict,
        day_number: int,
        caps: int,
        age: int,
        position_detail: str,
    ) -> dict:
        """Return risk_score, risk_category, top_features dict."""
        X = self._build_feature_row(metrics, baseline, day_number, caps, age, position_detail)

        risk_score = float(self.reg.predict(X)[0])
        risk_score = max(0.0, min(1.0, risk_score))

        proba = self.clf.predict_proba(X)[0]
        cls_idx = int(np.argmax(proba))
        risk_category = self.class_names[cls_idx] if cls_idx < len(self.class_names) else "low"
        confidence = {
            name: round(float(p), 4)
            for name, p in zip(self.class_names, proba)
        }

        # SHAP-lite: use booster's feature importance as proxy for top contributors
        scores = self.reg.get_booster().get_fscore()
        top_features = dict(
            sorted(
                {f: scores.get(f, 0) for f in FEATURE_COLS}.items(),
                key=lambda x: -x[1],
            )[:5]
        )

        return {
            "risk_score": risk_score,
            "risk_category": risk_category,
            "confidence": confidence,
            "top_features": top_features,
            "model_version": self.model_version,
        }
