"""
probe_bsd2.py — Deep BSD coverage report.
Finds WC 2026, maps 8 national teams, checks event stats + odds.
"""

import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

_env = {**load_env(ROOT / ".env"), **load_env(ROOT / "backend" / ".env")}
BSD_TOKEN = _env.get("BSD_TOKEN", "")
BASE = "https://sports.bzzoiro.com/api/v2"
HDR  = {"Authorization": f"Token {BSD_TOKEN}"}

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

def get(path, params=None):
    url = f"{BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, headers=HDR, params=params, timeout=15)
        time.sleep(0.25)
        try: return r.status_code, r.json()
        except: return r.status_code, {"raw": r.text[:300]}
    except Exception as e:
        return 0, {"error": str(e)}

def get_all(path, params=None, max_pages=5):
    """Paginate and collect all results."""
    all_results = []
    url = f"{BASE}/{path.lstrip('/')}"
    p = dict(params or {})
    p["limit"] = 100
    for _ in range(max_pages):
        try:
            r = requests.get(url, headers=HDR, params=p, timeout=15)
            time.sleep(0.25)
            if r.status_code != 200:
                break
            data = r.json()
            all_results.extend(data.get("results", []))
            nxt = data.get("next")
            if not nxt:
                break
            url = nxt
            p = {}
        except Exception:
            break
    return all_results

print("=" * 70)
print("BSD DEEP PROBE")
print("=" * 70)

# ── 1. All leagues — find WC 2026 ────────────────────────────────────────────
print("\n[1] ALL LEAGUES")
leagues = get_all("/leagues/")
print(f"  Total leagues: {len(leagues)}")
wc_leagues = [l for l in leagues if "world" in l["name"].lower() or "cup" in l["name"].lower() or "fifa" in l["name"].lower()]
print(f"  WC/Cup-related ({len(wc_leagues)}):")
for l in wc_leagues:
    cs = l.get("current_season") or {}
    print(f"    id={l['id']:>4}  {l['name']:<45}  season_id={cs.get('id','?')}")

# ── 2. Team search — all 8 national teams ────────────────────────────────────
print("\n[2] NATIONAL TEAM SEARCH")
TARGETS = {
    "Brazil":   ["Brazil","Brasil"],
    "Morocco":  ["Morocco","Maroc"],
    "Haiti":    ["Haiti"],
    "Scotland": ["Scotland"],
    "France":   ["France"],
    "Senegal":  ["Senegal"],
    "Iraq":     ["Iraq"],
    "Norway":   ["Norway","Norge"],
}
team_id_map = {}  # country_name -> team_id

teams_all = get_all("/teams/", max_pages=10)
print(f"  Loaded {len(teams_all)} teams from API")
for country, aliases in TARGETS.items():
    found = None
    for alias in aliases:
        found = next((t for t in teams_all if alias.lower() in t["name"].lower()), None)
        if found:
            break
    if found:
        team_id_map[country] = found["id"]
        print(f"  {country:<12} -> id={found['id']:>5}  name='{found['name']}'  short='{found.get('short_name','')}'")
    else:
        print(f"  {country:<12} -> NOT FOUND in /teams/")

# ── 3. WC 2026 events ────────────────────────────────────────────────────────
print("\n[3] WC 2026 EVENTS")
# Try known WC league IDs from league list
wc_id = next((l["id"] for l in wc_leagues
              if "2026" in l["name"] or ("world" in l["name"].lower() and "qual" not in l["name"].lower())), None)
if not wc_id:
    # Try the WC qualification leagues
    wc_id = next((l["id"] for l in wc_leagues if "qualification" not in l["name"].lower()), None)
print(f"  Best WC league candidate: id={wc_id}")

if wc_id:
    sc, body = get(f"/leagues/{wc_id}/")
    print(f"  GET /leagues/{wc_id}/ -> HTTP {sc}")
    if sc == 200:
        print(json.dumps(body, indent=2, default=str)[:1000])

    # Get events for this league
    sc, body = get("/events/", {"league_id": wc_id, "limit": 10})
    print(f"\n  GET /events/?league_id={wc_id} -> HTTP {sc}  count={body.get('count','?')}")
    if sc == 200 and body.get("results"):
        e = body["results"][0]
        print("  Sample event keys:", list(e.keys()))
        print("  Sample event:")
        print(json.dumps(e, indent=4, default=str)[:1500])

# ── 4. Search events by team ─────────────────────────────────────────────────
print("\n[4] EVENTS BY TEAM (Brazil if found)")
bra_id = team_id_map.get("Brazil")
if bra_id:
    sc, body = get("/events/", {"team_id": bra_id, "limit": 5})
    if sc != 200 or not body.get("results"):
        sc, body = get("/events/", {"home_team_id": bra_id, "limit": 5})
    if sc != 200 or not body.get("results"):
        sc, body = get("/events/", {"team": bra_id, "limit": 5})
    print(f"  GET /events/?team_id={bra_id} -> HTTP {sc}  count={body.get('count','?')}")
    if sc == 200 and body.get("results"):
        e = body["results"][0]
        print("  Sample Brazil event:", json.dumps(e, indent=4, default=str)[:800])

