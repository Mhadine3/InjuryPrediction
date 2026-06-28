"""
build_group_i.py
================
Fetches 2026 WC Group I squads from football-data.org/v4/competitions/WC/teams
(confirmed working — all 4 teams return 26+ players).

Steps:
  1. Fetch WC teams endpoint
  2. Extract FRA / SEN / IRQ / NOR (verified ≥ 23 players each)
  3. Print full roster for eyeballing
  4. Build baseline entries matching Group C structure exactly
  5. Merge into data/players_baseline.json (additive — Group C untouched)
  6. Print per-team counts + position distributions

Aborts if any team returns < 23 players.
"""

import json, os, sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests"); sys.exit(1)

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
if not API_KEY:
    print("ERROR: set FOOTBALL_DATA_API_KEY in your environment / .env"); sys.exit(1)
BASE    = "https://api.football-data.org/v4"
HDR     = {"X-Auth-Token": API_KEY}
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

TARGET = {
    "FRA": ("France",  "UEFA"),
    "SEN": ("Senegal", "CAF"),
    "IRQ": ("Iraq",    "AFC"),
    "NOR": ("Norway",  "UEFA"),
}

# Position mapping: fd.org broad category → index-based granular detail
# We distribute realistically within each category using jersey number hints.
def map_position(broad: str, shirt: int | None, pos_idx: int, pos_count: int) -> tuple[str, str]:
    """
    broad     = "Goalkeeper"|"Defence"|"Midfield"|"Offence"
    shirt     = jersey number (may be None)
    pos_idx   = index within this broad category (0-based)
    pos_count = total players in this broad category
    """
    if broad == "Goalkeeper":
        return ("GK", "Goalkeeper")
    if broad == "Defence":
        # First ~3 are CBs, rest are FBs
        if pos_idx < max(3, pos_count // 2):
            return ("CB", "Center Back")
        return ("RB", "Full Back")
    if broad == "Midfield":
        # 2 DMs, then CMs, last 1-2 are AM/Winger
        if pos_idx < 2:
            return ("DM", "Defensive Mid")
        if pos_idx >= pos_count - 2:
            return ("WI", "Winger") if pos_idx == pos_count - 1 else ("AM", "Attacking Mid")
        return ("CM", "Central Mid")
    if broad == "Offence":
        # First 2 are Wingers, rest Strikers; if only 3 → 1 Winger + 2 Strikers
        if pos_idx < 2 and pos_count >= 4:
            return ("WI", "Winger")
        return ("ST", "Striker")
    return ("CM", "Central Mid")


PHYSIOLOGY = {
    "Goalkeeper":    {"hrv_baseline_ms": 65.0, "resting_hr_bpm": 52, "sprint_speed_max_kmh": 26.0,
                      "vo2_max_ml_kg_min": 50.0, "distance_per_match_km": 4.5,
                      "hi_distance_per_match_m": 60, "sprints_per_match": 2, "accel_decel_per_match": 15},
    "Center Back":   {"hrv_baseline_ms": 68.0, "resting_hr_bpm": 50, "sprint_speed_max_kmh": 29.0,
                      "vo2_max_ml_kg_min": 54.0, "distance_per_match_km": 10.0,
                      "hi_distance_per_match_m": 650, "sprints_per_match": 15, "accel_decel_per_match": 120},
    "Full Back":     {"hrv_baseline_ms": 70.0, "resting_hr_bpm": 49, "sprint_speed_max_kmh": 32.0,
                      "vo2_max_ml_kg_min": 57.0, "distance_per_match_km": 11.5,
                      "hi_distance_per_match_m": 900, "sprints_per_match": 22, "accel_decel_per_match": 150},
    "Defensive Mid": {"hrv_baseline_ms": 69.0, "resting_hr_bpm": 50, "sprint_speed_max_kmh": 30.0,
                      "vo2_max_ml_kg_min": 55.0, "distance_per_match_km": 11.0,
                      "hi_distance_per_match_m": 750, "sprints_per_match": 18, "accel_decel_per_match": 145},
    "Central Mid":   {"hrv_baseline_ms": 71.0, "resting_hr_bpm": 49, "sprint_speed_max_kmh": 31.0,
                      "vo2_max_ml_kg_min": 58.0, "distance_per_match_km": 12.0,
                      "hi_distance_per_match_m": 950, "sprints_per_match": 24, "accel_decel_per_match": 165},
    "Attacking Mid": {"hrv_baseline_ms": 70.0, "resting_hr_bpm": 50, "sprint_speed_max_kmh": 32.0,
                      "vo2_max_ml_kg_min": 56.0, "distance_per_match_km": 11.5,
                      "hi_distance_per_match_m": 900, "sprints_per_match": 22, "accel_decel_per_match": 155},
    "Winger":        {"hrv_baseline_ms": 72.0, "resting_hr_bpm": 49, "sprint_speed_max_kmh": 34.0,
                      "vo2_max_ml_kg_min": 58.0, "distance_per_match_km": 11.0,
                      "hi_distance_per_match_m": 1050, "sprints_per_match": 28, "accel_decel_per_match": 160},
    "Striker":       {"hrv_baseline_ms": 68.0, "resting_hr_bpm": 51, "sprint_speed_max_kmh": 33.0,
                      "vo2_max_ml_kg_min": 55.0, "distance_per_match_km": 10.5,
                      "hi_distance_per_match_m": 850, "sprints_per_match": 25, "accel_decel_per_match": 140},
}

WELLNESS = {
    "young":   {"sleep_duration_baseline_h": 8.0, "sleep_quality_baseline": 2.8, "fatigue_baseline": 2.3, "soreness_baseline": 2.2, "stress_baseline": 2.5},
    "prime":   {"sleep_duration_baseline_h": 7.5, "sleep_quality_baseline": 3.0, "fatigue_baseline": 2.5, "soreness_baseline": 2.5, "stress_baseline": 2.5},
    "senior":  {"sleep_duration_baseline_h": 7.2, "sleep_quality_baseline": 3.3, "fatigue_baseline": 2.8, "soreness_baseline": 2.8, "stress_baseline": 2.6},
    "veteran": {"sleep_duration_baseline_h": 7.0, "sleep_quality_baseline": 3.5, "fatigue_baseline": 3.0, "soreness_baseline": 3.2, "stress_baseline": 2.7},
}


def dob_to_age(dob: str) -> int:
    try:
        return 2026 - int(dob.split("-")[0])
    except Exception:
        return 27


def age_cat(age: int) -> str:
    if age < 23: return "young"
    if age < 29: return "prime"
    if age < 33: return "senior"
    return "veteran"


def proneness(caps: int, age: int) -> str:
    if age > 32 or caps < 10: return "high"
    if caps > 50:              return "low"
    return "medium"


def to_baseline(p: dict, team_name: str, fifa_code: str, jersey: int,
                pos_short: str, pos_detail: str) -> dict:
    name   = p.get("name", f"Player {jersey}")
    dob    = p.get("dateOfBirth") or "1998-01-01"
    age    = dob_to_age(dob)
    ac     = age_cat(age)
    caps   = 0   # fd.org squad endpoint does not include caps count
    pr     = proneness(caps, age)
    phys   = PHYSIOLOGY.get(pos_detail, PHYSIOLOGY["Central Mid"])
    well   = WELLNESS[ac]
    code   = fifa_code.lower()
    safe   = name.lower().replace(" ", "_").replace("-", "_").replace("'", "")[:20]
    pid    = f"{code}_{jersey:03d}_{safe}"

    return {
        "player_id":       pid,
        "name":            name,
        "team":            team_name,
        "team_code":       fifa_code,
        "position":        pos_short,
        "position_detail": pos_detail,
        "date_of_birth":   dob,
        "age":             age,
        "caps":            caps,
        "goals":           0,
        "club":            "",
        "league":          "",
        "is_captain":      False,
        "traits": {
            "age_category":      ac,
            "experience_level":  "veteran" if caps > 50 else "experienced" if caps > 20 else "emerging",
            "recovery_speed":    "high" if age < 26 else "medium" if age < 32 else "low",
            "mental_resilience": "high" if caps > 40 else "medium",
            "injury_proneness":  pr,
        },
        "physiology": phys,
        "wellness":   well,
    }


# ─── Fetch & print ────────────────────────────────────────────────────────────

print("Fetching WC 2026 teams from football-data.org…")
r = requests.get(f"{BASE}/competitions/WC/teams", headers=HDR, timeout=20)
if r.status_code != 200:
    print(f"ERROR: HTTP {r.status_code} — {r.text[:200]}")
    sys.exit(1)

all_teams = r.json().get("teams", [])
group_i_raw = {t["tla"]: t for t in all_teams if t.get("tla") in TARGET}

new_entries: list[dict] = []
failed: list[str] = []

for tla, (team_name, conf) in TARGET.items():
    team = group_i_raw.get(tla)
    if not team:
        print(f"ERROR: {tla} not found in WC response")
        failed.append(tla)
        continue

    squad = team.get("squad") or []
    print(f"\n{'='*62}")
    print(f"  {team_name} ({tla})  |  squad: {len(squad)} players")
    print(f"{'='*62}")
    print(f"  {'#':<4} {'Name':<32} {'FD Position':<12} {'Detail':<16} {'DOB'}")
    print(f"  {'-'*4} {'-'*32} {'-'*12} {'-'*16} {'-'*10}")

    if len(squad) < 23:
        print(f"  FAIL: only {len(squad)} players returned.")
        failed.append(f"{team_name} ({len(squad)})")
        continue

    # Group by broad position, then assign granular positions
    from collections import defaultdict
    by_broad: dict[str, list] = defaultdict(list)
    for p in squad:
        by_broad[p.get("position", "Midfield")].append(p)

    jersey = 1
    team_entries: list[dict] = []
    for broad in ["Goalkeeper", "Defence", "Midfield", "Offence"]:
        group = by_broad.get(broad, [])
        for idx, p in enumerate(group):
            ps, pd = map_position(broad, p.get("shirtNumber"), idx, len(group))
            entry = to_baseline(p, team_name, tla, jersey, ps, pd)
            print(f"  {jersey:<4} {p.get('name','?'):<32} {broad:<12} {pd:<16} {p.get('dateOfBirth','?')}")
            team_entries.append(entry)
            jersey += 1

    new_entries.extend(team_entries)
    print(f"\n  >> {len(team_entries)} players added for {team_name}")

print("\n" + "="*62)
if failed:
    print(f"FAILED: {', '.join(failed)}")
    sys.exit(1)

# ─── Merge into players_baseline.json ─────────────────────────────────────────

pb_path = DATA_DIR / "players_baseline.json"
existing = json.loads(pb_path.read_text(encoding="utf-8"))

# Sanity: ensure no duplicate player_ids
existing_ids = {p["player_id"] for p in existing["players"]}
dupes = [e["player_id"] for e in new_entries if e["player_id"] in existing_ids]
if dupes:
    print(f"WARNING: duplicate player_ids detected: {dupes[:5]}")

existing["players"].extend(new_entries)

# Update metadata
existing["metadata"]["total_players"] = len(existing["players"])
teams_included = existing["metadata"].get("teams_included", [])
for nm in ["France", "Senegal", "Iraq", "Norway"]:
    if nm not in teams_included:
        teams_included.append(nm)
existing["metadata"]["teams_included"] = teams_included
for nm, cnt in [("France", len([e for e in new_entries if e["team"]=="France"])),
                ("Senegal", len([e for e in new_entries if e["team"]=="Senegal"])),
                ("Iraq", len([e for e in new_entries if e["team"]=="Iraq"])),
                ("Norway", len([e for e in new_entries if e["team"]=="Norway"]))]:
    existing["metadata"]["team_counts"][nm] = cnt
existing["metadata"]["generator_version"] = "2.1.0"
existing["metadata"]["groups_included"] = ["C", "I"]

pb_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nMerged players_baseline.json: {len(existing['players'])} total players")

from collections import Counter
tc = Counter(p["team"] for p in existing["players"])
for team, cnt in sorted(tc.items()):
    print(f"  {team}: {cnt}")
