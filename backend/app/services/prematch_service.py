"""
prematch_service.py
===================
Full pre-match prediction engine for WC 2026 Group C and Group I.

Data pipeline (loaded once at import, refreshed on explicit reload):
  data/team_results.json         <- BSD Bzzoiro historical results (backfilled)
  data/player_scoring_stats.json  <- BSD national-team player stats (backfilled)
  data/players_baseline.json     <- career goals/caps per player (always available)
  data/team_stat_rates.json      <- shots/corners/fouls rates (not available at BSD level)

Model:
  Dixon-Coles Poisson (1997) for scoreline probability matrix.
  Ratings pipeline (in order):
    1. Raw BSD results (goals scored/conceded per match)
    2. Shrinkage  — blend toward 1.0 (neutral) when n < 15 matches
    3. Confederation quality adjustment — scale attack/defence to account for
       opponent quality in qualification (CAF << UEFA, CONCACAF << CONMEBOL)
    4. Market blend — 60 % weight to BSD Consensus odds + 40 % conf-adj model
    5. Bisect back-solve — find λ_h, λ_a that reproduce the blended outcome,
       used for xG display, most-likely score, and scorer probabilities.

Market benchmark:
  BSD Bzzoiro Consensus odds (1x2, over_under_25, btts) fetched live per fixture.
  Devigged (margin removed) for fair comparison. Displayed with three columns:
  "model_conf_adj" | "model_blended" | "market_fair".

Graceful degradation:
  - No results history  → fallback FIFA-ranking-based ratings, flagged
  - Low n (<15)         → ratings shrunk toward 1.0, noted in coverage
  - BSD odds unavailable→ no blending; conf-adj model used as final output
  - No stat-rate data   → shots/corners/fouls reported as "unavailable"
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

ROOT     = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"

# ─── Model constants ──────────────────────────────────────────────────────────
DC_RHO          = -0.13   # Dixon-Coles low-score correction (1997 paper)
HOME_ADV        = 1.00    # Neutral WC 2026 venues: no home advantage
INTL_AVG_GOALS  = 1.35    # Typical international goals per team per match
MAX_GOALS       = 7       # Scoreline matrix dimension (0..MAX_GOALS)
_N_RATING_REF   = 15      # Qualifying matches for "full confidence" (shrinkage)
MARKET_BLEND_WEIGHT = 0.60  # weight given to BSD Consensus odds in final output

# ─── Confederation quality factors ───────────────────────────────────────────
# Correct for opponent quality in WC qualification.
# attack factor < 1  → raw goals scored are inflated (weak defensive opponents)
# defence factor > 1 → raw goals conceded are deflated (weak attacking opponents)
# Sources: UEFA/FIFA cross-confederation analysis, WC 2014-2022 goals per match by conf.
_CONF_FACTORS: dict[str, dict[str, float]] = {
    "UEFA":     {"attack": 1.05, "defence": 1.00},  # Tough opp → atk slightly underrated
    "CONMEBOL": {"attack": 1.15, "defence": 1.00},  # Very tough qual → atk significantly underrated
    "AFC":      {"attack": 0.90, "defence": 1.15},  # Below-avg opp
    "CAF":      {"attack": 0.82, "defence": 1.55},  # Weak opp → atk inflated, def too strong
    "CONCACAF": {"attack": 0.80, "defence": 1.60},  # Weakest opp on average
    "OFC":      {"attack": 0.75, "defence": 1.65},  # Weakest qualifying path
}

_TEAM_CONF: dict[str, str] = {
    "BRA": "CONMEBOL",
    "FRA": "UEFA",
    "MAR": "CAF",
    "SEN": "CAF",
    "NOR": "UEFA",
    "SCO": "UEFA",
    "IRQ": "AFC",
    "HAI": "CONCACAF",
}

# ─── BSD API ──────────────────────────────────────────────────────────────────
_BSD_BASE = "https://sports.bzzoiro.com/api/v2"
_BSD_TEAM_IDS: dict[str, int] = {
    "BRA": 463, "MAR": 464, "HAI": 465, "SCO": 466,
    "FRA": 485, "SEN": 486, "IRQ": 933, "NOR": 488,
}

# ─── Fallback ratings ─────────────────────────────────────────────────────────
# Used ONLY when BSD results are unavailable.
_FALLBACK_RATINGS: dict[str, dict] = {
    "BRA": {"attack_rating": 1.52, "defence_rating": 0.56,
            "stdev_scored": 1.0,   "stdev_conceded": 0.7},
    "FRA": {"attack_rating": 1.41, "defence_rating": 0.59,
            "stdev_scored": 1.1,   "stdev_conceded": 0.8},
    "MAR": {"attack_rating": 0.93, "defence_rating": 0.63,
            "stdev_scored": 0.9,   "stdev_conceded": 0.7},
    "SEN": {"attack_rating": 1.11, "defence_rating": 0.74,
            "stdev_scored": 1.0,   "stdev_conceded": 0.8},
    "NOR": {"attack_rating": 1.33, "defence_rating": 0.89,
            "stdev_scored": 1.1,   "stdev_conceded": 0.9},
    "SCO": {"attack_rating": 1.07, "defence_rating": 0.93,
            "stdev_scored": 0.9,   "stdev_conceded": 0.9},
    "IRQ": {"attack_rating": 0.93, "defence_rating": 1.11,
            "stdev_scored": 1.0,   "stdev_conceded": 1.0},
    "HAI": {"attack_rating": 0.67, "defence_rating": 1.56,
            "stdev_scored": 0.9,   "stdev_conceded": 1.2},
}

# ─── Position weights for expected minutes ────────────────────────────────────
_POS_EXPECTED_MIN: dict[str, float] = {
    "Goalkeeper":    90.0,
    "Center Back":   88.0,
    "Full Back":     82.0,
    "Defensive Mid": 80.0,
    "Central Mid":   72.0,
    "Attacking Mid": 68.0,
    "Winger":        65.0,
    "Striker":       72.0,
}


# ─── Data loading (module-level cache) ────────────────────────────────────────

def _load_json(path: Path) -> dict | list | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _load_all() -> tuple[dict, dict, dict, dict]:
    team_results  = _load_json(DATA_DIR / "team_results.json") or {}
    player_stats  = _load_json(DATA_DIR / "player_scoring_stats.json") or {}
    baseline_raw  = _load_json(DATA_DIR / "players_baseline.json") or {}
    stat_rates    = _load_json(DATA_DIR / "team_stat_rates.json") or {}
    return team_results, player_stats, baseline_raw, stat_rates


_TEAM_RESULTS, _PLAYER_STATS, _BASELINE_RAW, _STAT_RATES = _load_all()

_PLAYERS_BY_TEAM: dict[str, list[dict]] = {}
for _p in (_BASELINE_RAW.get("players") or []):
    _PLAYERS_BY_TEAM.setdefault(_p["team_code"], []).append(_p)

# Extend confederation + fallback-rating coverage to all 48 WC 2026 teams from
# the generated data files. Existing tuned entries (the original 8) take priority.
for _t in (_load_json(DATA_DIR / "wc_teams.json") or []):
    _TEAM_CONF.setdefault(_t["tla"], _t.get("confederation", "UEFA"))
for _tla, _r in (_load_json(DATA_DIR / "wc_ratings.json") or {}).items():
    _FALLBACK_RATINGS.setdefault(_tla, {
        "attack_rating":  _r.get("attack_rating", 1.0),
        "defence_rating": _r.get("defence_rating", 1.0),
        "stdev_scored":   _r.get("stdev_scored", 1.0),
        "stdev_conceded": _r.get("stdev_conceded", 1.0),
    })


# ─── BSD auth + HTTP helpers ──────────────────────────────────────────────────

def _bsd_token() -> str:
    for env_path in [ROOT / ".env", ROOT / "backend" / ".env"]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("BSD_TOKEN="):
                    return line.split("=", 1)[1].strip()
    return ""


def _bsd_headers() -> dict:
    tok = _bsd_token()
    return {"Authorization": f"Token {tok}"} if tok else {}


def _bsd_get(path: str, params: Optional[dict] = None) -> tuple[int, dict]:
    if not _REQUESTS_OK:
        return 0, {"error": "requests not installed"}
    try:
        url = f"{_BSD_BASE}/{path.lstrip('/')}"
        r = _requests.get(url, headers=_bsd_headers(), params=params, timeout=10)
        time.sleep(0.2)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:200]}
    except Exception as e:
        return 0, {"error": str(e)}


def _bsd_paginate(path: str, params: Optional[dict] = None, max_pages: int = 5) -> list:
    if not _REQUESTS_OK:
        return []
    results: list = []
    url = f"{_BSD_BASE}/{path.lstrip('/')}"
    p = {**(params or {}), "limit": 100}
    for _ in range(max_pages):
        try:
            r = _requests.get(url, headers=_bsd_headers(), params=p, timeout=10)
            time.sleep(0.2)
            if r.status_code != 200:
                break
            data = r.json()
            results.extend(data.get("results", []))
            nxt = data.get("next")
            if not nxt:
                break
            url = nxt
            p = {}
        except Exception:
            break
    return results


# ─── WC 2026 event index (lazy, cached) ──────────────────────────────────────

_wc_events: Optional[list] = None


def _get_wc_events() -> list:
    global _wc_events
    if _wc_events is not None:
        return _wc_events
    try:
        _wc_events = _bsd_paginate("/events/", {"league_id": 27}, max_pages=10)
    except Exception:
        _wc_events = []
    return _wc_events


def _find_bsd_event(home_tla: str, away_tla: str) -> Optional[int]:
    home_id = _BSD_TEAM_IDS.get(home_tla)
    away_id = _BSD_TEAM_IDS.get(away_tla)
    if not home_id or not away_id:
        return None
    for ev in _get_wc_events():
        if ev.get("home_team_id") == home_id and ev.get("away_team_id") == away_id:
            return ev["id"]
    return None


# ─── BSD odds fetch ───────────────────────────────────────────────────────────

_odds_cache: dict[str, tuple[float, Optional[dict]]] = {}
_ODDS_TTL = 60  # seconds


def _fetch_bsd_odds(home_tla: str, away_tla: str) -> Optional[dict]:
    key = f"{home_tla}_{away_tla}"
    now = time.time()
    if key in _odds_cache:
        ts, cached = _odds_cache[key]
        if now - ts < _ODDS_TTL:
            return cached

    event_id = _find_bsd_event(home_tla, away_tla)
    if event_id is None:
        _odds_cache[key] = (now, None)
        return None

    sc, body = _bsd_get("/odds/", {"event_id": event_id, "limit": 50})
    if sc != 200 or not body.get("results"):
        _odds_cache[key] = (now, None)
        return None

    by_market: dict[str, list[dict]] = {}
    for o in body["results"]:
        bk = (o.get("bookmaker_slug") or o.get("bookmaker_name") or "").lower()
        if "consensus" not in bk:
            continue
        mkt = o.get("market", "")
        by_market.setdefault(mkt, []).append(o)

    result: dict = {"event_id": event_id, "source": "bsd_bzzoiro_consensus"}

    if "1x2" in by_market:
        raw: dict[str, float] = {}
        dec: dict[str, float] = {}
        for o in by_market["1x2"]:
            outcome = (o.get("outcome") or "").upper()
            key = {"HOME": "home_win", "DRAW": "draw", "AWAY": "away_win"}.get(outcome)
            if key:
                raw[key] = float(o.get("implied_probability") or 0)
                dec[key] = float(o.get("decimal_odds") or 0)
        if len(raw) == 3:
            margin = round(sum(raw.values()) - 1.0, 4)
            total  = sum(raw.values())
            fair   = {k: round(v / total, 4) for k, v in raw.items()}
            result["1x2"] = {
                "implied":      {k: round(v, 4) for k, v in raw.items()},
                "fair":         fair,
                "decimal_odds": {k: round(v, 2) for k, v in dec.items()},
                "bookmaker_margin": margin,
            }

    for mkt_key in ("over_under_25", "over_under", "over_under_2_5"):
        if mkt_key in by_market:
            raw_ou: dict[str, float] = {}
            for o in by_market[mkt_key]:
                outcome = (o.get("outcome") or "").upper()
                key = {"OVER": "over", "UNDER": "under"}.get(outcome)
                if key:
                    raw_ou[key] = float(o.get("implied_probability") or 0)
            if len(raw_ou) == 2:
                total_ou = sum(raw_ou.values())
                result["over_under_25"] = {
                    "implied": {k: round(v, 4) for k, v in raw_ou.items()},
                    "fair":    {k: round(v / total_ou, 4) for k, v in raw_ou.items()},
                }
            break

    if "btts" in by_market:
        raw_bt: dict[str, float] = {}
        for o in by_market["btts"]:
            outcome = (o.get("outcome") or "").upper()
            key = {"YES": "yes", "NO": "no"}.get(outcome)
            if key:
                raw_bt[key] = float(o.get("implied_probability") or 0)
        if len(raw_bt) == 2:
            total_bt = sum(raw_bt.values())
            result["btts"] = {
                "implied": {k: round(v, 4) for k, v in raw_bt.items()},
                "fair":    {k: round(v / total_bt, 4) for k, v in raw_bt.items()},
            }

    if "1x2" not in result:
        _odds_cache[key] = (now, None)
        return None
    _odds_cache[key] = (now, result)
    return result


# ─── Rating lookup + shrinkage + conf adjustment ──────────────────────────────

def _apply_shrinkage(ratings: dict, n_matches: int) -> dict:
    """Blend toward 1.0 (neutral) for teams with fewer than _N_RATING_REF matches."""
    alpha = min(n_matches, _N_RATING_REF) / _N_RATING_REF
    shrunk = dict(ratings)
    for key in ("attack_rating", "defence_rating"):
        if key in shrunk and shrunk[key] is not None:
            shrunk[key] = round(alpha * float(shrunk[key]) + (1 - alpha) * 1.0, 4)
    return shrunk


def _apply_conf_factors(ratings: dict, tla: str) -> dict:
    """
    Apply confederation quality factors to BSD-shrunk ratings.
    Corrects for opponent-quality differences across WC qualifying confederations.
    Teams from weaker confederations (CAF, CONCACAF) have their attack ratings
    reduced and defence ratings increased to reflect easier opposition faced.
    """
    conf = _TEAM_CONF.get(tla, "UEFA")
    factors = _CONF_FACTORS.get(conf, {"attack": 1.0, "defence": 1.0})
    adj = dict(ratings)
    if "attack_rating" in adj and adj["attack_rating"] is not None:
        adj["attack_rating"] = round(float(adj["attack_rating"]) * factors["attack"], 4)
    if "defence_rating" in adj and adj["defence_rating"] is not None:
        adj["defence_rating"] = round(float(adj["defence_rating"]) * factors["defence"], 4)
    return adj


def _get_ratings(tla: str) -> tuple[dict, str, str, int]:
    """
    Returns (bsd_shrunk_ratings, source_label, data_status, n_matches).
    Ratings are BSD-shrunk only. Confederation adjustment is applied separately.
    """
    teams = (_TEAM_RESULTS.get("teams") or {})
    entry = teams.get(tla, {})
    ratings = entry.get("ratings")
    status  = entry.get("status", "unavailable")

    if ratings and entry.get("matches"):
        n = len(entry["matches"])
        label = f"bsd_bzzoiro_{n}_matches"
        shrunk = _apply_shrinkage(ratings, n)
        return shrunk, label, status, n

    fb = _FALLBACK_RATINGS.get(tla, {
        "attack_rating": 1.00, "defence_rating": 1.00,
        "stdev_scored":  1.0,  "stdev_conceded": 1.0,
    })
    return fb, "fallback_fifa_ranking", "unavailable", 0


# ─── Poisson / Dixon-Coles model ──────────────────────────────────────────────

def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _dc_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1.0 - lam_h * lam_a * rho
    if h == 0 and a == 1:
        return 1.0 + lam_h * rho
    if h == 1 and a == 0:
        return 1.0 + lam_a * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _scoreline_matrix(lam_h: float, lam_a: float) -> list[list[float]]:
    n = MAX_GOALS + 1
    mat = [[0.0] * n for _ in range(n)]
    total = 0.0
    for h in range(n):
        for a in range(n):
            p = (_poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
                 * _dc_tau(h, a, lam_h, lam_a, DC_RHO))
            p = max(p, 0.0)
            mat[h][a] = p
            total += p
    if total > 0:
        for h in range(n):
            for a in range(n):
                mat[h][a] = round(mat[h][a] / total, 6)
    return mat


def _outcome_probs(mat: list[list[float]]) -> dict[str, float]:
    hw = dw = aw = 0.0
    for h, row in enumerate(mat):
        for a, p in enumerate(row):
            if h > a:    hw += p
            elif h == a: dw += p
            else:        aw += p
    total = hw + dw + aw
    if total <= 0:
        return {"home_win": 0.333, "draw": 0.333, "away_win": 0.334}
    return {
        "home_win": round(hw / total, 4),
        "draw":     round(dw / total, 4),
        "away_win": round(aw / total, 4),
    }


def _most_likely_score(mat: list[list[float]]) -> dict:
    best, bh, ba = 0.0, 0, 0
    for h, row in enumerate(mat):
        for a, p in enumerate(row):
            if p > best:
                best, bh, ba = p, h, a
    return {"home": bh, "away": ba, "probability": round(best, 4)}


def _expected_goals_range(lam: float, stdev: float, n_matches: int) -> dict:
    n_eff  = max(n_matches, 1)
    margin = 1.5 * stdev / math.sqrt(n_eff)
    return {
        "mean": round(lam, 3),
        "low":  round(max(lam - margin, 0.0), 3),
        "high": round(lam + margin, 3),
    }


# ─── Market calibration helpers ───────────────────────────────────────────────

def _infer_lambda_from_ou25(p_over: float) -> float:
    """
    Bisect to find total_lambda such that P(Poisson(total_lambda) > 2.5) = p_over.
    Used to calibrate total expected goals from the over/under 2.5 market.
    """
    p_over = max(0.01, min(0.99, p_over))
    lo, hi = 0.01, 12.0
    for _ in range(40):
        lam = (lo + hi) / 2.0
        # P(total <= 2) for Poisson(lam)
        p_le2 = math.exp(-lam) * (1.0 + lam + lam * lam / 2.0)
        p_ov  = 1.0 - p_le2
        if p_ov < p_over:
            lo = lam
        else:
            hi = lam
    return (lo + hi) / 2.0


def _solve_scale_k(lam_h: float, lam_a: float, target_hw: float) -> float:
    """
    Bisect to find scale factor k such that
    P(home_win; lam_h*k, lam_a/k) ≈ target_hw.
    Scaling k>1 favours home, k<1 favours away.
    Total goals = lam_h*k + lam_a/k, roughly preserved around k=1.
    """
    target_hw = max(0.01, min(0.98, target_hw))
    lo, hi = 0.01, 15.0
    for _ in range(40):
        k   = (lo + hi) / 2.0
        mat = _scoreline_matrix(max(lam_h * k, 0.05), max(lam_a / k, 0.05))
        p   = _outcome_probs(mat)
        if p["home_win"] < target_hw:
            lo = k
        else:
            hi = k
    return (lo + hi) / 2.0


# ─── Lineup fetch (for scorer model) ─────────────────────────────────────────

import unicodedata as _ud

def _norm_name(s: str) -> str:
    s = _ud.normalize("NFD", s)
    s = "".join(c for c in s if _ud.category(c) != "Mn")
    return s.lower().strip()

def _lineup_names_match(bsd: str, baseline: str) -> bool:
    bn, ln = _norm_name(bsd), _norm_name(baseline)
    if bn == ln: return True
    bn = bn.replace("jr.", "junior"); ln = ln.replace("jr.", "junior")
    if bn == ln: return True
    bp, lp = bn.split(), ln.split()
    if not bp or not lp: return False
    if len(bp) == 1: return bp[0] == lp[0]   # mononym: match first word
    bl = next((w for w in reversed(bp) if w != "junior"), "")
    ll = next((w for w in reversed(lp) if w != "junior"), "")
    if not bl or bl != ll: return False
    bf = bp[0]; lf = lp[0]
    if bf.endswith("."): return lf.startswith(bf[:-1])
    return bf == lf or bf[0] == lf[0]

_match_lineup_cache: dict[int, tuple[float, Optional[dict]]] = {}
_LINEUP_TTL = 120  # seconds


def _fetch_match_lineup(event_id: int) -> Optional[dict]:
    now = time.time()
    if event_id in _match_lineup_cache:
        ts, cached = _match_lineup_cache[event_id]
        if now - ts < _LINEUP_TTL:
            return cached
    sc, data = _bsd_get(f"/events/{event_id}/lineups/")
    result = data if sc == 200 else None
    _match_lineup_cache[event_id] = (now, result)
    return result

def _last_played_event_for_team(tla: str) -> Optional[int]:
    """BSD event_id of the most-recently finished match for this team."""
    team_id = _BSD_TEAM_IDS.get(tla)
    if not team_id:
        return None
    for ev in reversed(_get_wc_events()):
        if ev.get("status") != "finished":
            continue
        if ev.get("home_team_id") == team_id or ev.get("away_team_id") == team_id:
            return ev["id"]
    return None

def _infer_role_from_formation(bsd_pos: str, formation: str, idx_in_group: int, group_size: int) -> str:
    """
    Use BSD position code + position within formation group to infer tactical role.

    BSD groups players by position letter (G / D / M / F), in formation order
    (left → right for D/F, defensive → attacking for M).

    Examples in 4-2-3-1:
      D group (4): idx 0,3 → Full Back; idx 1,2 → Center Back
      M group (5): idx 0,1 → Defensive Mid; idx 2,3,4 → Winger/Attacking Mid
      F group (1): idx 0 → Striker

    Examples in 4-3-3:
      M group (3): idx 0,1,2 → Central Mid
      F group (3): idx 0,2 → Winger; idx 1 → Striker
    """
    if bsd_pos == "G":
        return "Goalkeeper"

    if bsd_pos == "D":
        # Wing positions are always outermost
        if idx_in_group == 0 or idx_in_group == group_size - 1:
            return "Full Back"
        return "Center Back"

    if bsd_pos == "F":
        if group_size == 1:
            return "Striker"
        # 2 forwards: both are striker/shadow striker
        if group_size == 2:
            return "Striker"
        # 3 forwards: center is striker, wings are wingers
        return "Striker" if idx_in_group == 1 else "Winger"

    if bsd_pos == "M":
        # Parse formation to count defensive vs attacking mid lines
        parts = [int(x) for x in formation.split("-") if x.isdigit()]
        # For most formations: first N_def mids are defensive, rest attacking
        # Use a heuristic: the first ⌊group_size/2⌋ (rounded down) are defensive
        if group_size == 1:
            return "Central Mid"
        if group_size == 2:
            return "Defensive Mid"
        if group_size == 3:
            # 4-3-3 style: all central mids
            return "Central Mid"
        if group_size == 4:
            # 4-4-2 style: outermost are wingers, inner are central
            if idx_in_group == 0 or idx_in_group == 3:
                return "Winger"
            return "Central Mid"
        if group_size == 5:
            # 4-2-3-1 style: first 2 defensive, last 3 attacking/wide
            if idx_in_group < 2:
                return "Defensive Mid"
            return "Attacking Mid" if idx_in_group == 2 else "Winger"
        # Fallback: first half defensive, second half attacking
        half = group_size // 2
        if idx_in_group < half:
            return "Defensive Mid"
        return "Attacking Mid" if idx_in_group == half else "Winger"

    return "Central Mid"

def _resolve_lineup(tla: str, match_event_id: Optional[int]) -> tuple[list[tuple[str, str]], str, str]:
    """
    Return ([(bsd_name, inferred_role), ...], formation, source) for this team.
    source: "confirmed" | "predicted" | "last_match" | "none"
    Tries match lineup first, falls back to last played match.
    """
    for eid, src_label in [(match_event_id, None), (_last_played_event_for_team(tla), "last_match")]:
        if eid is None:
            continue
        data = _fetch_match_lineup(eid)
        if not data:
            continue
        lins = data.get("lineups") or {}
        home_tid = (lins.get("home") or {}).get("team_id")
        side = "home" if home_tid == _BSD_TEAM_IDS.get(tla) else "away"
        ldata = lins.get(side) or {}
        raw_players = ldata.get("players") or []
        formation = ldata.get("formation") or ""
        if not raw_players:
            continue

        # Group players by BSD position code to determine index within group
        from collections import defaultdict
        by_pos: dict[str, list] = defaultdict(list)
        for p in raw_players:
            by_pos[p.get("position", "M")].append(p)

        starters: list[tuple[str, str]] = []
        for p in raw_players:
            bsd_pos   = p.get("position", "M")
            group     = by_pos[bsd_pos]
            idx       = group.index(p)
            role      = _infer_role_from_formation(bsd_pos, formation, idx, len(group))
            starters.append((p["name"], role))

        status = src_label or data.get("lineup_status", "confirmed")
        return starters, formation, status
    return [], "", "none"


# ─── Formation-aware goal weight system ───────────────────────────────────────

# Base attacking weight by position_detail (starting point, before individual adjustment)
_ROLE_BASE_WEIGHT: dict[str, float] = {
    "Goalkeeper":    0.002,
    "Center Back":   0.025,
    "Full Back":     0.050,
    "Defensive Mid": 0.055,
    "Central Mid":   0.100,
    "Attacking Mid": 0.185,
    "Winger":        0.200,
    "Striker":       0.250,
}

# Per-formation multipliers: adjust role weight based on tactical context.
# E.g., wingers in 4-3-3 get far more license to attack than in 4-5-1.
_FORMATION_CONTEXT: dict[str, dict[str, float]] = {
    "4-3-3":   {"Winger": 1.25, "Striker": 1.05, "Central Mid": 0.85, "Attacking Mid": 0.90},
    "4-2-3-1": {"Attacking Mid": 1.20, "Winger": 1.10, "Defensive Mid": 0.75},
    "3-4-3":   {"Full Back": 1.40, "Winger": 1.15, "Striker": 0.95},
    "4-4-2":   {"Striker": 1.15, "Winger": 0.88, "Central Mid": 0.90},
    "4-1-4-1": {"Striker": 1.30, "Winger": 0.85, "Defensive Mid": 0.65},
    "5-3-2":   {"Full Back": 1.35, "Striker": 1.10},
    "3-5-2":   {"Full Back": 1.35, "Striker": 1.10, "Central Mid": 0.95},
    "4-5-1":   {"Striker": 1.35, "Winger": 0.75, "Central Mid": 0.95},
}

# International goals per cap by position (baseline for individual rate comparison)
_POS_AVG_GOALS_PER_CAP: dict[str, float] = {
    "Goalkeeper":    0.002,
    "Center Back":   0.025,
    "Full Back":     0.035,
    "Defensive Mid": 0.040,
    "Central Mid":   0.085,
    "Attacking Mid": 0.175,
    "Winger":        0.200,
    "Striker":       0.380,
}

def _goal_rate_factor(goals: int, caps: int, pos_detail: str) -> float:
    """
    How much above/below position average this player scores.
    Returns multiplier in [0.40, 2.20].

    When caps > 0: compare goals/cap vs position average (accurate).
    When caps == 0: use √goals as a relative proxy so prolific scorers
      (Mbappé 45) still rank well above low scorers (Tchouaméni 2)
      without needing caps data.
    """
    if caps > 0:
        pos_avg = _POS_AVG_GOALS_PER_CAP.get(pos_detail, 0.10)
        rate  = goals / caps
        ratio = rate / pos_avg if pos_avg > 0 else 1.0
        ratio = max(0.2, min(3.0, ratio))
        return round(0.4 + 0.6 * ratio, 4)
    else:
        # No caps data — use √goals as a differentiator (caps-free proxy)
        # √45 ≈ 6.7 → factor 1.84  |  √21 ≈ 4.6 → 1.42  |  √0 → 0.70
        return round(min(2.0, 0.70 + math.sqrt(max(goals, 0)) * 0.17), 4)


# ─── Scorer probabilities ─────────────────────────────────────────────────────

def _scorer_probs(tla: str, lam_team: float, match_event_id: Optional[int] = None) -> list[dict]:
    """
    Per-player P(scores >= 1) using formation-aware role weights and individual
    goal rate personalisation.

    Pipeline:
      1. Fetch BSD lineup (confirmed > predicted > last match > squad fallback).
      2. Match starters to baseline players to get position_detail + goals/caps.
      3. Weight = base_role_weight × formation_context × individual_goal_rate_factor.
      4. Normalize weights → each player's expected-goals share of team xG.
      5. Exclude players not in lineup (injured/absent disappear automatically).
    """
    all_players = _PLAYERS_BY_TEAM.get(tla, [])
    api_stats   = (_PLAYER_STATS.get("stats") or {})

    starters_with_roles, formation, lineup_src = _resolve_lineup(tla, match_event_id)
    formation_mods = _FORMATION_CONTEXT.get(formation, {})

    # Build name→inferred_role map from lineup
    role_map: dict[str, str] = {}   # baseline_player_name → inferred tactical role
    starter_bsd_names: list[str] = []
    for bsd_name, role in starters_with_roles:
        starter_bsd_names.append(bsd_name)
        for p in all_players:
            if _lineup_names_match(bsd_name, p["name"]):
                role_map[p["name"]] = role
                break

    # Determine which baseline players are in the lineup
    if starter_bsd_names:
        active = [
            p for p in all_players
            if p.get("position") != "GK"
            and any(_lineup_names_match(bsd, p["name"]) for bsd in starter_bsd_names)
        ]
        if not active:          # matching failed — fall back to full squad
            active = [p for p in all_players if p.get("position") != "GK"]
            lineup_src = "squad_fallback"
    else:
        active = [p for p in all_players if p.get("position") != "GK"]
        lineup_src = "squad_fallback"

    # Build weighted records
    weighted: list[tuple[dict, float, dict, str]] = []   # (player, raw_weight, bsd_stats, tactical_role)
    for p in active:
        pid            = p["player_id"]
        ps             = api_stats.get(pid, {})
        goals          = int(ps.get("goals") or 0) if ps.get("status") == "full" else int(p.get("goals") or 0)
        caps           = int(p.get("caps") or 0)
        pos_detail     = p.get("position_detail", "Central Mid")
        # Use formation-inferred role for weight; fall back to baseline position
        tactical_role  = role_map.get(p["name"], pos_detail)

        base  = _ROLE_BASE_WEIGHT.get(tactical_role, 0.10)
        ctx   = formation_mods.get(tactical_role, 1.0)
        ind   = _goal_rate_factor(goals, caps, tactical_role)
        raw_w = base * ctx * ind
        weighted.append((p, raw_w, ps, tactical_role))

    total_w = sum(w for _, w, _, _ in weighted) or 1.0

    result: list[dict] = []
    for p, raw_w, ps, tactical_role in weighted:
        pos_detail = p.get("position_detail", "Central Mid")
        caps       = int(p.get("caps") or 0)
        goals_bsl  = int(p.get("goals") or 0)
        goals_bsd  = int(ps.get("goals") or 0) if ps.get("status") == "full" else None
        goals_used = goals_bsd if goals_bsd is not None else goals_bsl
        exp_min    = _POS_EXPECTED_MIN.get(tactical_role, 72.0)

        share      = raw_w / total_w
        lam_player = lam_team * share * (exp_min / 90.0)
        p_score    = round(1.0 - math.exp(-lam_player), 4) if lam_player > 0 else 0.0

        result.append({
            "player_id":            p["player_id"],
            "name":                 p["name"],
            "position":             tactical_role,    # formation-inferred role
            "position_baseline":    pos_detail,       # what baseline says
            "p_scores_one_or_more": p_score,
            "expected_goals":       round(lam_player, 4),
            "goal_rate_factor":     round(_goal_rate_factor(goals_used, caps, tactical_role), 3),
            "formation_context":    round(formation_mods.get(tactical_role, 1.0), 3),
            "intl_goals_bsd":       goals_bsd,
            "intl_goals_baseline":  goals_bsl,
            "intl_caps":            caps if caps > 0 else None,
            "data_source":          "bsd_national_team_stats" if goals_bsd is not None else "career_goals_estimate",
        })

    result.sort(key=lambda x: x["p_scores_one_or_more"], reverse=True)
    return result[:8], formation, lineup_src


# ─── Team stat profile ────────────────────────────────────────────────────────

def _stat_profile(tla: str) -> dict:
    rates       = ((_STAT_RATES.get("teams") or {}).get(tla) or {})
    source_note = _STAT_RATES.get("reason", "")

    def _stat_entry(key: str) -> dict:
        val = rates.get(key)
        if val is not None:
            return {"expected": val, "range": None, "status": "available"}
        return {"expected": None, "range": None, "status": "unavailable"}

    return {
        "shots_on_target": _stat_entry("shots_on_target"),
        "corners":         _stat_entry("corners"),
        "fouls":           _stat_entry("fouls"),
        "source_note":     source_note or "BSD Bzzoiro does not expose team-level shots/corners/fouls",
    }


# ─── BSD event → team TLA map (Group C and I only) ───────────────────────────
_OUR_BSD_EVENTS: dict[int, tuple[str, str]] = {
    8293: ("BRA", "MAR"), 8294: ("HAI", "SCO"),
    8317: ("SCO", "MAR"), 8318: ("BRA", "HAI"),
    8337: ("MAR", "HAI"), 8338: ("SCO", "BRA"),
    8304: ("FRA", "SEN"), 8305: ("IRQ", "NOR"),
    8328: ("FRA", "IRQ"), 8329: ("NOR", "SEN"),
    8347: ("NOR", "FRA"), 8348: ("SEN", "IRQ"),
}

_LIVE_STATUS = {"1st_half", "2nd_half", "halftime", "extra_time", "extra_time_ht"}

_live_scores_cache: Optional[list] = None
_live_scores_ts: float = 0.0
_LIVE_SCORES_TTL = 30  # seconds — refresh at most every 30 s


def get_bsd_live_scores() -> list[dict]:
    """Return live/final/upcoming scores for all Group C and Group I matches."""
    global _wc_events, _live_scores_cache, _live_scores_ts
    now = time.time()
    if _live_scores_cache is not None and (now - _live_scores_ts) < _LIVE_SCORES_TTL:
        return _live_scores_cache
    _wc_events = None          # flush event index so we get fresh statuses
    events = _get_wc_events()

    result: list[dict] = []
    for ev in events:
        ev_id = ev.get("id")
        if ev_id not in _OUR_BSD_EVENTS:
            continue
        home_tla, away_tla = _OUR_BSD_EVENTS[ev_id]
        status = ev.get("status", "unknown")
        result.append({
            "event_id":       ev_id,
            "match_id":       f"{ev.get('event_date','')[:10]}_{home_tla}_{away_tla}",
            "home_tla":       home_tla,
            "away_tla":       away_tla,
            "home_score":     ev.get("home_score"),
            "away_score":     ev.get("away_score"),
            "home_score_ht":  ev.get("home_score_ht"),
            "away_score_ht":  ev.get("away_score_ht"),
            "status":         status,
            "current_minute": ev.get("current_minute"),
            "period":         ev.get("period"),
            "event_date":     ev.get("event_date"),
            "is_live":        status in _LIVE_STATUS,
            "is_finished":    status == "finished",
        })
    result.sort(key=lambda x: x.get("event_date") or "")
    _live_scores_cache = result
    _live_scores_ts = now
    return result


def compute_live_outcome(
    home_tla: str, away_tla: str,
    score_h: int, score_a: int, minute: int,
    lam_h_eff: float, lam_a_eff: float,
) -> dict:
    """Win/draw/loss probabilities given current score, minute, and prematch lambdas."""
    remaining = max(0.0, 90 - minute) / 90.0
    lam_h_rem = max(lam_h_eff * remaining, 0.001)
    lam_a_rem = max(lam_a_eff * remaining, 0.001)
    rem_matrix = _scoreline_matrix(lam_h_rem, lam_a_rem)

    p_hw = p_dr = p_aw = 0.0
    for h_rem, row in enumerate(rem_matrix):
        for a_rem, p in enumerate(row):
            ft_h = score_h + h_rem
            ft_a = score_a + a_rem
            if ft_h > ft_a:
                p_hw += p
            elif ft_h == ft_a:
                p_dr += p
            else:
                p_aw += p

    total = max(p_hw + p_dr + p_aw, 1e-9)
    return {
        "home_win":      round(p_hw / total, 4),
        "draw":          round(p_dr / total, 4),
        "away_win":      round(p_aw / total, 4),
        "score":         f"{score_h}-{score_a}",
        "minute":        minute,
        "remaining_xg":  {"home": round(lam_h_rem, 3), "away": round(lam_a_rem, 3)},
    }


# ─── Public API ───────────────────────────────────────────────────────────────

_prematch_cache: dict[str, tuple[float, dict]] = {}
_PREMATCH_TTL = 120  # seconds


def compute_prematch(home_tla: str, away_tla: str, match_id: str) -> dict:
    """Full pre-match prediction package. Returns a JSON-serialisable dict."""
    cache_key = f"{home_tla}_{away_tla}"
    now = time.time()
    if cache_key in _prematch_cache:
        ts, cached = _prematch_cache[cache_key]
        if now - ts < _PREMATCH_TTL:
            return cached

    # ── Step 1: BSD shrunk ratings ────────────────────────────────────────────
    h_ratings, h_src, h_status, h_n = _get_ratings(home_tla)
    a_ratings, a_src, a_status, a_n = _get_ratings(away_tla)

    # ── Step 2: Confederation quality adjustment ──────────────────────────────
    h_adj = _apply_conf_factors(h_ratings, home_tla)
    a_adj = _apply_conf_factors(a_ratings, away_tla)

    h_conf_str = _TEAM_CONF.get(home_tla, "?")
    a_conf_str = _TEAM_CONF.get(away_tla, "?")
    h_cf = _CONF_FACTORS.get(h_conf_str, {"attack": 1.0, "defence": 1.0})
    a_cf = _CONF_FACTORS.get(a_conf_str, {"attack": 1.0, "defence": 1.0})

    # ── Step 3: Conf-adjusted Dixon-Coles model ───────────────────────────────
    lam_h_conf = max(
        h_adj["attack_rating"] * a_adj["defence_rating"] * INTL_AVG_GOALS * HOME_ADV,
        0.05,
    )
    lam_a_conf = max(
        a_adj["attack_rating"] * h_adj["defence_rating"] * INTL_AVG_GOALS,
        0.05,
    )

    mat_conf    = _scoreline_matrix(lam_h_conf, lam_a_conf)
    outcome_conf = _outcome_probs(mat_conf)

    # ── Step 4: BSD live odds + market blend ──────────────────────────────────
    bsd_odds    = _fetch_bsd_odds(home_tla, away_tla)
    market_available = bsd_odds is not None and "1x2" in bsd_odds

    lam_h_eff = lam_h_conf  # defaults: no blending
    lam_a_eff = lam_a_conf
    blended_outcome = outcome_conf

    if market_available:
        fair = bsd_odds["1x2"]["fair"]

        # Blend W/D/L
        raw_hw = MARKET_BLEND_WEIGHT * fair["home_win"] + (1 - MARKET_BLEND_WEIGHT) * outcome_conf["home_win"]
        raw_dr = MARKET_BLEND_WEIGHT * fair["draw"]     + (1 - MARKET_BLEND_WEIGHT) * outcome_conf["draw"]
        raw_aw = MARKET_BLEND_WEIGHT * fair["away_win"] + (1 - MARKET_BLEND_WEIGHT) * outcome_conf["away_win"]
        _tot   = raw_hw + raw_dr + raw_aw
        blended_hw = round(raw_hw / _tot, 4)
        blended_dr = round(raw_dr / _tot, 4)
        blended_aw = round(raw_aw / _tot, 4)
        blended_outcome = {"home_win": blended_hw, "draw": blended_dr, "away_win": blended_aw}

        # Back-solve λ_h, λ_a that reproduce blended home-win probability
        k = _solve_scale_k(lam_h_conf, lam_a_conf, blended_hw)
        lam_h_eff = max(lam_h_conf * k, 0.05)
        lam_a_eff = max(lam_a_conf / k, 0.05)

        # If over/under 2.5 available: calibrate total λ to market as well
        if "over_under_25" in bsd_odds:
            p_over_mkt = bsd_odds["over_under_25"]["fair"].get("over", 0.5)
            lam_total_mkt   = _infer_lambda_from_ou25(p_over_mkt)
            lam_total_conf  = lam_h_conf + lam_a_conf
            lam_total_blend = (MARKET_BLEND_WEIGHT * lam_total_mkt
                               + (1 - MARKET_BLEND_WEIGHT) * lam_total_conf)
            ratio = lam_h_eff / max(lam_h_eff + lam_a_eff, 1e-9)
            lam_h_eff = max(lam_total_blend * ratio, 0.05)
            lam_a_eff = max(lam_total_blend * (1.0 - ratio), 0.05)

    # ── Step 5: Final DC matrix with effective lambdas ────────────────────────
    matrix   = _scoreline_matrix(lam_h_eff, lam_a_eff)
    outcome  = _outcome_probs(matrix)   # ≈ blended_outcome after solving
    mls      = _most_likely_score(matrix)

    # ── Expected goals range (for display, use effective lambdas) ─────────────
    h_stdev    = h_ratings.get("stdev_scored", 1.0)
    a_stdev    = a_ratings.get("stdev_scored", 1.0)
    h_exp_range = _expected_goals_range(lam_h_eff, h_stdev, h_n if h_n > 0 else 1)
    a_exp_range = _expected_goals_range(lam_a_eff, a_stdev, a_n if a_n > 0 else 1)

    # ── Scorer probabilities (scaled to effective xG) ─────────────────────────
    match_event_id = _find_bsd_event(home_tla, away_tla)
    home_scorers, h_formation, h_lineup_src = _scorer_probs(home_tla, lam_h_eff, match_event_id)
    away_scorers, a_formation, a_lineup_src = _scorer_probs(away_tla, lam_a_eff, match_event_id)

    h_stat = _stat_profile(home_tla)
    a_stat = _stat_profile(away_tla)

    # ── Market odds section (model vs market side by side) ────────────────────
    market_odds_section: dict
    if market_available:
        fair = bsd_odds["1x2"]["fair"]
        model_vs_market = {
            "home_win": {
                "model_conf_adj": outcome_conf["home_win"],
                "model_blended":  outcome["home_win"],
                "market_fair":    fair.get("home_win"),
                "gap_blended_vs_market": round(outcome["home_win"] - (fair.get("home_win") or 0), 4),
            },
            "draw": {
                "model_conf_adj": outcome_conf["draw"],
                "model_blended":  outcome["draw"],
                "market_fair":    fair.get("draw"),
                "gap_blended_vs_market": round(outcome["draw"] - (fair.get("draw") or 0), 4),
            },
            "away_win": {
                "model_conf_adj": outcome_conf["away_win"],
                "model_blended":  outcome["away_win"],
                "market_fair":    fair.get("away_win"),
                "gap_blended_vs_market": round(outcome["away_win"] - (fair.get("away_win") or 0), 4),
            },
        }
        market_odds_section = {
            **bsd_odds,
            "model_vs_market": model_vs_market,
            "blend_weight": MARKET_BLEND_WEIGHT,
            "note": (
                f"Final outcome = {int(MARKET_BLEND_WEIGHT*100)}% market_fair + "
                f"{int((1-MARKET_BLEND_WEIGHT)*100)}% model_conf_adj. "
                "'gap_blended_vs_market' = blended - market_fair (negative = model still below market)."
            ),
        }
    else:
        market_odds_section = {
            "status": "unavailable",
            "reason": "No BSD Bzzoiro consensus odds found for this fixture; using conf-adj model only",
        }

    # ── Data coverage notes ───────────────────────────────────────────────────
    h_matches_note = f"{h_n} matches (BSD Bzzoiro)" if h_n > 0 else "unavailable"
    a_matches_note = f"{a_n} matches (BSD Bzzoiro)" if a_n > 0 else "unavailable"

    h_scorer_srcs = list({s["data_source"] for s in home_scorers})
    a_scorer_srcs = list({s["data_source"] for s in away_scorers})

    def _player_note(srcs: list[str]) -> str:
        if "bsd_national_team_stats" in srcs:
            return "BSD Bzzoiro national team goals (career)"
        return "career goals from players_baseline.json"

    h_shrink_note = (
        f"shrunk (n={h_n} < N_REF={_N_RATING_REF})"
        if 0 < h_n < _N_RATING_REF else "full weight"
    )
    a_shrink_note = (
        f"shrunk (n={a_n} < N_REF={_N_RATING_REF})"
        if 0 < a_n < _N_RATING_REF else "full weight"
    )

    matrix_dict = {
        str(h): {str(a): mat for a, mat in enumerate(row)}
        for h, row in enumerate(matrix)
    }

    payload = {
        "match_id":    match_id,
        "home_team":   home_tla,
        "away_team":   away_tla,
        "generated_at": datetime.now(timezone.utc).isoformat(),

        # Primary outcome: conf-adj model blended with market (60/40)
        "outcome_probabilities": {
            **outcome,
            "model": "dixon_coles_conf_adj_market_blended",
            "sum_check": round(sum(outcome.values()), 4),
        },

        "market_odds": market_odds_section,

        "expected_goals": {
            "home": {**h_exp_range, "lambda_eff": round(lam_h_eff, 3),
                     "lambda_conf_adj": round(lam_h_conf, 3),
                     "data_source": h_src},
            "away": {**a_exp_range, "lambda_eff": round(lam_a_eff, 3),
                     "lambda_conf_adj": round(lam_a_conf, 3),
                     "data_source": a_src},
            "note": (
                "WC at neutral venues — HOME_ADV=1.00. "
                "lambda_conf_adj = raw model after shrinkage + confederation adjustment. "
                "lambda_eff = after market blend (used for scorers and most-likely score)."
            ),
        },

        "most_likely_score": mls,

        "scoreline_matrix": {
            "axes": "matrix[home_goals][away_goals]",
            "range": f"0-{MAX_GOALS}",
            "probabilities": matrix_dict,
        },

        "top_scorers": {
            "home": home_scorers,
            "away": away_scorers,
            "lineup": {
                "home_formation":  h_formation or "unknown",
                "away_formation":  a_formation or "unknown",
                "home_lineup_src": h_lineup_src,
                "away_lineup_src": a_lineup_src,
            },
            "note": (
                "P(scores >= 1) from formation-aware role weights × individual goal-rate factor. "
                "Only confirmed/predicted starters included — injured/absent players excluded. "
                "Lineup source: confirmed (≤2h before KO) > predicted > last match > squad."
            ),
        },

        "team_stat_profile": {
            "home": h_stat,
            "away": a_stat,
        },

        "data_coverage": {
            "home": {
                "team":               home_tla,
                "confederation":      h_conf_str,
                "conf_attack_factor": h_cf["attack"],
                "conf_defence_factor": h_cf["defence"],
                "results_history":    h_matches_note,
                "rating_source":      h_src,
                "rating_status":      h_status,
                "shrinkage":          h_shrink_note,
                "player_stats":       _player_note(h_scorer_srcs),
                "player_stat_source": "bsd_bzzoiro",
            },
            "away": {
                "team":               away_tla,
                "confederation":      a_conf_str,
                "conf_attack_factor": a_cf["attack"],
                "conf_defence_factor": a_cf["defence"],
                "results_history":    a_matches_note,
                "rating_source":      a_src,
                "rating_status":      a_status,
                "shrinkage":          a_shrink_note,
                "player_stats":       _player_note(a_scorer_srcs),
                "player_stat_source": "bsd_bzzoiro",
            },
        },

        "model_notes": [
            "Dixon-Coles Poisson (1997) with rho=-0.13 low-score correction.",
            "WC 2026 at neutral venues — HOME_ADV=1.00. International average: 1.35 goals/team/match.",
            (f"Ratings pipeline: BSD results → shrinkage (blend toward 1.0, N_REF={_N_RATING_REF}) → "
             "confederation quality adjustment → market blend."),
            (f"Confederation factors: UEFA att×{_CONF_FACTORS['UEFA']['attack']} def×{_CONF_FACTORS['UEFA']['defence']}  "
             f"CONMEBOL att×{_CONF_FACTORS['CONMEBOL']['attack']} def×{_CONF_FACTORS['CONMEBOL']['defence']}  "
             f"CAF att×{_CONF_FACTORS['CAF']['attack']} def×{_CONF_FACTORS['CAF']['defence']}  "
             f"CONCACAF att×{_CONF_FACTORS['CONCACAF']['attack']} def×{_CONF_FACTORS['CONCACAF']['defence']}."),
            (f"NOR shrinkage: raw attack=3.426 → shrunk=2.294 at n=8 (11-1 vs Moldova inflates raw)."),
            (f"Market blend: {int(MARKET_BLEND_WEIGHT*100)}% BSD Consensus fair odds + "
             f"{int((1-MARKET_BLEND_WEIGHT)*100)}% conf-adj model. "
             "xG back-solved via bisect to reproduce blended W/D/L."),
            ("Player scorer probs from BSD Bzzoiro national team career goals. "
             "Run scripts/bsd_backfill.py to refresh."),
            "Shots/corners/fouls unavailable: BSD does not expose team-level match stats.",
        ],
    }
    _prematch_cache[cache_key] = (now, payload)
    return payload