# ── 5. Event detail (check for stats) ────────────────────────────────────────
print("\n[5] EVENT DETAIL with STATS")
sc, body = get("/events/", {"limit": 1, "status": "finished"})
if sc == 200 and body.get("results"):
    eid = body["results"][0]["id"]
    sc2, ev = get(f"/events/{eid}/")
    print(f"  GET /events/{eid}/ -> HTTP {sc2}")
    if sc2 == 200:
        print("  Keys:", list(ev.keys()))
        # Look for stats subobjects
        for k in ["stats", "statistics", "match_stats", "home_stats", "away_stats", "shots", "corners", "fouls"]:
            if k in ev:
                print(f"  '{k}' found:", json.dumps(ev[k], default=str)[:500])
        print("  Full event sample:")
        print(json.dumps(ev, indent=2, default=str)[:2000])

    # Try stats sub-endpoint
    sc3, stats = get(f"/events/{eid}/stats/")
    print(f"\n  GET /events/{eid}/stats/ -> HTTP {sc3}")
    if sc3 == 200:
        print(json.dumps(stats, indent=2, default=str)[:1000])

    sc4, stats2 = get(f"/events/{eid}/statistics/")
    print(f"  GET /events/{eid}/statistics/ -> HTTP {sc4}")

# ── 6. Player detail (stats) ──────────────────────────────────────────────────
print("\n[6] PLAYER DETAIL (France national team)")
fra_id = team_id_map.get("France")
if fra_id:
    sc, body = get("/players/", {"national_team_id": fra_id, "limit": 5})
    print(f"  GET /players/?national_team_id={fra_id} -> HTTP {sc}  count={body.get('count','?')}")
    if sc == 200 and body.get("results"):
        p = body["results"][0]
        print("  Sample player keys:", list(p.keys()))
        print("  Player:", json.dumps(p, indent=2, default=str)[:600])
        pid = p["id"]
        sc2, pd = get(f"/players/{pid}/")
        print(f"\n  GET /players/{pid}/ -> HTTP {sc2}")
        if sc2 == 200:
            print("  Detail keys:", list(pd.keys()))
            print(json.dumps(pd, indent=2, default=str)[:800])
        sc3, pst = get(f"/players/{pid}/statistics/")
        print(f"\n  GET /players/{pid}/statistics/ -> HTTP {sc3}")
        if sc3 == 200:
            print(json.dumps(pst, indent=2, default=str)[:800])

# ── 7. Odds for upcoming WC fixtures ─────────────────────────────────────────
print("\n[7] ODDS — upcoming WC fixtures")
# Find recent odds for a WC event
sc, body = get("/odds/", {"limit": 5})
if sc == 200 and body.get("results"):
    sample_event_id = body["results"][0]["event_id"]
    print(f"  Sample odds event_id: {sample_event_id}")
    sc2, ev = get(f"/events/{sample_event_id}/")
    if sc2 == 200:
        print(f"  Event: {ev.get('home_team')} vs {ev.get('away_team')}  date={ev.get('event_date','')[:10]}  status={ev.get('status')}")
    # Get all odds for this event
    sc3, ods = get("/odds/", {"event_id": sample_event_id, "limit": 20})
    if sc3 == 200:
        markets = {}
        for o in ods.get("results", []):
            m = o["market"]
            markets.setdefault(m, []).append({
                "outcome": o["outcome"],
                "bookmaker": o["bookmaker_slug"],
                "decimal": o["decimal_odds"],
                "implied_prob": o["implied_probability"],
            })
        print("  Markets available:", sorted(markets.keys()))
        for mkt, entries in list(markets.items())[:3]:
            print(f"\n  [{mkt}]")
            for e in entries[:4]:
                print(f"    {e}")

# ── 8. Player season stats endpoint ──────────────────────────────────────────
print("\n[8] PLAYER SEASON STATS")
for path in ["/player-statistics/", "/player-season-stats/", "/statistics/", "/season-stats/"]:
    sc, body = get(path)
    print(f"  GET {path} -> HTTP {sc}")
    if sc == 200:
        print("  Keys:", list(body.keys()) if isinstance(body, dict) else "list")
        if isinstance(body, dict) and body.get("results"):
            print("  Sample:", json.dumps(body["results"][0], indent=2, default=str)[:400])
        break

# ── 9. Event statistics endpoint ─────────────────────────────────────────────
print("\n[9] EVENT STATISTICS (finished match)")
sc, body = get("/events/", {"status": "finished", "limit": 3})
if sc == 200 and body.get("results"):
    for ev in body["results"]:
        eid = ev["id"]
        sc2, stat = get(f"/events/{eid}/statistics/")
        if sc2 == 200:
            print(f"  Event {eid} ({ev.get('home_team')} vs {ev.get('away_team')}) statistics:")
            print(json.dumps(stat, indent=2, default=str)[:1000])
            break

print("\n" + "=" * 70)
print("PROBE 2 DONE")
print("=" * 70)
