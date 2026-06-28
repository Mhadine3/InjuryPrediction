import json
from pathlib import Path

stats_data = json.loads(Path("data/player_scoring_stats.json").read_text(encoding="utf-8"))
stats = stats_data["stats"]
bl = json.loads(Path("data/players_baseline.json").read_text(encoding="utf-8"))

fra_players = [p for p in bl["players"] if p["team_code"] == "FRA"][:6]
sen_players = [p for p in bl["players"] if p["team_code"] == "SEN"][:5]

print("=== FRA player IDs and stats lookup ===")
for p in fra_players:
    pid = p["player_id"]
    st = stats.get(pid, {})
    print(f"  {p['name']:<25} pid={pid:<35} status={st.get('status','NOT_FOUND'):<12} goals={st.get('goals')} baseline_goals={p.get('goals')}")

print()
print("=== SEN player IDs and stats lookup ===")
for p in sen_players:
    pid = p["player_id"]
    st = stats.get(pid, {})
    print(f"  {p['name']:<25} pid={pid:<35} status={st.get('status','NOT_FOUND'):<12} goals={st.get('goals')} baseline_goals={p.get('goals')}")

print()
print("=== First 10 keys in stats dict ===")
for k in list(stats.keys())[:10]:
    st = stats[k]
    print(f"  key={k:<40} status={st.get('status'):<12} goals={st.get('goals')}")

print()
total = len(stats)
full  = sum(1 for v in stats.values() if v.get("status") == "full")
print(f"Total stats entries: {total}  full={full}")
