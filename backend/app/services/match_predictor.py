"""
Match prediction service.

Pre-match:   baseline expected totals from season per-90 rates (Dixon-Coles-lite).
Live next-15: rolling Poisson with game-state multipliers.
             Only goals and red_cards are used as live inputs (confirmed available).
             Shots / corners / fouls multipliers are predicted from game-state only.
"""

import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

# ── Per-90 baselines (2024-2026 qualifiers + friendlies) ─────────────────────
# Source: FIFA/UEFA public stats aggregated to per-90-minute averages.

TEAM_BASELINES: dict[str, dict[str, float]] = {
    "BRA": {
        "goals_for_per90":     2.05,
        "shots_per90":         14.8,
        "corners_per90":        6.4,
        "fouls_per90":         10.2,
        "goals_against_per90":  0.75,
    },
    "MAR": {
        "goals_for_per90":     1.55,
        "shots_per90":         11.2,
        "corners_per90":        5.2,
        "fouls_per90":         13.8,
        "goals_against_per90":  0.90,
    },
    "HAI": {
        "goals_for_per90":     0.90,
        "shots_per90":          7.5,
        "corners_per90":        3.8,
        "fouls_per90":         16.2,
        "goals_against_per90":  2.10,
    },
    "SCO": {
        "goals_for_per90":     1.45,
        "shots_per90":         10.5,
        "corners_per90":        5.8,
        "fouls_per90":         12.5,
        "goals_against_per90":  1.25,
    },
    # Group I — baselines from FIFA rankings + WC 2022/AFCON/EURO performance
    "FRA": {
        "goals_for_per90":     1.90,
        "shots_per90":         15.2,
        "corners_per90":        6.8,
        "fouls_per90":         11.0,
        "goals_against_per90":  0.80,
    },
    "SEN": {
        "goals_for_per90":     1.50,
        "shots_per90":         11.8,
        "corners_per90":        5.4,
        "fouls_per90":         13.0,
        "goals_against_per90":  1.00,
    },
    "IRQ": {
        "goals_for_per90":     1.25,
        "shots_per90":          9.5,
        "corners_per90":        4.5,
        "fouls_per90":         14.5,
        "goals_against_per90":  1.50,
    },
    "NOR": {
        "goals_for_per90":     1.80,
        "shots_per90":         13.5,
        "corners_per90":        6.0,
        "fouls_per90":         11.8,
        "goals_against_per90":  1.20,
    },
}

MATCH_MINUTES     = 90
BLOCK_MINUTES     = 15
PREDICTION_TARGETS = ["goals", "shots", "corners", "fouls"]

# Full WC 2026 schedule — loaded from data/wc_fixtures.json (all 104 matches incl.
# knockout) produced by build_full_wc.py. Falls back to the original 12 group games.
import json as _json
from pathlib import Path as _Path

_FIXTURES_FILE = _Path(__file__).resolve().parents[3] / "data" / "wc_fixtures.json"

_FALLBACK_SCHEDULE = [
    {"match_id": "2026-06-13_BRA_MAR", "home": "BRA", "away": "MAR", "date": "2026-06-13", "group": "C"},
    {"match_id": "2026-06-14_HAI_SCO", "home": "HAI", "away": "SCO", "date": "2026-06-14", "group": "C"},
    {"match_id": "2026-06-19_SCO_MAR", "home": "SCO", "away": "MAR", "date": "2026-06-19", "group": "C"},
    {"match_id": "2026-06-20_BRA_HAI", "home": "BRA", "away": "HAI", "date": "2026-06-20", "group": "C"},
    {"match_id": "2026-06-24_MAR_HAI", "home": "MAR", "away": "HAI", "date": "2026-06-24", "group": "C"},
    {"match_id": "2026-06-24_SCO_BRA", "home": "SCO", "away": "BRA", "date": "2026-06-24", "group": "C"},
    {"match_id": "2026-06-16_FRA_SEN", "home": "FRA", "away": "SEN", "date": "2026-06-16", "group": "I"},
    {"match_id": "2026-06-16_IRQ_NOR", "home": "IRQ", "away": "NOR", "date": "2026-06-16", "group": "I"},
    {"match_id": "2026-06-22_FRA_IRQ", "home": "FRA", "away": "IRQ", "date": "2026-06-22", "group": "I"},
    {"match_id": "2026-06-23_NOR_SEN", "home": "NOR", "away": "SEN", "date": "2026-06-23", "group": "I"},
    {"match_id": "2026-06-26_NOR_FRA", "home": "NOR", "away": "FRA", "date": "2026-06-26", "group": "I"},
    {"match_id": "2026-06-26_SEN_IRQ", "home": "SEN", "away": "IRQ", "date": "2026-06-26", "group": "I"},
]

