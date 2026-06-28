"""
probe_bsd3.py — Targeted BSD probe:
  - Find group-stage WC 2026 events + map national team IDs
  - Check qual competition results for form
  - Check event stats + odds coverage
  - Check player stats sub-endpoint
"""

import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
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
    url = f"{BASE}/{path.lstrip('/')}" if not path.startswith("http") else path
    r = requests.get(url, headers=HDR, params=params, timeout=15)
    time.sleep(0.3)
    try: return r.status_code, r.json()
    except: return r.status_code, {"raw": r.text[:300]}

def get_all_pages(path, params=None, max_pages=10):
    results = []
    url = f"{BASE}/{path.lstrip('/')}"
    p = {**(params or {}), "limit": 100}
    for _ in range(max_pages):
        r = requests.get(url, headers=HDR, params=p, timeout=15)
        time.sleep(0.3)
        if r.status_code != 200: break
        data = r.json()
        results.extend(data.get("results", []))
        nxt = data.get("next")
        if not nxt: break
        url = nxt
        p = {}
    return results

print("=" * 70)
print("BSD PROBE 3 — WC 2026 Group Stage + Coverage")
print("=" * 70)

# ── 1. WC 2026 group stage events (first 3 pages) ────────────────────────────
print("\n[1] WC 2026 GROUP STAGE EVENTS")
wc_events = get_all_pages("/events/", {"league_id": 27}, max_pages=3)
print(f"  Fetched {len(wc_events)} WC 2026 events")

TARGET_NAMES = {
    "brazil", "morocco", "haiti", "scotland",
    "france", "senegal", "iraq", "norway"
}
team_id_map = {}  # name -> id

group_events = []
for ev in wc_events:
    ht = ev.get("home_team", "").lower()
    at = ev.get("away_team", "").lower()
    is_target = any(t in ht or t in at for t in TARGET_NAMES)
    if is_target:
        group_events.append(ev)
        for name in TARGET_NAMES:
            if name in ht and name not in team_id_map:
                team_id_map[name] = ev["home_team_id"]
            if name in at and name not in team_id_map:
                team_id_map[name] = ev["away_team_id"]

print(f"  Group C/I events found: {len(group_events)}")
print(f"  Team ID map: {team_id_map}")
if group_events:
    print("\n  Group C/I fixtures:")
    for ev in group_events[:20]:
        status = ev.get("status", "?")
        score = f"{ev.get('home_score','?')}-{ev.get('away_score','?')}" if status == "finished" else "upcoming"
        print(f"    id={ev['id']}  {ev['home_team']} vs {ev['away_team']}  {ev.get('event_date','')[:10]}  {status}  {score}")

# ── 2. Stats for a finished WC group-stage event ─────────────────────────────
print("\n[2] WC EVENT STATS (finished match)")
finished_wc = [e for e in group_events if e.get("status") == "finished"]
print(f"  Finished group events: {len(finished_wc)}")
if finished_wc:
    eid = finished_wc[0]["id"]
    sc, stats = get(f"/events/{eid}/stats/")
    print(f"  GET /events/{eid}/stats/ -> HTTP {sc}")
    if sc == 200:
        print(json.dumps(stats, indent=2, default=str)[:1500])
else:
    # Get stats for any recently finished event
    all_finished = [e for e in wc_events if e.get("status") == "finished"]
    if all_finished:
        eid = all_finished[0]["id"]
        sc, stats = get(f"/events/{eid}/stats/")
        print(f"  GET /events/{eid}/stats/ -> HTTP {sc}")
        print(json.dumps(stats, indent=2, default=str)[:1500])

# ── 3. Odds for a WC event ───────────────────────────────────────────────────
print("\n[3] ODDS FOR WC 2026 EVENTS")
if group_events:
    # Try first upcoming group event
    upcoming = [e for e in group_events if e.get("status") != "finished"]
    test_event = (upcoming or group_events)[0]
    eid = test_event["id"]
    sc, odds = get("/odds/", {"event_id": eid, "limit": 50})
    print(f"  Event: {test_event['home_team']} vs {test_event['away_team']} (id={eid})")
    print(f"  GET /odds/?event_id={eid} -> HTTP {sc}  count={odds.get('count','?')}")
    if sc == 200 and odds.get("results"):
        markets = {}
        for o in odds["results"]:
            m = o["market"]
            markets.setdefault(m, []).append(o)
        print(f"  Markets: {sorted(markets.keys())}")
        for mkt in ["1x2", "over_under", "btts"]:
            if mkt in markets:
                print(f"\n  [{mkt}]")
                for o in markets[mkt][:6]:
                    print(f"    {o['bookmaker_name']:<15} {o['outcome']:<10} "
                          f"decimal={o['decimal_odds']:.2f}  implied={o['implied_probability']:.4f}")

