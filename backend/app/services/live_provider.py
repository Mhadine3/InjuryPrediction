"""
Live match data provider.

Probed June 13 2026 (BRA vs MAR match day):
  TheSportsDB v1 free (key=3) — score + minute only; livescore.php requires Patreon tier.
  football-data.org v4 free  — score, status, minute, card events; no shot/corner/foul detail.

Live-available:   goals (score), red_cards (card events)
Unavailable:      shots, corners, fouls, possession, attacks, dangerous_attacks
"""

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

logger = logging.getLogger(__name__)

SPORTSDB_BASE     = "https://www.thesportsdb.com/api/v1/json/3"
FOOTBALLDATA_BASE = "https://api.football-data.org/v4"

# Confirmed by probe — free-tier providers
LIVE_AVAILABLE: dict[str, bool] = {
    "goals":              True,
    "red_cards":          True,
    "shots":              False,
    "corners":            False,
    "fouls":              False,
    "possession_pct":     False,
    "attacks":            False,
    "dangerous_attacks":  False,
}

AVAILABLE_TARGETS   = [t for t, v in LIVE_AVAILABLE.items() if v]
UNAVAILABLE_TARGETS = [t for t, v in LIVE_AVAILABLE.items() if not v]


@dataclass
class LiveSnapshot:
    match_id:             str
    provider:             str   # "football-data.org" | "thesportsdb" | "demo"
    minute:               int
    home_score:           int
    away_score:           int
    home_red_cards:       int   = 0
    away_red_cards:       int   = 0
    # None when not supplied by provider
    home_shots:           int | None = None
    away_shots:           int | None = None
    home_corners:         int | None = None
    away_corners:         int | None = None
    home_fouls:           int | None = None
    away_fouls:           int | None = None
    home_possession_pct:  float | None = None
    away_possession_pct:  float | None = None
    home_attacks:         int | None = None
    away_attacks:         int | None = None
    is_live:              bool  = True
    captured_at:          str   = ""

    def to_dict(self) -> dict:
        return asdict(self)


# In-memory store — keyed by match_id string
_MATCH_STATE: dict[str, LiveSnapshot] = {}


def get_match_state(match_id: str) -> LiveSnapshot | None:
    return _MATCH_STATE.get(match_id)


def set_match_state(snap: LiveSnapshot) -> None:
    _MATCH_STATE[snap.match_id] = snap


def inject_demo_snapshot(
    match_id: str,
    minute: int,
    home_score: int,
    away_score: int,
    home_red: int = 0,
    away_red: int = 0,
) -> LiveSnapshot:
    """Inject a demo match state (used when no live API data is available)."""
    snap = LiveSnapshot(
        match_id       = match_id,
        provider       = "demo",
        minute         = minute,
        home_score     = home_score,
        away_score     = away_score,
        home_red_cards = home_red,
        away_red_cards = away_red,
        is_live        = True,
        captured_at    = datetime.now(timezone.utc).isoformat(),
    )
    set_match_state(snap)
    return snap


def _normalize_football_data(raw: dict, match_id: str) -> LiveSnapshot | None:
    """Normalize a football-data.org /matches/{id} response."""
    score    = raw.get("score", {})
    ft       = score.get("fullTime", {})
    ht       = score.get("halfTime", {})
    status   = raw.get("status", "")
    home_id  = raw.get("homeTeam", {}).get("id")

    home_score = ft.get("home") or ht.get("home") or 0
    away_score = ft.get("away") or ht.get("away") or 0

    home_red = away_red = 0
    for bk in raw.get("bookings", []):
        if bk.get("card") in ("RED_CARD", "YELLOW_RED_CARD"):
            if bk.get("team", {}).get("id") == home_id:
                home_red += 1
            else:
                away_red += 1

    return LiveSnapshot(
        match_id       = match_id,
        provider       = "football-data.org",
        minute         = int(raw.get("minute") or 0),
        home_score     = int(home_score or 0),
        away_score     = int(away_score or 0),
        home_red_cards = home_red,
        away_red_cards = away_red,
        is_live        = status in ("IN_PLAY", "PAUSED"),
        captured_at    = datetime.now(timezone.utc).isoformat(),
    )


def fetch_from_football_data(
    football_data_match_id: int,
    api_key: str,
    match_id: str,
) -> LiveSnapshot | None:
    if not _REQUESTS_OK or not api_key:
        return None
    try:
        url = f"{FOOTBALLDATA_BASE}/matches/{football_data_match_id}"
        r   = _requests.get(url, headers={"X-Auth-Token": api_key}, timeout=8)
        r.raise_for_status()
        snap = _normalize_football_data(r.json(), match_id)
        if snap:
            set_match_state(snap)
        return snap
    except Exception as e:
        logger.warning("football-data.org fetch failed for match %s: %s", football_data_match_id, e)
        return None


def get_live_snapshot(match_id: str, football_data_id: int | None = None, api_key: str = "") -> LiveSnapshot | None:
    """Return in-memory state if present; otherwise try the API."""
    snap = get_match_state(match_id)
    if snap:
        return snap
    if football_data_id and api_key:
        return fetch_from_football_data(football_data_id, api_key, match_id)
    return None
