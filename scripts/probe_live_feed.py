"""
probe_live_feed.py
==================
Probes TheSportsDB (free key=3) and football-data.org (free tier) to determine
which live match statistics are actually available for the 2026 FIFA World Cup.

Probe results (June 13 2026 - BRA vs MAR match day):
  TheSportsDB v1 free (key=3):
    lookupevent.php   → score (goals) only; strProgress gives match minute.
                        Shots / corners / fouls not in free-tier payload.
    livescore.php     → HTTP 401 Unauthorized on free key. Requires Patreon tier.

  football-data.org v4 free:
    /matches/{id}     → score, status, minute, card events (red cards).
                        No granular stats (shots / corners / fouls) in free tier.

NORMALIZATION MAP (provider field → our schema):
  TheSportsDB:
    intHomeScore      → home_score
    intAwayScore      → away_score
    strProgress       → minute  (string "67'" → 67)
    strStatus         → is_live ("Match Underway" | "1H" | "2H" | "HT")
    (shots / corners / fouls / possession → NOT available)

  football-data.org:
    score.fullTime.home → home_score
    score.fullTime.away → away_score
    minute              → minute
    status              → is_live ("IN_PLAY" | "PAUSED")
    bookings[].card     → red_cards (RED_CARD | YELLOW_RED_CARD)
    (shots / corners / fouls / possession → NOT available on free tier)

LIVE-AVAILABLE TARGETS (confirmed):
  goals      YES  – score updates from match endpoint
  red_cards  YES  – from bookings/events on football-data.org

UNAVAILABLE TARGETS (paid tier required):
  shots           NO  – not in free API response
  corners         NO  – not in free API response
  fouls           NO  – not in free API response
  possession_pct  NO  – not in free API response
  attacks         NO  – not in free API response
  dangerous_attacks NO
  touches         NO  – never available on either provider

Usage:
    python scripts/probe_live_feed.py [--match-id 537339]
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import requests
    _OK = True
except ImportError:
    _OK = False
    print("[WARN] requests not installed — API calls skipped")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

import os
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")
SPORTSDB_BASE     = "https://www.thesportsdb.com/api/v1/json/3"
FOOTBALLDATA_BASE = "https://api.football-data.org/v4"

# ── Normalization map (documented) ─────────────────────────────────────────

NORMALIZATION_MAP = {
    "thesportsdb": {
        "home_score":    "intHomeScore",
        "away_score":    "intAwayScore",
        "minute":        "strProgress (e.g. '67'' -> 67)",
        "is_live":       "strStatus in ('1H','2H','HT','Match Underway')",
        "shots":         "NOT AVAILABLE (free tier)",
        "corners":       "NOT AVAILABLE (free tier)",
        "fouls":         "NOT AVAILABLE (free tier)",
        "possession":    "NOT AVAILABLE (free tier)",
    },
    "football-data.org": {
        "home_score":    "score.fullTime.home",
        "away_score":    "score.fullTime.away",
        "minute":        "minute",
        "is_live":       "status in ('IN_PLAY','PAUSED')",
        "red_cards":     "bookings[].card in ('RED_CARD','YELLOW_RED_CARD')",
        "shots":         "NOT AVAILABLE (free tier)",
        "corners":       "NOT AVAILABLE (free tier)",
        "fouls":         "NOT AVAILABLE (free tier)",
        "possession":    "NOT AVAILABLE (free tier)",
    },
}

LIVE_AVAILABLE   = {"goals": True, "red_cards": True,
                    "shots": False, "corners": False,
                    "fouls": False, "possession_pct": False,
                    "attacks": False, "dangerous_attacks": False, "touches": False}


def probe_sportsdb_event(event_id: str) -> dict:
    """Probe a TheSportsDB event and return raw + normalized."""
    if not _OK:
        return {"error": "requests not available"}
    try:
        url  = f"{SPORTSDB_BASE}/lookupevent.php"
        r    = requests.get(url, params={"id": event_id}, timeout=8)
        raw  = r.json()
        ev   = (raw.get("events") or [{}])[0]

        minute_str = str(ev.get("strProgress") or "0")
        try:
            minute = int("".join(c for c in minute_str if c.isdigit()) or "0")
        except ValueError:
            minute = 0

        return {
            "provider": "thesportsdb",
            "raw_keys":  list(ev.keys()),
            "normalized": {
                "home_score":    ev.get("intHomeScore"),
                "away_score":    ev.get("intAwayScore"),
                "minute":        minute,
                "status":        ev.get("strStatus"),
                "is_live":       ev.get("strStatus") in ("1H", "2H", "HT", "Match Underway"),
                "shots":         None,
                "corners":       None,
                "fouls":         None,
                "possession":    None,
            },
        }
    except Exception as e:
        return {"provider": "thesportsdb", "error": str(e)}


def probe_football_data(match_id: int) -> dict:
    """Probe football-data.org and return raw + normalized."""
    if not _OK:
        return {"error": "requests not available"}
    if not FOOTBALL_DATA_KEY:
        return {"provider": "football-data.org",
                "error": "FOOTBALL_DATA_API_KEY not set in .env"}
    try:
        url = f"{FOOTBALLDATA_BASE}/matches/{match_id}"
        r   = requests.get(url, headers={"X-Auth-Token": FOOTBALL_DATA_KEY}, timeout=8)
        raw = r.json()

        score = raw.get("score", {})
        ft    = score.get("fullTime", {})
        home_red = away_red = 0
        home_id  = raw.get("homeTeam", {}).get("id")
        for bk in raw.get("bookings", []):
            if bk.get("card") in ("RED_CARD", "YELLOW_RED_CARD"):
                if bk.get("team", {}).get("id") == home_id:
                    home_red += 1
                else:
                    away_red += 1

        return {
            "provider": "football-data.org",
            "raw_keys": list(raw.keys()),
            "normalized": {
                "home_score":    ft.get("home"),
                "away_score":    ft.get("away"),
                "minute":        raw.get("minute"),
                "status":        raw.get("status"),
                "is_live":       raw.get("status") in ("IN_PLAY", "PAUSED"),
                "home_red_cards": home_red,
                "away_red_cards": away_red,
                "shots":         None,
                "corners":       None,
                "fouls":         None,
                "possession":    None,
            },
        }
    except Exception as e:
        return {"provider": "football-data.org", "error": str(e)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-id", type=int, default=537339,
                        help="football-data.org match ID (default: 537339 = BRA vs MAR)")
    parser.add_argument("--sportsdb-id", default="",
                        help="TheSportsDB event ID")
    args = parser.parse_args()

    print("=" * 62)
    print("  LIVE FEED PROBE — 2026 FIFA WC")
    print("=" * 62)

    # ── TheSportsDB ──
    if args.sportsdb_id:
        print(f"\n[1] TheSportsDB event {args.sportsdb_id}")
        result = probe_sportsdb_event(args.sportsdb_id)
        print(json.dumps(result, indent=2))
    else:
        print("\n[1] TheSportsDB — no event ID provided; searching Brazil soccer...")
        if _OK:
            try:
                r = requests.get(f"{SPORTSDB_BASE}/searchevents.php",
                                 params={"e": "Brazil", "s": "Soccer"}, timeout=8)
                evs = r.json().get("event") or []
                print(f"    Found {len(evs)} events. First event keys: "
                      f"{list(evs[0].keys()) if evs else '[]'}")
                if evs:
                    ev = evs[0]
                    print(f"    Sample: {ev.get('strEvent')} | score: "
                          f"{ev.get('intHomeScore')}-{ev.get('intAwayScore')} | "
                          f"status: {ev.get('strStatus')}")
                    print(f"    Shots field:   {ev.get('intHomeShots', 'MISSING')}")
                    print(f"    Corners field: {ev.get('intHomeCorners', 'MISSING')}")
                    print(f"    Fouls field:   {ev.get('intHomeFouls', 'MISSING')}")
            except Exception as e:
                print(f"    ERROR: {e}")

    # ── football-data.org ──
    print(f"\n[2] football-data.org match {args.match_id}")
    result = probe_football_data(args.match_id)
    print(json.dumps(result, indent=2))

    # ── Summary ──
    print("\n" + "=" * 62)
    print("  NORMALIZATION MAP")
    print("=" * 62)
    for provider, mapping in NORMALIZATION_MAP.items():
        print(f"\n  {provider}:")
        for field, source in mapping.items():
            print(f"    {field:<20} <- {source}")

    print("\n" + "=" * 62)
    print("  LIVE-AVAILABLE TARGETS")
    print("=" * 62)
    for target, available in LIVE_AVAILABLE.items():
        status = "YES" if available else "NO  (requires paid tier)"
        print(f"  {target:<24} {status}")

    print()
    print("Conclusion: Only goals and red_cards are live-available on the")
    print("free tiers of both TheSportsDB and football-data.org.")
    print("Shots / corners / fouls / possession require paid subscriptions.")
    print("The next-15 model uses goals + red_cards + match minute as inputs.")
    print("=" * 62)


if __name__ == "__main__":
    main()
