"""
probe_bsd.py — Step 0: Probe Bzzoiro Sports Data (BSD) API coverage.
Reads BSD_TOKEN from root .env (or env var). Reports what endpoints exist
and what data is available for all 8 WC 2026 teams.
"""

import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Load .env from root
def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

_env = load_env(ROOT / ".env")
_env.update(load_env(ROOT / "backend" / ".env"))

BSD_TOKEN = _env.get("BSD_TOKEN") or os.environ.get("BSD_TOKEN", "")
BASE      = "https://sports.bzzoiro.com/api/v2"
HDR       = {"Authorization": f"Token {BSD_TOKEN}"} if BSD_TOKEN else {}

try:
    import requests
except ImportError:
    sys.exit("pip install requests")


def get(path: str, params: dict | None = None, label: str = "") -> tuple[int, dict | None]:
    url = f"{BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, headers=HDR, params=params, timeout=15)
        time.sleep(0.3)
        body = None
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:500]}
        return r.status_code, body
    except Exception as exc:
        print(f"  ERROR {label}: {exc}")
        return 0, None


def pp(obj, indent=2):
    print(json.dumps(obj, indent=indent, default=str)[:2000])


print("=" * 60)
print("BSD API PROBE")
print(f"Base: {BASE}")
print(f"Token: {'set (' + BSD_TOKEN[:8] + '...)' if BSD_TOKEN else 'NOT SET'}")
print("=" * 60)

# ── 1. Root / discovery ───────────────────────────────────────────────────────
print("\n[1] Root endpoint")
sc, body = get("/")
print(f"  GET / -> HTTP {sc}")
if body: pp(body)

# ── 2. Competitions / sports ──────────────────────────────────────────────────
for path in ["/competitions/", "/sports/", "/leagues/", "/tournaments/"]:
    sc, body = get(path)
    print(f"\n[2] GET {path} -> HTTP {sc}")
    if body and sc == 200:
        pp(body)
        break

# ── 3. Search for WC 2026 ─────────────────────────────────────────────────────
for path in ["/competitions/", "/leagues/", "/tournaments/"]:
    sc, body = get(path, {"search": "World Cup"})
    if sc == 200:
        print(f"\n[3] Search 'World Cup' at {path} -> HTTP {sc}")
        pp(body)
        break
    sc2, body2 = get(path, {"name": "World Cup"})
    if sc2 == 200:
        print(f"\n[3] Name 'World Cup' at {path} -> HTTP {sc2}")
        pp(body2)
        break
    # Try numeric FIFA WC ID guesses
    for wc_id in [1, 17, 77, 132, 5, 43]:
        sc3, body3 = get(f"{path}{wc_id}/")
        if sc3 == 200:
            print(f"\n[3] {path}{wc_id}/ -> HTTP {sc3}")
            pp(body3)
            break

# ── 4. Teams endpoint ─────────────────────────────────────────────────────────
print("\n[4] Teams endpoint exploration")
for path in ["/teams/", "/national-teams/", "/countries/"]:
    sc, body = get(path)
    print(f"  GET {path} -> HTTP {sc}")
    if sc == 200:
        pp(body)
        break

TARGET = ["Brazil", "Morocco", "Haiti", "Scotland",
          "France", "Senegal", "Iraq", "Norway"]
for name in TARGET[:3]:
    sc, body = get("/teams/", {"search": name})
    if sc == 200:
        print(f"\n  Teams search '{name}': HTTP {sc}")
        pp(body)
        break
    sc, body = get("/teams/", {"name": name})
    if sc == 200:
        print(f"\n  Teams name '{name}': HTTP {sc}")
        pp(body)
        break

# ── 5. Fixtures / matches ─────────────────────────────────────────────────────
print("\n[5] Fixtures endpoint")
for path in ["/fixtures/", "/matches/", "/events/"]:
    sc, body = get(path)
    print(f"  GET {path} -> HTTP {sc}")
    if sc == 200:
        pp(body)
        break

# ── 6. Player stats ───────────────────────────────────────────────────────────
print("\n[6] Player stats endpoint")
for path in ["/players/", "/player-stats/", "/statistics/players/"]:
    sc, body = get(path)
    print(f"  GET {path} -> HTTP {sc}")
    if sc == 200:
        pp(body)
        break

# ── 7. Team stats ─────────────────────────────────────────────────────────────
print("\n[7] Team stats endpoint")
for path in ["/team-stats/", "/statistics/teams/", "/team-statistics/"]:
    sc, body = get(path)
    print(f"  GET {path} -> HTTP {sc}")
    if sc == 200:
        pp(body)
        break

# ── 8. Odds ───────────────────────────────────────────────────────────────────
print("\n[8] Odds endpoint")
for path in ["/odds/", "/bookmakers/odds/", "/predictions/odds/"]:
    sc, body = get(path)
    print(f"  GET {path} -> HTTP {sc}")
    if sc == 200:
        pp(body)
        break

print("\n" + "=" * 60)
print("PROBE DONE")
print("=" * 60)
