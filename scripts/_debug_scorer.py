"""Debug exactly what _scorer_probs returns for FRA."""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0].parent / "backend"))
from app.services.prematch_service import _scorer_probs, _PLAYER_STATS, _PLAYERS_BY_TEAM

# Show FRA player stats lookup for first 5 outfield players
api_stats = _PLAYER_STATS.get("stats") or {}
fra_players = _PLAYERS_BY_TEAM.get("FRA", [])

print("=== FRA outfield player lookup (first 10) ===")
count = 0
for p in fra_players:
    if p.get("position") == "GK":
        continue
    pid = p["player_id"]
    ps = api_stats.get(pid, {})
    print(f"  {p['name']:<25} pid={pid:<38} status={ps.get('status','NOT_FOUND'):<12} bsd_goals={ps.get('goals')} baseline={p.get('goals')}")
    count += 1
    if count >= 10:
        break

print()
print("=== compute_prematch FRA scorer output ===")
result = _scorer_probs("FRA", 0.78)
for r in result:
    print(f"  {r['name']:<25} src={r['data_source']:<25} bsd_goals={r.get('intl_goals_bsd')} baseline={r['intl_goals_baseline']}")

print()
print("=== Top 3 FRA players by BSD goals ===")
fra_with_goals = [(p["player_id"], api_stats.get(p["player_id"], {})) for p in fra_players if p.get("position") != "GK"]
fra_with_goals.sort(key=lambda x: x[1].get("goals", 0), reverse=True)
for pid, st in fra_with_goals[:5]:
    name = next((p["name"] for p in fra_players if p["player_id"] == pid), pid)
    print(f"  {name:<25} goals={st.get('goals')} status={st.get('status')}")
