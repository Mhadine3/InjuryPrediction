"""
fetch_player_scoring_stats.py
==============================
Fetch per-player goal/shot stats from API-Football free tier (100 req/day).
Uses api_player_ids.json for the player → API-Football ID mapping.

Set APIFOOTBALL_KEY env var to enable live fetch.
Without it, all player stats are marked unavailable and the prematch model
falls back to career goals from players_baseline.json.

Saves: data/player_scoring_stats.json
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
KEY      = os.environ.get("APIFOOTBALL_KEY", "")


def main() -> None:
    baseline   = json.loads((DATA_DIR / "players_baseline.json").read_text(encoding="utf-8"))
    id_map_raw = json.loads((DATA_DIR / "api_player_ids.json").read_text(encoding="utf-8"))
    id_map     = {p["our_player_id"]: p for p in id_map_raw}

    stats:    dict[str, dict] = {}
    coverage: dict[str, str]  = {}

    if not KEY:
        print("APIFOOTBALL_KEY not set — all player stats unavailable.")
        print("Set env APIFOOTBALL_KEY=<key> and re-run to fetch real season stats.")
        for p in baseline["players"]:
            stats[p["player_id"]] = {"status": "unavailable", "reason": "no_api_key"}
        for tla in ["BRA", "MAR", "HAI", "SCO", "FRA", "SEN", "IRQ", "NOR"]:
            coverage[tla] = "unavailable (no API-Football key)"
    else:
        try:
            import requests
        except ImportError:
            raise SystemExit("pip install requests")

        BASE = "https://api-football-v1.p.rapidapi.com/v3"
        HDR  = {
            "x-rapidapi-key":  KEY,
            "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
        }

        fetched = 0
        for p in baseline["players"]:
            pid       = p["player_id"]
            api_entry = id_map.get(pid)
            if not api_entry:
                stats[pid] = {"status": "unavailable", "reason": "no_api_id"}
                continue

            api_id = api_entry["api_player_id"]
            try:
                r = requests.get(
                    f"{BASE}/players",
                    headers=HDR,
                    params={"id": api_id, "season": 2025},
                    timeout=10,
                )
                time.sleep(0.5)
                fetched += 1

                if r.status_code == 200:
                    resp = r.json().get("response", [])
                    if resp:
                        s = resp[0].get("statistics", [{}])[0]
                        g = s.get("goals", {}) or {}
                        sh = s.get("shots", {}) or {}
                        gm = s.get("games", {}) or {}
                        stats[pid] = {
                            "status":           "full",
                            "season":           2025,
                            "goals":            g.get("total") or 0,
                            "shots":            sh.get("total") or 0,
                            "shots_on_target":  sh.get("on") or 0,
                            "minutes":          gm.get("minutes") or 0,
                            "appearances":      gm.get("appearences") or 0,
                        }
                    else:
                        stats[pid] = {"status": "unavailable", "reason": "empty_response"}
                else:
                    stats[pid] = {"status": "unavailable", "reason": f"http_{r.status_code}"}

            except Exception as exc:
                stats[pid] = {"status": "unavailable", "reason": str(exc)}

            if fetched >= 90:  # stay within 100/day cap
                print(f"  Request cap reached at {fetched}. Remaining players marked unavailable.")
                break

        # Mark any remaining players
        for p in baseline["players"]:
            if p["player_id"] not in stats:
                stats[p["player_id"]] = {"status": "unavailable", "reason": "cap_reached"}

        for tla in ["BRA", "MAR", "HAI", "SCO", "FRA", "SEN", "IRQ", "NOR"]:
            team_players = [p for p in baseline["players"] if p["team_code"] == tla]
            n_full = sum(1 for p in team_players
                         if stats.get(p["player_id"], {}).get("status") == "full")
            coverage[tla] = f"{n_full}/{len(team_players)} players with full season stats"

    out = {
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "coverage":   coverage,
        "stats":      stats,
    }
    out_path = DATA_DIR / "player_scoring_stats.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Saved -> {out_path}")

    print("\n=== Per-team player coverage ===")
    for tla, summary in sorted(coverage.items()):
        print(f"  {tla}: {summary}")


if __name__ == "__main__":
    main()
