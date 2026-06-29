"""
build_full_wc.py
================
Expand the platform from 8 teams (Groups C+I) to the FULL 48-team / 12-group
FIFA World Cup 2026, plus the complete knockout bracket — all from the live
football-data.org WC competition endpoints (same source build_group_i.py used).

Produces (in data/):
  players_baseline.json   merged — existing 8 BSD-backed teams kept verbatim,
                          the other 40 teams added the same way (fd.org squads)
  wc_teams.json           48 teams: name, tla, group, confederation, iso2 (flag)
  wc_fixtures.json        all 104 matches incl. knockout (group/stage/status/score)
  wc_ratings.json         attack/defence ratings for all 48, derived from live
                          WC results (prematch Dixon-Coles fallback for non-BSD teams)

Idempotent. Run from repo root with FOOTBALL_DATA_API_KEY in env/.env:
    python scripts/build_full_wc.py
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests"); sys.exit(1)

ROOT     = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

# Load .env so the key is available when run from the repo root
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
if not API_KEY:
    print("ERROR: set FOOTBALL_DATA_API_KEY in your environment / .env"); sys.exit(1)
BASE = "https://api.football-data.org/v4"
HDR  = {"X-Auth-Token": API_KEY}

# Teams whose richer BSD-backed baseline entries we KEEP untouched (real caps/goals).
KEEP_TEAMS = {"BRA", "MAR", "HAI", "SCO", "FRA", "SEN", "IRQ", "NOR"}

# ── Confederation + ISO-2 (flagcdn) for all 48 WC 2026 nations ──────────────────
CONFEDERATION = {
    # UEFA
    "AUT": "UEFA", "BEL": "UEFA", "BIH": "UEFA", "CRO": "UEFA", "CZE": "UEFA",
    "ENG": "UEFA", "ESP": "UEFA", "FRA": "UEFA", "GER": "UEFA", "NED": "UEFA",
    "NOR": "UEFA", "POR": "UEFA", "SCO": "UEFA", "SUI": "UEFA", "SWE": "UEFA", "TUR": "UEFA",
    # CONMEBOL
    "ARG": "CONMEBOL", "BRA": "CONMEBOL", "COL": "CONMEBOL", "ECU": "CONMEBOL",
    "PAR": "CONMEBOL", "URU": "CONMEBOL",
    # CONCACAF
    "USA": "CONCACAF", "MEX": "CONCACAF", "CAN": "CONCACAF", "CUW": "CONCACAF",
    "HAI": "CONCACAF", "PAN": "CONCACAF",
    # CAF
    "ALG": "CAF", "CIV": "CAF", "COD": "CAF", "CPV": "CAF", "EGY": "CAF",
    "GHA": "CAF", "MAR": "CAF", "RSA": "CAF", "SEN": "CAF", "TUN": "CAF",
    # AFC
    "AUS": "AFC", "IRN": "AFC", "IRQ": "AFC", "JOR": "AFC", "JPN": "AFC",
    "KOR": "AFC", "KSA": "AFC", "QAT": "AFC", "UZB": "AFC",
    # OFC
    "NZL": "OFC",
}
ISO2 = {
    "AUT": "at", "BEL": "be", "BIH": "ba", "CRO": "hr", "CZE": "cz", "ENG": "gb-eng",
    "ESP": "es", "FRA": "fr", "GER": "de", "NED": "nl", "NOR": "no", "POR": "pt",
    "SCO": "gb-sct", "SUI": "ch", "SWE": "se", "TUR": "tr",
    "ARG": "ar", "BRA": "br", "COL": "co", "ECU": "ec", "PAR": "py", "URU": "uy",
    "USA": "us", "MEX": "mx", "CAN": "ca", "CUW": "cw", "HAI": "ht", "PAN": "pa",
    "ALG": "dz", "CIV": "ci", "COD": "cd", "CPV": "cv", "EGY": "eg", "GHA": "gh",
    "MAR": "ma", "RSA": "za", "SEN": "sn", "TUN": "tn",
    "AUS": "au", "IRN": "ir", "IRQ": "iq", "JOR": "jo", "JPN": "jp", "KOR": "kr",
    "KSA": "sa", "QAT": "qa", "UZB": "uz", "NZL": "nz",
}

# ── Position / physiology / wellness tables (identical to build_group_i.py) ─────
def map_position(broad, pos_idx, pos_count):
    if broad == "Goalkeeper":
        return ("GK", "Goalkeeper")
    if broad == "Defence":
        if pos_idx < max(3, pos_count // 2):
            return ("CB", "Center Back")
        return ("RB", "Full Back")
    if broad == "Midfield":
        if pos_idx < 2:
            return ("DM", "Defensive Mid")
        if pos_idx >= pos_count - 2:
            return ("WI", "Winger") if pos_idx == pos_count - 1 else ("AM", "Attacking Mid")
        return ("CM", "Central Mid")
    if broad == "Offence":
        if pos_idx < 2 and pos_count >= 4:
            return ("WI", "Winger")
        return ("ST", "Striker")
    return ("CM", "Central Mid")

PHYSIOLOGY = {
    "Goalkeeper":    {"hrv_baseline_ms": 65.0, "resting_hr_bpm": 52, "sprint_speed_max_kmh": 26.0, "vo2_max_ml_kg_min": 50.0, "distance_per_match_km": 4.5, "hi_distance_per_match_m": 60, "sprints_per_match": 2, "accel_decel_per_match": 15},
    "Center Back":   {"hrv_baseline_ms": 68.0, "resting_hr_bpm": 50, "sprint_speed_max_kmh": 29.0, "vo2_max_ml_kg_min": 54.0, "distance_per_match_km": 10.0, "hi_distance_per_match_m": 650, "sprints_per_match": 15, "accel_decel_per_match": 120},
    "Full Back":     {"hrv_baseline_ms": 70.0, "resting_hr_bpm": 49, "sprint_speed_max_kmh": 32.0, "vo2_max_ml_kg_min": 57.0, "distance_per_match_km": 11.5, "hi_distance_per_match_m": 900, "sprints_per_match": 22, "accel_decel_per_match": 150},
    "Defensive Mid": {"hrv_baseline_ms": 69.0, "resting_hr_bpm": 50, "sprint_speed_max_kmh": 30.0, "vo2_max_ml_kg_min": 55.0, "distance_per_match_km": 11.0, "hi_distance_per_match_m": 750, "sprints_per_match": 18, "accel_decel_per_match": 145},
    "Central Mid":   {"hrv_baseline_ms": 71.0, "resting_hr_bpm": 49, "sprint_speed_max_kmh": 31.0, "vo2_max_ml_kg_min": 58.0, "distance_per_match_km": 12.0, "hi_distance_per_match_m": 950, "sprints_per_match": 24, "accel_decel_per_match": 165},
    "Attacking Mid": {"hrv_baseline_ms": 70.0, "resting_hr_bpm": 50, "sprint_speed_max_kmh": 32.0, "vo2_max_ml_kg_min": 56.0, "distance_per_match_km": 11.5, "hi_distance_per_match_m": 900, "sprints_per_match": 22, "accel_decel_per_match": 155},
    "Winger":        {"hrv_baseline_ms": 72.0, "resting_hr_bpm": 49, "sprint_speed_max_kmh": 34.0, "vo2_max_ml_kg_min": 58.0, "distance_per_match_km": 11.0, "hi_distance_per_match_m": 1050, "sprints_per_match": 28, "accel_decel_per_match": 160},
    "Striker":       {"hrv_baseline_ms": 68.0, "resting_hr_bpm": 51, "sprint_speed_max_kmh": 33.0, "vo2_max_ml_kg_min": 55.0, "distance_per_match_km": 10.5, "hi_distance_per_match_m": 850, "sprints_per_match": 25, "accel_decel_per_match": 140},
}
WELLNESS = {
    "young":   {"sleep_duration_baseline_h": 8.0, "sleep_quality_baseline": 2.8, "fatigue_baseline": 2.3, "soreness_baseline": 2.2, "stress_baseline": 2.5},
    "prime":   {"sleep_duration_baseline_h": 7.5, "sleep_quality_baseline": 3.0, "fatigue_baseline": 2.5, "soreness_baseline": 2.5, "stress_baseline": 2.5},
    "senior":  {"sleep_duration_baseline_h": 7.2, "sleep_quality_baseline": 3.3, "fatigue_baseline": 2.8, "soreness_baseline": 2.8, "stress_baseline": 2.6},
    "veteran": {"sleep_duration_baseline_h": 7.0, "sleep_quality_baseline": 3.5, "fatigue_baseline": 3.0, "soreness_baseline": 3.2, "stress_baseline": 2.7},
}

def dob_to_age(dob):
    try:    return 2026 - int(dob.split("-")[0])
    except Exception: return 27
def age_cat(a):
    return "young" if a < 23 else "prime" if a < 29 else "senior" if a < 33 else "veteran"
def proneness(caps, age):
    if age > 32 or caps < 10: return "high"
    if caps > 50: return "low"
    return "medium"

def to_baseline(p, team_name, tla, jersey, pos_short, pos_detail):
    name = p.get("name", f"Player {jersey}")
    dob  = p.get("dateOfBirth") or "1998-01-01"
    age  = dob_to_age(dob)
    ac   = age_cat(age)
    caps = 0
    pr   = proneness(caps, age)
    phys = PHYSIOLOGY.get(pos_detail, PHYSIOLOGY["Central Mid"])
    safe = name.lower().replace(" ", "_").replace("-", "_").replace("'", "")[:20]
    pid  = f"{tla.lower()}_{jersey:03d}_{safe}"
    return {
        "player_id": pid, "name": name, "team": team_name, "team_code": tla,
        "position": pos_short, "position_detail": pos_detail,
        "date_of_birth": dob, "age": age, "caps": caps, "goals": 0,
        "club": "", "league": "", "is_captain": False,
        "traits": {
            "age_category": ac,
            "experience_level": "veteran" if caps > 50 else "experienced" if caps > 20 else "emerging",
            "recovery_speed": "high" if age < 26 else "medium" if age < 32 else "low",
            "mental_resilience": "high" if caps > 40 else "medium",
            "injury_proneness": pr,
        },
        "physiology": phys, "wellness": WELLNESS[ac],
    }

def get(path):
    r = requests.get(f"{BASE}{path}", headers=HDR, timeout=30)
    if r.status_code != 200:
        print(f"ERROR: {path} -> HTTP {r.status_code}: {r.text[:200]}"); sys.exit(1)
    return r.json()

def main():
    print("Fetching WC 2026 teams / matches / standings from football-data.org …")
    teams_raw = get("/competitions/WC/teams")["teams"]
    matches   = get("/competitions/WC/matches")["matches"]
    standings = get("/competitions/WC/standings")["standings"]

    # team -> group from standings
    team_group, team_name_api = {}, {}
    for g in standings:
        if g.get("type") != "TOTAL":
            continue
        grp = g.get("group", "").replace("Group ", "")
        for row in g.get("table", []):
            t = row["team"]
            team_group[t["tla"]] = grp
            team_name_api[t["tla"]] = t.get("name")

    by_tla = {t.get("tla"): t for t in teams_raw if t.get("tla")}
    print(f"  {len(by_tla)} teams, {len(matches)} matches, {len(standings)} groups")

    # ── 1. wc_teams.json ────────────────────────────────────────────────────────
    wc_teams = []
    for tla in sorted(by_tla):
        wc_teams.append({
            "tla": tla,
            "name": team_name_api.get(tla) or by_tla[tla].get("name"),
            "group": team_group.get(tla, "?"),
            "confederation": CONFEDERATION.get(tla, "UEFA"),
            "iso2": ISO2.get(tla, ""),
        })
    (DATA_DIR / "wc_teams.json").write_text(json.dumps(wc_teams, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote wc_teams.json ({len(wc_teams)} teams)")

    # ── 2. players_baseline.json (keep existing 8, add the other 40) ─────────────
    pb_path = DATA_DIR / "players_baseline.json"
    existing = json.loads(pb_path.read_text(encoding="utf-8"))
    kept = [p for p in existing["players"] if p["team_code"] in KEEP_TEAMS]
    new_players, skipped = [], []
    for tla in sorted(by_tla):
        if tla in KEEP_TEAMS:
            continue
        squad = by_tla[tla].get("squad") or []
        if len(squad) < 23:
            skipped.append(f"{tla}({len(squad)})"); continue
        by_broad = defaultdict(list)
        for pl in squad:
            by_broad[pl.get("position", "Midfield")].append(pl)
        jersey = 1
        for broad in ["Goalkeeper", "Defence", "Midfield", "Offence"]:
            grp = by_broad.get(broad, [])
            for idx, pl in enumerate(grp):
                ps, pd = map_position(broad, idx, len(grp))
                new_players.append(to_baseline(pl, team_name_api.get(tla) or by_tla[tla].get("name"), tla, jersey, ps, pd))
                jersey += 1
    if skipped:
        print(f"  WARNING: teams with thin squads skipped: {skipped}")
    all_players = kept + new_players
    from collections import Counter
    tc = Counter(p["team"] for p in all_players)
    existing["players"] = all_players
    existing["metadata"]["total_players"] = len(all_players)
    existing["metadata"]["teams_included"] = sorted(tc.keys())
    existing["metadata"]["team_counts"] = dict(tc)
    existing["metadata"]["groups_included"] = sorted(set(team_group.values()))
    existing["metadata"]["generator_version"] = "3.0.0"
    pb_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote players_baseline.json (kept {len(kept)} + added {len(new_players)} = {len(all_players)})")

    # ── 3. wc_fixtures.json (all 104) ────────────────────────────────────────────
    fixtures = []
    for m in matches:
        h, a = m["homeTeam"], m["awayTeam"]
        htla, atla = h.get("tla"), a.get("tla")
        date = (m.get("utcDate") or "")[:10]
        stage = m.get("stage")
        grp = (m.get("group") or "").replace("GROUP_", "") if m.get("group") else None
        sc = m.get("score", {}).get("fullTime", {})
        # match_id mirrors the existing convention: DATE_HOME_AWAY
        mid = f"{date}_{htla}_{atla}" if htla and atla else f"match_{m.get('id')}"
        fixtures.append({
            "match_id": mid, "fd_id": m.get("id"),
            "home": htla, "away": atla,
            "home_name": h.get("name"), "away_name": a.get("name"),
            "date": date, "stage": stage, "group": grp,
            "status": m.get("status"),
            "home_score": sc.get("home"), "away_score": sc.get("away"),
        })
    (DATA_DIR / "wc_fixtures.json").write_text(json.dumps(fixtures, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote wc_fixtures.json ({len(fixtures)} matches)")

    # ── 4. wc_ratings.json (attack/defence from live results) ────────────────────
    gf, ga, n = defaultdict(float), defaultdict(float), defaultdict(int)
    for m in matches:
        if m.get("status") != "FINISHED":
            continue
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None:
            continue
        htla, atla = m["homeTeam"].get("tla"), m["awayTeam"].get("tla")
        if not htla or not atla:
            continue
        gf[htla] += hg; ga[htla] += ag; n[htla] += 1
        gf[atla] += ag; ga[atla] += hg; n[atla] += 1
    total_goals = sum(gf.values()); total_n = sum(n.values())
    avg = (total_goals / total_n) if total_n else 1.3  # tournament avg goals/team/match
    # WC group samples are tiny (~3 games), so shrink toward neutral (1.0) and clamp
    # to keep noisy ratings from exploding once confederation factors are applied.
    SHRINK_REF, LO, HI = 5.0, 0.45, 1.90
    ratings = {}
    for tla in sorted(by_tla):
        if n[tla] > 0:
            atk = (gf[tla] / n[tla]) / avg
            dfc = (ga[tla] / n[tla]) / avg
            alpha = min(n[tla], SHRINK_REF) / SHRINK_REF
            atk = alpha * atk + (1 - alpha) * 1.0
            dfc = alpha * dfc + (1 - alpha) * 1.0
        else:
            atk, dfc = 1.0, 1.0
        ratings[tla] = {
            "attack_rating": round(min(HI, max(LO, atk)), 4),
            "defence_rating": round(min(HI, max(LO, dfc)), 4),
            "stdev_scored": 1.0, "stdev_conceded": 1.0,
            "n_results": n[tla],
        }
    (DATA_DIR / "wc_ratings.json").write_text(json.dumps(ratings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote wc_ratings.json (avg goals/team/match = {avg:.2f})")

    print("\nDone. Teams:")
    for team, cnt in sorted(tc.items()):
        print(f"  {team}: {cnt}")

if __name__ == "__main__":
    main()
