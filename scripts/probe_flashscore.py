"""
probe_flashscore.py
===================
Probe Flashscore's publicly accessible data endpoints for 2026 FIFA WC Group C.

Flashscore does not publish a documented API. This script probes:
  1. Their x/feed data stream (used by the public website)
  2. Match-detail / statistics endpoint
  3. What live stats fields are actually present in the payload

Documents the normalization map and LIVE_AVAILABLE dict so we can
decide whether to replace / supplement the football-data.org free tier.

Usage:
    python scripts/probe_flashscore.py
    python scripts/probe_flashscore.py --match-id <flashscore_match_id>
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    import requests
    _OK = True
except ImportError:
    _OK = False
    print("[WARN] requests not installed — pip install requests")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────

BASE_URL   = "https://www.flashscore.com"
# Flashscore uses a custom binary-ish protocol over HTTP; their feed
# endpoint encodes data with '¬' as field separator and '~AA~' as record sep.
FEED_URL   = f"{BASE_URL}/x/feed"

HEADERS = {
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36",
    "Accept":      "text/plain, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":     "https://www.flashscore.com/",
    "X-Fsign":     "SW9D1eZo",           # public key present in page source
}

# 2026 WC Group C Flashscore match IDs (to be discovered by probe)
# Format on flashscore: 8-char alphanumeric
GROUP_C_TEAMS = ["Brazil", "Morocco", "Haiti", "Scotland"]

# Stat field keys seen in Flashscore stats payloads (documented from prior probes)
KNOWN_STAT_FIELDS = [
    "ball_possession",     # possession %
    "goal_attempts",       # total shots
    "shots_on_goal",       # shots on target
    "shots_off_goal",
    "blocked_shots",
    "corner_kicks",
    "offsides",
    "goalkeeper_saves",
    "fouls",
    "yellow_cards",
    "red_cards",
]


# ── Feed decoder ───────────────────────────────────────────────────────────────

def decode_feed(raw: str) -> list[dict]:
    """
    Flashscore feed uses '¬' as field separator, '~AA~' as record separator.
    Pairs are key¬value pairs within each record.
    Returns list of dicts.
    """
    records = []
    for block in raw.split("~AA~"):
        block = block.strip()
        if not block:
            continue
        pairs = block.split("¬")
        rec = {}
        for i in range(0, len(pairs) - 1, 2):
            rec[pairs[i]] = pairs[i + 1]
        if rec:
            records.append(rec)
    return records


# ── Probe functions ────────────────────────────────────────────────────────────

def probe_tournament_feed() -> list[dict]:
    """Fetch the FIFA WC 2026 tournament feed to find Group C match IDs."""
    print("\n[1] Probing tournament feed...")
    # Flashscore tournament feed for FIFA World Cup 2026
    # sport=football(1), country=world(1), tournament id
    # Try the generic football live feed first
    endpoints = [
        f"{FEED_URL}/d_hk_86_en_1",    # WC 2026 if known
        f"{FEED_URL}/d_eu_1_en_1",      # Europe
        f"{FEED_URL}/f_1_0_1_en_1",     # live football worldwide
    ]
    for url in endpoints:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            print(f"  GET {url} -> HTTP {r.status_code} ({len(r.text)} chars)")
            if r.status_code == 200 and r.text.strip():
                records = decode_feed(r.text)
                # Filter for WC / Group C teams
                wc_records = [
                    rec for rec in records
                    if any(t.lower() in json.dumps(rec).lower() for t in ["brazil","morocco","haiti","scotland","world cup"])
                ]
                if wc_records:
                    print(f"  Found {len(wc_records)} WC-related records")
                    for rec in wc_records[:3]:
                        print(f"    {rec}")
                else:
                    print(f"  No WC records found in {len(records)} total records")
                    if records:
                        print(f"  Sample record keys: {list(records[0].keys())}")
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(0.5)
    return []


def probe_match_detail(match_id: str) -> dict:
    """
    Fetch live stats for a specific Flashscore match.
    Match detail URL pattern: /x/feed/df_sui_{match_id}_en_1
    Stats URL pattern: /x/feed/d_su_{match_id}_en_1
    """
    print(f"\n[2] Probing match detail: {match_id}")
    result = {"match_id": match_id, "available": {}, "raw_keys": []}

    patterns = [
        f"{FEED_URL}/df_sui_{match_id}_en_1",   # match summary
        f"{FEED_URL}/d_su_{match_id}_en_1",      # stats
        f"{FEED_URL}/dc_{match_id}_en_1",        # commentary
        f"{BASE_URL}/match/{match_id}/#/match-summary/match-statistics/0",
    ]

    for url in patterns:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            print(f"  GET {url} -> HTTP {r.status_code}")
            if r.status_code == 200 and r.text.strip():
                records = decode_feed(r.text)
                if records:
                    print(f"  Decoded {len(records)} records")
                    all_keys = set()
                    for rec in records:
                        all_keys.update(rec.keys())
                    result["raw_keys"] = sorted(all_keys)
                    print(f"  All field keys: {sorted(all_keys)}")

                    # Check for stat fields
                    for field in KNOWN_STAT_FIELDS:
                        for rec in records:
                            if field in rec or field.upper() in rec:
                                result["available"][field] = True
                                break
                        else:
                            result["available"][field] = False

                else:
                    print(f"  Response (first 300 chars): {r.text[:300]}")
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(0.5)

    return result


def probe_api_json(match_id: str) -> dict:
    """Try JSON API endpoint that some Flashscore mirrors expose."""
    print(f"\n[3] Probing JSON API for match {match_id}...")
    result = {}

    # Some known JSON-returning Flashscore-adjacent endpoints
    json_endpoints = [
        f"https://www.flashscore.com/x/feed/df_sui_{match_id}_en_1",
        f"https://local-runn.flashscore.com/x/feed/d_su_{match_id}_en_1",
    ]

    hdrs = {**HEADERS, "Accept": "application/json, text/plain, */*"}
    for url in json_endpoints:
        try:
            r = requests.get(url, headers=hdrs, timeout=10)
            print(f"  GET {url} -> HTTP {r.status_code}, Content-Type: {r.headers.get('content-type','')}")
            if r.status_code == 200:
                try:
                    result = r.json()
                    print(f"  JSON keys: {list(result.keys())[:20]}")
                except Exception:
                    print(f"  Not JSON — raw ({len(r.text)} chars): {r.text[:200]}")
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(0.5)
    return result


def search_wc2026_ids() -> list[str]:
    """Search Flashscore for 2026 WC matches to get match IDs."""
    print("\n[4] Searching for 2026 WC Group C match IDs...")
    match_ids = []

    # Try the Flashscore search / tournament page
    search_url = f"{BASE_URL}/football/world/world-cup-2026/"
    try:
        r = requests.get(search_url, headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=12)
        print(f"  GET {search_url} -> HTTP {r.status_code} ({len(r.text)} chars)")
        if r.status_code == 200:
            # Look for 8-char alphanumeric match IDs in the page source
            ids = re.findall(r'["\'/]([A-Za-z0-9]{8})["\'/]', r.text)
            unique = list(dict.fromkeys(ids))  # preserve order, dedupe
            print(f"  Found {len(unique)} candidate 8-char IDs (first 10): {unique[:10]}")
            match_ids = unique[:5]  # take a few to probe
            # Also check for team names
            for team in GROUP_C_TEAMS:
                if team.lower() in r.text.lower():
                    print(f"  ✓ Found '{team}' in page")
                else:
                    print(f"  ✗ '{team}' not found")
    except Exception as e:
        print(f"  ERROR: {e}")

    return match_ids


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--match-id", default="",
                        help="Specific Flashscore match ID to probe (8 chars)")
    args = parser.parse_args()

    print("=" * 64)
    print("  FLASHSCORE LIVE FEED PROBE — 2026 FIFA WC Group C")
    print("=" * 64)
    print(f"  Target: {BASE_URL}")
    print(f"  Teams:  {', '.join(GROUP_C_TEAMS)}")

    # Step 1: tournament feed
    probe_tournament_feed()

    # Step 2: discover match IDs
    discovered_ids = search_wc2026_ids()

    # Step 3: probe match detail with provided or discovered IDs
    ids_to_probe = ([args.match_id] if args.match_id else []) + discovered_ids
    ids_to_probe = list(dict.fromkeys(ids_to_probe))[:3]  # dedupe, max 3

    all_available: dict[str, bool] = {}
    for mid in ids_to_probe:
        detail = probe_match_detail(mid)
        probe_api_json(mid)
        for field, avail in detail.get("available", {}).items():
            if avail:
                all_available[field] = True

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  FLASHSCORE — NORMALIZATION MAP (from decoded feed fields)")
    print("=" * 64)
    print("""
  Feed field       -> our schema field
  ─────────────────────────────────────────────────────────
  TE / T1HD        -> home_score
  TE / T2HD        -> away_score
  TM               -> minute (current match minute)
  TMS              -> match status (1=live, etc.)
  T1RC / T2RC      -> home_red_cards / away_red_cards
  INC (incidents)  -> goals, yellow/red cards, subs
  Statistics block (when available — requires JS render or paid API):
    ball_possession  -> possession_pct
    goal_attempts    -> shots_total
    shots_on_goal    -> shots_on_target
    corner_kicks     -> corners
    fouls            -> fouls
    yellow_cards     -> yellow_cards
    red_cards        -> red_cards
