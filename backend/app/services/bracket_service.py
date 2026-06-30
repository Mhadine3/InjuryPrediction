"""
bracket_service.py
==================
Projected knockout bracket for WC 2026.

The knockout tree is a single-elimination binary tree: when each round's matches
are sorted by football-data match id, round-R match `i` is fed by previous-round
matches `2i` and `2i+1` (verified against live results — e.g. the winner of
R32[2] propagates into R16[1]).

For every match from the Round of 32 upward we fill the two participants —
the ACTUAL team if the result/draw is already known, otherwise the model's
predicted winner of that feeder match — and predict the tie with the same
Dixon-Coles model used by the pre-match engine (model-only, no per-match network).

This yields, for any team, a full road to the final with predicted opponents that
stops the moment the team is eliminated (actual loss) or is predicted to lose.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

try:
    import requests as _requests
except ImportError:
    _requests = None

from app.services.prematch_service import (
    _get_ratings, _apply_conf_factors, _scoreline_matrix, _outcome_probs,
    INTL_AVG_GOALS, HOME_ADV, DATA_DIR,
)

_STAGES = ["LAST_32", "LAST_16", "QUARTER_FINALS", "SEMI_FINALS", "FINAL"]
_FD_BASE = "https://api.football-data.org/v4"
_CACHE: dict = {"t": 0.0, "proj": None}
_TTL = 90  # seconds — short so live knockout results propagate quickly


# ── Model-only outcome (no live odds / no network) ─────────────────────────────
def _model_outcome(home: str, away: str) -> dict:
    h_r, *_ = _get_ratings(home)
    a_r, *_ = _get_ratings(away)
    h = _apply_conf_factors(h_r, home)
    a = _apply_conf_factors(a_r, away)
    lam_h = max(h["attack_rating"] * a["defence_rating"] * INTL_AVG_GOALS * HOME_ADV, 0.05)
    lam_a = max(a["attack_rating"] * h["defence_rating"] * INTL_AVG_GOALS, 0.05)
    return _outcome_probs(_scoreline_matrix(lam_h, lam_a))


def _adv_prob(o: dict) -> float:
    """P(home advances): regulation win + half of a draw (ET/penalties ≈ 50/50)."""
    return o["home_win"] + 0.5 * o["draw"]


# ── Actual-winner resolution (handles penalty shootouts & extra time) ──────────
def _resolve_actual(home: Optional[str], away: Optional[str], score: dict) -> Optional[str]:
    """The team that ACTUALLY advanced, or None if the data can't confirm it yet.
    Knockout ties can be decided on penalties, so check the most decisive level
    first. Never guesses a winner for a tie the API hasn't resolved."""
    if not home or not away:
        return None
    wf = score.get("winner")
    if wf == "HOME_TEAM":
        return home
    if wf == "AWAY_TEAM":
        return away
    for level in ("penalties", "extraTime", "regularTime", "fullTime"):
        s = score.get(level) or {}
        h, a = s.get("home"), s.get("away")
        if h is not None and a is not None and h != a:
            return home if h > a else away
    return None  # finished but undecided in the data (e.g. shootout not yet posted)


