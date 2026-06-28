"""
generate_daily_metrics.py
==========================
Project : 2026 FIFA World Cup Group C — Injury Prediction Platform
Purpose : Generate synthetic daily_metrics for 104 players over a 90-day
          pre-tournament preparation block (2026-03-01 to 2026-05-29).

Training Periodization (Weeks 0-12)
------------------------------------
  Weeks  0-3  : Foundation   — low volume, adaptive base  (phase_mult 0.85)
  Weeks  4-7  : Build        — progressive overload        (phase_mult 1.05)
  Weeks  8-10 : Intensification — pre-tournament camp      (phase_mult 1.60)
  Weeks 11-12 : Taper        — competition readiness       (phase_mult 0.70)

Individual variation stacked on top:
  • Per-player fixed modifier based on injury_proneness
  • Per-player per-week random variation (±15 %)

This produces realistic ACWR spikes in weeks 8-10, pushing some players
into moderate / high / very_high risk zones for ML training.

Scientific Sources
------------------
Foster (2001)            : sRPE = RPE x duration_min (AU)
Hooper & Mackinnon (1995): 4-item wellness questionnaire, 1-7 Likert
Gabbett (2016)           : ACWR = acute(7d_sum) / chronic(28d_sum / 4)
Buchheit (2014)          : HRV post-match recovery timeline
Bradley (2009)           : GPS positional demands (EPL)
Dellal (2010)            : GPS La Liga / Serie A
Armstrong (1994)         : Urine specific gravity hydration benchmarks

Usage
-----
    python scripts/generate_daily_metrics.py

Output
------
    data/daily_metrics.json   — full dataset with metadata
    data/daily_metrics.csv    — flat table ready for DB loading / ML training
"""

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(2026)

# ─── PATHS ────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).resolve().parent
DATA_DIR     = SCRIPT_DIR.parent / "data"
PLAYERS_FILE = DATA_DIR / "players_baseline.json"
SCI_FILE     = SCRIPT_DIR.parent / "scientific_tables.json"
OUTPUT_JSON  = DATA_DIR / "daily_metrics.json"
OUTPUT_CSV   = DATA_DIR / "daily_metrics.csv"
GENERATOR_VERSION = "2.1.0"

# ─── SIMULATION PARAMETERS ────────────────────────────────────────────────────

START_DATE = date(2026, 3, 1)
N_DAYS     = 90    # 2026-03-01 to 2026-05-29

GPS_KEY_MAP: dict[str, str] = {
    "Goalkeeper":    "goalkeeper",
    "Center Back":   "center_back",
    "Full Back":     "fullback",
    "Defensive Mid": "defensive_midfielder",
    "Central Mid":   "central_midfielder",
    "Attacking Mid": "attacking_midfielder",
    "Winger":        "winger",
    "Striker":       "striker",
}

