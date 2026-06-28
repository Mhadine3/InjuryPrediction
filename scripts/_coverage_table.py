"""
todoF — Final per-team BSD coverage table.
Shows: BSD matches, raw ratings, shrunk ratings, player stats coverage, WC event ID.
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.prematch_service import (
    _TEAM_RESULTS, _PLAYER_STATS, _PLAYERS_BY_TEAM,
    _FALLBACK_RATINGS, _N_RATING_REF, _apply_shrinkage,
    _BSD_TEAM_IDS, _get_wc_events, _find_bsd_event,
)

TEAMS = ["BRA", "MAR", "HAI", "SCO", "FRA", "SEN", "IRQ", "NOR"]

api_stats = _PLAYER_STATS.get("stats") or {}
teams_data = _TEAM_RESULTS.get("teams") or {}

print("\n" + "=" * 100)
print("BSD COVERAGE TABLE — WC 2026 Group C + Group I")
print("=" * 100)

# Header
print(f"\n{'TLA':<5} {'n_mat':<6} {'att_raw':<9} {'att_shrk':<9} {'def_raw':<9} "
      f"{'def_shrk':<9} {'shrink%':<9} {'pl_full':<8} {'pl_0goal':<9} {'source':<12}")
print("-" * 100)

for tla in TEAMS:
    entry   = teams_data.get(tla, {})
    ratings = entry.get("ratings") or _FALLBACK_RATINGS.get(tla, {})
    matches = entry.get("matches") or []
    n       = len(matches)
    status  = entry.get("status", "unavailable")

    att_raw = ratings.get("attack_rating", 1.0)
    def_raw = ratings.get("defence_rating", 1.0)

    if n > 0:
        shrunk = _apply_shrinkage(ratings, n)
        att_shrk = shrunk["attack_rating"]
        def_shrk = shrunk["defence_rating"]
        alpha    = round(min(n, _N_RATING_REF) / _N_RATING_REF * 100)
        src      = f"bsd({n}m)"
    else:
        att_shrk = att_raw
        def_shrk = def_raw
        alpha    = 0
        src      = "fallback"

    # Player stats
    players = _PLAYERS_BY_TEAM.get(tla, [])
    pl_full   = sum(1 for p in players if api_stats.get(p["player_id"], {}).get("status") == "full")
    pl_0goal  = sum(1 for p in players if api_stats.get(p["player_id"], {}).get("status") == "full"
                    and (api_stats.get(p["player_id"], {}).get("goals") or 0) == 0)

    print(f"  {tla:<5} {n:<6} {att_raw:<9.3f} {att_shrk:<9.3f} {def_raw:<9.3f} "
          f"{def_shrk:<9.3f} {alpha:<9} {pl_full:<8} {pl_0goal:<9} {src:<12}  [{status}]")

print()
print("Legend: att=attack_rating (>1=above avg), def=defence_rating (<1=stronger defence),")
print("        shrk%=weight on raw data (100%=no shrinkage), pl_full=BSD full stats,")
print("        pl_0goal=full-status players with 0 goals (non-scorers).")
print()
print(f"Shrinkage: alpha=min(n,{_N_RATING_REF})/{_N_RATING_REF}. "
      f"Ratings are blended: shrunk = alpha*raw + (1-alpha)*1.0")

# NOR detail
print("\n── NOR RATING DETAIL (shrinkage key case) ──────────────────────────────")
nor = teams_data.get("NOR", {})
nor_r = nor.get("ratings", {})
n_nor = len(nor.get("matches", []))
alpha_nor = min(n_nor, _N_RATING_REF) / _N_RATING_REF
print(f"  n_matches = {n_nor}  (only UEFA qual group stage)")
print(f"  raw attack = {nor_r.get('attack_rating')}  (incl. 11-1 vs Moldova, 5-0 vs Moldova)")
print(f"  raw defence = {nor_r.get('defence_rating')}")
print(f"  alpha = min({n_nor},{_N_RATING_REF})/{_N_RATING_REF} = {alpha_nor:.3f}")
shrunk_nor = _apply_shrinkage(nor_r, n_nor)
print(f"  shrunk attack  = {alpha_nor:.3f} * {nor_r.get('attack_rating')} + {1-alpha_nor:.3f} * 1.0 = {shrunk_nor['attack_rating']}")
print(f"  shrunk defence = {alpha_nor:.3f} * {nor_r.get('defence_rating')} + {1-alpha_nor:.3f} * 1.0 = {shrunk_nor['defence_rating']}")

# WC event IDs
print("\n── WC 2026 BSD EVENT IDs (upcoming fixtures) ───────────────────────────")
FIXTURES = [
    ("BRA", "MAR"), ("HAI", "SCO"), ("BRA", "SCO"), ("HAI", "MAR"),
    ("BRA", "HAI"), ("SCO", "MAR"),
    ("FRA", "NOR"), ("SEN", "IRQ"), ("FRA", "SEN"), ("NOR", "IRQ"),
    ("FRA", "IRQ"), ("NOR", "SEN"),
]
found = 0
for home, away in FIXTURES:
    eid = _find_bsd_event(home, away)
    status = f"event_id={eid}" if eid else "NOT FOUND"
    print(f"  {home} vs {away:<5}: {status}")
    if eid:
        found += 1

print(f"\n  {found}/{len(FIXTURES)} fixtures found in BSD WC 2026 events")

# Player stats summary
print("\n── PLAYER STATS SUMMARY ────────────────────────────────────────────────")
total_players = sum(len(_PLAYERS_BY_TEAM.get(t, [])) for t in TEAMS)
total_full    = sum(
    sum(1 for p in _PLAYERS_BY_TEAM.get(t, []) if api_stats.get(p["player_id"], {}).get("status") == "full")
    for t in TEAMS
)
print(f"  Total squad players tracked: {total_players}")
print(f"  Players with BSD full stats: {total_full} ({100*total_full//total_players}%)")
print(f"  Scorer source: bsd_national_team_stats (career goals from BSD-tracked competitions)")
print(f"  Shots/corners/fouls: not available at BSD team-match level")

print("\n" + "=" * 100)
print("COVERAGE TABLE DONE")
print("=" * 100)
