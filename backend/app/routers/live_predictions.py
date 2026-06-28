"""
Live match prediction endpoints.

GET /api/v1/predictions/match/{match_id}/pre
    -> pre-match expected totals per team per target (all prediction targets)
    -> available_targets / unavailable_targets reported in response

GET /api/v1/predictions/match/{match_id}/next15
    -> live next-15-minute Poisson prediction
    -> query params used as fallback when no in-memory snapshot exists
    -> goals shown as P(>=1), not expected count

match_id format: "2026-06-13_BRA_MAR"
"""

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from app.services.live_provider import (
    AVAILABLE_TARGETS,
    UNAVAILABLE_TARGETS,
    get_match_state,
    inject_demo_snapshot,
)
from app.services.match_predictor import (
    SCHEDULED_MATCHES,
    PreMatchPrediction,
    compute_next15,
    make_pre_match_prediction,
)
from app.services.prematch_service import compute_prematch

router = APIRouter(prefix="/predictions/match", tags=["live-match"])

ROOT        = Path(__file__).resolve().parents[3]
MATCHES_DIR = ROOT / "data" / "matches"

# Module-level pre-match cache: match_id -> PreMatchPrediction
_PRE_CACHE: dict[str, PreMatchPrediction] = {}

# The demo match that runs when no API key is configured
DEMO_MATCH_ID = "2026-06-13_BRA_MAR"
DEMO_MINUTE   = 67
DEMO_HOME_SCR = 1
DEMO_AWAY_SCR = 0


def _load_match_file(match_id: str) -> dict | None:
    p = MATCHES_DIR / f"{match_id}.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def _get_pre(match_id: str) -> PreMatchPrediction:
    if match_id in _PRE_CACHE:
        return _PRE_CACHE[match_id]

    match_file = _load_match_file(match_id)
    if match_file:
        home_tla      = match_file.get("home_code", "BRA")
        away_tla      = match_file.get("away_code", "MAR")
        lineup_locked = match_file.get("lineup_locked", False)
    else:
        parts         = match_id.split("_")
        home_tla      = parts[1].upper() if len(parts) > 1 else "BRA"
        away_tla      = parts[2].upper() if len(parts) > 2 else "MAR"
        lineup_locked = False

    pre = make_pre_match_prediction(
        match_id            = match_id,
        home_tla            = home_tla,
        away_tla            = away_tla,
        lineup_locked       = lineup_locked,
        available_targets   = AVAILABLE_TARGETS,
        unavailable_targets = UNAVAILABLE_TARGETS,
    )
    _PRE_CACHE[match_id] = pre
    return pre


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{match_id}/pre")
async def get_pre_match(match_id: str):
    """
    Pre-match expected totals per team for every prediction target.
    Computed from season per-90 baselines adjusted for opponent strength.
    available_targets / unavailable_targets indicate live-feed coverage.
    """
    return _get_pre(match_id).to_dict()


@router.get("/{match_id}/next15")
async def get_next_15(
    match_id: str,
    minute: int = Query(0, ge=0, le=90, description="Current match minute"),
    home_score: int = Query(0, ge=0),
    away_score: int = Query(0, ge=0),
    home_red_cards: int = Query(0, ge=0),
    away_red_cards: int = Query(0, ge=0),
    home_goals_last_block: int = Query(0, ge=0,
        description="Goals scored by home in the last completed 15-min block (momentum proxy)"),
    away_goals_last_block: int = Query(0, ge=0),
    demo: bool = Query(False, description="Use demo match state (minute=67, score 1-0)"),
):
    """
    Live next-15-minute Poisson prediction per team per target.

    Priority order for match state:
      1. In-memory snapshot from live polling (if scheduler is running)
      2. Demo snapshot (if demo=true or match_id is the demo match)
      3. Query parameters (manual override / testing)

    Goals are returned as P(>=1 goal in next 15 min), not as an expected count.
    Unavailable live stats (shots/corners/fouls) still have model predictions
    but their live_available flag is false.
    """
    pre  = _get_pre(match_id)
    snap = get_match_state(match_id)

    # Auto-inject demo if this is the demo match and no live state exists
    if snap is None and (demo or match_id == DEMO_MATCH_ID):
        snap = inject_demo_snapshot(
            match_id   = match_id,
            minute     = DEMO_MINUTE,
            home_score = DEMO_HOME_SCR,
            away_score = DEMO_AWAY_SCR,
        )

    if snap and snap.is_live:
        minute         = snap.minute
        home_score     = snap.home_score
        away_score     = snap.away_score
        home_red_cards = snap.home_red_cards
        away_red_cards = snap.away_red_cards

    return compute_next15(
        pre                   = pre,
        minute                = minute,
        home_score            = home_score,
        away_score            = away_score,
        home_red_cards        = home_red_cards,
        away_red_cards        = away_red_cards,
        home_goals_last_block = home_goals_last_block,
        away_goals_last_block = away_goals_last_block,
    ).to_dict()


@router.get("/live-scores")
async def get_live_scores():
    """Real BSD Bzzoiro live/final/upcoming scores for all Group C and Group I matches."""
    from app.services.prematch_service import get_bsd_live_scores
    return {"matches": get_bsd_live_scores(), "source": "bsd_bzzoiro"}


@router.get("/{match_id}/prematch")
async def get_prematch(match_id: str):
    """
    Full pre-match prediction package for any Group C or Group I fixture.

    match_id format: "YYYY-MM-DD_HOME_AWAY"  e.g. "2026-06-15_FRA_SEN"

    Returns:
      - Outcome probabilities (W/D/L) summing to 1.0
      - Expected goals per team with range
      - Most-likely scoreline with probability
      - Scoreline probability matrix (8x8, home×away goals 0-7)
      - Ranked scorer probabilities per team (P scores >=1)
      - Team stat profile (shots on target / corners / fouls) with ranges
      - Per-team data-coverage note explaining sources and quality

    Model: Dixon-Coles Poisson with rho=-0.13 low-score correction.
    Ratings sourced from football-data.org results when available;
    falls back to FIFA ranking-derived estimates with explicit flag.
    """
    parts = match_id.split("_")
    home_tla = parts[1].upper() if len(parts) > 1 else "BRA"
    away_tla = parts[2].upper() if len(parts) > 2 else "MAR"
    return compute_prematch(home_tla, away_tla, match_id)


@router.get("/schedule/next")
async def get_next_scheduled():
    """Return the next upcoming scheduled Group C match and its pre-match prediction."""
    from datetime import date
    today = date.today().isoformat()

    upcoming = [m for m in SCHEDULED_MATCHES if m["date"] >= today]
    if not upcoming:
        upcoming = SCHEDULED_MATCHES  # wrap around to first match

    nxt = upcoming[0]
    pre = _get_pre(nxt["match_id"])
    return {
        "next_match":  nxt,
        "pre_match":   pre.to_dict(),
    }
