import json
from pathlib import Path

d = json.loads(Path("data/team_results.json").read_text(encoding="utf-8"))
print("=== NOR matches ===")
nor = d["teams"]["NOR"]
print("ratings:", nor["ratings"])
for m in nor["matches"]:
    print(f"  {m['date']}  {m['venue']:<5}  vs {m['opponent']:<30}  {m['scored']}-{m['conceded']}  {m['result']}  lid={m['league_id']}")

print("\n=== BRA matches ===")
for m in d["teams"]["BRA"]["matches"][:5]:
    print(f"  {m['date']}  {m['venue']:<5}  vs {m['opponent']:<30}  {m['scored']}-{m['conceded']}  {m['result']}")

print("\n=== HAI matches ===")
for m in d["teams"]["HAI"]["matches"]:
    print(f"  {m['date']}  {m['venue']:<5}  vs {m['opponent']:<30}  {m['scored']}-{m['conceded']}  {m['result']}")

print("\n=== All team ratings ===")
for tla, t in sorted(d["teams"].items()):
    r = t.get("ratings") or {}
    print(f"  {tla}: n={r.get('n_matches')} attack={r.get('attack_rating')} defence={r.get('defence_rating')} avg_s={r.get('avg_scored')} avg_c={r.get('avg_conceded')}")
