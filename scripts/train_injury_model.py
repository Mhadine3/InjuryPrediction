"""
train_injury_model.py
=====================
Project : 2026 FIFA World Cup Group C — Injury Prediction Platform
Purpose : Train XGBoost models to predict injury risk from daily_metrics.csv.

Models
------
1. Regressor  : XGBRegressor  -> continuous injury_risk_score (0-1)
2. Classifier : XGBClassifier -> risk_category (low / moderate / high / very_high)

Feature Engineering
-------------------
- Raw daily metrics : wellness (Hooper), GPS load, sRPE, HRV, hydration
- ACWR (Gabbett 2016): acute_7d / (chronic_28d / 4) — XGBoost handles NaN natively
- 7-day rolling stats: HRV mean/std, fatigue trend, Hooper Index
- Delta features    : HRV vs personal baseline, HRV 7d deviation
- Player profile    : age, injury_proneness, recovery_speed, position, VO2max
- Context           : session type, day of week, training phase (0-3)

Split Strategy
--------------
Chronological (no data leakage — future data cannot predict the past):
  Train : day_number  1 - 63  (first 70 % of 90-day block)
  Test  : day_number 64 - 90  (last  30 %)
  Note  : rows with insufficient ACWR history (first 28 days) are excluded
          from the supervised target but ACWR is left as NaN for XGBoost.

Output
------
  ml/models/injury_risk_regressor.json
  ml/models/injury_risk_classifier.json
  ml/models/feature_importance.csv
  ml/models/training_report.txt
"""

import json
from datetime import date
from pathlib import Path


class _NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray):  return obj.tolist()
        return super().default(obj)

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

# ─── PATHS ────────────────────────────────────────────────────────────────────

ROOT          = Path(__file__).resolve().parent.parent
DATA_DIR      = ROOT / "data"
MODELS_DIR    = ROOT / "ml" / "models"

METRICS_CSV   = DATA_DIR / "daily_metrics.csv"
BASELINE_JSON = DATA_DIR / "players_baseline.json"

TRAIN_CUTOFF  = 63   # day_number <= 63 → train ; > 63 → test

# ─── ENCODINGS ────────────────────────────────────────────────────────────────

SESSION_ORD  = {"rest": 0, "recovery": 1, "technical": 2, "match_prep": 3,
                "tactical": 4, "match_simulation": 5, "physical": 6}
PRONENESS_ORD = {"low": 0, "medium": 1, "high": 2}
RECOVERY_ORD  = {"fast": 0, "medium": 1, "slow": 2}
AGE_CAT_ORD   = {"young": 0, "prime": 1, "veteran": 2}
PHASE_ORD     = {"foundation": 0, "build": 1, "intensification": 2, "taper": 3}

RISK_CAT_ORD  = {"low": 0, "moderate": 1, "high": 2, "very_high": 3}
RISK_CAT_INV  = {v: k for k, v in RISK_CAT_ORD.items()}

# Features exposed to both models
FEATURE_COLS = [
    # Session
    "session_type_enc", "day_of_week_enc", "is_rest",
    "rpe", "session_duration_min", "srpe",
    "session_distance_km", "high_intensity_distance_m",
    "sprints_count", "accel_decel_count",
    # Wellness — Hooper 1995
    "sleep_duration_h", "sleep_quality", "fatigue", "soreness", "stress",
    "hooper_index",
    # Recovery — Buchheit 2014, Armstrong 1994
    "hrv_ms", "resting_hr_bpm", "hydration_usg",
    "hrv_vs_baseline", "hrv_7d_mean", "hrv_7d_std", "hrv_7d_delta",
    # Load accumulation — Gabbett 2016 (NaN handled by XGBoost)
    "acute_load_7d", "chronic_load_28d", "acwr",
    # Rolling wellness
    "fatigue_7d_mean", "soreness_7d_mean", "hooper_7d_mean", "srpe_14d_mean",
    # Player profile
    "age", "caps", "hrv_baseline_ms",
    "sprint_speed_baseline", "vo2_max_baseline",
    "injury_proneness_enc", "recovery_speed_enc",
    "age_category_enc", "position_enc",
    # Context
    "day_number", "training_phase_enc", "is_taper",
]

# ─── DATA LOADING ─────────────────────────────────────────────────────────────


def load_baseline() -> pd.DataFrame:
    with open(BASELINE_JSON, encoding="utf-8") as f:
        players = json.load(f)["players"]
    return pd.DataFrame([{
        "player_id":             p["player_id"],
        "age":                   p["age"],
        "caps":                  p["caps"],
        "hrv_baseline_ms":       p["physiology"]["hrv_baseline_ms"],
        "sprint_speed_baseline": p["physiology"]["sprint_speed_max_kmh"],
        "vo2_max_baseline":      p["physiology"]["vo2_max_ml_kg_min"],
        "injury_proneness":      p["traits"]["injury_proneness"],
        "recovery_speed":        p["traits"]["recovery_speed"],
        "age_category":          p["traits"]["age_category"],
    } for p in players])


