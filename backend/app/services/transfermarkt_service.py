"""
Transfermarkt API service — fetches player profile, career stats, and injury history.
Caches Transfermarkt player IDs locally to avoid repeated searches.
Rate-limited to ~1 req/sec to respect the public instance limits.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

TM_BASE = "https://transfermarkt-api.fly.dev"
TM_TIMEOUT = 25.0

# Local cache: our_player_id → transfermarkt_id
_CACHE_FILE = Path(__file__).resolve().parents[3] / "data" / "tm_player_ids.json"
_tm_id_cache: dict[str, str] = {}


def _load_cache() -> None:
    global _tm_id_cache
    if _CACHE_FILE.exists():
        try:
            _tm_id_cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _tm_id_cache = {}


def _save_cache() -> None:
    _CACHE_FILE.write_text(json.dumps(_tm_id_cache, indent=2), encoding="utf-8")


_load_cache()


async def _get(client: httpx.AsyncClient, path: str, retries: int = 2) -> dict | list | None:
    for attempt in range(retries + 1):
        try:
            r = await client.get(f"{TM_BASE}{path}", timeout=TM_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 2 ** attempt
                logger.warning("TM API %s → 429 rate-limited, retrying in %ss", path, wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("TM API %s → HTTP %s", path, r.status_code)
            return None
        except httpx.TimeoutException:
            logger.warning("TM API %s → timeout (attempt %d/%d)", path, attempt + 1, retries + 1)
        except httpx.ConnectError as exc:
            logger.warning("TM API %s → connection error: %r", path, exc)
            return None  # no point retrying a connection error
        except Exception as exc:
            logger.warning("TM API %s → %s: %r", path, type(exc).__name__, exc)
        if attempt < retries:
            await asyncio.sleep(1.5 * (attempt + 1))
    return None


async def _resolve_tm_id(client: httpx.AsyncClient, player_id: str, name: str) -> str | None:
    """Return Transfermarkt player ID, searching by name if not cached."""
    if player_id in _tm_id_cache:
        return _tm_id_cache[player_id]

    data = await _get(client, f"/players/search/{quote(name)}")
    await asyncio.sleep(0.8)

    if not data or not data.get("results"):
        return None

    # Pick best match: prefer exact name match
    players = data["results"]
    name_lower = name.lower()
    for p in players:
        if p.get("name", "").lower() == name_lower:
            tm_id = str(p["id"])
            _tm_id_cache[player_id] = tm_id
            _save_cache()
            return tm_id

    # Fallback: first result
    tm_id = str(players[0]["id"])
    _tm_id_cache[player_id] = tm_id
    _save_cache()
    return tm_id


async def get_player_full_profile(player_id: str, name: str) -> dict[str, Any]:
    """
    Fetch profile + injuries + stats from Transfermarkt for one player.
    Returns merged dict; missing sections are empty but never raise.
    """
    async with httpx.AsyncClient() as client:
        tm_id = await _resolve_tm_id(client, player_id, name)
        if not tm_id:
            return {"tm_id": None, "profile": None, "injuries": [], "stats": []}

        # Fetch all three in sequence (rate limit)
        profile = await _get(client, f"/players/{tm_id}/profile")
        await asyncio.sleep(0.8)

        injuries_data = await _get(client, f"/players/{tm_id}/injuries")
        await asyncio.sleep(0.8)

        stats_data = await _get(client, f"/players/{tm_id}/stats")

        return {
            "tm_id": tm_id,
            "profile": _parse_profile(profile),
            "injuries": _parse_injuries(injuries_data),
            "stats": _parse_stats(stats_data),
        }


async def get_squad_tm_overview(players: list[dict]) -> list[dict]:
    """
    Fetch TM market values + injury counts for an entire squad (lightweight).
    Only does a name search per player — fast single call per player.
    """
    results = []
    async with httpx.AsyncClient() as client:
        for p in players:
            pid = p["player_id"]
            name = p["name"]
            tm_id = _tm_id_cache.get(pid)

            market_value = None
            if not tm_id:
                data = await _get(client, f"/players/search/{quote(name)}")
                await asyncio.sleep(0.8)
                if data and data.get("results"):
                    first = data["results"][0]
                    tm_id = str(first["id"])
                    _tm_id_cache[pid] = tm_id
                    market_value = first.get("marketValue")
            if tm_id and market_value is None:
                search = await _get(client, f"/players/search/{quote(name)}")
                await asyncio.sleep(0.5)
                if search and search.get("results"):
                    market_value = search["results"][0].get("marketValue")

            results.append({**p, "tm_id": tm_id, "market_value": market_value})

    _save_cache()
    return results


# ── Parsers ──────────────────────────────────────────────────────────────────

def _parse_profile(raw: dict | None) -> dict | None:
    if not raw:
        return None
    pos = raw.get("position") or {}
    club = raw.get("club") or {}
    birth = raw.get("placeOfBirth") or {}
    return {
        "tm_id":            raw.get("id"),
        "name":             raw.get("name"),
        "image_url":        raw.get("imageUrl"),
        "height_cm":        raw.get("height"),
        "foot":             raw.get("foot"),
        "citizenship":      raw.get("citizenship", []),
        "place_of_birth":   f"{birth.get('city','')}, {birth.get('country','')}".strip(", "),
        "is_retired":       raw.get("isRetired", False),
        "position_main":    pos.get("main"),
        "position_other":   pos.get("other", []),
        "current_club":     club.get("name"),
        "most_games_for":   club.get("mostGamesFor"),
        "agent":            (raw.get("agent") or {}).get("name"),
    }


def _parse_injuries(raw: dict | None) -> list[dict]:
    if not raw or not isinstance(raw.get("injuries"), list):
        return []
    out = []
    for inj in raw["injuries"]:
        out.append({
            "season":        inj.get("season"),
            "injury":        inj.get("injury"),
            "from_date":     inj.get("fromDate"),
            "until_date":    inj.get("untilDate"),
            "days_missed":   inj.get("days"),
            "games_missed":  inj.get("gamesMissed"),
            "club":          ", ".join(str(c) for c in inj.get("gamesMissedClubs", [])) or None,
        })
    return out


def _parse_stats(raw: dict | None) -> list[dict]:
    if not raw or not isinstance(raw.get("stats"), list):
        return []
    out = []
    for s in raw["stats"]:
        club = s.get("club") or {}
        comp = s.get("competition") or {}
        out.append({
            "season":          s.get("season"),
            "club":            club.get("name"),
            "competition":     comp.get("name"),
            "appearances":     s.get("appearances"),
            "goals":           s.get("goals"),
            "assists":         s.get("assists"),
            "yellow_cards":    s.get("yellowCards"),
            "red_cards":       s.get("redCards"),
            "minutes_played":  s.get("minutesPlayed"),
        })
    return out
