"""
fetch_wc_team_results.py
========================
One-time fetch of international match results for 8 WC 2026 teams
from football-data.org free tier.

Strategy (quota-safe):
  1. /competitions/WC/teams   -> team IDs for all 8 teams
  2. /competitions/WC/matches?season=2022 -> WC 2022 full results
  3. /competitions/WC/matches?season=2026 -> WC 2026 results so far
  4. /teams/{id}/matches?status=FINISHED  -> broader recent history for
     teams with < 5 WC appearances (HAI, IRQ, NOR, SCO)

Saves:
  data/team_results.json   — per-team match list + attack/defence ratings
  data/team_stat_rates.json — shots/corners/fouls (unavailable on free tier)
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    raise SystemExit("ERROR: pip install requests")

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
if not API_KEY:
    raise SystemExit("ERROR: set FOOTBALL_DATA_API_KEY in your environment / .env")
BASE    = "https://api.football-data.org/v4"
HDR     = {"X-Auth-Token": API_KEY}

DATA_DIR     = Path(__file__).resolve().parents[1] / "data"
TARGET_TLAS  = {"BRA", "MAR", "HAI", "SCO", "FRA", "SEN", "IRQ", "NOR"}
INTL_AVG     = 1.35  # typical international goals per team per match


def _get(url: str, params: dict | None = None) -> dict | None:
    try:
        r = requests.get(url, headers=HDR, params=params, timeout=15)
        time.sleep(0.7)  # free tier: 10 req/min
        if r.status_code == 200:
            return r.json()
        print(f"  HTTP {r.status_code}: {url}")
        return None
    except Exception as exc:
        print(f"  Error fetching {url}: {exc}")
        return None


def fetch_wc_team_ids() -> dict[str, int]:
    data = _get(f"{BASE}/competitions/WC/teams")
    if not data:
        return {}
    result = {}
    for team in data.get("teams", []):
        tla = (team.get("tla") or "").upper()
        if tla in TARGET_TLAS:
            result[tla] = team["id"]
    return result


def fetch_wc_matches(season: int) -> list[dict]:
    data = _get(f"{BASE}/competitions/WC/matches",
                {"season": str(season), "status": "FINISHED"})
    return data.get("matches", []) if data else []


def fetch_team_recent_matches(team_id: int) -> list[dict]:
    """Recent finished matches for a team across all competitions."""
    data = _get(f"{BASE}/teams/{team_id}/matches",
                {"status": "FINISHED", "limit": "20"})
    return data.get("matches", []) if data else []


def _parse_result(match: dict, team_id: int) -> dict | None:
    score = match.get("score", {}).get("fullTime", {})
    h_goals = score.get("home")
    a_goals = score.get("away")
    if h_goals is None or a_goals is None:
        return None
    home_id = (match.get("homeTeam") or {}).get("id")
    if home_id == team_id:
        scored, conceded = h_goals, a_goals
        venue = "home"
        opp   = (match.get("awayTeam") or {}).get("tla", "?")
    else:
        scored, conceded = a_goals, h_goals
        venue = "away"
        opp   = (match.get("homeTeam") or {}).get("tla", "?")
    return {
        "date":     (match.get("utcDate") or "")[:10],
        "opponent": opp,
        "venue":    venue,
        "scored":   scored,
        "conceded": conceded,
        "result":   "W" if scored > conceded else ("D" if scored == conceded else "L"),
        "competition": (match.get("competition") or {}).get("code", "?"),
    }


def compute_ratings(match_list: list[dict]) -> dict | None:
    if not match_list:
        return None
    scored_lst    = [m["scored"]   for m in match_list]
    conceded_lst  = [m["conceded"] for m in match_list]
    n             = len(match_list)
    avg_s         = sum(scored_lst)   / n
    avg_c         = sum(conceded_lst) / n
    var_s         = sum((x - avg_s)**2 for x in scored_lst)   / n
    var_c         = sum((x - avg_c)**2 for x in conceded_lst) / n
    return {
        "n_matches":       n,
        "avg_scored":      round(avg_s, 3),
        "avg_conceded":    round(avg_c, 3),
        "stdev_scored":    round(var_s ** 0.5, 3),
        "stdev_conceded":  round(var_c ** 0.5, 3),
        "attack_rating":   round(avg_s / INTL_AVG, 4),
        "defence_rating":  round(avg_c / INTL_AVG, 4),
    }


def main() -> None:
    print("=== Fetching WC team IDs ===")
    team_ids = fetch_wc_team_ids()
    print(f"  Found: {sorted(team_ids)}")
    missing = TARGET_TLAS - set(team_ids)
    if missing:
        print(f"  Not found: {sorted(missing)}")

    print("\n=== Fetching WC 2022 match results ===")
    wc2022 = fetch_wc_matches(2022)
    print(f"  {len(wc2022)} finished matches")

    print("\n=== Fetching WC 2026 match results ===")
    wc2026 = fetch_wc_matches(2026)
    print(f"  {len(wc2026)} finished matches")

    all_wc = wc2022 + wc2026

    results: dict = {}
    coverage: dict = {}

    for tla in sorted(TARGET_TLAS):
        team_id = team_ids.get(tla)
        if not team_id:
            results[tla] = {
                "status":  "unavailable",
                "reason":  "team_id_not_found",
                "matches": [],
                "ratings": None,
            }
            coverage[tla] = "unavailable (team_id not found)"
            continue

        # Filter WC matches involving this team
        parsed: list[dict] = []
        for m in all_wc:
            home_id = (m.get("homeTeam") or {}).get("id")
            away_id = (m.get("awayTeam") or {}).get("id")
            if home_id == team_id or away_id == team_id:
                p = _parse_result(m, team_id)
                if p:
                    parsed.append(p)

        # Teams with limited WC history: supplement from general team matches
        if len(parsed) < 5:
            print(f"  {tla}: {len(parsed)} WC matches — fetching team history …")
            recent = fetch_team_recent_matches(team_id)
            seen_dates = {p["date"] for p in parsed}
            for m in recent:
                comp_type = (m.get("competition") or {}).get("type", "")
                if comp_type not in ("INTERNATIONAL", "CUP"):
                    continue
                p = _parse_result(m, team_id)
                if p and p["date"] not in seen_dates:
                    parsed.append(p)
                    seen_dates.add(p["date"])
            parsed.sort(key=lambda x: x["date"], reverse=True)

        # Take up to 20 most recent
        parsed = parsed[:20]
        ratings = compute_ratings(parsed)
        n = len(parsed)
        status = "full" if n >= 5 else ("limited" if n > 0 else "unavailable")

        results[tla] = {
            "status":  status,
            "team_id": team_id,
            "matches": parsed,
            "ratings": ratings,
        }
        coverage[tla] = f"{n} matches | {status}"

    # Save team results
    out = {
        "fetched_at":    datetime.utcnow().isoformat() + "Z",
        "international_avg_goals": INTL_AVG,
        "teams":         results,
    }
    out_path = DATA_DIR / "team_results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nSaved -> {out_path}")

    # Stat rates: football-data.org free tier does not expose shots/corners/fouls
    stat_rates = {
        "source": "unavailable",
        "reason": "football-data.org free tier does not expose per-match shots/corners/fouls",
        "teams":  {tla: {"shots_on_target": None, "corners": None, "fouls": None}
                   for tla in TARGET_TLAS},
    }
    (DATA_DIR / "team_stat_rates.json").write_text(
        json.dumps(stat_rates, indent=2), encoding="utf-8"
    )

    print("\n=== Per-team data coverage ===")
    for tla, summary in sorted(coverage.items()):
        print(f"  {tla}: {summary}")

    print("\nDone.")


if __name__ == "__main__":
    main()
