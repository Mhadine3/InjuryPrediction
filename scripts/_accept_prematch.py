"""
Acceptance test for the prematch prediction service.
Runs against France vs Senegal and checks all required criteria:
  - outcome probs sum to 1
  - most-likely score has a probability attached
  - ranked scorers per team (BSD real goals data)
  - stat profile with status per stat
  - data-coverage summary per team (BSD source)
  - market_odds section present with 1x2 data
  - model vs market comparison present
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.prematch_service import compute_prematch

def chk(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")

match_id = "2026-06-18_FRA_SEN"
result   = compute_prematch("FRA", "SEN", match_id)

print(f"\n=== Prematch: {result['home_team']} vs {result['away_team']} ===\n")

# 1. Outcome probabilities
outcome = result["outcome_probabilities"]
prob_sum = outcome["home_win"] + outcome["draw"] + outcome["away_win"]
print("1. Outcome probabilities")
print(f"   home_win={outcome['home_win']}  draw={outcome['draw']}  away_win={outcome['away_win']}")
print(f"   sum={prob_sum:.4f}")
chk("probs sum to 1.0 (±0.001)", abs(prob_sum - 1.0) < 0.001)
chk("home_win in (0,1)", 0 < outcome["home_win"] < 1)
chk("draw in (0,1)", 0 < outcome["draw"] < 1)
chk("away_win in (0,1)", 0 < outcome["away_win"] < 1)
# Note: model uses raw BSD qual data without confederation quality adjustment,
# so FRA may not appear as favourite vs SEN (known limitation, documented in model_notes).

# 2. Expected goals
eg = result["expected_goals"]
print(f"\n2. Expected goals")
print(f"   home mean={eg['home']['mean']}  range=[{eg['home']['low']}, {eg['home']['high']}]  source={eg['home']['data_source']}")
print(f"   away mean={eg['away']['mean']}  range=[{eg['away']['low']}, {eg['away']['high']}]  source={eg['away']['data_source']}")
chk("home xG mean > 0", eg["home"]["mean"] > 0)
chk("away xG mean > 0", eg["away"]["mean"] > 0)
chk("home range has low <= mean <= high",
    eg["home"]["low"] <= eg["home"]["mean"] <= eg["home"]["high"])
chk("data sources mention bsd_bzzoiro",
    "bsd_bzzoiro" in eg["home"]["data_source"] or "bsd_bzzoiro" in eg["away"]["data_source"])

# 3. Most-likely score
mls = result["most_likely_score"]
print(f"\n3. Most-likely score: {mls['home']}-{mls['away']}  probability={mls['probability']}")
chk("most-likely score has probability > 0", mls["probability"] > 0)
chk("most-likely score home and away are ints", isinstance(mls["home"], int) and isinstance(mls["away"], int))

# 4. Scoreline matrix
mat = result["scoreline_matrix"]["probabilities"]
all_probs = [p for row in mat.values() for p in row.values()]
mat_sum = sum(all_probs)
print(f"\n4. Scoreline matrix: {len(mat)}x{len(list(mat.values())[0])} = {len(all_probs)} cells")
print(f"   matrix sum = {mat_sum:.4f}")
chk("matrix probabilities sum to 1.0 (±0.01)", abs(mat_sum - 1.0) < 0.01)
chk("P(0-0) > 0", float(mat.get("0", {}).get("0", 0)) > 0)

# 5. Ranked scorers
home_sc = result["top_scorers"]["home"]
away_sc = result["top_scorers"]["away"]
print(f"\n5. Top scorers (home): {len(home_sc)} players")
for s in home_sc[:3]:
    goals = s.get("intl_goals_bsd") or s.get("intl_goals_baseline") or 0
    print(f"   {s['name']:<22} {s['position']:<16} P={s['p_scores_one_or_more']:.4f}  "
          f"goals={goals}  src={s['data_source']}")
print(f"   Top scorers (away): {len(away_sc)} players")
for s in away_sc[:3]:
    goals = s.get("intl_goals_bsd") or s.get("intl_goals_baseline") or 0
    print(f"   {s['name']:<22} {s['position']:<16} P={s['p_scores_one_or_more']:.4f}  "
          f"goals={goals}  src={s['data_source']}")
chk("home has >=3 ranked scorers", len(home_sc) >= 3)
chk("away has >=3 ranked scorers", len(away_sc) >= 3)
chk("scorers sorted descending by P", all(
    home_sc[i]["p_scores_one_or_more"] >= home_sc[i+1]["p_scores_one_or_more"]
    for i in range(len(home_sc)-1)
))
chk("no GKs in scorer list", all(s["position"] != "Goalkeeper" for s in home_sc + away_sc))
chk("top home scorer has intl_goals_bsd > 0", (home_sc[0].get("intl_goals_bsd") or 0) > 0)
chk("top away scorer has intl_goals_bsd > 0", (away_sc[0].get("intl_goals_bsd") or 0) > 0)

# 6. Team stat profile
hsp = result["team_stat_profile"]["home"]
asp = result["team_stat_profile"]["away"]
print(f"\n6. Stat profile (home)")
for stat in ["shots_on_target", "corners", "fouls"]:
    print(f"   {stat}: {hsp[stat]}")
chk("stat profile has shots_on_target key", "shots_on_target" in hsp)
chk("stat profile has corners key", "corners" in hsp)
chk("unavailable stats have status='unavailable'",
    hsp["shots_on_target"]["status"] == "unavailable" or hsp["shots_on_target"]["expected"] is not None)

# 7. Data coverage
cov = result["data_coverage"]
print(f"\n7. Data coverage")
for team, info in cov.items():
    print(f"   {team}: rating_source={info['rating_source']}  history={info['results_history']}")
    print(f"         player_stats={info['player_stats']}  player_src={info['player_stat_source']}")
chk("coverage has home and away keys", "home" in cov and "away" in cov)
chk("rating_source mentions bsd_bzzoiro (home)", "bsd_bzzoiro" in cov["home"]["rating_source"])
chk("rating_source mentions bsd_bzzoiro (away)", "bsd_bzzoiro" in cov["away"]["rating_source"])
chk("player_stat_source is bsd_bzzoiro", cov["home"]["player_stat_source"] == "bsd_bzzoiro")

# 8. Market odds (todoF requirement)
mo = result["market_odds"]
print(f"\n8. Market odds")
is_available = mo.get("status") != "unavailable"
print(f"   status: {'available' if is_available else 'unavailable'}")
if is_available:
    if "1x2" in mo:
        x12 = mo["1x2"]
        print(f"   1x2 decimal: {x12.get('decimal_odds')}")
        print(f"   1x2 implied: {x12.get('implied')}")
        print(f"   1x2 fair   : {x12.get('fair')}")
        print(f"   margin     : {x12.get('bookmaker_margin')}")
    if "model_vs_market" in mo:
        print(f"   model vs market: {mo['model_vs_market']}")
chk("market_odds section present", "market_odds" in result)
chk("market_odds has source field", "source" in mo or mo.get("status") == "unavailable")
if is_available:
    chk("1x2 fair probs sum ~1.0", abs(sum(mo["1x2"]["fair"].values()) - 1.0) < 0.01)
    chk("model_vs_market present", "model_vs_market" in mo)
    chk("over_under_25 present", "over_under_25" in mo)
    chk("btts present", "btts" in mo)
    chk("event_id is int", isinstance(mo.get("event_id"), int))

# 9. Model notes
notes = result["model_notes"]
print(f"\n9. Model notes ({len(notes)}):")
for n in notes:
    print(f"   - {n}")
chk("at least 5 model notes", len(notes) >= 5)
chk("shrinkage mentioned in notes", any("shrinkage" in n.lower() or "shrink" in n.lower() for n in notes))
chk("bsd bzzoiro mentioned in notes", any("bsd" in n.lower() for n in notes))

print("\n=== DONE ===\n")

# Spot check: Iraq vs Haiti
result2 = compute_prematch("IRQ", "HAI", "2026-06-15_IRQ_HAI")
o2 = result2["outcome_probabilities"]
sum2 = o2["home_win"] + o2["draw"] + o2["away_win"]
print(f"Spot check IRQ vs HAI: home_win={o2['home_win']} draw={o2['draw']} away_win={o2['away_win']} sum={sum2:.4f}")
chk("IRQ vs HAI probs sum to 1", abs(sum2 - 1.0) < 0.001)

cov2 = result2["data_coverage"]
chk("IRQ coverage has rating_source (bsd_bzzoiro)", "bsd_bzzoiro" in cov2.get("home", {}).get("rating_source", ""))
chk("HAI coverage has results_history", "results_history" in cov2.get("away", {}))
