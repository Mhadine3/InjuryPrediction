"""
Final todoF acceptance: run compute_prematch for France vs Senegal,
print the full report including market odds, scorer list, coverage table.
"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.prematch_service import compute_prematch

result = compute_prematch("FRA", "SEN", "2026-06-16_FRA_SEN")

print("=" * 70)
print("FRA vs SEN — Pre-match prediction report (todoF)")
print("=" * 70)

print("\n── OUTCOME PROBABILITIES ─────────────────────────────────────────")
op = result["outcome_probabilities"]
print(f"  FRA win : {op['home_win']:.1%}")
print(f"  Draw    : {op['draw']:.1%}")
print(f"  SEN win : {op['away_win']:.1%}")
print(f"  Model   : {op['model']}")

print("\n── MARKET ODDS (BSD Bzzoiro Consensus) ───────────────────────────")
mo = result["market_odds"]
if mo.get("status") == "unavailable":
    print(f"  UNAVAILABLE: {mo.get('reason')}")
else:
    if "1x2" in mo:
        x12 = mo["1x2"]
        print(f"  1x2  decimal: FRA={x12['decimal_odds'].get('home_win')}  "
              f"Draw={x12['decimal_odds'].get('draw')}  "
              f"SEN={x12['decimal_odds'].get('away_win')}")
        print(f"  1x2  implied: FRA={x12['implied'].get('home_win'):.1%}  "
              f"Draw={x12['implied'].get('draw'):.1%}  "
              f"SEN={x12['implied'].get('away_win'):.1%}  "
              f"margin={x12['bookmaker_margin']:.1%}")
        print(f"  1x2  fair   : FRA={x12['fair'].get('home_win'):.1%}  "
              f"Draw={x12['fair'].get('draw'):.1%}  "
              f"SEN={x12['fair'].get('away_win'):.1%}")
    if "over_under_25" in mo:
        ou = mo["over_under_25"]
        print(f"  O/U 2.5: over={ou['fair'].get('over'):.1%}  under={ou['fair'].get('under'):.1%}")
    if "btts" in mo:
        bt = mo["btts"]
        print(f"  BTTS   : yes={bt['fair'].get('yes'):.1%}  no={bt['fair'].get('no'):.1%}")
    if "model_vs_market" in mo:
        print("\n  Model vs Market:")
        for label, rec in mo["model_vs_market"].items():
            conf_adj = rec.get('model_conf_adj', '?')
            blended  = rec.get('model_blended', '?')
            mkt      = rec.get('market_fair', '?')
            gap      = rec.get('gap_blended_vs_market', 0)
            print(f"    {label:<12}: conf_adj={conf_adj:.1%}  blended={blended:.1%}  "
                  f"market={mkt:.1%}  gap={gap:+.1%}")

print("\n── EXPECTED GOALS ────────────────────────────────────────────────")
eg = result["expected_goals"]
print(f"  FRA xG: {eg['home']['mean']:.2f}  [{eg['home']['low']:.2f}–{eg['home']['high']:.2f}]")
print(f"  SEN xG: {eg['away']['mean']:.2f}  [{eg['away']['low']:.2f}–{eg['away']['high']:.2f}]")
print(f"  {eg['note']}")

print("\n── MOST LIKELY SCORE ─────────────────────────────────────────────")
mls = result["most_likely_score"]
print(f"  {mls['home']}-{mls['away']}  (p={mls['probability']:.1%})")

print("\n── TOP SCORERS — FRA ─────────────────────────────────────────────")
for p in result["top_scorers"]["home"]:
    src = "BSD" if p["data_source"] == "bsd_national_team_stats" else p["data_source"]
    goals = p.get("intl_goals_bsd") if p.get("intl_goals_bsd") else p.get("intl_goals_baseline")
    print(f"  {p['name']:<25} {p['position']:<15} "
          f"P(goal)={p['p_scores_one_or_more']:.1%}  "
          f"xG={p['expected_goals']:.3f}  goals={goals}  [{src}]")

print("\n── TOP SCORERS — SEN ─────────────────────────────────────────────")
for p in result["top_scorers"]["away"]:
    src = "BSD" if p["data_source"] == "bsd_national_team_stats" else p["data_source"]
    goals = p.get("intl_goals_bsd") if p.get("intl_goals_bsd") else p.get("intl_goals_baseline")
    print(f"  {p['name']:<25} {p['position']:<15} "
          f"P(goal)={p['p_scores_one_or_more']:.1%}  "
          f"xG={p['expected_goals']:.3f}  goals={goals}  [{src}]")

print("\n── TEAM STAT PROFILE ─────────────────────────────────────────────")
for side, tla in [("FRA", result["home_team"]), ("SEN", result["away_team"])]:
    sp = result["team_stat_profile"][("home" if side == "FRA" else "away")]
    sot = sp["shots_on_target"]
    c   = sp["corners"]
    f   = sp["fouls"]
    print(f"  {side}: shots_on_target={sot.get('expected','unavail')}  "
          f"corners={c.get('expected','unavail')}  fouls={f.get('expected','unavail')}")

print("\n── DATA COVERAGE ─────────────────────────────────────────────────")
cov = result["data_coverage"]
for side in ["home", "away"]:
    c = cov[side]
    print(f"  {c['team']} ({c.get('confederation','?')}): results={c['results_history']}  "
          f"source={c['rating_source']}  status={c['rating_status']}")
    print(f"        shrinkage={c.get('shrinkage','?')}  "
          f"conf_att_factor={c.get('conf_attack_factor','?')}  "
          f"conf_def_factor={c.get('conf_defence_factor','?')}")
    print(f"        players={c['player_stats']}  player_src={c['player_stat_source']}")

print("\n── MODEL NOTES ───────────────────────────────────────────────────")
for note in result["model_notes"]:
    print(f"  • {note}")

print("\n" + "=" * 70)
print("DONE")
