"""Read-only phase stats from daily_metrics.json. Numbers only."""
import json
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
rows = json.loads((DATA / "daily_metrics.json").read_text(encoding="utf-8"))["records"]

# Phase boundaries (week_num = (day_number - 1) // 7, 0-based)
# Foundation:      weeks 0-3  -> day_number 1-28
# Build:           weeks 4-7  -> day_number 29-56
# Intensification: weeks 8-10 -> day_number 57-77
# Taper:           weeks 11-12-> day_number 78-90

def phase(day_number):
    w = (day_number - 1) // 7
    if w < 4:  return "foundation"
    if w < 8:  return "build"
    if w < 11: return "intensification"
    return "taper"

CATS = ["low", "moderate", "high", "very_high"]

# Accumulate per phase
phase_scores  = defaultdict(list)
phase_cats    = defaultdict(lambda: defaultdict(int))

for r in rows:
    ph = phase(r["day_number"])
    phase_scores[ph].append(r["injury_risk_score"])
    phase_cats[ph][r["risk_category"]] += 1

PHASES = ["foundation", "build", "intensification", "taper"]

print("=== 1. Avg injury_risk_score per phase ===")
for ph in PHASES:
    scores = phase_scores[ph]
    avg = sum(scores) / len(scores)
    print(f"  {ph:<18} n={len(scores):>6}  avg_risk={avg:.4f}")

print()
print("=== 2. Risk-category % per phase ===")
header = f"  {'phase':<18} " + "  ".join(f"{c:<12}" for c in CATS)
print(header)
for ph in PHASES:
    cats = phase_cats[ph]
    total = sum(cats.values())
    row = f"  {ph:<18} "
    for c in CATS:
        n = cats.get(c, 0)
        row += f"{n:>5} ({100*n/total:4.1f}%)  "
    print(row)

print()
print("=== 3. Taper days 85-90 only — risk breakdown ===")
taper_rows = [r for r in rows if 85 <= r["day_number"] <= 90]
taper_cats = defaultdict(int)
taper_scores = []
for r in taper_rows:
    taper_cats[r["risk_category"]] += 1
    taper_scores.append(r["injury_risk_score"])
total_t = len(taper_rows)
print(f"  rows: {total_t}  avg_risk: {sum(taper_scores)/len(taper_scores):.4f}")
for c in CATS:
    n = taper_cats.get(c, 0)
    print(f"  {c:<12} {n:>5}  ({100*n/total_t:5.1f}%)")

print()
print("=== 3b. Taper days 85-90 — ACWR distribution ===")
acwr_vals = [r["acwr"] for r in taper_rows if r.get("acwr")]
acwr_vals.sort()
n = len(acwr_vals)
print(f"  n={n}  min={acwr_vals[0]:.3f}  p25={acwr_vals[n//4]:.3f}  "
      f"median={acwr_vals[n//2]:.3f}  p75={acwr_vals[3*n//4]:.3f}  max={acwr_vals[-1]:.3f}")
below_065 = sum(1 for v in acwr_vals if v < 0.65)
below_080 = sum(1 for v in acwr_vals if v < 0.80)
print(f"  ACWR < 0.65: {below_065} ({100*below_065/n:.1f}%)")
print(f"  ACWR < 0.80: {below_080} ({100*below_080/n:.1f}%)")
