"""
fetch_group_i_squads.py
=======================
Fetch 2026 WC Group I squads for France, Senegal, Iraq, Norway
from TheSportsDB free API (key=3).

Reports full roster per team for eyeballing BEFORE any data generation.
Exits with error if any team returns < 23 players.

Output: data/group_i_squads_raw.json  (raw API response)
        data/group_i_players_baseline.json (formatted for generator)
"""

import json, sys, time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests"); sys.exit(1)

DATA_DIR  = Path(__file__).resolve().parents[1] / "data"
BASE_URL  = "https://www.thesportsdb.com/api/v1/json/3"
HEADERS   = {"User-Agent": "Mozilla/5.0"}

# Group I teams: (name, search_term, fifa_code, confederation, fallback_id)
GROUP_I = [
    ("France",   "France",   "FRA", "UEFA",     152956),
    ("Senegal",  "Senegal",  "SEN", "CAF",      153037),
    ("Iraq",     "Iraq",     "IRQ", "AFC",      153015),
    ("Norway",   "Norway",   "NOR", "UEFA",     153022),
]

# Position mapping from TheSportsDB strPosition to our schema
POS_MAP = {
    "Goalkeeper":         ("GK", "Goalkeeper"),
    "Defender":           ("CB", "Center Back"),
    "Centre-Back":        ("CB", "Center Back"),
    "Left-Back":          ("LB", "Full Back"),
    "Right-Back":         ("RB", "Full Back"),
    "Left Back":          ("LB", "Full Back"),
    "Right Back":         ("RB", "Full Back"),
    "Sweeper":            ("CB", "Center Back"),
    "Defensive Midfielder":("DM","Defensive Mid"),
    "Midfielder":         ("CM", "Central Mid"),
    "Central Midfielder": ("CM", "Central Mid"),
    "Attacking Midfielder":("AM","Attacking Mid"),
    "Left Midfielder":    ("WI", "Winger"),
    "Right Midfielder":   ("WI", "Winger"),
    "Left Winger":        ("WI", "Winger"),
    "Right Winger":       ("WI", "Winger"),
    "Winger":             ("WI", "Winger"),
    "Forward":            ("ST", "Striker"),
    "Striker":            ("ST", "Striker"),
    "Centre Forward":     ("ST", "Striker"),
    "Second Striker":     ("ST", "Striker"),
}