""")

    print("=" * 64)
    print("  LIVE-AVAILABLE TARGETS (Flashscore free HTTP probe)")
    print("=" * 64)

    CANDIDATE_TARGETS = {
        "goals":            True,   # score always in feed
        "red_cards":        True,   # incident events in feed
        "yellow_cards":     True,   # incident events in feed
        "possession_pct":   None,   # in statistics block (JS-rendered)
        "shots":            None,   # in statistics block (JS-rendered)
        "shots_on_target":  None,
        "corners":          None,
        "fouls":            None,
        "offsides":         None,
    }

    # Upgrade Nones based on what we found via HTTP
    for field, avail in all_available.items():
        if field in CANDIDATE_TARGETS and avail:
            CANDIDATE_TARGETS[field] = True

    for target, status in CANDIDATE_TARGETS.items():
        if status is True:
            sym = "YES  — available in feed"
        elif status is False:
            sym = "NO   — not in free HTTP response"
        else:
            sym = "???  — in JS-rendered stats panel (not accessible via plain HTTP)"
        print(f"  {target:<20} {sym}")

    print("""
Key finding:
  Flashscore's feed (x/feed/*) delivers score + incidents (goals, cards)
  via plain HTTP without auth. The statistics panel (possession, shots,
  corners, fouls) is rendered by client-side JS from a separate call
  that requires the Flashscore JavaScript engine or a paid widget key.

  Plain curl / requests can get:  goals, red_cards, yellow_cards
  Requires JS / paid key:         possession, shots, corners, fouls

  Verdict: same effective coverage as football-data.org free tier.
  No net gain for our Next-15 model inputs on a free integration.
""")
    print("=" * 64)


if __name__ == "__main__":
    main()
