"""
bsd_backfill.py
===============
One-time BSD (Bzzoiro Sports Data) backfill for the prematch model.

Fetches and overwrites:
  data/team_results.json       -- results history + attack/defence ratings
  data/player_scoring_stats.json -- per-player goals/shots/minutes

Coverage sources per team:
  BRA  : WC 2022 (league 27) + CONMEBOL qual (league 59)
  FRA  : WC 2022 (league 27) + UEFA qual (league 58)
  MAR  : WC 2022 (league 27) + CAF qual (league 60) + AFCON (league 30)
  SEN  : WC 2022 (league 27) + CAF qual (league 60) + AFCON (league 30)
  SCO  : WC 2022 (league 27) + UEFA qual (league 58)
  NOR  : UEFA qual (league 58)
  IRQ  : AFC qual (league 61) + AFC Asian Cup (league 68)
  HAI  : CONCACAF qual (league 62)

BSD does NOT expose shots/corners/fouls at team-match level.
  -> team_stat_rates stay unavailable for shots/corners/fouls (marked honestly).
  -> Shots-on-target are available at PLAYER level and aggregated per match.

Auth: BSD_TOKEN from root .env
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── Env loading ───────────────────────────────────────────────────────────────

def _load_env(p: Path) -> dict:
    env = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

_env = {**_load_env(ROOT / ".env"), **_load_env(ROOT / "backend" / ".env")}
BSD_TOKEN = _env.get("BSD_TOKEN", "")
if not BSD_TOKEN:
    sys.exit("BSD_TOKEN not set in .env")

BASE = "https://sports.bzzoiro.com/api/v2"
HDR  = {"Authorization": f"Token {BSD_TOKEN}"}
DATA = ROOT / "data"

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

# ── Constants ─────────────────────────────────────────────────────────────────

INTL_AVG = 1.35

# BSD national team IDs (confirmed from probe)
BSD_TEAM_IDS: dict[str, int] = {
    "BRA": 463, "MAR": 464, "HAI": 465, "SCO": 466,
    "FRA": 485, "SEN": 486, "IRQ": 933, "NOR": 488,
}
BSD_TEAM_NAMES: dict[str, str] = {
    "BRA": "brazil",   "MAR": "morocco", "HAI": "haiti",    "SCO": "scotland",
    "FRA": "france",   "SEN": "senegal", "IRQ": "iraq",     "NOR": "norway",
}

# Leagues to search for each team (in priority order)
TEAM_LEAGUES: dict[str, list[int]] = {
    "BRA": [27, 59],        # WC2026/WC2022, CONMEBOL qual
    "FRA": [27, 58],        # WC2026/WC2022, UEFA qual
    "MAR": [27, 60, 30],   # WC2026/WC2022, CAF qual, AFCON
    "SEN": [27, 60, 30],   # WC2026/WC2022, CAF qual, AFCON
    "SCO": [27, 58],        # WC2026/WC2022, UEFA qual
    "NOR": [27, 58],        # WC2026/WC2022, UEFA qual
    "IRQ": [27, 61, 68],   # WC2026/WC2022, AFC qual, AFC Asian Cup
    "HAI": [27, 62],        # WC2026/WC2022, CONCACAF qual
}

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None) -> tuple[int, dict]:
    url = path if path.startswith("http") else f"{BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, headers=HDR, params=params, timeout=15)
        time.sleep(0.3)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {}
    except Exception as exc:
        print(f"  NETWORK ERROR: {exc}")
        return 0, {}


def _get_all(path: str, params: dict | None = None, max_pages: int = 8) -> list:
    results = []
    url = f"{BASE}/{path.lstrip('/')}"
    p = {**(params or {}), "limit": 100}
    for _ in range(max_pages):
        sc, data = _get(url, p)
        if sc != 200:
            break
        results.extend(data.get("results", []))
        nxt = data.get("next")
        if not nxt:
            break
        url = nxt
        p = {}
    return results


# ── Step 1: Results history ───────────────────────────────────────────────────

def fetch_results_for_team(tla: str) -> list[dict]:
    """Fetch finished matches for a team from its relevant leagues, filter by name."""
    team_name = BSD_TEAM_NAMES[tla]
    leagues   = TEAM_LEAGUES[tla]
    parsed: list[dict] = []
    seen_ids: set[int] = set()

    for lid in leagues:
        events = _get_all("/events/", {"league_id": lid, "status": "finished"})
        for ev in events:
            eid = ev.get("id")
            if eid in seen_ids:
                continue
            ht = (ev.get("home_team") or "").lower()
            at = (ev.get("away_team") or "").lower()
            if team_name not in ht and team_name not in at:
                continue
            seen_ids.add(eid)

            h_score = ev.get("home_score")
            a_score = ev.get("away_score")
            if h_score is None or a_score is None:
                continue

            if team_name in ht:
                scored, conceded = h_score, a_score
                venue = "home"
                opp   = ev.get("away_team", "?")
            else:
                scored, conceded = a_score, h_score
                venue = "away"
                opp   = ev.get("home_team", "?")

            parsed.append({
                "bsd_event_id": eid,
                "league_id":    lid,
                "date":         (ev.get("event_date") or "")[:10],
                "opponent":     opp,
                "venue":        venue,
                "scored":       scored,
                "conceded":     conceded,
                "result":       "W" if scored > conceded else ("D" if scored == conceded else "L"),
            })

    parsed.sort(key=lambda x: x["date"], reverse=True)
    return parsed[:25]  # cap at 25 most recent


def compute_ratings(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    s = [m["scored"]   for m in matches]
    c = [m["conceded"] for m in matches]
    n = len(matches)
    avg_s = sum(s) / n
    avg_c = sum(c) / n
    var_s = sum((x - avg_s) ** 2 for x in s) / n
    var_c = sum((x - avg_c) ** 2 for x in c) / n
    return {
        "n_matches":       n,
        "avg_scored":      round(avg_s, 3),
        "avg_conceded":    round(avg_c, 3),
        "stdev_scored":    round(var_s ** 0.5, 3),
        "stdev_conceded":  round(var_c ** 0.5, 3),
        "attack_rating":   round(avg_s / INTL_AVG, 4),
        "defence_rating":  round(avg_c / INTL_AVG, 4),
    }


# ── Step 2: Player stats ──────────────────────────────────────────────────────

def fetch_player_stats_for_team(tla: str) -> tuple[list[dict], dict]:
    """
    Fetch BSD player list + per-player career stats for a national team.
    Returns (bsd_players, coverage_info).
    """
    team_id  = BSD_TEAM_IDS[tla]
    players  = _get_all("/players/", {"national_team_id": team_id})
    print(f"    {tla}: {len(players)} BSD players found")

    enriched: list[dict] = []
    n_with_stats = 0

    for p in players:
        pid   = p["id"]
        name  = p.get("name", "?")
        pos   = p.get("position", "?")
        spec  = p.get("specific_position", "?")
        nat   = p.get("nationality", "?")

        sc, pdata = _get(f"/players/{pid}/stats/")
        goals = shots = sot = minutes = apps = 0
        has_real_stats = False

        if sc == 200:
            for entry in pdata.get("results", []):
                goals   += entry.get("goals", 0) or 0
                shots   += entry.get("total_shots", 0) or 0
                sot     += entry.get("shots_on_target", 0) or 0
                minutes += entry.get("minutes_played", 0) or 0
                if entry.get("minutes_played", 0) and entry.get("minutes_played", 0) > 0:
                    apps += 1
                    has_real_stats = True

        if has_real_stats:
            n_with_stats += 1

        enriched.append({
            "bsd_id":       pid,
            "name":         name,
            "position":     pos,
            "specific_pos": spec,
            "nationality":  nat,
            "goals":        goals,
            "shots":        shots,
            "shots_on_target": sot,
            "minutes_played":  minutes,
            "appearances":     apps,
            "has_real_stats":  has_real_stats,
        })

    coverage = {
        "total_bsd_players":  len(players),
        "with_real_stats":    n_with_stats,
        "note": (f"{n_with_stats}/{len(players)} players have non-zero match stats" if players
                 else "no players found via national_team_id"),
    }
    return enriched, coverage


# ── Step 3: Name matching ─────────────────────────────────────────────────────

def _normalise(name: str) -> str:
    import unicodedata
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.lower().strip()


def match_players(baseline_players: list[dict], bsd_players: list[dict]) -> dict[str, dict]:
    """
    Match baseline player IDs to BSD player stats by name similarity.
    Returns {baseline_player_id -> bsd_stats_entry or None}.
    """
    bsd_index: dict[str, dict] = {}
    for bp in bsd_players:
        key = _normalise(bp["name"])
        bsd_index[key] = bp
        # Also index by last name
        parts = key.split()
        if parts:
            bsd_index[parts[-1]] = bp

    matched: dict[str, dict] = {}
    for p in baseline_players:
        pid    = p["player_id"]
        norm   = _normalise(p["name"])
        parts  = norm.split()

        hit = bsd_index.get(norm)
        if not hit and parts:
            hit = bsd_index.get(parts[-1])
        if not hit and len(parts) > 1:
            hit = bsd_index.get(parts[0])

        matched[pid] = hit
    return matched


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    baseline_raw = json.loads((DATA / "players_baseline.json").read_text(encoding="utf-8"))
    baseline_by_team: dict[str, list[dict]] = {}
    for p in baseline_raw["players"]:
        baseline_by_team.setdefault(p["team_code"], []).append(p)

    team_results:       dict = {}
    player_stats_out:   dict = {}
    player_coverage:    dict = {}
    results_coverage:   dict = {}

    # ── Results ────────────────────────────────────────────────────────────────
    print("=== RESULTS HISTORY BACKFILL ===")
    for tla in BSD_TEAM_IDS:
        print(f"\n  {tla}:")
        matches = fetch_results_for_team(tla)
        ratings = compute_ratings(matches)
        n = len(matches)
        status  = "full" if n >= 5 else ("limited" if n > 0 else "unavailable")

        # Source breakdown
        league_counts: dict[int, int] = {}
        for m in matches:
            league_counts[m["league_id"]] = league_counts.get(m["league_id"], 0) + 1
        league_desc = ", ".join(f"league_{lid}:{cnt}" for lid, cnt in sorted(league_counts.items()))

        team_results[tla] = {
            "status":        status,
            "bsd_team_id":   BSD_TEAM_IDS[tla],
            "matches":       matches,
            "ratings":       ratings,
            "sources":       league_desc,
        }
        results_coverage[tla] = (
            f"{n} matches ({league_desc}) | {status}"
            if n > 0 else "unavailable"
        )
        print(f"    {n} matches | {status} | {league_desc}")
        if ratings:
            print(f"    attack={ratings['attack_rating']:.3f}  defence={ratings['defence_rating']:.3f}"
                  f"  avg_scored={ratings['avg_scored']:.2f}  avg_conceded={ratings['avg_conceded']:.2f}")

    # ── Player stats ──────────────────────────────────────────────────────────
    print("\n=== PLAYER STATS BACKFILL ===")
    for tla in BSD_TEAM_IDS:
        print(f"\n  {tla}:")
        bsd_players, cov = fetch_player_stats_for_team(tla)
        baseline_players  = baseline_by_team.get(tla, [])
        matches_map       = match_players(baseline_players, bsd_players)

        n_matched = sum(1 for v in matches_map.values() if v is not None)
        print(f"    {n_matched}/{len(baseline_players)} baseline players matched to BSD")

        for pid, bsd in matches_map.items():
            if bsd and bsd["has_real_stats"]:
                player_stats_out[pid] = {
                    "status":           "full",
                    "source":           "bsd_national_team_stats",
                    "bsd_id":           bsd["bsd_id"],
                    "goals":            bsd["goals"],
                    "shots":            bsd["shots"],
                    "shots_on_target":  bsd["shots_on_target"],
                    "minutes_played":   bsd["minutes_played"],
                    "appearances":      bsd["appearances"],
                    "goals_per90":      round(bsd["goals"] / bsd["minutes_played"] * 90, 4)
                                        if bsd["minutes_played"] > 0 else 0.0,
                    "shots_per90":      round(bsd["shots"] / bsd["minutes_played"] * 90, 4)
                                        if bsd["minutes_played"] > 0 else 0.0,
                }
            elif bsd:
                player_stats_out[pid] = {
                    "status":  "limited",
                    "source":  "bsd_national_team_stats",
                    "bsd_id":  bsd["bsd_id"],
                    "reason":  "player found in BSD but all match stats are zero",
                }
            else:
                player_stats_out[pid] = {
                    "status":  "unavailable",
                    "reason":  "no BSD match for this player",
                }

        cov["matched_baseline"] = n_matched
        player_coverage[tla] = cov

    # ── Save outputs ──────────────────────────────────────────────────────────
    print("\n=== SAVING FILES ===")
    now = datetime.now(timezone.utc).isoformat()

    # team_results.json
    results_out = {
        "fetched_at":              now,
        "source":                  "bsd_bzzoiro",
        "international_avg_goals": INTL_AVG,
        "teams":                   team_results,
    }
    (DATA / "team_results.json").write_text(
        json.dumps(results_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Saved data/team_results.json")

    # player_scoring_stats.json
    stats_out = {
        "fetched_at": now,
        "source":     "bsd_bzzoiro",
        "coverage":   player_coverage,
        "stats":      player_stats_out,
    }
    (DATA / "player_scoring_stats.json").write_text(
        json.dumps(stats_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Saved data/player_scoring_stats.json")

    # team_stat_rates.json — shots/corners/fouls not exposed by BSD
    stat_rates = {
        "source":     "unavailable",
        "reason":     (
            "BSD /events/{id}/stats/ exposes attack/dangerous_attack counts but NOT "
            "shots-on-target, corners, or fouls at team level. "
            "Player-level shots_on_target are aggregated in player_scoring_stats.json."
        ),
        "corners_available": False,
        "fouls_available":   False,
        "shots_on_target_available": "player_level_only",
        "teams": {tla: {"shots_on_target": None, "corners": None, "fouls": None}
                  for tla in BSD_TEAM_IDS},
    }
    (DATA / "team_stat_rates.json").write_text(
        json.dumps(stat_rates, indent=2), encoding="utf-8"
    )
    print(f"  Saved data/team_stat_rates.json")

    # ── Coverage report ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PER-TEAM COVERAGE REPORT")
    print("=" * 70)
    print(f"\n{'Team':<8} {'Results':<50} {'Players matched'}")
    print("-" * 80)
    for tla in BSD_TEAM_IDS:
        res_note   = results_coverage.get(tla, "?")
        pc         = player_coverage.get(tla, {})
        pl_note    = f"{pc.get('matched_baseline','?')} baseline matched, {pc.get('with_real_stats','?')} with non-zero stats"
        print(f"  {tla:<6} {res_note[:50]:<50} {pl_note}")

    print(f"\n{'Stat type':<25} {'Available?'}")
    print("-" * 45)
    for stat, status in [
        ("Results history",       "✓  (WC 2022 + qual leagues)"),
        ("Player goals (intl)",   "✓  (BSD /players/{id}/stats/)"),
        ("Player shots",          "✓  (BSD /players/{id}/stats/)"),
        ("Player xG",             "✗  (BSD xg fields are null)"),
        ("Team shots-on-target",  "✗  (team-level not exposed; player-level only)"),
        ("Corners",               "✗  NOT AVAILABLE in BSD"),
        ("Fouls",                 "✗  NOT AVAILABLE in BSD"),
        ("1x2 odds",              "✓  (BSD /odds/?event_id=)"),
        ("over/under odds",       "✓  (over_under_25 available)"),
        ("BTTS odds",             "✓  (btts market)"),
    ]:
        print(f"  {stat:<25} {status}")

    print("\nDone.")


if __name__ == "__main__":
    main()
