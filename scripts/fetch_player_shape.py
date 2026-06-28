"""
fetch_player_shape.py
=====================
Fetches the last 5 club matches for every Brazil and Morocco player
from Football-Data.org and computes a shape score that seeds each
player's physical condition going into the tournament.

Shape model
-----------
  minutes_last_28d  — total competitive minutes in the 28 days before camp
  match_load_au     — estimated sRPE: minutes × RPE_match (8.0 AU/min proxy)
  days_since_last   — detraining penalty applied to chronic load
  form_score        — composite 0.0-1.0 (1.0 = peak form, 0.0 = long-term inactive)

Output
------
  data/player_shape.json   — shape data per player for use by simulate_match.py

Usage
-----
    python scripts/fetch_player_shape.py

Note: Free tier rate limit = 10 req/min. Script sleeps automatically.
"""

import json
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).resolve().parent.parent
DATA_DIR     = ROOT / "data"
MAPPING_FILE = DATA_DIR / "api_player_ids.json"
OUTPUT_FILE  = DATA_DIR / "player_shape.json"

# ── Config ─────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
import os
API_KEY  = os.getenv("FOOTBALL_DATA_API_KEY", "")
BASE_URL = "https://api.football-data.org/v4"
HEADERS  = {"X-Auth-Token": API_KEY}

CAMP_START         = date(2026, 5, 20)   # approx national team camp start
TRAINING_END       = date(2026, 5, 29)   # our synthetic data ends here
SHAPE_WINDOW_DAYS  = 56                  # look back 8 weeks for club form
REQUESTS_PER_MIN   = 9                   # stay under free-tier limit of 10
SLEEP_BETWEEN      = 60.0 / REQUESTS_PER_MIN

# GK plays full 90 min every match; field players sometimes less
POSITION_MINUTES_ESTIMATE = {
    "Goalkeeper": 90, "Center Back": 87, "Full Back": 85,
    "Wingback": 83, "Defensive Mid": 88, "Central Mid": 85,
    "Attacking Mid": 78, "Winger": 75, "Second Striker": 72, "Striker": 78,
}
RPE_MATCH_PROXY = 8.0   # Foster 2001 CR-10: competitive match ≈ 8.0


def norm(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower().strip()


def fetch_player_matches(api_id: int) -> list[dict]:
    url = f"{BASE_URL}/persons/{api_id}/matches?limit=8"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        print(f"    [WARN] API error for id={api_id}: {e}")
        return []


def compute_shape(matches: list[dict], position_detail: str, reference_date: date) -> dict:
    """
    Compute shape score from last 5 club matches before the national team camp.
    Returns a dict that simulate_match.py uses to calibrate the player's state.
    """
    cutoff    = reference_date - timedelta(days=SHAPE_WINDOW_DAYS)
    est_mins  = POSITION_MINUTES_ESTIMATE.get(position_detail, 80)

    relevant  = []
    for m in matches:
        m_date = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).date()
        if cutoff <= m_date < CAMP_START:
            relevant.append(m_date)

    relevant.sort(reverse=True)
    recent5  = relevant[:5]

    # Minutes in last 28 days before camp
    window_28 = CAMP_START - timedelta(days=28)
    mins_28d  = sum(est_mins for d in recent5 if d >= window_28)

    # Estimated sRPE load per match
    srpe_per_match = round(est_mins * RPE_MATCH_PROXY)
    total_srpe_28d = round(mins_28d / est_mins * srpe_per_match) if est_mins > 0 else 0

    # Days since last club match (detraining)
    days_since = (CAMP_START - recent5[0]).days if recent5 else 60

    # Form score: 5 matches in window = 1.0; 0 matches = 0.2 (minimum baseline)
    form_score = round(min(1.0, 0.2 + len(recent5) * 0.16), 2)

    # Detraining penalty: >21 days since last match reduces form
    if days_since > 21:
        form_score = round(form_score * max(0.7, 1.0 - (days_since - 21) / 100), 2)

    return {
        "matches_last_8wk":      len(relevant),
        "matches_last_28d":      sum(1 for d in recent5 if d >= window_28),
        "est_minutes_last_28d":  mins_28d,
        "est_srpe_last_28d":     total_srpe_28d,
        "days_since_last_club":  days_since,
        "form_score":            form_score,
        "last_5_match_dates":    [d.isoformat() for d in recent5],
    }


def main() -> None:
    if not API_KEY:
        print("[ERROR] FOOTBALL_DATA_API_KEY not set in .env")
        return

    with open(MAPPING_FILE, encoding="utf-8") as f:
        mapping = json.load(f)

    print("=" * 60)
    print("  Fetching player shape — Football-Data.org")
    print(f"  Players : {len(mapping)} (BRA + MAR)")
    print(f"  Shape window : last {SHAPE_WINDOW_DAYS} days before {CAMP_START}")
    print(f"  Rate limit : {REQUESTS_PER_MIN} req/min  (~{len(mapping)//REQUESTS_PER_MIN+1} min total)")
    print("=" * 60)

    results   = {}
    ref_date  = date.today()

    for i, player in enumerate(mapping):
        pid      = player["our_player_id"]
        api_id   = player["api_player_id"]
        name     = player["our_name"]
        tc       = player["team_code"]
        pos      = player["position_detail"]

        print(f"  [{i+1:>2}/{len(mapping)}] {name:<30} ({tc}) ...", end=" ", flush=True)

        matches  = fetch_player_matches(api_id)
        shape    = compute_shape(matches, pos, ref_date)

        results[pid] = {
            "our_player_id":    pid,
            "api_player_id":    api_id,
            "name":             name,
            "team_code":        tc,
            "position_detail":  pos,
            **shape,
        }

        status = f"form={shape['form_score']:.2f}  matches={shape['matches_last_8wk']}  days_off={shape['days_since_last_club']}"
        print(status)

        # Respect rate limit
        if i < len(mapping) - 1:
            time.sleep(SLEEP_BETWEEN)

    # Save
    output = {
        "generated_at":    ref_date.isoformat(),
        "api_source":      "football-data.org v4",
        "shape_window_days": SHAPE_WINDOW_DAYS,
        "camp_start":      CAMP_START.isoformat(),
        "players":         results,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print()
    print(f"Saved -> {OUTPUT_FILE.name}")

    # Summary
    bra = [v for v in results.values() if v["team_code"] == "BRA"]
    mar = [v for v in results.values() if v["team_code"] == "MAR"]
    for team, players in [("Brazil", bra), ("Morocco", mar)]:
        avg_form = sum(p["form_score"] for p in players) / len(players)
        avg_days = sum(p["days_since_last_club"] for p in players) / len(players)
        low_form = [p["name"] for p in players if p["form_score"] < 0.5]
        print(f"\n  {team}: avg form={avg_form:.2f}  avg days off={avg_days:.0f}")
        if low_form:
            print(f"    Low form (<0.5): {', '.join(low_form)}")

    print()


if __name__ == "__main__":
    main()
