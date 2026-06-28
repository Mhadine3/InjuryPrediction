"""
Player Profile router — combines internal data with Transfermarkt (profile, stats, injuries).
Endpoints:
  GET /player-profile/squad/{team_code}   → squad list with market values
  GET /player-profile/{player_id}         → full profile for one player
"""
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.transfermarkt_service import get_player_full_profile
from app.services.risk_adjustment import save_injury_summary

router = APIRouter(prefix="/player-profile", tags=["player-profile"])

_BASELINE_FILE = Path(__file__).resolve().parents[3] / "data" / "players_baseline.json"
_TM_IDS_FILE   = Path(__file__).resolve().parents[3] / "data" / "tm_player_ids.json"


def _load_baseline() -> list[dict]:
    if not _BASELINE_FILE.exists():
        return []
    return json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))["players"]


def _load_tm_cache() -> dict:
    if not _TM_IDS_FILE.exists():
        return {}
    try:
        return json.loads(_TM_IDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.get("/squad/{team_code}")
async def squad_profiles(team_code: str) -> list[dict[str, Any]]:
    """
    Return all players for a team with their baseline data + cached TM market value.
    Does NOT trigger live TM fetches — returns cached values only (fast).
    """
    players = _load_baseline()
    tm_cache = _load_tm_cache()

    squad = [p for p in players if p["team_code"].upper() == team_code.upper()]
    if not squad:
        raise HTTPException(status_code=404, detail=f"No players found for team {team_code}")

    result = []
    for p in squad:
        tm_id = tm_cache.get(p["player_id"])
        result.append({
            "player_id":       p["player_id"],
            "name":            p["name"],
            "team_code":       p["team_code"],
            "team":            p["team"],
            "position":        p["position"],
            "position_detail": p["position_detail"],
            "age":             p["age"],
            "caps":            p["caps"],
            "club":            p["club"],
            "league":          p.get("league"),
            "is_captain":      p.get("is_captain", False),
            "traits":          p.get("traits", {}),
            "physiology":      p.get("physiology", {}),
            "wellness":        p.get("wellness", {}),
            "tm_id":           tm_id,
            "tm_linked":       tm_id is not None,
        })

    return result


@router.get("/{player_id}")
async def player_full_profile(player_id: str) -> dict[str, Any]:
    """
    Full coach profile for one player:
    - Internal baseline data (physical, biometric, traits)
    - Transfermarkt: profile, complete injury history, career stats
    """
    players = _load_baseline()
    player = next((p for p in players if p["player_id"] == player_id), None)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    # Fetch from Transfermarkt (may take ~3-5 sec on first call)
    tm_data = await get_player_full_profile(player_id, player["name"])

    # Compute injury summary
    injuries = tm_data.get("injuries", [])

    total_days_missed = sum(
        (i.get("days_missed") or 0) for i in injuries if i.get("days_missed")
    )
    injury_types = list({i["injury"] for i in injuries if i.get("injury")})

    # Cache injury summary for risk adjustment layer
    injury_sum = {
        "total_injuries":    len(injuries),
        "total_days_missed": sum((i.get("days_missed") or 0) for i in injuries),
        "injury_types":      list({i["injury"] for i in injuries if i.get("injury")}),
        "most_recent":       injuries[0] if injuries else None,
    }
    if injuries:
        save_injury_summary(player_id, injury_sum, injuries)

    # Career stats summary
    stats = tm_data.get("stats", [])
    total_goals   = sum((s.get("goals")   or 0) for s in stats)
    total_assists = sum((s.get("assists") or 0) for s in stats)
    total_apps    = sum((s.get("appearances") or 0) for s in stats)

    return {
        # ── Internal data ──────────────────────────────────────────
        "player_id":       player["player_id"],
        "name":            player["name"],
        "team":            player["team"],
        "team_code":       player["team_code"],
        "position":        player["position"],
        "position_detail": player["position_detail"],
        "age":             player["age"],
        "date_of_birth":   player.get("date_of_birth"),
        "caps":            player["caps"],
        "goals":           player.get("goals", 0),
        "club":            player["club"],
        "league":          player.get("league"),
        "is_captain":      player.get("is_captain", False),
        "traits":          player.get("traits", {}),
        "physiology":      player.get("physiology", {}),
        "wellness":        player.get("wellness", {}),

        # ── Transfermarkt data ─────────────────────────────────────
        "tm_id":           tm_data.get("tm_id"),
        "profile":         tm_data.get("profile"),
        "injuries":        injuries,
        "stats":           stats,

        # ── Computed summaries ─────────────────────────────────────
        "injury_summary": {
            "total_injuries":    len(injuries),
            "total_days_missed": total_days_missed,
            "injury_types":      injury_types,
            "most_recent":       injuries[0] if injuries else None,
        },
        "career_summary": {
            "total_goals":    total_goals or player.get("goals", 0),
            "total_assists":  total_assists,
            "total_apps":     total_apps or player.get("caps", 0),
            "seasons_played": len(stats),
        },
    }
