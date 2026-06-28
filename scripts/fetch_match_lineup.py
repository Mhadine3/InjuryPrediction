"""
fetch_match_lineup.py
=====================
Fetches the official lineup for a match from Football-Data.org
and writes a structured JSON file used by simulate_match.py.

If the lineup is not yet published (match not started), it creates
a template that coaching staff can fill in manually.

Usage
-----
    # Auto-fetch when lineup is published (match day):
    python scripts/fetch_match_lineup.py --match-id 537339

    # Create manual template for a match not in the API (e.g. friendlies):
    python scripts/fetch_match_lineup.py --manual --home BRA --away MAR --date 2026-06-05

Output
------
    data/matches/{date}_{home}_{away}.json

JSON structure
--------------
{
  "match_id":     537339,           # null for manual
  "date":         "2026-06-13",
  "home_code":    "BRA",
  "away_code":    "MAR",
  "source":       "api" | "manual",
  "lineup_locked": false,           # true once officially published
  "home": {
    "formation": "4-3-3",
    "starters": [
      {"our_player_id": "bra_001_alisson", "name": "Alisson", "position_detail": "Goalkeeper", "shirt_number": 1}
    ],
    "subs": [
      {"our_player_id": "bra_022_martinelli", "name": "Martinelli", "position_detail": "Winger",
       "shirt_number": 11, "came_on_for": "Raphinha", "minute_on": 68, "minute_off": 90}
    ]
  },
  "away": { ... }
}
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT         = Path(__file__).resolve().parent.parent
DATA_DIR     = ROOT / "data"
MATCHES_DIR  = DATA_DIR / "matches"
MAPPING_FILE = DATA_DIR / "api_player_ids.json"

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
import os
API_KEY  = os.getenv("FOOTBALL_DATA_API_KEY", "")
BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": API_KEY}

TLA_TO_TEAM_ID = {"BRA": 764, "MAR": 815, "HAI": 836, "SCO": 8873}

# Default empty player slot for manual templates
def _empty_player(team_code: str) -> list[dict]:
    return [{"our_player_id": f"{team_code.lower()}_XXX", "name": "FILL IN",
             "position_detail": "FILL IN", "shirt_number": 0}]


def load_mapping() -> dict:
    """Returns dict: api_player_id -> our mapping entry."""
    with open(MAPPING_FILE, encoding="utf-8") as f:
        rows = json.load(f)
    return {r["api_player_id"]: r for r in rows}


def fetch_lineup(match_id: int) -> dict | None:
    url = f"{BASE_URL}/matches/{match_id}"
    r   = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def parse_lineup(match_data: dict, mapping: dict) -> dict:
    """Parse API lineup response into our JSON structure."""
    home_id = match_data["homeTeam"]["id"]
    away_id = match_data["awayTeam"]["id"]

    # Reverse lookup: team_id -> tla
    id_to_tla = {v: k for k, v in TLA_TO_TEAM_ID.items()}
    home_tla  = match_data["homeTeam"].get("tla") or id_to_tla.get(home_id, "UNK")
    away_tla  = match_data["awayTeam"].get("tla") or id_to_tla.get(away_id, "UNK")
    match_date = match_data["utcDate"][:10]

    lineups = match_data.get("lineups", [])

    def build_team(tla: str, team_id: int) -> dict:
        team_lineup = next((l for l in lineups if l.get("team", {}).get("id") == team_id), None)
        if not team_lineup:
            return {"formation": "FILL IN", "starters": _empty_player(tla), "subs": []}

        starters = []
        for p in team_lineup.get("startXI", []):
            ap = p.get("player", {})
            m  = mapping.get(ap.get("id"), {})
            starters.append({
                "our_player_id":   m.get("our_player_id", f"{tla.lower()}_unknown"),
                "api_player_id":   ap.get("id"),
                "name":            m.get("our_name") or ap.get("name", "Unknown"),
                "position_detail": m.get("position_detail", "Unknown"),
                "shirt_number":    ap.get("shirtNumber", 0),
            })

        subs = []
        for p in team_lineup.get("substitutes", []):
            ap = p.get("player", {})
            m  = mapping.get(ap.get("id"), {})
            sub_entry = {
                "our_player_id":   m.get("our_player_id", f"{tla.lower()}_unknown"),
                "api_player_id":   ap.get("id"),
                "name":            m.get("our_name") or ap.get("name", "Unknown"),
                "position_detail": m.get("position_detail", "Unknown"),
                "shirt_number":    ap.get("shirtNumber", 0),
                "came_on_for":     None,
                "minute_on":       None,
                "minute_off":      90,
            }
            subs.append(sub_entry)

        return {
            "formation": team_lineup.get("formation", "FILL IN"),
            "starters":  starters,
            "subs":      subs,
        }

    return {
        "match_id":      match_data["id"],
        "date":          match_date,
        "home_code":     home_tla,
        "away_code":     away_tla,
        "source":        "api",
        "lineup_locked": bool(lineups),
        "home":          build_team(home_tla, home_id),
        "away":          build_team(away_tla, away_id),
    }


def build_manual_template(home_code: str, away_code: str, match_date: str) -> dict:
    """Empty template for staff to fill in (friendlies or pre-match)."""
    def team_template(tla: str) -> dict:
        return {
            "formation": "FILL IN e.g. 4-3-3",
            "starters": [
                {"our_player_id": f"{tla.lower()}_XXX", "name": "FILL IN",
                 "position_detail": "FILL IN", "shirt_number": i + 1}
                for i in range(11)
            ],
            "subs": [
                {"our_player_id": f"{tla.lower()}_XXX", "name": "FILL IN",
                 "position_detail": "FILL IN", "shirt_number": 0,
                 "came_on_for": "FILL IN player replaced", "minute_on": 0, "minute_off": 90}
            ],
        }

    return {
        "match_id":      None,
        "date":          match_date,
        "home_code":     home_code,
        "away_code":     away_code,
        "source":        "manual",
        "lineup_locked": False,
        "note":          "Fill in our_player_id from data/api_player_ids.json. "
                         "Set minute_on/minute_off for subs. Delete unused sub slots.",
        "home":          team_template(home_code),
        "away":          team_template(away_code),
    }


def output_path(match_date: str, home: str, away: str) -> Path:
    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    return MATCHES_DIR / f"{match_date}_{home}_{away}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch or template a match lineup")
    parser.add_argument("--match-id",  type=int,  help="Football-Data.org match ID")
    parser.add_argument("--manual",    action="store_true", help="Create a manual template")
    parser.add_argument("--home",      help="Home team TLA (e.g. BRA)")
    parser.add_argument("--away",      help="Away team TLA (e.g. MAR)")
    parser.add_argument("--date",      help="Match date YYYY-MM-DD (manual mode)")
    args = parser.parse_args()

    MATCHES_DIR.mkdir(parents=True, exist_ok=True)
    mapping = load_mapping()

    if args.manual:
        if not (args.home and args.away and args.date):
            print("[ERROR] --manual requires --home, --away, --date")
            sys.exit(1)
        result = build_manual_template(args.home.upper(), args.away.upper(), args.date)
        out    = output_path(args.date, args.home.upper(), args.away.upper())
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Template created -> {out}")
        print("Fill in player IDs from: data/api_player_ids.json")
        return

    if not args.match_id:
        # Default: fetch the BRA vs MAR WC match
        args.match_id = 537339
        print(f"No --match-id given. Defaulting to BRA vs MAR (WC, id={args.match_id})")

    if not API_KEY:
        print("[ERROR] FOOTBALL_DATA_API_KEY not set in .env")
        sys.exit(1)

    print(f"Fetching match {args.match_id} from Football-Data.org ...")
    data   = fetch_lineup(args.match_id)
    result = parse_lineup(data, mapping)
    out    = output_path(result["date"], result["home_code"], result["away_code"])

    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    locked = result["lineup_locked"]
    print(f"Saved -> {out.name}")
    print(f"Lineup published: {'YES — ready for simulation' if locked else 'NO — template only, re-run on match day'}")

    if not locked:
        print()
        print("  The match has not started yet. Lineup will be published ~1h before kickoff.")
        print(f"  Re-run this script on match day (June 13) to get the official XI.")


if __name__ == "__main__":
    main()
