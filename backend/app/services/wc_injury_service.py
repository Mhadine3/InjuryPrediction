"""
wc_injury_service.py
Real WC 2026 competition injury risk assessment.

Sources:
  - BSD Bzzoiro /events/{id}/lineups/ — confirmed starters, bench, unavailable + injury reasons
  - data/players_baseline.json        — age, position, injury_proneness, caps
  - SCHEDULED_MATCHES                 — days to next match per team

No ML model. Risk is computed transparently from:
  participation status in most recent WC match
  + player age
  + physiological injury_proneness (from baseline)
  + days until next WC match
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

from app.services.prematch_service import (
    _PLAYERS_BY_TEAM,
    _OUR_BSD_EVENTS,
    _bsd_get,
    _BSD_TEAM_IDS,
)
from app.services.match_predictor import SCHEDULED_MATCHES
from app.services.risk_adjustment import compute_risk_adjustment, load_injury_summaries, is_currently_injured

# ── Risk constants ─────────────────────────────────────────────────────────────

_BASE_RISK: dict[str, float] = {
    "injured":      0.85,   # confirmed injury reason from BSD unavailable_players
    "unavailable":  0.68,   # in unavailable list, no specific reason
    "not_in_squad": 0.50,   # known player absent (no match or not selected)
    "benched":      0.18,   # named substitute who sat the full match
    "starter":      0.28,   # played from kick-off (~90 min)
    "no_match_yet": 0.20,   # team hasn't played a WC match yet
}

# Minimum risk floors for confirmed-injury statuses — ML score can only raise these
_ML_FLOOR: dict[str, float] = {
    "injured":     0.72,
    "unavailable": 0.55,
}

# ── Wellness / ML score cache ──────────────────────────────────────────────────

_WELLNESS_FILE  = Path(__file__).resolve().parents[3] / "data" / "latest_wellness.json"
_wellness_cache: dict[str, dict] = {}


def _load_wellness_cache() -> None:
    global _wellness_cache
    if _WELLNESS_FILE.exists():
        try:
            _wellness_cache = json.loads(_WELLNESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _wellness_cache = {}


_load_wellness_cache()

_TEAM_NAMES: dict[str, str] = {
    "BRA": "Brazil", "MAR": "Morocco", "HAI": "Haiti", "SCO": "Scotland",
    "FRA": "France", "SEN": "Senegal", "IRQ": "Iraq",  "NOR": "Norway",
}

# BSD team IDs reversed: bsd_team_id -> tla
_BSD_ID_TO_TLA: dict[int, str] = {v: k for k, v in _BSD_TEAM_IDS.items()}


# ── Risk formula helpers ───────────────────────────────────────────────────────

def _age_risk(age: int) -> float:
    if age >= 35: return 0.15
    if age >= 33: return 0.10
    if age >= 30: return 0.05
    return 0.0


def _proneness_risk(proneness: str) -> float:
    return {"high": 0.08, "medium": 0.03, "low": 0.0}.get(proneness, 0.03)


def _recovery_risk(days: int | None) -> float:
    if days is None: return 0.0
    if days <= 4:    return 0.10
    if days <= 6:    return 0.05
    return 0.0


def _categorize(score: float) -> str:
    if score >= 0.75: return "very_high"
    if score >= 0.55: return "high"
    if score >= 0.35: return "moderate"
    return "low"


# ── Name normalization + matching ──────────────────────────────────────────────

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def _names_match(bsd_name: str, baseline_name: str) -> bool:
    """Fuzzy match BSD name (may be abbreviated or a mononym) to baseline full name."""
    bn = _norm(bsd_name)
    ln = _norm(baseline_name)

    if bn == ln:
        return True

    # Normalise "jr." ↔ "junior"
    bn2 = bn.replace("jr.", "junior").strip()
    ln2 = ln.replace("jr.", "junior").strip()
    if bn2 == ln2:
        return True

    bn_parts = bn2.split()
    ln_parts = ln2.split()

    if not bn_parts or not ln_parts:
        return False

    # Single-word BSD name (mononym like "Danilo", "Neymar", "Raphinha"):
    # match if it equals the first word of the baseline name.
    if len(bn_parts) == 1:
        return bn_parts[0] == ln_parts[0]

    # Multi-word: last-name match
    bn_last = next((w for w in reversed(bn_parts) if w != "junior"), "")
    ln_last = next((w for w in reversed(ln_parts) if w != "junior"), "")

    if not bn_last or bn_last != ln_last:
        return False

    # Last name matched — verify first name / initial
    bn_first = bn_parts[0]
    ln_first = ln_parts[0]
    if bn_first.endswith("."):
        # abbreviated: "l." → check baseline starts with "l"
        return ln_first.startswith(bn_first[:-1])
    return bn_first == ln_first or bn_first[0] == ln_first[0]


def _find_in_set(baseline_name: str, bsd_names: set[str]) -> bool:
    return any(_names_match(n, baseline_name) for n in bsd_names)


def _find_reason(baseline_name: str, injured: dict[str, str]) -> Optional[str]:
    for bsd_name, reason in injured.items():
        if _names_match(bsd_name, baseline_name):
            return reason or "Unavailable"
    return None


# ── BSD lineup fetch (cached — lineups for finished matches are immutable) ─────

_lineup_cache: dict[int, tuple[float, Optional[dict]]] = {}
_LINEUP_TTL = 120  # seconds


def _fetch_lineup(event_id: int) -> Optional[dict]:
    import time as _time
    now = _time.time()
    if event_id in _lineup_cache:
        ts, cached = _lineup_cache[event_id]
        if now - ts < _LINEUP_TTL:
            return cached
    sc, data = _bsd_get(f"/events/{event_id}/lineups/")
    result = data if sc == 200 else None
    _lineup_cache[event_id] = (now, result)
    return result


# ── Schedule helpers ───────────────────────────────────────────────────────────

def _days_to_next_match(team_tla: str) -> Optional[int]:
    today = date.today().isoformat()
    upcoming = [
        m for m in SCHEDULED_MATCHES
        if (m["home"] == team_tla or m["away"] == team_tla) and m["date"] > today
    ]
    if not upcoming:
        return None
    nxt = min(m["date"] for m in upcoming)
    return (date.fromisoformat(nxt) - date.today()).days


def _last_played_event_id(team_tla: str) -> Optional[int]:
    """Find most-recent finished BSD event_id for this team."""
    from app.services.prematch_service import get_bsd_live_scores
    finished = [
        m for m in get_bsd_live_scores()
        if m["is_finished"] and (m["home_tla"] == team_tla or m["away_tla"] == team_tla)
    ]
    if not finished:
        return None
    latest = max(finished, key=lambda m: m.get("event_date") or "")
    return latest["event_id"]


# ── Main public function ───────────────────────────────────────────────────────

def compute_wc_team_risk(team_tla: str) -> dict:
    """
    Real WC 2026 injury risk for all players in a team.

    Response shape mirrors TeamRiskSummary with extra fields:
      participation, injury_reason, days_to_next_match, age.
    """
    players = _PLAYERS_BY_TEAM.get(team_tla, [])
    if not players:
        return {"error": f"No baseline data for {team_tla}"}

    _load_wellness_cache()
    days_to_next = _days_to_next_match(team_tla)
    event_id = _last_played_event_id(team_tla)
    lineup_data = _fetch_lineup(event_id) if event_id else None

    # ── Parse lineup ──────────────────────────────────────────────────────────
    starters:  set[str]        = set()
    bench:     set[str]        = set()
    injured:   dict[str, str]  = {}   # bsd_name -> reason string

    if lineup_data:
        lins = lineup_data.get("lineups", {})
        # Determine which side (home/away) is our team
        home_tid = (lins.get("home") or {}).get("team_id")
        away_tid = (lins.get("away") or {}).get("team_id")
        our_tid  = _BSD_TEAM_IDS.get(team_tla)

        side = "home" if home_tid == our_tid else "away"
        ldata = lins.get(side, {})

        starters = {p["name"] for p in (ldata.get("players")     or [])}
        bench    = {p["name"] for p in (ldata.get("substitutes") or [])}

        for p in (lineup_data.get("unavailable_players") or {}).get(side, []):
            injured[p["name"]] = p.get("reason") or ""

    # ── Per-player risk ───────────────────────────────────────────────────────
    load_injury_summaries()
    result_rows: list[dict] = []
    scores: list[float]     = []

    for p in players:
        name       = p["name"]
        age        = int(p.get("age") or 25)
        proneness  = (p.get("traits") or {}).get("injury_proneness", "medium")
        pos_detail = p.get("position_detail") or p.get("position") or ""

        if lineup_data:
            # Starters take priority — if they played, ignore stale injured records
            if _find_in_set(name, starters):
                status = "starter"
                base   = _BASE_RISK["starter"]
                reason = "Started match"
            elif _find_in_set(name, bench):
                status = "benched"
                base   = _BASE_RISK["benched"]
                reason = "Named substitute"
            else:
                injury_reason = _find_reason(name, injured)
                if injury_reason is not None:
                    status = "injured"
                    base   = _BASE_RISK["injured"]
                    reason = injury_reason or "Injury"
                else:
                    inj, inj_reason = is_currently_injured(p["player_id"])
                    if inj:
                        status = "injured"
                        base   = _BASE_RISK["injured"]
                        reason = inj_reason
                    else:
                        status = "not_in_squad"
                        base   = _BASE_RISK["not_in_squad"]
                        reason = "Not selected"
        else:
            inj, inj_reason = is_currently_injured(p["player_id"])
            if inj:
                status = "injured"
                base   = _BASE_RISK["injured"]
                reason = inj_reason
            else:
                status = "no_match_yet"
                base   = _BASE_RISK["no_match_yet"]
                reason = "Pre-tournament baseline"

        # ── ML base score (replaces rule formula when logged data is recent) ──
        wellness    = _wellness_cache.get(p["player_id"])
        ml_score    = wellness.get("ml_risk_score")    if wellness else None
        ml_category = wellness.get("ml_risk_category") if wellness else None
        ml_top      = wellness.get("ml_top_features")  if wellness else None
        days_ago    = int(wellness.get("days_ago") or 99) if wellness else 99
        use_ml      = ml_score is not None and days_ago <= 3

        if use_ml:
            if status in _ML_FLOOR:
                # Confirmed injured/unavailable: ML can only raise above the floor
                base_score = round(max(float(ml_score), _ML_FLOOR[status]), 4)
            else:
                base_score = round(float(ml_score), 4)
            score_source = "xgboost_ml"
        else:
            base_score = min(round(
                base
                + _age_risk(age)
                + _proneness_risk(proneness)
                + _recovery_risk(days_to_next),
                4,
            ), 1.0)
            score_source = "rule_based"

        # ── Risk adjustment layer ──────────────────────────────────────────────
        adj = compute_risk_adjustment(p["player_id"], p, skip_live_wellness=use_ml, participation=status)
        adjusted_score = round(min(1.0, max(0.0, base_score + adj["total_adjustment"])), 4)
        adjusted_cat   = _categorize(adjusted_score)

        scores.append(adjusted_score)
        result_rows.append({
            "player_id":              p["player_id"],
            "full_name":              name,
            "position":               pos_detail,
            "caps":                   int(p.get("caps") or 0),
            "age":                    age,
            "risk_score":             base_score,
            "risk_category":          ml_category if use_ml else _categorize(base_score),
            "adjusted_risk_score":    adjusted_score,
            "adjusted_risk_category": adjusted_cat,
            "risk_adjustment":        adj["total_adjustment"],
            "risk_factors":           adj["factors"],
            "participation":          status,
            "injury_reason":          reason,
            "days_to_next_match":     days_to_next,
            "acwr":                   wellness.get("acwr") if wellness else None,
            "alert":                  adjusted_cat in ("high", "very_high"),
            "score_source":           score_source,
            "ml_top_features":        ml_top if use_ml else None,
            "wellness_days_ago":      days_ago if wellness else None,
        })

    result_rows.sort(key=lambda r: -r["adjusted_risk_score"])
    cats = [r["adjusted_risk_category"] for r in result_rows]

    return {
        "team_fifa_code":     team_tla,
        "team_name":          _TEAM_NAMES.get(team_tla, team_tla),
        "as_of_date":         date.today().isoformat(),
        "data_source":        "bsd_bzzoiro_lineups",
        "last_match_event_id": event_id,
        "days_to_next_match": days_to_next,
        "total_players":      len(result_rows),
        "low_count":          cats.count("low"),
        "moderate_count":     cats.count("moderate"),
        "high_count":         cats.count("high"),
        "very_high_count":    cats.count("very_high"),
        "mean_risk_score":    round(sum(scores) / len(scores), 4) if scores else 0.0,
        "players":            result_rows,
    }
