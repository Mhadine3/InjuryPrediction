"""
Performance Readiness Service — Fitness-Fatigue Model.

Form = Fitness - Fatigue
Readiness Score (0-100) = weighted combination of:
  35% ACWR zone         (Gabbett 2016)
  25% HRV vs baseline   (Buchheit 2014)
  20% Wellness          (Hooper 1995)
  20% Fitness trend     (chronic load direction)
"""

from dataclasses import dataclass, field

# ── Group C match schedule (day numbers in the 90-day camp) ──────────────────
MATCH_SCHEDULE: dict[str, list[int]] = {
    "BRA": [20, 35, 50],
    "MAR": [21, 36, 50],
    "HAI": [20, 36, 51],
    "SCO": [21, 35, 51],
}

SESSION_TYPES: dict[str, dict] = {
    "rest":      {"label": "Full Rest",       "au_min": 0,   "au_max": 0},
    "recovery":  {"label": "Active Recovery", "au_min": 30,  "au_max": 150},
    "technical": {"label": "Technical",       "au_min": 200, "au_max": 420},
    "tactical":  {"label": "Tactical",        "au_min": 380, "au_max": 580},
    "physical":  {"label": "Physical",        "au_min": 520, "au_max": 760},
}

READINESS_CATEGORIES = [
    (80, "peak"),
    (65, "ready"),
    (45, "moderate"),
    (25, "low"),
    (0,  "rest"),
]


@dataclass
class ReadinessResult:
    readiness_score:      float
    readiness_category:   str
    acwr_score:           float
    hrv_score:            float
    wellness_score:       float
    fitness_trend_score:  float
    recommended_session:  str
    recommended_load_min: int
    recommended_load_max: int
    days_to_next_match:   int | None
    acwr:                 float | None
    flags:                list[str] = field(default_factory=list)


# ── Component scorers ─────────────────────────────────────────────────────────

def _acwr_score(acwr: float | None) -> float:
    if acwr is None:
        return 55.0
    if acwr <= 0:
        return 40.0
    if 0.85 <= acwr <= 1.15:   return 100.0
    if 0.80 <= acwr < 0.85:    return 80 + (acwr - 0.80) / 0.05 * 20
    if 1.15 < acwr <= 1.30:    return 100 - (acwr - 1.15) / 0.15 * 30
    if acwr < 0.80:            return max(40, 50 + (acwr / 0.80) * 30)
    if 1.30 < acwr <= 1.50:    return max(30, 70 - (acwr - 1.30) / 0.20 * 40)
    if 1.50 < acwr <= 2.00:    return max(5,  30 - (acwr - 1.50) / 0.50 * 25)
    return max(0, 5 - (acwr - 2.00) * 5)


def _hrv_score(hrv_ms: float, hrv_baseline: float) -> float:
    if hrv_baseline <= 0:
        return 60.0
    ratio = hrv_ms / hrv_baseline
    if ratio >= 1.05:           return 100.0
    if ratio >= 0.95:           return 75 + (ratio - 0.95) / 0.10 * 25
    if ratio >= 0.85:           return 40 + (ratio - 0.85) / 0.10 * 35
    return max(0, (ratio / 0.85) * 40)


def _wellness_score(hooper: float) -> float:
    clamped = max(4.0, min(28.0, hooper))
    return max(0.0, 100 - ((clamped - 4) / 24) * 100)


def _fitness_trend_score(srpe_values: list[float]) -> float:
    if len(srpe_values) < 14:
        return 60.0
    recent = sum(srpe_values[-7:])
    prior  = sum(srpe_values[-14:-7])
    if prior == 0:
        return 60.0
    ratio = recent / prior
    if 1.00 <= ratio <= 1.20:  return min(100, 80 + (ratio - 1.0) * 100)
    if ratio < 1.00:           return max(20, 80 * ratio)
    return max(30, 100 - (ratio - 1.20) * 150)