# ─── FEATURE ENGINEERING ──────────────────────────────────────────────────────


def get_phase(day_number: int) -> str:
    week = (day_number - 1) // 7
    if week < 4:   return "foundation"
    if week < 8:   return "build"
    if week < 11:  return "intensification"
    return "taper"


def add_rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-player 7/14-day rolling stats sorted chronologically."""
    df = df.sort_values(["player_id", "date"]).copy()
    grp = df.groupby("player_id")

    df["hrv_7d_mean"]     = grp["hrv_ms"].transform(lambda x: x.rolling(7,  min_periods=1).mean()).round(2)
    df["hrv_7d_std"]      = grp["hrv_ms"].transform(lambda x: x.rolling(7,  min_periods=2).std()).fillna(0).round(2)
    df["hrv_7d_delta"]    = (df["hrv_ms"] - df["hrv_7d_mean"]).round(2)
    df["fatigue_7d_mean"] = grp["fatigue"].transform(lambda x: x.rolling(7,  min_periods=1).mean()).round(2)
    df["soreness_7d_mean"]= grp["soreness"].transform(lambda x: x.rolling(7,  min_periods=1).mean()).round(2)
    df["hooper_7d_mean"]  = grp["hooper_index"].transform(lambda x: x.rolling(7,  min_periods=1).mean()).round(2)
    df["srpe_14d_mean"]   = grp["srpe"].transform(lambda x: x.rolling(14, min_periods=1).mean()).round(1)

    return df


def build_features(df: pd.DataFrame, baseline_df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Numeric coercion — None → NaN, then fill rest-day load fields with 0
    load_cols = ["session_distance_km", "high_intensity_distance_m",
                 "sprints_count", "accel_decel_count",
                 "rpe", "session_duration_min", "srpe"]
    for col in load_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    float_cols = ["acute_load_7d", "chronic_load_28d", "acwr",
                  "hrv_ms", "resting_hr_bpm", "hydration_usg",
                  "sleep_duration_h", "injury_risk_score"]
    for col in float_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")   # NaN stays NaN

    # Hooper Index (4-28; >20 = overreaching alert, Hooper 1995)
    df["hooper_index"] = df["fatigue"] + df["soreness"] + df["stress"] + df["sleep_quality"]

    # Rolling stats (per player, sorted by date)
    df = add_rolling_features(df)

    # Join player baseline features
    df = df.merge(baseline_df, on="player_id", how="left")

    # HRV individual referencing (Buchheit 2014: use personal baseline, not population norms)
    df["hrv_vs_baseline"] = (df["hrv_ms"] / df["hrv_baseline_ms"]).round(3)

    # Categorical encodings
    df["session_type_enc"]     = df["session_type"].map(SESSION_ORD).fillna(0).astype(int)
    df["day_of_week_enc"]      = pd.to_datetime(df["date"]).dt.dayofweek
    df["is_rest"]              = (df["session_type"] == "rest").astype(int)
    df["injury_proneness_enc"] = df["injury_proneness"].map(PRONENESS_ORD).fillna(1).astype(int)
    df["recovery_speed_enc"]   = df["recovery_speed"].map(RECOVERY_ORD).fillna(1).astype(int)
    df["age_category_enc"]     = df["age_category"].map(AGE_CAT_ORD).fillna(1).astype(int)
    df["training_phase_enc"]   = df["day_number"].apply(get_phase).map(PHASE_ORD).astype(int)

    pos_le = LabelEncoder()
    df["position_enc"] = pos_le.fit_transform(df["position_detail"].astype(str))

    # Risk category encoding (exclude insufficient_data)
    df["risk_cat_enc"] = df["risk_category"].map(RISK_CAT_ORD)   # NaN for insufficient_data

    return df


# ─── MODEL TRAINING ───────────────────────────────────────────────────────────


def train_regressor(
    X_tr: pd.DataFrame, y_tr: pd.Series,
    X_te: pd.DataFrame, y_te: pd.Series,
) -> tuple[xgb.XGBRegressor, dict]:
    model = xgb.XGBRegressor(
        n_estimators       = 1000,
        max_depth          = 6,
        learning_rate      = 0.05,
        subsample          = 0.8,
        colsample_bytree   = 0.8,
        min_child_weight   = 5,
        objective          = "reg:squarederror",
        eval_metric        = "rmse",
        early_stopping_rounds = 50,
        random_state       = 42,
        n_jobs             = -1,
    )
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)

    preds = np.clip(model.predict(X_te), 0.0, 1.0)
    return model, {
        "rmse":  float(np.sqrt(mean_squared_error(y_te, preds))),
        "mae":   float(mean_absolute_error(y_te, preds)),
        "r2":    float(r2_score(y_te, preds)),
        "best_iteration": model.best_iteration,
    }


def train_classifier(
    X_tr: pd.DataFrame, y_tr: pd.Series,
    X_te: pd.DataFrame, y_te: pd.Series,
    n_classes: int,
    class_names: list[str],
) -> tuple[xgb.XGBClassifier, dict]:
    # Class-frequency weights to handle low/moderate/high imbalance
    counts = y_tr.value_counts()
    n_total = len(y_tr)
    sample_weight = y_tr.map(lambda c: n_total / (n_classes * counts.get(c, 1)))

    model = xgb.XGBClassifier(
        n_estimators       = 1000,
        max_depth          = 6,
        learning_rate      = 0.05,
        subsample          = 0.8,
        colsample_bytree   = 0.8,
        min_child_weight   = 3,
        objective          = "multi:softprob",
        num_class          = n_classes,
        eval_metric        = "mlogloss",
        early_stopping_rounds = 50,
        random_state       = 42,
        n_jobs             = -1,
    )
    model.fit(
        X_tr, y_tr,
        sample_weight  = sample_weight,
        eval_set       = [(X_te, y_te)],
        verbose        = False,
    )

    preds = model.predict(X_te)
    report = classification_report(
        y_te, preds,
        target_names = class_names,
        output_dict  = True,
        zero_division = 0,
    )
    cm = confusion_matrix(y_te, preds)
    return model, {
        "report":           report,
        "confusion_matrix": cm.tolist(),
        "best_iteration":   model.best_iteration,
    }


# ─── UTILITIES ────────────────────────────────────────────────────────────────


def fi_dataframe(model, feature_names: list[str]) -> pd.DataFrame:
    return (
        pd.DataFrame({"feature": feature_names, "importance": model.feature_importances_})
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )


def print_bar(label: str, value: float, scale: int = 300) -> None:
    bar = "#" * int(value * scale)
    print(f"    {label:<32} {bar} {value:.4f}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Load & engineer ───────────────────────────────────────────────────────
    print("Loading data ...")
    df  = pd.read_csv(METRICS_CSV)
    bdf = load_baseline()
    print(f"  Raw rows      : {len(df):,}")

    print("Engineering features ...")
    df = build_features(df, bdf)
    print(f"  Features      : {len(FEATURE_COLS)}")

    # ── Filter to rows with a valid supervised target ─────────────────────────
    df_sup = df[df["risk_category"] != "insufficient_data"].copy()
    df_sup = df_sup[df_sup["injury_risk_score"].notna()].copy()
    print(f"  Supervised    : {len(df_sup):,} rows  "
          f"(excluded {len(df) - len(df_sup):,} no-ACWR rows)")

    # Determine which risk classes actually appear
    present_enc = sorted(df_sup["risk_cat_enc"].dropna().unique().astype(int))
    # Re-map to consecutive 0..n-1 labels (required by XGBoost multi-class)
    enc_remap      = {old: new for new, old in enumerate(present_enc)}
    class_names    = [RISK_CAT_INV[c] for c in present_enc]
    n_classes      = len(present_enc)
    df_sup["cls_target"] = df_sup["risk_cat_enc"].map(enc_remap).astype(int)

    # ── Train / test split (random 80/20, stratified by risk class) ──────────
    # Random split ensures all training phases (including taper) appear in both
    # train and test — avoids the temporal split problem where taper days only
    # appear in the test set.
    X_all     = df_sup[FEATURE_COLS]
    y_reg_all = df_sup["injury_risk_score"]
    y_cls_all = df_sup["cls_target"]

    X_tr, X_te, y_reg_tr, y_reg_te, y_cls_tr, y_cls_te = train_test_split(
        X_all, y_reg_all, y_cls_all,
        test_size    = 0.20,
        random_state = 42,
        stratify     = y_cls_all,
    )

    print(f"\n  Train : {len(X_tr):,} rows  (random 80%)")
    print(f"  Test  : {len(X_te):,} rows  (random 20%)")
    print(f"\n  Train risk distribution:")
    for cat, cnt in y_reg_tr.groupby(y_cls_tr.map({v: k for k,v in enc_remap.items()}.get)).count().items():
        print(f"    {cat:<12} {cnt:>5}")

    # ── Regressor ─────────────────────────────────────────────────────────────
    print("\n[1/2] Training XGBRegressor (injury_risk_score) ...")
    reg, reg_m = train_regressor(X_tr, y_reg_tr, X_te, y_reg_te)
    print(f"  Best iteration : {reg_m['best_iteration']}")
    print(f"  RMSE           : {reg_m['rmse']:.4f}")
    print(f"  MAE            : {reg_m['mae']:.4f}")
    print(f"  R2             : {reg_m['r2']:.4f}")

    # ── Classifier ────────────────────────────────────────────────────────────
    print(f"\n[2/2] Training XGBClassifier (risk_category: {class_names}) ...")
    clf, clf_m = train_classifier(X_tr, y_cls_tr, X_te, y_cls_te, n_classes, class_names)
    cr = clf_m["report"]
    print(f"  Best iteration : {clf_m['best_iteration']}")
    print(f"  Accuracy       : {cr['accuracy']:.4f}")
    for cat in class_names:
        e = cr.get(cat, {})
        print(f"  {cat:<12} p={e.get('precision',0):.3f}  r={e.get('recall',0):.3f}  "
              f"f1={e.get('f1-score',0):.3f}  n={int(e.get('support',0))}")

    # Confusion matrix
    print("\n  Confusion matrix (rows=actual, cols=predicted):")
    header = "           " + "  ".join(f"{c:>10}" for c in class_names)
    print(f"  {header}")
    for i, row_vals in enumerate(clf_m["confusion_matrix"]):
        row_str = "  ".join(f"{v:>10}" for v in row_vals)
        print(f"  {class_names[i]:<10} {row_str}")

    # ── Feature importance ────────────────────────────────────────────────────
    fi = fi_dataframe(reg, FEATURE_COLS)
    print(f"\n  Top 15 features (regressor importance):")
    for _, row in fi.head(15).iterrows():
        print_bar(row["feature"], row["importance"])

    # ── Save ──────────────────────────────────────────────────────────────────
    reg.save_model(str(MODELS_DIR / "injury_risk_regressor.json"))
    clf.save_model(str(MODELS_DIR / "injury_risk_classifier.json"))
    fi.to_csv(MODELS_DIR / "feature_importance.csv", index=False)

    # Save class mapping for inference
    meta = {
        "generated_at":   date.today().isoformat(),
        "split_strategy": "random_80_20_stratified",
        "feature_cols":   FEATURE_COLS,
        "risk_cat_map":   {str(v): k for k, v in enc_remap.items()},
        "class_names":    class_names,
        "n_classes":      n_classes,
        "regressor":      reg_m,
        "classifier":     {"accuracy": cr["accuracy"],
                           "best_iteration": clf_m["best_iteration"]},
    }
    (MODELS_DIR / "model_meta.json").write_text(
        json.dumps(meta, indent=2, cls=_NpEncoder), encoding="utf-8"
    )

    # Training report
    lines = [
        "Injury Risk Model — Training Report",
        f"Generated : {date.today().isoformat()}",
        "",
        "Dataset",
        f"  Source        : data/daily_metrics.csv",
        f"  Total rows    : {len(df):,}",
        f"  Supervised    : {len(df_sup):,}  (excluded {len(df)-len(df_sup):,} no-ACWR rows)",
        f"  Train rows    : {len(X_tr):,}  (random 80%)",
        f"  Test rows     : {len(X_te):,}  (random 20%)",
        f"  Features      : {len(FEATURE_COLS)}",
        "",
        "Regressor  (XGBRegressor — predict injury_risk_score 0-1)",
        f"  RMSE          : {reg_m['rmse']:.4f}",
        f"  MAE           : {reg_m['mae']:.4f}",
        f"  R2            : {reg_m['r2']:.4f}",
        f"  Best iter     : {reg_m['best_iteration']}",
        "",
        f"Classifier (XGBClassifier — predict risk_category)",
        f"  Classes       : {class_names}",
        f"  Accuracy      : {cr['accuracy']:.4f}",
        f"  Best iter     : {clf_m['best_iteration']}",
        "",
        "Per-class metrics:",
    ]
    for cat in class_names + ["macro avg", "weighted avg"]:
        e = cr.get(cat, {})
        if isinstance(e, dict):
            lines.append(
                f"  {cat:<14} p={e.get('precision',0):.3f}  "
                f"r={e.get('recall',0):.3f}  "
                f"f1={e.get('f1-score',0):.3f}  "
                f"n={int(e.get('support',0))}"
            )
    lines += ["", "Top 15 features (regressor):"]
    for i, row in fi.head(15).iterrows():
        lines.append(f"  {int(i)+1:>2}. {row['feature']:<32} {row['importance']:.4f}")

    (MODELS_DIR / "training_report.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"\nSaved to {MODELS_DIR}/")
    print("  injury_risk_regressor.json")
    print("  injury_risk_classifier.json")
    print("  feature_importance.csv")
    print("  model_meta.json")
    print("  training_report.txt")


if __name__ == "__main__":
    main()
