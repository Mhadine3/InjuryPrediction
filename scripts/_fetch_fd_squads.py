"""Fetch WC 2026 Group I squads from football-data.org v4."""
import json, os, sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
if not API_KEY:
    print("set FOOTBALL_DATA_API_KEY in your environment / .env"); sys.exit(1)
BASE    = "https://api.football-data.org/v4"
HDR     = {"X-Auth-Token": API_KEY}

TARGET_CODES = {"FRA", "SEN", "IRQ", "NOR"}

# 1. Try WC competition teams endpoint
for ep in [
    f"{BASE}/competitions/WC/teams",
    f"{BASE}/competitions/WC/teams?season=2026",
    f"{BASE}/competitions/2000/teams",
    f"{BASE}/competitions/2000/teams?season=2026",
]:
    print(f"\nGET {ep}")
    r = requests.get(ep, headers=HDR, timeout=15)
    print(f"  HTTP {r.status_code}  len={len(r.text)}")
    if r.status_code == 200:
        data = r.json()
        teams = data.get("teams", [])
        print(f"  Teams in response: {len(teams)}")
        # filter our 4
        for t in teams:
            tla = (t.get("tla") or "").upper()
            if tla in TARGET_CODES:
                squad = t.get("squad", [])
                print(f"  {tla}: {t.get('name')} — squad size: {len(squad)}")
                for p in squad[:5]:
                    print(f"    {p.get('name')} | {p.get('position')} | {p.get('nationality')}")
        break
    else:
        try:
            print(f"  Error: {r.json().get('message','')}")
        except Exception:
            print(f"  Body: {r.text[:200]}")

# 2. Try direct team lookup
print("\n--- Direct team lookups ---")
TEAM_IDS = {
    "FRA": 773,   # France
    "SEN": 907,   # Senegal
    "IRQ": 890,   # Iraq
    "NOR": 781,   # Norway
}
for tla, tid in TEAM_IDS.items():
    r2 = requests.get(f"{BASE}/teams/{tid}", headers=HDR, timeout=10)
    print(f"\n{tla} (id={tid}): HTTP {r2.status_code}")
    if r2.status_code == 200:
        d = r2.json()
        squad = d.get("squad", [])
        print(f"  Name: {d.get('name')} | Squad size: {len(squad)}")
        for p in squad[:8]:
            print(f"  {p.get('name'):<28} {p.get('position'):<20} {p.get('nationality')}")
    else:
        try:
            print(f"  Error: {r2.json().get('message','')}")
        except Exception:
            print(f"  {r2.text[:150]}")