# 0=Monday ... 6=Sunday; None = complete rest
WEEKLY_SCHEDULE: dict[int, str | None] = {
    0: "recovery",
    1: "technical",
    2: "tactical",
    3: "physical",
    4: "match_prep",
    5: "match_simulation",
    6: None,
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Periodization: phase load multiplier applied to session RPE
PHASE_MULTIPLIERS: list[tuple[int, float]] = [
    (4,  0.85),   # weeks  0-3  Foundation
    (8,  1.05),   # weeks  4-7  Build
    (11, 1.60),   # weeks  8-10 Intensification
    (13, 0.80),   # weeks 11-12 Taper (0.70→0.80: ACWR lands ~0.75-0.85 not ~0.55)
]

# Individual player modifier based on injury_proneness
PRONENESS_MULT: dict[str, float] = {
    "high":   1.12,   # over-loaded / under-managed
    "medium": 1.00,
    "low":    0.90,   # carefully managed
}

SRPE_HIGH_LOAD = 300   # AU — trigger for consecutive high-load streak modulator
SRPE_VERY_HIGH = 500   # AU — trigger for non-match HRV suppression

# ACWR detraining thresholds (U-shape risk — Gabbett 2016 + detraining literature)
ACWR_DETRAINING_SEVERE = 0.50  # below = severe deconditioning risk
ACWR_DETRAINING_MOD    = 0.65  # below = moderate detraining risk

# Per-player position load modifier (sRPE volume tendency by role)
POSITION_LOAD_MOD: dict[str, float] = {
    "Goalkeeper":    0.78,
    "Center Back":   0.92,
    "Full Back":     1.02,
    "Defensive Mid": 1.00,
    "Central Mid":   1.10,
    "Attacking Mid": 1.05,
    "Winger":        1.12,
    "Striker":       0.97,
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def gauss_c(mean: float, std: float, lo: float, hi: float, decimals: int = 1) -> float:
    return round(clamp(random.gauss(mean, std), lo, hi), decimals)


# ─── DATA LOADING ─────────────────────────────────────────────────────────────


def load_inputs() -> tuple[list[dict], dict]:
    with open(PLAYERS_FILE, encoding="utf-8") as f:
        players = json.load(f)["players"]
    with open(SCI_FILE, encoding="utf-8") as f:
        sci = json.load(f)
    return players, sci


# ─── PERIODIZATION ────────────────────────────────────────────────────────────


def phase_mult(day_idx: int) -> float:
    week = day_idx // 7
    for cutoff, mult in PHASE_MULTIPLIERS:
        if week < cutoff:
            return mult
    return PHASE_MULTIPLIERS[-1][1]


def build_weekly_mults(n_weeks: int) -> list[float]:
    """Pre-generate a per-week random variation multiplier for one player."""
    mults = []
    for wk in range(n_weeks):
        if wk < 4:       # Foundation — tight variance
            mults.append(gauss_c(1.0, 0.08, 0.82, 1.18))
        elif wk < 8:     # Build — moderate variance
            mults.append(gauss_c(1.0, 0.12, 0.76, 1.24))
        elif wk < 11:    # Intensification — widest variance (some players overtrained)
            mults.append(gauss_c(1.0, 0.20, 0.65, 1.40))
        else:             # Taper
            mults.append(gauss_c(1.0, 0.07, 0.85, 1.15))
    return mults


# ─── ACWR ─────────────────────────────────────────────────────────────────────


def compute_acwr(srpe_history: list[int]) -> tuple[int, float | None, float | None]:
    """
    Gabbett 2016: ACWR = acute_7d / (chronic_28d_sum / 4).
    Returns (acute_7d, chronic_28d_sum, acwr).
    chronic and acwr are None until 28+ days of history exist.
    """
    n     = len(srpe_history)
    acute = sum(srpe_history[-7:]) if n >= 1 else 0
    if n < 28:
        return acute, None, None
    chronic_total  = float(sum(srpe_history[-28:]))
    chronic_weekly = chronic_total / 4.0
    if chronic_weekly == 0:
        return acute, None, None
    return acute, round(chronic_total, 1), round(acute / chronic_weekly, 3)


# ─── SESSION METRICS ──────────────────────────────────────────────────────────


def sample_session(
    session_type: str, sci: dict, load_mult: float
) -> tuple[float, int, int]:
    """
    Return (rpe, duration_min, srpe) drawn from Foster 2001 distributions,
    scaled by the composite load multiplier.
    RPE is scaled (intensity increases); duration is partially scaled (volume).
    """
    s = sci["session_types"][session_type]

    rpe_mean = clamp(s["rpe_mean"] * load_mult, 1.0, 10.0)
    rpe_max  = clamp(s["rpe_max"]  * load_mult, 1.0, 10.0)
    rpe      = gauss_c(rpe_mean, s["rpe_std"], s["rpe_min"], rpe_max)

    # Duration: only partially affected by load_mult (coaches control session length)
    dur_scale = 1.0 + (load_mult - 1.0) * 0.25
    dur_mean  = clamp(s["duration_min_mean"] * dur_scale, s["duration_min_min"], 120.0)
    dur       = int(gauss_c(dur_mean, s["duration_min_std"], s["duration_min_min"], 120.0, decimals=0))

    return rpe, dur, round(rpe * dur)


def sample_gps(
    position_detail: str, session_type: str, sci: dict, load_mult: float
) -> tuple[float, int, int, int]:
    """
    Return (dist_km, hi_dist_m, sprints, accels) scaled by position baseline,
    session type multiplier, and the composite load multiplier.
    """
    gps  = sci["gps_load_by_position"]
    pos  = gps[GPS_KEY_MAP[position_detail]]
    smul = gps["training_load_multipliers"][session_type]
    m    = smul * load_mult   # combined multiplier

    dist = gauss_c(pos["total_distance_km_mean"]         * m,
                   pos["total_distance_km_std"]          * m, 0.2, 15.0, decimals=2)
    hi   = int(gauss_c(pos["high_intensity_distance_m_mean"] * m,
                        pos["high_intensity_distance_m_std"]  * m, 0, 5000, decimals=0))
    spr  = int(gauss_c(pos["sprints_count_mean"]             * m,
                        pos["sprints_count_std"]              * m, 0, 120,  decimals=0))
    acc  = int(gauss_c(pos["accel_decel_count_mean"]         * m,
                        pos["accel_decel_count_std"]          * m, 0, 400,  decimals=0))
    return dist, hi, spr, acc


# ─── WELLNESS ─────────────────────────────────────────────────────────────────

_ZERO_MOD: dict = {
    "sleep_duration_h_delta": 0.0, "sleep_quality_delta": 0,
    "fatigue_delta": 0, "soreness_delta": 0, "stress_delta": 0,
}


def compute_wellness(
    player: dict,
    session_type: str | None,
    days_since_match: int,
    high_load_streak: int,
    sci: dict,
) -> tuple[float, int, int, int, int]:
    """
    Hooper 1995 questionnaire modulated by training context, post-match
    recovery phase, and cumulative fatigue streak.
    Returns (sleep_h, sleep_q, fatigue, soreness, stress).
    """
    b   = player["wellness"]
    mod = sci["wellness_modulators"]

    if session_type == "match_simulation":
        ctx = mod["match_day"]
    elif session_type is None:
        ctx = mod["rest_day"]
    else:
        ctx = mod["normal_training_day"]

    if days_since_match == 1:
        pm = mod["day_1_post_match"]
    elif days_since_match == 2:
        pm = mod["day_2_post_match"]
    elif days_since_match == 3:
        pm = mod["day_3_post_match"]
    else:
        pm = _ZERO_MOD

    streak_fat = mod["consecutive_high_load_streak"]["fatigue_delta"]  if high_load_streak >= 3 else 0
    streak_sor = mod["consecutive_high_load_streak"]["soreness_delta"] if high_load_streak >= 3 else 0

    sleep_h = gauss_c(
        b["sleep_duration_baseline_h"]
        + ctx["sleep_duration_h_delta"] + pm["sleep_duration_h_delta"],
        0.35, 4.0, 11.0, decimals=1,
    )
    sleep_q = int(clamp(round(
        b["sleep_quality_baseline"]
        + ctx["sleep_quality_delta"] + pm["sleep_quality_delta"]
        + gauss_c(0, 0.5, -1, 1, decimals=0)
    ), 1, 7))
    fatigue = int(clamp(round(
        b["fatigue_baseline"]
        + ctx["fatigue_delta"] + pm["fatigue_delta"]
        + streak_fat + gauss_c(0, 0.5, -1, 1, decimals=0)
    ), 1, 7))
    soreness = int(clamp(round(
        b["soreness_baseline"]
        + ctx["soreness_delta"] + pm["soreness_delta"]
        + streak_sor + gauss_c(0, 0.5, -1, 1, decimals=0)
    ), 1, 7))
    stress = int(clamp(round(
        b["stress_baseline"]
        + ctx["stress_delta"] + pm["stress_delta"]
        + gauss_c(0, 0.3, -0.5, 0.5, decimals=0)
    ), 1, 7))

    return sleep_h, sleep_q, fatigue, soreness, stress


# ─── HYDRATION ────────────────────────────────────────────────────────────────


def compute_hydration(prev_srpe: int) -> float:
    """
    Morning urine specific gravity reflects yesterday's training load.
    Armstrong 1994: <1.010 well-hydrated; 1.010-1.020 minimal dehydration.
    """
    if prev_srpe == 0:
        base = 1.007
    elif prev_srpe > 700:
        base = 1.022
    elif prev_srpe > 500:
        base = 1.018
    elif prev_srpe > 300:
        base = 1.014
    else:
        base = 1.010
    return round(gauss_c(base, 0.003, 1.001, 1.035, decimals=3), 3)


# ─── HRV / HR ─────────────────────────────────────────────────────────────────


def compute_hrv_hr(
    player: dict,
    days_since_match: int,
    prev_srpe: int,
    hydration_usg: float,
    sci: dict,
    acwr: float | None = None,
) -> tuple[float, int]:
    """
    Buchheit 2014 post-match recovery timeline.
    Morning values, measured before the day's session.
    Returns (hrv_ms, resting_hr_bpm).
    """
    rec      = sci["recovery_modulators"]
    tl       = rec["post_match_recovery_timeline"]
    base_hrv = player["physiology"]["hrv_baseline_ms"]
    base_hr  = player["physiology"]["resting_hr_bpm"]

    if days_since_match == 1:
        mult = tl["day_1"]["hrv_multiplier"];      hr_d = tl["day_1"]["resting_hr_delta_bpm"]
    elif days_since_match == 2:
        mult = tl["day_2"]["hrv_multiplier"];      hr_d = tl["day_2"]["resting_hr_delta_bpm"]
    elif days_since_match == 3:
        mult = tl["day_3"]["hrv_multiplier"];      hr_d = tl["day_3"]["resting_hr_delta_bpm"]
    else:
        mult = tl["day_4_plus"]["hrv_multiplier"]; hr_d = tl["day_4_plus"]["resting_hr_delta_bpm"]

    if days_since_match > 3 and prev_srpe > SRPE_VERY_HIGH:
        mult = rec["high_load_training_day"]["hrv_multiplier"]
        hr_d = rec["high_load_training_day"]["resting_hr_delta_bpm"]

    if hydration_usg > 1.025:
        mult -= rec["dehydration_penalty"]["hrv_multiplier_penalty"]
        hr_d += rec["dehydration_penalty"]["resting_hr_delta_bpm_penalty"]

    # Sustained high/low ACWR suppresses parasympathetic tone (cumulative fatigue)
    if acwr is not None:
        if acwr > 1.50:
            mult -= min(0.15, (acwr - 1.50) * 0.12)
        elif acwr > 1.30:
            mult -= 0.05
        elif acwr < ACWR_DETRAINING_MOD:
            mult -= 0.04   # detraining also reduces HRV adaptation

    hrv = round(gauss_c(base_hrv * mult, 2.5, 20.0, 150.0), 1)
    hr  = int(gauss_c(base_hr + hr_d, 2.0, 30.0, 100.0, decimals=0))
    return hrv, hr


# ─── RISK SCORE ───────────────────────────────────────────────────────────────


def compute_risk(
    acwr: float | None,
    fatigue: int,
    soreness: int,
    age: int = 27,
    injury_proneness: str = "medium",
    is_taper: bool = False,
    chronic_ratio: float | None = None,
) -> tuple[float | None, str]:
    """
    Taper-aware U-shaped ACWR risk function (Gabbett 2016 + detraining evidence).

    Low ACWR is interpreted differently depending on context:
      - Taper phase OR chronic base still healthy (ratio >= 0.90):
          low ACWR = programmed freshness  → base 0.07 (below sweet-spot floor)
      - Chronic base genuinely eroded (ratio < 0.80), not tapering:
          low ACWR = detraining            → graduated 0.18 / 0.30 / 0.45
      - Ambiguous (ratio 0.80-0.90, not taper): mild undertraining → 0.13

    Above 0.80 (overloading side) is unchanged from Gabbett 2016 U-curve.
    """
    if acwr is None:
        return None, "insufficient_data"

    # Taper phase is the only context that turns low ACWR into freshness.
    # All other low-ACWR cases apply the graduated detraining penalty.
    # chronic_ratio is available for future explainability but not used for branching.
    fresh_context = is_taper

    # Base risk
    if acwr < 0.80:
        if fresh_context:
            base = 0.07          # planned taper freshness — at/below sweet-spot floor
        else:
            # Detraining / undertraining U-curve (Gabbett 2016)
            if acwr < ACWR_DETRAINING_SEVERE:   # < 0.50
                base = 0.45
            elif acwr < ACWR_DETRAINING_MOD:    # < 0.65
                base = 0.30
            else:                                # 0.65–0.80
                base = 0.18
    elif acwr <= 1.30:
        base = 0.08              # sweet spot — lowest risk
    elif acwr <= 1.49:
        base = 0.38              # caution zone
    elif acwr <= 1.99:
        base = 0.63              # danger zone
    else:
        base = 0.85              # severe overreaching

    # Additive modifiers
    pen = 0.0
    pen += 0.07 if fatigue >= 5 else 0.0
    pen += 0.05 if soreness >= 5 else 0.0
    pen += 0.04 if age >= 32 else 0.0        # veteran tissue vulnerability
    pen += 0.03 if injury_proneness == "high" else 0.0

    # Freshness bonus: taper phase reduces risk below what ACWR alone implies
    freshness_bonus = -0.02 if is_taper else 0.0

    score = round(clamp(
        base + pen + freshness_bonus + gauss_c(0, 0.025, -0.06, 0.06),
        0.0, 1.0,
    ), 3)

    # Category thresholds
    if score >= 0.75:   cat = "very_high"
    elif score >= 0.55: cat = "high"
    elif score >= 0.30: cat = "moderate"
    else:               cat = "low"

    return score, cat


# ─── PRE-CAMP HISTORY ─────────────────────────────────────────────────────────


def build_precamp_history(player: dict, rng: random.Random) -> list[int]:
    """
    28-day sRPE history before the 90-day camp (end of club season).
    High-caps players arrive with high chronic load; injury-prone / low-caps
    players arrive undertrained.  This seeds heterogeneous starting ACWR values
    so the spread is visible from day 1 rather than only after week 4.
    """
    caps  = player["caps"]
    prone = player["traits"]["injury_proneness"]

    if prone == "high":
        base_weekly = clamp(rng.gauss(1100, 200), 600, 1800)   # rest/rehab period
    elif caps > 50:
        base_weekly = clamp(rng.gauss(2400, 250), 1600, 3200)  # UCL/league until end
    elif caps > 20:
        base_weekly = clamp(rng.gauss(1900, 250), 1200, 2800)
    else:
        base_weekly = clamp(rng.gauss(1400, 200), 800, 2200)   # limited club minutes

    history: list[int] = []
    for _ in range(4):
        for dow in range(7):
            if dow == 6:
                history.append(0)
            elif dow == 5:
                history.append(int(clamp(rng.gauss(base_weekly * 0.22, 50), 80, 600)))
            else:
                daily = clamp(rng.gauss(base_weekly / 5, base_weekly * 0.06), 0, base_weekly * 0.40)
                history.append(int(daily))
    return history[:28]


# ─── PLAYER SIMULATION ────────────────────────────────────────────────────────


def simulate_player(player: dict, sci: dict) -> list[dict]:
    # Per-player fixed modifier (injury_proneness determines how hard they are pushed)
    proneness_mod = PRONENESS_MULT[player["traits"]["injury_proneness"]]

    # Per-player deterministic load modifier: position + age + individual factor
    # Uses a player-specific RNG so the spread is reproducible without touching
    # the global seed (which drives the weekly variation below).
    _player_rng  = random.Random(abs(hash(player["player_id"])) % (2 ** 31))
    _age         = player["age"]
    _age_mod     = 0.88 if _age > 34 else (0.93 if _age > 31 else (0.93 if _age < 22 else 1.0))
    _pos_mod     = POSITION_LOAD_MOD.get(player["position_detail"], 1.0)
    _individual  = clamp(_player_rng.gauss(1.0, 0.12), 0.78, 1.22)
    base_load_mod = _pos_mod * _age_mod * _individual

    # Per-player per-week random variation (generated once, before the loop)
    n_weeks = (N_DAYS // 7) + 2
    weekly_var = build_weekly_mults(n_weeks)

    # Seed 28 days of pre-camp club-season load → enables ACWR from day 1
    srpe_history:     list[int]  = build_precamp_history(player, _player_rng)
    # Pre-camp 28d sum used as chronic baseline to distinguish taper from detraining
    _chronic_baseline_au: float  = float(sum(srpe_history)) if srpe_history else 1.0
    last_match_idx:   int        = -999
    high_load_streak: int        = 0
    prev_srpe:        int        = srpe_history[-1] if srpe_history else 0
    rows:             list[dict] = []

    for day_idx in range(N_DAYS):
        d            = START_DATE + timedelta(days=day_idx)
        dow          = d.weekday()
        session_type = WEEKLY_SCHEDULE[dow]
        is_rest      = session_type is None

        # Composite load multiplier: phase x individual x weekly variation
        week_num     = day_idx // 7
        total_mult   = phase_mult(day_idx) * proneness_mod * base_load_mod * weekly_var[week_num]

        # days_since_match: 0 = match morning, 1 = day after, ...
        days_since_match = day_idx - last_match_idx

        # ACWR from history before today
        acute_7d, chronic_28d, acwr = compute_acwr(srpe_history)

        # Wellness
        sleep_h, sleep_q, fatigue, soreness, stress = compute_wellness(
            player, session_type, days_since_match, high_load_streak, sci
        )

        # Hydration (morning measurement, reflects yesterday's load)
        hydration = compute_hydration(prev_srpe)

        # HRV / HR (morning measurement)
        hrv, hr = compute_hrv_hr(player, days_since_match, prev_srpe, hydration, sci, acwr=acwr)

        # Session metrics
        if is_rest:
            rpe = duration = srpe = None
            dist = hi_dist = sprints = accels = None
            today_srpe = 0
        else:
            rpe, duration, today_srpe = sample_session(session_type, sci, total_mult)
            dist, hi_dist, sprints, accels = sample_gps(
                player["position_detail"], session_type, sci, total_mult
            )
            srpe = today_srpe

        # Taper context: weeks 11-12 (day_idx 77-89)
        _is_taper = day_idx >= 77

        # Chronic ratio: current 28d load vs pre-camp baseline
        # >= 0.90 → healthy base (taper not eroded it yet); < 0.80 → genuine detraining
        _chronic_ratio: float | None = (
            round(chronic_28d / _chronic_baseline_au, 3)
            if chronic_28d is not None and _chronic_baseline_au > 0
            else None
        )

        # Risk
        risk_score, risk_cat = compute_risk(
            acwr, fatigue, soreness,
            age=player["age"],
            injury_proneness=player["traits"]["injury_proneness"],
            is_taper=_is_taper,
            chronic_ratio=_chronic_ratio,
        )

        rows.append({
            "player_id":                  player["player_id"],
            "name":                       player["name"],
            "team":                       player["team"],
            "team_code":                  player["team_code"],
            "position":                   player["position"],
            "position_detail":            player["position_detail"],
            "date":                       d.isoformat(),
            "day_number":                 day_idx + 1,
            "day_of_week":                DAY_NAMES[dow],
            "session_type":               session_type or "rest",
            # Wellness — Hooper & Mackinnon 1995
            "sleep_duration_h":           sleep_h,
            "sleep_quality":              sleep_q,
            "fatigue":                    fatigue,
            "soreness":                   soreness,
            "stress":                     stress,
            # GPS external load — Bradley 2009, Dellal 2010
            "session_distance_km":        dist,
            "high_intensity_distance_m":  hi_dist,
            "sprints_count":              sprints,
            "accel_decel_count":          accels,
            # Internal load — Foster 2001
            "rpe":                        rpe,
            "session_duration_min":       duration,
            "srpe":                       srpe,
            # Recovery — Buchheit 2014, Armstrong 1994
            "hrv_ms":                     hrv,
            "resting_hr_bpm":             hr,
            "hydration_usg":              hydration,
            # ACWR — Gabbett 2016
            "acute_load_7d":              acute_7d,
            "chronic_load_28d":           chronic_28d,
            "acwr":                       acwr,
            # Taper context flag (taper-aware risk branch)
            "is_taper":                   int(_is_taper),
            # ML target
            "injury_risk_score":          risk_score,
            "risk_category":              risk_cat,
        })

        # State update
        srpe_history.append(today_srpe)
        prev_srpe = today_srpe
        if session_type == "match_simulation":
            last_match_idx = day_idx
        high_load_streak = high_load_streak + 1 if today_srpe > SRPE_HIGH_LOAD else 0

    return rows


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main() -> None:
    players, sci = load_inputs()
    end_date = START_DATE + timedelta(days=N_DAYS - 1)

    all_rows: list[dict] = []
    for player in players:
        all_rows.extend(simulate_player(player, sci))

    # JSON output
    payload = {
        "metadata": {
            "generated_at":       date.today().isoformat(),
            "generator_version":  GENERATOR_VERSION,
            "random_seed":        2026,
            "start_date":         START_DATE.isoformat(),
            "end_date":           end_date.isoformat(),
            "n_days":             N_DAYS,
            "n_players":          len(players),
            "total_rows":         len(all_rows),
            "periodization": {
                "foundation_weeks":        "0-3  (phase_mult 0.85)",
                "build_weeks":             "4-7  (phase_mult 1.05)",
                "intensification_weeks":   "8-10 (phase_mult 1.60)",
                "taper_weeks":             "11-12 (phase_mult 0.80)",
            },
            "acwr_formula":       "acute(7d_sum) / (chronic_28d_sum / 4)",
            "scientific_sources": [
                "Foster 2001 - sRPE methodology",
                "Hooper & Mackinnon 1995 - wellness questionnaire",
                "Gabbett 2016 - ACWR injury risk zones",
                "Buchheit 2014 - HRV post-match recovery timeline",
                "Bradley 2009 - GPS positional demands (EPL)",
                "Dellal 2010 - GPS positional demands (La Liga / Serie A)",
                "Armstrong 1994 - urine specific gravity hydration benchmarks",
            ],
        },
        "records": all_rows,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # CSV output
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_rows[0].keys())
        writer.writeheader()
        writer.writerows(all_rows)

    # Verification
    risk_dist: dict[str, int] = {}
    for r in all_rows:
        risk_dist[r["risk_category"]] = risk_dist.get(r["risk_category"], 0) + 1

    acwr_vals = [r["acwr"]   for r in all_rows if r["acwr"]   is not None]
    hrv_vals  = [r["hrv_ms"] for r in all_rows]

    print(f"\n{'=' * 60}")
    print("  DAILY METRICS GENERATION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Total rows        : {len(all_rows):,}")
    print(f"Players           : {len(players)}")
    print(f"Days              : {N_DAYS}  ({START_DATE} to {end_date})")
    print(f"Rows with ACWR    : {len(acwr_vals):,} / {len(all_rows):,}")
    if acwr_vals:
        mean_acwr = sum(acwr_vals) / len(acwr_vals)
        print(f"ACWR range        : {min(acwr_vals):.3f} - {max(acwr_vals):.3f}  (mean {mean_acwr:.3f})")
    if hrv_vals:
        print(f"HRV range         : {min(hrv_vals):.1f} - {max(hrv_vals):.1f} ms")

    print("\nRisk category distribution:")
    for cat in ("low", "moderate", "high", "very_high", "insufficient_data"):
        cnt = risk_dist.get(cat, 0)
        pct = cnt / len(all_rows) * 100
        print(f"  {cat:<22}  {cnt:>5}  ({pct:.1f}%)")

    json_kb = OUTPUT_JSON.stat().st_size / 1024
    csv_kb  = OUTPUT_CSV.stat().st_size  / 1024
    print(f"\nOutput JSON       : {OUTPUT_JSON}  ({json_kb:.0f} KB)")
    print(f"Output CSV        : {OUTPUT_CSV}  ({csv_kb:.0f} KB)")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