PHYSIOLOGY_BY_POS = {
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

WELLNESS_BY_AGE = {
    "young":   {"sleep_duration_baseline_h": 8.0, "sleep_quality_baseline": 2.8, "fatigue_baseline": 2.3, "soreness_baseline": 2.2, "stress_baseline": 2.5},
    "prime":   {"sleep_duration_baseline_h": 7.5, "sleep_quality_baseline": 3.0, "fatigue_baseline": 2.5, "soreness_baseline": 2.5, "stress_baseline": 2.5},
    "senior":  {"sleep_duration_baseline_h": 7.2, "sleep_quality_baseline": 3.3, "fatigue_baseline": 2.8, "soreness_baseline": 2.8, "stress_baseline": 2.6},
    "veteran": {"sleep_duration_baseline_h": 7.0, "sleep_quality_baseline": 3.5, "fatigue_baseline": 3.0, "soreness_baseline": 3.2, "stress_baseline": 2.7},
}


def age_category(age: int) -> str:
    if age < 23: return "young"
    if age < 29: return "prime"
    if age < 33: return "senior"
    return "veteran"


def injury_proneness(caps: int, age: int) -> str:
    if age > 32 or caps < 10: return "high"
    if caps > 50: return "low"
    return "medium"


def map_position(raw_pos: str) -> tuple[str, str]:
    for key, val in POS_MAP.items():
        if key.lower() in raw_pos.lower():
            return val
    return ("CM", "Central Mid")


def dob_to_age(dob_str: str) -> int:
    try:
        parts = dob_str.split("-")
        birth_year = int(parts[0])
        return 2026 - birth_year
    except Exception:
        return 27


def fetch_team_id(search_term: str, fallback_id: int) -> int | None:
    """Search TheSportsDB for a national football team and return its ID."""
    url = f"{BASE_URL}/searchteams.php"
    try:
        r = requests.get(url, params={"t": search_term}, headers=HEADERS, timeout=10)
        data = r.json()
        teams = data.get("teams") or []
        for team in teams:
            sport = team.get("strSport", "")
            name  = team.get("strTeam", "")
            if "soccer" in sport.lower() or "football" in sport.lower():
                if search_term.lower() in name.lower():
                    tid = int(team.get("idTeam", 0))
                    if tid:
                        print(f"  Found team '{name}' id={tid}")
                        return tid
        print(f"  Search found no exact match — using fallback id={fallback_id}")
        return fallback_id
    except Exception as e:
        print(f"  Search error: {e} — using fallback id={fallback_id}")
        return fallback_id


def fetch_players(team_id: int) -> list[dict]:
    """Fetch all players for a team from TheSportsDB."""
    url = f"{BASE_URL}/lookup_all_players.php"
    try:
        r = requests.get(url, params={"id": team_id}, headers=HEADERS, timeout=15)
        data = r.json()
        return data.get("player") or []
    except Exception as e:
        print(f"  Player fetch error: {e}")
        return []


def to_baseline_entry(player: dict, team_name: str, fifa_code: str, idx: int) -> dict:
    raw_pos = player.get("strPosition") or "Midfielder"
    pos_short, pos_detail = map_position(raw_pos)
    dob  = player.get("strBirthdate") or player.get("dateBorn") or "1998-01-01"
    age  = dob_to_age(dob)
    caps = 0
    try:
        n = player.get("strNationalitynow") or ""
        c_val = player.get("intCaps") or player.get("strCaps") or "0"
        caps = int(str(c_val).replace(",", "")) if str(c_val).strip().isdigit() else 0
    except Exception:
        caps = 0

    code = fifa_code.lower()
    safe_name = (player.get("strPlayer") or f"player_{idx}").lower().replace(" ", "_").replace("-", "_")[:20]
    player_id = f"{code}_{idx:03d}_{safe_name}"
    age_cat   = age_category(age)
    proneness = injury_proneness(caps, age)
    phys      = PHYSIOLOGY_BY_POS.get(pos_detail, PHYSIOLOGY_BY_POS["Central Mid"])
    well      = WELLNESS_BY_AGE[age_cat]
    name      = player.get("strPlayer") or f"Player {idx}"

    return {
        "player_id":      player_id,
        "name":           name,
        "team":           team_name,
        "team_code":      fifa_code,
        "position":       pos_short,
        "position_detail":pos_detail,
        "date_of_birth":  dob,
        "age":            age,
        "caps":           caps,
        "goals":          0,
        "club":           player.get("strTeam") or "",
        "league":         "",
        "is_captain":     False,
        "traits": {
            "age_category":      age_cat,
            "experience_level":  "veteran" if caps > 50 else ("experienced" if caps > 20 else "emerging"),
            "recovery_speed":    "high" if age < 26 else ("medium" if age < 32 else "low"),
            "mental_resilience": "high" if caps > 40 else "medium",
            "injury_proneness":  proneness,
        },
        "physiology": phys,
        "wellness":   well,
    }


def fetch_and_validate(team_name: str, search_term: str, fifa_code: str, fallback_id: int) -> list[dict]:
    print(f"\n{'='*56}")
    print(f"  {team_name} ({fifa_code})")
    print(f"{'='*56}")

    team_id = fetch_team_id(search_term, fallback_id)
    time.sleep(0.5)
    raw_players = fetch_players(team_id)
    time.sleep(0.5)

    print(f"  Raw player count from API: {len(raw_players)}")

    if len(raw_players) < 5:
        print(f"  ERROR: only {len(raw_players)} players returned — API may not have this squad.")
        return []

    # Print the raw roster for eyeballing
    print(f"\n  {'#':<4} {'Name':<28} {'Position':<22} {'DOB':<12} {'Caps'}")
    print(f"  {'-'*4} {'-'*28} {'-'*22} {'-'*12} {'-'*5}")
    entries = []
    for i, p in enumerate(raw_players, 1):
        name = p.get("strPlayer") or "?"
        pos  = p.get("strPosition") or "?"
        dob  = p.get("strBirthdate") or p.get("dateBorn") or "?"
        caps = p.get("intCaps") or p.get("strCaps") or "0"
        print(f"  {i:<4} {name:<28} {pos:<22} {dob:<12} {caps}")
        entries.append(to_baseline_entry(p, team_name, fifa_code, i))

    return entries


def main():
    all_entries: list[dict] = []
    failed: list[str] = []

    for team_name, search_term, fifa_code, conf, fallback_id in GROUP_I:
        entries = fetch_and_validate(team_name, search_term, fifa_code, fallback_id)
        if len(entries) < 23:
            failed.append(f"{team_name} ({len(entries)} players)")
            print(f"\n  FAIL: {team_name} only returned {len(entries)} players. STOPPING.")
        else:
            # Take first 26; if fewer take all
            all_entries.extend(entries[:26])
            print(f"\n  OK: {len(entries[:26])} players added for {team_name}")

    print("\n" + "="*56)
    if failed:
        print(f"FAILED teams (< 23 players): {', '.join(failed)}")
        print("Aborting — do NOT invent or pad players per todoD spec.")
        sys.exit(1)

    # Save raw-formatted baseline (to be merged with existing players_baseline.json)
    out_path = DATA_DIR / "group_i_players_baseline.json"
    payload = {
        "metadata": {
            "source": "TheSportsDB free API (key=3)",
            "fetched_at": "2026-06-13",
            "group": "I",
            "teams": ["France", "Senegal", "Iraq", "Norway"],
            "total_players": len(all_entries),
        },
        "players": all_entries,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {len(all_entries)} players → {out_path}")

    # Print summary
    from collections import Counter
    team_counts = Counter(p["team"] for p in all_entries)
    for team, cnt in sorted(team_counts.items()):
        print(f"  {team}: {cnt} players")


if __name__ == "__main__":
    main()