def _categorise(score: float) -> str:
    for threshold, label in READINESS_CATEGORIES:
        if score >= threshold:
            return label
    return "rest"


def _next_match(team_code: str, day_number: int) -> int | None:
    upcoming = [d for d in MATCH_SCHEDULE.get(team_code.upper(), []) if d > day_number]
    return min(upcoming) if upcoming else None


def _recommend_session(
    acwr: float | None,
    readiness: float,
    days_to_match: int | None,
) -> str:
    if days_to_match is not None and days_to_match <= 1:
        return "rest"
    if days_to_match is not None and days_to_match == 2:
        return "recovery"
    if readiness < 25 or (acwr and acwr > 2.0):
        return "rest"
    if readiness < 45 or (acwr and acwr > 1.5):
        return "recovery"
    if acwr and acwr > 1.3:
        return "technical"
    if readiness >= 80 and (acwr is None or acwr < 1.2):
        return "physical"
    if readiness >= 65:
        return "tactical"
    return "technical"


# ── Public API ────────────────────────────────────────────────────────────────

def compute_readiness(
    metrics: list[dict],
    hrv_baseline: float,
    acwr: float | None,
    team_code: str,
    day_number: int,
) -> ReadinessResult:
    if not metrics:
        return ReadinessResult(
            readiness_score=50, readiness_category="moderate",
            acwr_score=50, hrv_score=50, wellness_score=50, fitness_trend_score=50,
            recommended_session="technical", recommended_load_min=200,
            recommended_load_max=420, days_to_next_match=None, acwr=acwr,
            flags=["Insufficient metric data"],
        )

    latest     = metrics[-1]
    srpe_vals  = [float(m.get("srpe") or 0) for m in metrics]
    hrv_ms     = float(latest.get("hrv_ms") or hrv_baseline)

    fat = float(latest.get("fatigue")      or 3)
    sor = float(latest.get("soreness")     or 3)
    str = float(latest.get("stress")       or 3)
    slq = float(latest.get("sleep_quality") or 3)
    hooper = fat + sor + str + slq

    a = _acwr_score(acwr)
    h = _hrv_score(hrv_ms, hrv_baseline)
    w = _wellness_score(hooper)
    t = _fitness_trend_score(srpe_vals)

    score    = round(max(0, min(100, a * 0.35 + h * 0.25 + w * 0.20 + t * 0.20)), 1)
    category = _categorise(score)

    next_match    = _next_match(team_code, day_number)
    days_to_match = (next_match - day_number) if next_match is not None else None

    session = _recommend_session(acwr, score, days_to_match)
    rec     = SESSION_TYPES[session]

    # ── Flags ────────────────────────────────────────────────────────────────
    flags: list[str] = []
    if acwr is not None:
        if acwr > 1.5:
            flags.append(f"ACWR {acwr:.2f} — danger zone, reduce load")
        elif acwr > 1.3:
            flags.append(f"ACWR {acwr:.2f} — caution zone")
        elif acwr < 0.8:
            flags.append(f"ACWR {acwr:.2f} — under-training, increase load")

    if hrv_baseline > 0:
        ratio = hrv_ms / hrv_baseline
        if ratio < 0.88:
            flags.append(f"HRV {round((1 - ratio) * 100)}% below baseline — not recovered")
        elif ratio > 1.05:
            flags.append("HRV above baseline — fully recovered")

    if hooper > 18:
        flags.append("Hooper Index elevated — poor wellness reported")

    if days_to_match is not None:
        flags.append(f"{days_to_match} day{'s' if days_to_match != 1 else ''} to next match")

    return ReadinessResult(
        readiness_score=score,
        readiness_category=category,
        acwr_score=round(a, 1),
        hrv_score=round(h, 1),
        wellness_score=round(w, 1),
        fitness_trend_score=round(t, 1),
        recommended_session=session,
        recommended_load_min=rec["au_min"],
        recommended_load_max=rec["au_max"],
        days_to_next_match=days_to_match,
        acwr=acwr,
        flags=flags,
    )
