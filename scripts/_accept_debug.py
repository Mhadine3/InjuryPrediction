"""Acceptance check for the taper-risk fix."""
import json
from collections import defaultdict, Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
rows = json.loads((DATA / "daily_metrics.json").read_text(encoding="utf-8"))["records"]

CATS = ["low", "moderate", "high", "very_high"]

def phase(d):
    w = (d - 1) // 7
    if w < 4:  return "foundation"
    if w < 8:  return "build"
    if w < 11: return "intensification"
    return "taper"

phase_scores = defaultdict(list)
phase_cats   = defaultdict(lambda: defaultdict(int))
for r in rows:
    ph = phase(r["day_number"])
    phase_scores[ph].append(r["injury_risk_score"])
    phase_cats[ph][r["risk_category"]] += 1

PHASES = ["foundation", "build", "intensification", "taper"]

BEFORE = {"foundation": 0.1877, "build": 0.1237, "intensification": 0.1960, "taper": 0.2446}

print("=== Avg injury_risk_score by phase (before -> after) ===")
for ph in PHASES:
    scores = phase_scores[ph]
    avg = sum(scores) / len(scores)
    print(f"  {ph:<18} before={BEFORE[ph]:.4f}  after={avg:.4f}")

print()
print("=== Risk-category % per phase ===")
print(f"  {'phase':<18} " + "  ".join(f"{c:<14}" for c in CATS))
for ph in PHASES:
    cats = phase_cats[ph]
    total = sum(cats.values())
    row = f"  {ph:<18} "
    for c in CATS:
        n = cats.get(c, 0)
        row += f"{n:>5} ({100*n/total:4.1f}%)  "
    print(row)

print()
print("=== Taper days 85-90 breakdown ===")
t_rows = [r for r in rows if 85 <= r["day_number"] <= 90]
t_cats = Counter(r["risk_category"] for r in t_rows)
t_scores = [r["injury_risk_score"] for r in t_rows]
t_acwr   = [r["acwr"] for r in t_rows if r.get("acwr")]
total_t = len(t_rows)
print(f"  rows={total_t}  avg_risk={sum(t_scores)/len(t_scores):.4f}  "
      f"avg_acwr={sum(t_acwr)/len(t_acwr):.3f}")
for c in CATS:
    n = t_cats.get(c, 0)
    print(f"  {c:<12} {n:>5}  ({100*n/total_t:5.1f}%)")

print()
print("=== Day 30->60 risk trend (intensification rising) ===")
for day in [30, 45, 60, 70, 85]:
    dr = [r for r in rows if r["day_number"] == day]
    avg = sum(r["injury_risk_score"] for r in dr) / len(dr)
    acwr_avg = sum(r["acwr"] for r in dr if r.get("acwr")) / max(1, sum(1 for r in dr if r.get("acwr")))
    cats = Counter(r["risk_category"] for r in dr)
    non_low = sum(v for k,v in cats.items() if k != "low")
    print(f"  day={day:>2}  avg_risk={avg:.4f}  avg_acwr={acwr_avg:.3f}  "
          f"non-low={non_low}/{len(dr)} ({100*non_low/len(dr):.0f}%)")

print()
print("=== Synthetic detrain check (chronic_ratio < 0.80, NOT taper) ===")
# Approximate: look at foundation players (day 1-28) with low ACWR + low risk_score
# These should still be moderate/high if they have low chronic ratio
# Proxy: rows with acwr < 0.65 in days 1-70 (not taper) — check risk
non_taper_low_acwr = [r for r in rows
                      if r["day_number"] <= 76
                      and r.get("acwr") is not None
                      and r["acwr"] < 0.65]
print(f"  Non-taper rows with ACWR < 0.65: {len(non_taper_low_acwr)}")
if non_taper_low_acwr:
    cats2 = Counter(r["risk_category"] for r in non_taper_low_acwr)
    total2 = len(non_taper_low_acwr)
    avg2 = sum(r["injury_risk_score"] for r in non_taper_low_acwr) / total2
    print(f"  avg_risk={avg2:.4f}")
    for c in CATS:
        n = cats2.get(c, 0)
        print(f"    {c:<12} {n:>5}  ({100*n/total2:5.1f}%)")

print()
insuf = sum(1 for r in rows if r.get("risk_category") == "insufficient_data")
taper_avg = sum(phase_scores["taper"]) / len(phase_scores["taper"])
build_avg  = sum(phase_scores["build"])  / len(phase_scores["build"])
print(f"ACCEPTANCE:")
print(f"  taper < build?       {taper_avg:.4f} < {build_avg:.4f} -> {'PASS' if taper_avg < build_avg else 'FAIL'}")
taper_low_pct = 100 * phase_cats["taper"].get("low",0) / sum(phase_cats["taper"].values())
print(f"  taper mostly low?    {taper_low_pct:.1f}% low -> {'PASS' if taper_low_pct > 60 else 'FAIL'}")
print(f"  insufficient_data=0? {insuf} -> {'PASS' if insuf == 0 else 'FAIL'}")
detrain_elevated = avg2 > 0.15 if non_taper_low_acwr else False
print(f"  detrain still elevated? avg={avg2:.4f} -> {'PASS' if detrain_elevated else 'FAIL'}")
