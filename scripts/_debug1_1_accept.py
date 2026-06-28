"""
debug1.1 acceptance — two fixtures side by side.
1. FRA vs SEN: France must be clearly favoured.
2. BRA vs MAR: genuinely close — confirm favourites are not hardcoded.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.prematch_service import compute_prematch

def chk(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

def summarise(home, away, r):
    op = r["outcome_probabilities"]
    eg = r["expected_goals"]
    mls = r["most_likely_score"]
    sc_h = r["top_scorers"]["home"][0]
    sc_a = r["top_scorers"]["away"][0]
    mo = r["market_odds"]

    print(f"\n{'='*65}")
    print(f"  {home} vs {away}")
    print(f"{'='*65}")
    print(f"  Outcome : {home} {op['home_win']:.1%}  Draw {op['draw']:.1%}  {away} {op['away_win']:.1%}")
    print(f"  xG      : {home} {eg['home']['mean']:.2f}  {away} {eg['away']['mean']:.2f}")
    print(f"  Most likely: {mls['home']}-{mls['away']} (p={mls['probability']:.1%})")
    print(f"  Top scorer {home}: {sc_h['name']} {sc_h['p_scores_one_or_more']:.1%} (xG={sc_h['expected_goals']:.3f})")
    print(f"  Top scorer {away}: {sc_a['name']} {sc_a['p_scores_one_or_more']:.1%} (xG={sc_a['expected_goals']:.3f})")

    if mo.get("status") != "unavailable" and "model_vs_market" in mo:
        mv = mo["model_vs_market"]
        print(f"\n  Model vs Market ({int(mo.get('blend_weight',0)*100)}% mkt + {int((1-mo.get('blend_weight',0))*100)}% model):")
        for key, rec in mv.items():
            print(f"    {key:<12}: conf_adj={rec['model_conf_adj']:.1%}  "
                  f"blended={rec['model_blended']:.1%}  "
                  f"market={rec['market_fair']:.1%}  "
                  f"gap={rec['gap_blended_vs_market']:+.1%}")
        if "1x2" in mo:
            print(f"\n  BSD odds: {home} {mo['1x2']['decimal_odds'].get('home_win')}  "
                  f"Draw {mo['1x2']['decimal_odds'].get('draw')}  "
                  f"{away} {mo['1x2']['decimal_odds'].get('away_win')}")
    else:
        print(f"  Market odds: UNAVAILABLE (conf-adj model only)")

    cov = r["data_coverage"]
    print(f"\n  Coverage:")
    for side in ["home", "away"]:
        c = cov[side]
        print(f"    {c['team']} ({c['confederation']}): n={c['results_history']}  "
              f"conf_att={c['conf_attack_factor']}  conf_def={c['conf_defence_factor']}")


# ── FRA vs SEN ────────────────────────────────────────────────────────────────
fra_sen = compute_prematch("FRA", "SEN", "2026-06-16_FRA_SEN")
summarise("FRA", "SEN", fra_sen)

op = fra_sen["outcome_probabilities"]
eg = fra_sen["expected_goals"]
mls = fra_sen["most_likely_score"]
sc_fra = fra_sen["top_scorers"]["home"][0]

print("\n  Acceptance checks — FRA vs SEN:")
chk("FRA win > SEN win (France favoured)", op["home_win"] > op["away_win"])
chk("FRA win > 50%", op["home_win"] > 0.50)
chk("gap from market < 20pp", abs(op["home_win"] - fra_sen["market_odds"]["1x2"]["fair"]["home_win"]) < 0.20)
chk("FRA xG > SEN xG", eg["home"]["mean"] > eg["away"]["mean"])
chk("most likely score NOT 0-0", not (mls["home"] == 0 and mls["away"] == 0))
chk("Mbappé P(goal) > 20%", sc_fra["p_scores_one_or_more"] > 0.20)
chk("model_vs_market present", "model_vs_market" in fra_sen["market_odds"])


# ── BRA vs MAR ────────────────────────────────────────────────────────────────
bra_mar = compute_prematch("BRA", "MAR", "2026-06-14_BRA_MAR")
summarise("BRA", "MAR", bra_mar)

op2  = bra_mar["outcome_probabilities"]
eg2  = bra_mar["expected_goals"]
mls2 = bra_mar["most_likely_score"]

print("\n  Acceptance checks — BRA vs MAR:")
chk("probs sum to 1.0", abs(op2["home_win"] + op2["draw"] + op2["away_win"] - 1.0) < 0.001)
chk("BRA win > 0 (not hardcoded zero)", op2["home_win"] > 0)
chk("MAR win > 10% (not artificially suppressed)", op2["away_win"] > 0.10)
chk("draw > 15%", op2["draw"] > 0.15)
chk("BRA xG > 0", eg2["home"]["mean"] > 0)
chk("MAR xG > 0", eg2["away"]["mean"] > 0)
# Market says BRA ~59%, our blend is 52.6% — sensible, model tracks market direction
mo2 = bra_mar["market_odds"]
if mo2.get("status") != "unavailable" and "1x2" in mo2:
    mkt_hw = mo2["1x2"]["fair"]["home_win"]
    chk(f"BRA favoured (model {op2['home_win']:.1%} vs market {mkt_hw:.1%}, both agree BRA ahead)",
        op2["home_win"] > op2["away_win"] and mkt_hw > 0.5)
    gap_from_mkt = abs(op2["home_win"] - mkt_hw)
    chk(f"blended within 15pp of market (gap={gap_from_mkt:.1%})", gap_from_mkt < 0.15)
chk("model_vs_market present", "model_vs_market" in mo2)

print("\n" + "="*65)
print("debug1.1 DONE")
print("="*65)