# ── Load knockout matches (live football-data, cached; snapshot fallback) ───────
def _fetch_knockout() -> list[dict]:
    """Normalised knockout matches: {id, stage, home, away, status, hs, as,
    pen_h, pen_a, awinner}. `awinner` is the confirmed advancer (or None)."""
    out: list[dict] = []
    key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if _requests and key:
        try:
            r = _requests.get(f"{_FD_BASE}/competitions/WC/matches",
                              headers={"X-Auth-Token": key}, timeout=15)
            if r.status_code == 200:
                for m in r.json().get("matches", []):
                    if m.get("stage") not in _STAGES:
                        continue
                    score = m.get("score") or {}
                    reg = score.get("regularTime") or {}
                    ft = score.get("fullTime") or {}
                    pen = score.get("penalties") or {}
                    home = (m.get("homeTeam") or {}).get("tla")
                    away = (m.get("awayTeam") or {}).get("tla")
                    hs = reg.get("home") if reg.get("home") is not None else ft.get("home")
                    as_ = reg.get("away") if reg.get("away") is not None else ft.get("away")
                    out.append({
                        "id": m["id"], "stage": m["stage"], "home": home, "away": away,
                        "status": m.get("status"), "hs": hs, "as": as_,
                        "pen_h": pen.get("home"), "pen_a": pen.get("away"),
                        "awinner": _resolve_actual(home, away, score),
                    })
                if out:
                    return out
        except Exception:
            pass
    # Fallback: the build_full_wc snapshot (full-time scores only)
    try:
        fx = json.loads((DATA_DIR / "wc_fixtures.json").read_text(encoding="utf-8"))
        for f in fx:
            if f.get("stage") not in _STAGES:
                continue
            hs, as_ = f.get("home_score"), f.get("away_score")
            aw = None
            if hs is not None and as_ is not None and hs != as_:
                aw = f.get("home") if hs > as_ else f.get("away")
            out.append({
                "id": f.get("fd_id"), "stage": f.get("stage"),
                "home": f.get("home"), "away": f.get("away"),
                "status": f.get("status"), "hs": hs, "as": as_,
                "pen_h": None, "pen_a": None, "awinner": aw,
            })
    except Exception:
        pass
    return out


def _node(home: Optional[str], away: Optional[str], m: dict,
          home_pred: bool, away_pred: bool) -> dict:
    finished = m.get("status") == "FINISHED"
    awinner = m.get("awinner")
    p = None
    if finished and awinner and home and away:
        winner, winner_predicted = awinner, False          # confirmed result
    elif home and away:
        p = _model_outcome(home, away)                     # not decided yet → predict
        winner = home if _adv_prob(p) >= 0.5 else away
        winner_predicted = True
    else:
        winner, winner_predicted = None, True
    return {
        "home": home, "away": away,
        "home_predicted": home_pred, "away_predicted": away_pred,
        "status": m.get("status"), "hs": m.get("hs"), "as": m.get("as"),
        "pen_h": m.get("pen_h"), "pen_a": m.get("pen_a"),
        "winner": winner, "winner_predicted": winner_predicted,
        "p_home": (p or {}).get("home_win"), "p_draw": (p or {}).get("draw"),
        "p_away": (p or {}).get("away_win"),
        "id": m.get("id"),
    }


def build_projection(force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["proj"] and now - _CACHE["t"] < _TTL:
        return _CACHE["proj"]

    matches = _fetch_knockout()
    by_stage = {s: sorted([m for m in matches if m["stage"] == s], key=lambda m: (m["id"] or 0))
                for s in _STAGES}
    stages: dict[str, list] = {s: [] for s in _STAGES}

    # Round of 32 — participants are the actual drawn teams.
    for m in by_stage["LAST_32"]:
        stages["LAST_32"].append(_node(m["home"], m["away"], m, False, False))

    # Subsequent rounds — fill empty slots with predicted feeder winners.
    for si in range(1, len(_STAGES)):
        s, prev = _STAGES[si], stages[_STAGES[si - 1]]
        for i, m in enumerate(by_stage[s]):
            fh_node = prev[2 * i] if 2 * i < len(prev) else None
            fa_node = prev[2 * i + 1] if 2 * i + 1 < len(prev) else None
            home = m["home"] or (fh_node and fh_node["winner"])
            away = m["away"] or (fa_node and fa_node["winner"])
            # A slot is "predicted" only when the API hasn't filled it AND the
            # feeder result isn't decided yet (an actual advancer is not a guess).
            home_pred = (m["home"] is None) and bool(fh_node and fh_node["winner_predicted"])
            away_pred = (m["away"] is None) and bool(fa_node and fa_node["winner_predicted"])
            stages[s].append(_node(home, away, m, home_pred, away_pred))

    proj = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stages": stages}
    _CACHE.update(t=now, proj=proj)
    return proj


# Human labels (exposed for the API/UI)
STAGE_LABEL = {
    "LAST_32": "Round of 32", "LAST_16": "Round of 16",
    "QUARTER_FINALS": "Quarter-finals", "SEMI_FINALS": "Semi-finals", "FINAL": "Final",
}