def _load_schedule() -> list[dict]:
    if _FIXTURES_FILE.exists():
        try:
            fx = _json.loads(_FIXTURES_FILE.read_text(encoding="utf-8"))
            # Only matches with both teams decided are usable for scheduling.
            return [f for f in fx if f.get("home") and f.get("away")]
        except Exception:
            pass
    return _FALLBACK_SCHEDULE

SCHEDULED_MATCHES = _load_schedule()

# Football-data.org match IDs for the tracked matches
FOOTBALLDATA_MATCH_IDS: dict[str, int] = {
    "2026-06-13_BRA_MAR": 537339,
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class PreMatchPrediction:
    match_id:             str
    home_team:            str
    away_team:            str
    lineup_locked:        bool
    home_expected:        dict   # target -> expected total over 90 min
    away_expected:        dict
    home_lambda0:         dict   # target -> per-minute base rate
    away_lambda0:         dict
    available_targets:    list
    unavailable_targets:  list
    generated_at:         str
    note:                 str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Next15Prediction:
    match_id:             str
    home_team:            str
    away_team:            str
    current_minute:       int
    block_start:          int
    block_end:            int
    score:                dict
    home_goal_prob:       float   # P(>=1 goal in next 15) — always shown for goals
    away_goal_prob:       float
    home_targets:         dict    # target -> {expected, live_available}
    away_targets:         dict
    game_state:           dict    # multipliers used
    available_targets:    list
    unavailable_targets:  list
    generated_at:         str

    def to_dict(self) -> dict:
        return asdict(self)


# ── Pre-match model ───────────────────────────────────────────────────────────

def _expected_match_totals(home_tla: str, away_tla: str) -> tuple[dict, dict]:
    """
    Dixon-Coles-lite: blend team attack strength vs opponent defensive weakness.
    Goals use opponent's defensive baseline; other stats use team's own per-90.
    """
    hb = TEAM_BASELINES.get(home_tla, TEAM_BASELINES["SCO"])
    ab = TEAM_BASELINES.get(away_tla, TEAM_BASELINES["SCO"])

    home_exp = {
        "goals":   round((hb["goals_for_per90"] + ab["goals_against_per90"]) / 2, 3),
        "shots":   round(hb["shots_per90"], 3),
        "corners": round(hb["corners_per90"], 3),
        "fouls":   round(hb["fouls_per90"], 3),
    }
    away_exp = {
        "goals":   round((ab["goals_for_per90"] + hb["goals_against_per90"]) / 2, 3),
        "shots":   round(ab["shots_per90"], 3),
        "corners": round(ab["corners_per90"], 3),
        "fouls":   round(ab["fouls_per90"], 3),
    }
    return home_exp, away_exp


def make_pre_match_prediction(
    match_id: str,
    home_tla: str,
    away_tla: str,
    lineup_locked: bool,
    available_targets: list,
    unavailable_targets: list,
) -> PreMatchPrediction:
    home_exp, away_exp = _expected_match_totals(home_tla, away_tla)

    home_lambda0 = {t: round(home_exp[t] / MATCH_MINUTES, 6) for t in PREDICTION_TARGETS}
    away_lambda0 = {t: round(away_exp[t] / MATCH_MINUTES, 6) for t in PREDICTION_TARGETS}

    note = ("Pre-match baseline from season per-90 averages. "
            "Lineup not yet confirmed — prediction may shift once starters are known."
            if not lineup_locked else
            "Lineup confirmed — prediction locked to confirmed starters.")

    return PreMatchPrediction(
        match_id             = match_id,
        home_team            = home_tla,
        away_team            = away_tla,
        lineup_locked        = lineup_locked,
        home_expected        = home_exp,
        away_expected        = away_exp,
        home_lambda0         = home_lambda0,
        away_lambda0         = away_lambda0,
        available_targets    = available_targets,
        unavailable_targets  = unavailable_targets,
        generated_at         = datetime.now(timezone.utc).isoformat(),
        note                 = note,
    )


# ── Game-state multipliers ────────────────────────────────────────────────────

def _score_mult(score_diff: int, is_home: bool) -> float:
    """
    Leading team defends (reduces rate); trailing team attacks more.
    score_diff = home_score - away_score
    """
    diff = score_diff if is_home else -score_diff
    if diff == 0:   return 1.00
    if diff == 1:   return 0.85
    if diff >= 2:   return 0.72
    if diff == -1:  return 1.20
    return 1.38


def _red_card_mult(red_cards: int) -> float:
    """Each red card reduces attack rate ~20%; floored at 0.50."""
    return max(0.50, 1.0 - 0.20 * red_cards)


def _phase_mult(block_end: int) -> float:
    """Late-game push: both teams elevate in final 15 min."""
    if block_end >= 85: return 1.25
    if block_end >= 75: return 1.10
    return 1.00


def _momentum_mult(goals_last_block: int) -> float:
    """Proxy for momentum when attack counts unavailable: recent goal = +15%."""
    return 1.15 if goals_last_block >= 1 else 1.00


def _p_at_least_one(lam: float) -> float:
    return 1.0 - math.exp(-max(lam, 0.0))


# ── Next-15 model ─────────────────────────────────────────────────────────────

def compute_next15(
    pre: PreMatchPrediction,
    minute: int,
    home_score: int,
    away_score: int,
    home_red_cards: int = 0,
    away_red_cards: int = 0,
    home_goals_last_block: int = 0,
    away_goals_last_block: int = 0,
) -> Next15Prediction:
    from app.services.live_provider import AVAILABLE_TARGETS, UNAVAILABLE_TARGETS

    score_diff  = home_score - away_score
    block_start = minute
    block_end   = min(minute + BLOCK_MINUTES, MATCH_MINUTES)
    remaining   = max(block_end - block_start, 0)

    h_score_m = _score_mult(score_diff, is_home=True)
    a_score_m = _score_mult(score_diff, is_home=False)
    h_red_m   = _red_card_mult(home_red_cards)
    a_red_m   = _red_card_mult(away_red_cards)
    phase_m   = _phase_mult(block_end)
    h_mom_m   = _momentum_mult(home_goals_last_block)
    a_mom_m   = _momentum_mult(away_goals_last_block)

    h_total = h_score_m * h_red_m * phase_m * h_mom_m
    a_total = a_score_m * a_red_m * phase_m * a_mom_m

    home_targets: dict = {}
    away_targets: dict = {}

    for target in PREDICTION_TARGETS:
        lam_h = pre.home_lambda0.get(target, 0.0)
        lam_a = pre.away_lambda0.get(target, 0.0)
        live_ok = target in AVAILABLE_TARGETS

        home_targets[target] = {
            "expected":       round(lam_h * remaining * h_total, 3),
            "live_available": live_ok,
        }
        away_targets[target] = {
            "expected":       round(lam_a * remaining * a_total, 3),
            "live_available": live_ok,
        }

    # Goals: surface probability, not count
    h_goal_lam = pre.home_lambda0["goals"] * remaining * h_total
    a_goal_lam = pre.away_lambda0["goals"] * remaining * a_total

    return Next15Prediction(
        match_id            = pre.match_id,
        home_team           = pre.home_team,
        away_team           = pre.away_team,
        current_minute      = minute,
        block_start         = block_start,
        block_end           = block_end,
        score               = {"home": home_score, "away": away_score},
        home_goal_prob      = round(_p_at_least_one(h_goal_lam), 4),
        away_goal_prob      = round(_p_at_least_one(a_goal_lam), 4),
        home_targets        = home_targets,
        away_targets        = away_targets,
        game_state          = {
            "score_diff":          score_diff,
            "home_score_mult":     round(h_score_m, 3),
            "away_score_mult":     round(a_score_m, 3),
            "home_red_card_mult":  round(h_red_m, 3),
            "away_red_card_mult":  round(a_red_m, 3),
            "phase_mult":          round(phase_m, 3),
            "home_momentum_mult":  round(h_mom_m, 3),
            "away_momentum_mult":  round(a_mom_m, 3),
        },
        available_targets   = AVAILABLE_TARGETS,
        unavailable_targets = UNAVAILABLE_TARGETS,
        generated_at        = datetime.now(timezone.utc).isoformat(),
    )
