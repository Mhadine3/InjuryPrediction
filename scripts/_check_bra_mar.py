import sys
sys.path.insert(0, "backend")
from app.services.prematch_service import compute_prematch

result = compute_prematch("BRA", "MAR", "2026-06-13_BRA_MAR")
print("BRA vs MAR scorers (home=BRA):")
for s in result["top_scorers"]["home"]:
    print(f"  {s['name']:<22} {s['position']:<16} P={s['p_scores_one_or_more']:.4f}  goals={s['intl_goals']}  src={s['data_source']}")

print("\nCoverage:")
for team, info in result["data_coverage"].items():
    print(f"  {info['team']}: {info['rating_source']} | {info['player_stats']}")