# ── 4. Qualification results for our 8 teams ────────────────────────────────
print("\n[4] QUALIFICATION COMPETITIONS")
# WC qual leagues: CAF=60, AFC=61, UEFA=58, CONCACAF=62, CONMEBOL=59
qual_leagues = {
    "BRA/SCO/NOR/FRA": 58,   # UEFA
    "BRA/SCO": 59,            # CONMEBOL for BRA
    "MAR/SEN": 60,            # CAF
    "IRQ": 61,                # AFC
    "HAI": 62,                # CONCACAF
}

# Get qual results for each confederation
for label, lid in [(58, "UEFA"), (59, "CONMEBOL"), (60, "CAF"), (61, "AFC"), (62, "CONCACAF")]:
    sc, body = get("/events/", {"league_id": label, "status": "finished", "limit": 3})
    if sc == 200:
        cnt = body.get("count", 0)
        sample = body.get("results", [])
        teams_seen = set()
        for ev in sample:
            teams_seen.add(ev.get("home_team", "?"))
            teams_seen.add(ev.get("away_team", "?"))
        print(f"  League {label} ({lid}): {cnt} finished events")
        if sample:
            e = sample[0]
            print(f"    Sample: {e.get('home_team')} vs {e.get('away_team')} {e.get('event_date','')[:10]}")

# ── 5. Events with known team IDs ────────────────────────────────────────────
print("\n[5] EVENTS FILTERED BY TEAM ID")
for name, tid in list(team_id_map.items())[:4]:
    sc, body = get("/events/", {"home_team_id": tid, "status": "finished", "limit": 3})
    cnt_home = body.get("count", 0) if sc == 200 else "err"
    sc2, body2 = get("/events/", {"away_team_id": tid, "status": "finished", "limit": 3})
    cnt_away = body2.get("count", 0) if sc2 == 200 else "err"
    print(f"  {name.capitalize():<12} (id={tid}): {cnt_home} home finished, {cnt_away} away finished")
    # Also try league_id + team filter
    sc3, body3 = get("/events/", {"league_id": 27, "home_team_id": tid})
    sc4, body4 = get("/events/", {"league_id": 27, "away_team_id": tid})
    wc_cnt = (body3.get("count", 0) if sc3 == 200 else 0) + (body4.get("count", 0) if sc4 == 200 else 0)
    print(f"    WC 2026 events: {wc_cnt}")

# ── 6. Player search for national team ───────────────────────────────────────
print("\n[6] PLAYER SEARCH")
# Try by team_id for our national team
for name, tid in list(team_id_map.items())[:2]:
    for param in ["team_id", "national_team_id", "current_team_id"]:
        sc, body = get("/players/", {param: tid, "limit": 5})
        if sc == 200 and body.get("results"):
            print(f"  {name.capitalize()} (id={tid}) via {param}: {body.get('count')} players")
            p = body["results"][0]
            print(f"    Sample: {p.get('name')} pos={p.get('position')} nationality={p.get('nationality')}")
            # Check player stats sub-endpoint
            pid = p["id"]
            for sub in ["statistics", "stats", "season-stats", "goals"]:
                sc2, st = get(f"/players/{pid}/{sub}/")
                if sc2 == 200:
                    print(f"    /players/{pid}/{sub}/ -> 200: {json.dumps(st, default=str)[:400]}")
            break

# ── 7. Check AFCON / AFC events for our teams ────────────────────────────────
print("\n[7] AFCON (lid=30) / AFC Asian Cup (lid=68) EVENTS")
for lid, label in [(30, "AFCON"), (68, "AFC Asian Cup")]:
    evs = get_all_pages("/events/", {"league_id": lid, "status": "finished"}, max_pages=3)
    team_hits = {}
    for ev in evs:
        for t in ["senegal", "morocco", "iraq"]:
            if t in ev.get("home_team", "").lower() or t in ev.get("away_team", "").lower():
                team_hits.setdefault(t, 0)
                team_hits[t] += 1
    print(f"  {label} (id={lid}): {len(evs)} finished events")
    print(f"    Target teams found: {team_hits}")
    if evs:
        e = evs[0]
        print(f"    Sample: {e.get('home_team')} vs {e.get('away_team')} {e.get('event_date','')[:10]}")

print("\n" + "=" * 70)
print("PROBE 3 DONE — Coverage summary to follow")
print("=" * 70)
