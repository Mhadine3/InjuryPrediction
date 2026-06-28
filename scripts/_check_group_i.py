"""Acceptance check for Group I in daily_metrics.json."""
import json
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"

# Players baseline
pb = json.loads((DATA / "players_baseline.json").read_text(encoding="utf-8"))
players = pb["players"]
team_counts = Counter(p["team"] for p in players)
print("=== players_baseline.json ===")
for team, cnt in sorted(team_counts.items()):
    group = "I" if team in ("France","Senegal","Iraq","Norway") else "C"
    print(f"  Group {group} | {team}: {cnt} players")
print(f"  Total: {len(players)}")

# Daily metrics
dm = json.loads((DATA / "daily_metrics.json").read_text(encoding="utf-8"))
rows = dm["records"]
print(f"\n=== daily_metrics.json: {len(rows)} rows ===")

# Per-team counts and risk distribution
team_rows: dict[str, list] = {}
for row in rows:
    t = row["team"]
    team_rows.setdefault(t, []).append(row)

GROUP_I = ["France", "Senegal", "Iraq", "Norway"]
GROUP_C = ["Brazil", "Morocco", "Haiti", "Scotland"]

print("\nGroup I — per-team risk distribution (day 70 sample):")
for team in GROUP_I:
    day70 = [r for r in team_rows.get(team, []) if r["day_number"] == 70]
    all_rows = team_rows.get(team, [])
    risk_dist = Counter(r["risk_category"] for r in all_rows)
    total = sum(risk_dist.values())
    players_count = len({r["player_id"] for r in all_rows})
    print(f"\n  {team}: {players_count} players, {len(all_rows)} rows")
    for cat in ["low","moderate","high","very_high"]:
        n = risk_dist.get(cat, 0)
        pct = 100 * n / total if total else 0
        print(f"    {cat:<12} {n:>5}  ({pct:.1f}%)")
    # Taper check (day 85-90 should have low ACWR)
    taper_rows = [r for r in all_rows if r["day_number"] >= 85]
    if taper_rows:
        acwr_vals = [r["acwr"] for r in taper_rows if r.get("acwr")]
        avg_acwr = sum(acwr_vals) / len(acwr_vals) if acwr_vals else 0
        low_risk = sum(1 for r in taper_rows if r["risk_category"] == "low")
        print(f"    Taper (day 85-90): avg ACWR={avg_acwr:.3f}, low-risk={low_risk}/{len(taper_rows)} ({100*low_risk/len(taper_rows):.0f}%)")

print("\nGroup C — verification (unchanged):")
for team in GROUP_C:
    all_rows = team_rows.get(team, [])
    risk_dist = Counter(r["risk_category"] for r in all_rows)
    total = sum(risk_dist.values())
    players_count = len({r["player_id"] for r in all_rows})
    low_pct = 100 * risk_dist.get("low", 0) / total if total else 0
    print(f"  {team}: {players_count} players, {len(all_rows)} rows  |  low={low_pct:.1f}%")

print("\n=== ACWR global stats ===")
acwr_vals = [r["acwr"] for r in rows if r.get("acwr")]
print(f"  min={min(acwr_vals):.3f}  max={max(acwr_vals):.3f}  mean={sum(acwr_vals)/len(acwr_vals):.3f}")
insuf = sum(1 for r in rows if r.get("acwr_status") == "insufficient_data")
print(f"  insufficient_data rows: {insuf}")
print("\nACCEPTANCE: PASS" if insuf == 0 else "\nACCEPTANCE: FAIL — insufficient_data present")
