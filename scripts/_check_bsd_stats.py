import json
from pathlib import Path
from collections import Counter

d = json.loads(Path("data/player_scoring_stats.json").read_text(encoding="utf-8"))
stats = d["stats"]

# Per-team breakdown
bl = json.loads(Path("data/players_baseline.json").read_text(encoding="utf-8"))
by_team = {}
for p in bl["players"]:
    by_team.setdefault(p["team_code"], []).append(p["player_id"])

print("=== BSD player stats coverage ===")
for tla in ["BRA","MAR","HAI","SCO","FRA","SEN","IRQ","NOR"]:
    pids = by_team.get(tla, [])
    n_full    = sum(1 for pid in pids if stats.get(pid,{}).get("status") == "full")
    n_limited = sum(1 for pid in pids if stats.get(pid,{}).get("status") == "limited")
    n_unavail = sum(1 for pid in pids if stats.get(pid,{}).get("status") == "unavailable")
    # Top scorers
    scorers = sorted(
        [(pid, stats.get(pid,{})) for pid in pids if stats.get(pid,{}).get("status") == "full"],
        key=lambda x: x[1].get("goals",0), reverse=True
    )
    scorer_names = []
    for pid, st in scorers[:3]:
        name = next((p["name"] for p in bl["players"] if p["player_id"] == pid), pid)
        scorer_names.append(f"{name}({st.get('goals','?')}g/{st.get('minutes_played','?')}min)")
    print(f"  {tla}: full={n_full}  limited={n_limited}  unavailable={n_unavail}  top={scorer_names}")
