"""
Risk Adjustment Layer (Path A).

Takes the base risk score from wc_injury_service and applies
additional adjustments from:
  1. Physiology quality (HRV baseline, VO2 max, recovery speed)
  2. Injury history burden (from Transfermarkt cache)
  3. Recent injury return window (recurrence danger zone)
  4. Match load (days since last club match, matches last 28d, form score)

Returns the adjusted score, new category, and a breakdown of every
contributing factor so the UI can explain the change to coaches.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_SUMMARIES_FILE  = Path(__file__).resolve().parents[3] / "data" / "tm_injury_summaries.json"
_SHAPE_FILE      = Path(__file__).resolve().parents[3] / "data" / "player_shape.json"
_WELLNESS_FILE   = Path(__file__).resolve().parents[3] / "data" / "latest_wellness.json"

_injury_summaries: dict[str, dict] = {}
_player_shape:     dict[str, dict] = {}
_wellness_cache:   dict[str, dict] = {}


def load_injury_summaries() -> None:
    global _injury_summaries
    if _SUMMARIES_FILE.exists():
        try:
            _injury_summaries = json.loads(
                _SUMMARIES_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            _injury_summaries = {}


def _load_shape() -> None:
    global _player_shape
    if _SHAPE_FILE.exists():
        try:
            raw = json.loads(_SHAPE_FILE.read_text(encoding="utf-8"))
            _player_shape = raw.get("players", {})
        except Exception:
            _player_shape = {}


def _load_wellness() -> None:
    global _wellness_cache
    if _WELLNESS_FILE.exists():
        try:
            _wellness_cache = json.loads(_WELLNESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _wellness_cache = {}


def save_injury_summary(player_id: str, summary: dict, injuries: list[dict]) -> None:
    """Called by player_profile router after a successful TM fetch."""
    load_injury_summaries()

    # Compute recurring-injury type counts here while we have the full list
    types = [i.get("injury") for i in injuries if i.get("injury")]
    type_counts = dict(Counter(types).most_common(5)) if types else {}

    _injury_summaries[player_id] = {
        "total_injuries":    summary.get("total_injuries", 0),
        "total_days_missed": summary.get("total_days_missed", 0),
        "most_recent":       summary.get("most_recent"),
        "type_counts":       type_counts,
        "cached_at":         date.today().isoformat(),
    }
    try:
        _SUMMARIES_FILE.write_text(
            json.dumps(_injury_summaries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not save injury summaries: %s", exc)


load_injury_summaries()
_load_shape()
_load_wellness()


# ── Category thresholds (must match wc_injury_service._categorize) ─────────────

def _categorize(score: float) -> str:
    if score >= 0.75: return "very_high"
    if score >= 0.55: return "high"
    if score >= 0.35: return "moderate"
    return "low"


# ── Main adjustment function ───────────────────────────────────────────────────

def compute_risk_adjustment(player_id: str, player: dict, skip_live_wellness: bool = False, participation: str = "") -> dict:
    """
    Returns:
        {
          "total_adjustment": float,
          "adjusted_score":   float,          # base already added by caller
          "adjusted_category": str,
          "factors": {
              "factor_key": {"delta": float, "label": str, "direction": "up"|"down"},
              ...
          }
        }

    Pass `base_score` is NOT in this dict — caller adds it:
        adjusted = base + result["total_adjustment"]
    """
    # Reload file-based caches on every call so new data is picked up live
    _load_shape()
    _load_wellness()

    physio  = player.get("physiology") or {}
    traits  = player.get("traits")     or {}
    summary = _injury_summaries.get(player_id)

    factors: dict[str, dict] = {}

    # ── 1. Physiology quality ──────────────────────────────────────────────────
    hrv = physio.get("hrv_baseline_ms")
    if hrv is not None:
        if hrv < 50:
            factors["low_hrv"] = {
                "delta": 0.06,
                "label": f"Low HRV baseline ({hrv} ms) — limited recovery capacity",
                "direction": "up",
            }
        elif hrv < 60:
            factors["low_hrv"] = {
                "delta": 0.03,
                "label": f"Below-average HRV ({hrv} ms)",
                "direction": "up",
            }
        elif hrv >= 80:
            factors["high_hrv"] = {
                "delta": -0.03,
                "label": f"Strong HRV baseline ({hrv} ms) — good recovery buffer",
                "direction": "down",
            }

    vo2 = physio.get("vo2_max_ml_kg_min")
    if vo2 is not None:
        if vo2 < 50:
            factors["low_vo2"] = {
                "delta": 0.04,
                "label": f"Below-average aerobic capacity (VO₂ {vo2} ml/kg/min)",
                "direction": "up",
            }
        elif vo2 >= 65:
            factors["high_vo2"] = {
                "delta": -0.02,
                "label": f"Elite aerobic capacity (VO₂ {vo2} ml/kg/min)",
                "direction": "down",
            }

    recovery_speed = traits.get("recovery_speed", "medium")
    if recovery_speed == "slow":
        factors["slow_recovery"] = {
            "delta": 0.05,
            "label": "Slow physiological recovery — needs more rest between matches",
            "direction": "up",
        }
    elif recovery_speed == "fast":
        factors["fast_recovery"] = {
            "delta": -0.03,
            "label": "Fast physiological recovery",
            "direction": "down",
        }

    resilience = traits.get("mental_resilience", "medium")
    if resilience == "fragile":
        factors["low_resilience"] = {
            "delta": 0.03,
            "label": "Low mental resilience — higher stress-injury correlation",
            "direction": "up",
        }

    # ── 2. Injury history burden ───────────────────────────────────────────────
    if summary:
        n = summary.get("total_injuries", 0)
        if n >= 12:
            factors["injury_burden"] = {
                "delta": 0.12,
                "label": f"High injury burden — {n} career injuries on record",
                "direction": "up",
            }
        elif n >= 8:
            factors["injury_burden"] = {
                "delta": 0.08,
                "label": f"Significant injury history — {n} injuries",
                "direction": "up",
            }
        elif n >= 5:
            factors["injury_burden"] = {
                "delta": 0.04,
                "label": f"Notable injury history — {n} injuries",
                "direction": "up",
            }

        # Recurring injury type
        type_counts: dict[str, int] = summary.get("type_counts", {})
        if type_counts:
            top_type, top_count = max(type_counts.items(), key=lambda x: x[1])
            if top_count >= 3:
                factors["recurring_injury"] = {
                    "delta": 0.10,
                    "label": f"Chronic weakness — {top_type} ({top_count}×)",
                    "direction": "up",
                }
            elif top_count == 2:
                factors["recurring_injury"] = {
                    "delta": 0.05,
                    "label": f"Repeated {top_type} ({top_count}×)",
                    "direction": "up",
                }

        # Recent injury return window — skip if player confirmed active in WC match
        # (stale TM until_date contradicts confirmed participation)
        if participation not in ("starter", "benched"):
            mr = summary.get("most_recent")
            if mr and mr.get("until_date"):
                try:
                    until = date.fromisoformat(mr["until_date"])
                    days_ago = (date.today() - until).days
                    inj_name = mr.get("injury", "injury")
                    if days_ago < 0:
                        factors["currently_injured"] = {
                            "delta": 0.18,
                            "label": f"Currently recovering from {inj_name}",
                            "direction": "up",
                        }
                    elif days_ago < 14:
                        factors["recent_return"] = {
                            "delta": 0.20,
                            "label": f"Returned only {days_ago}d ago from {inj_name}",
                            "direction": "up",
                        }
                    elif days_ago < 30:
                        factors["recent_return"] = {
                            "delta": 0.14,
                            "label": f"Returned {days_ago}d ago from {inj_name} — recurrence window",
                            "direction": "up",
                        }
                    elif days_ago < 60:
                        factors["recent_return"] = {
                            "delta": 0.07,
                            "label": f"Returned {days_ago}d ago from {inj_name}",
                            "direction": "up",
                        }
                except (ValueError, TypeError):
                    pass

    # ── 3. Match load factors (player_shape.json) ──────────────────────────────
    shape = _player_shape.get(player_id)
    if shape:
        matches_28d = int(shape.get("matches_last_28d") or 0)
        days_off    = shape.get("days_since_last_club")
        form_score  = float(shape.get("form_score") or 0.5)
        mins_28d    = int(shape.get("est_minutes_last_28d") or 0)
        age         = int(player.get("age") or 25)

        # High match frequency → accumulated fatigue
        if matches_28d >= 10:
            factors["match_overload"] = {
                "delta": 0.10,
                "label": f"Match overload — {matches_28d} games in last 28 days",
                "direction": "up",
            }
        elif matches_28d >= 7:
            factors["match_overload"] = {
                "delta": 0.06,
                "label": f"High match load — {matches_28d} games in last 28 days",
                "direction": "up",
            }

        # Long absence → deconditioning — skip if confirmed active in WC match
        # (club season ends before WC; stale days_since_last_club contradicts confirmed play)
        if days_off is not None and participation not in ("starter", "benched"):
            days_off = int(days_off)
            if days_off > 60:
                factors["deconditioning"] = {
                    "delta": 0.09,
                    "label": f"Deconditioning risk — {days_off} days since last club match",
                    "direction": "up",
                }
            elif days_off > 30:
                factors["deconditioning"] = {
                    "delta": 0.05,
                    "label": f"Below match fitness — {days_off} days since last game",
                    "direction": "up",
                }

        # Poor recent form with active match schedule
        if form_score < 0.25 and matches_28d > 0:
            factors["poor_form"] = {
                "delta": 0.05,
                "label": f"Poor recent form (score {form_score:.2f}) — possible hidden fatigue",
                "direction": "up",
            }

        # High minutes load for older players
        if mins_28d > 900 and age >= 30:
            factors["veteran_minutes"] = {
                "delta": 0.05,
                "label": f"Heavy minutes load ({mins_28d} min in 28d) for age {age}",
                "direction": "up",
            }

        # Good recent activity = positive signal (match-fit)
        if 3 <= matches_28d <= 6 and form_score >= 0.5 and (days_off or 99) <= 14:
            factors["match_fit"] = {
                "delta": -0.04,
                "label": f"Match fit — {matches_28d} games, good form ({form_score:.2f})",
                "direction": "down",
            }

    # ── 4. Live wellness data (from latest_wellness.json) ─────────────────────
    # Skipped when XGBoost ML score is used as base — wellness inputs are already
    # incorporated inside the model, so applying them again would double-count.
    if skip_live_wellness:
        total = round(sum(f["delta"] for f in factors.values()), 4)
        return {"total_adjustment": total, "factors": factors}

    wellness = _wellness_cache.get(player_id)
    if wellness:
        days_ago = int(wellness.get("days_ago") or 0)
        # Attenuate signals older than 3 days — still informative but less acute
        staleness = 1.0 if days_ago <= 1 else (0.7 if days_ago <= 3 else 0.4)
        stale_label = f" (logged {days_ago}d ago)" if days_ago > 1 else ""

        hrv_drop = wellness.get("hrv_pct_drop")   # positive = bad (dropped below baseline)
        if hrv_drop is not None:
            if hrv_drop >= 15:
                factors["live_hrv_drop"] = {
                    "delta": round(0.10 * staleness, 3),
                    "label": f"HRV dropped {hrv_drop:.1f}% below baseline{stale_label} — acute fatigue signal",
                    "direction": "up",
                    "live": True,
                }
            elif hrv_drop >= 8:
                factors["live_hrv_drop"] = {
                    "delta": round(0.05 * staleness, 3),
                    "label": f"HRV {hrv_drop:.1f}% below baseline{stale_label}",
                    "direction": "up",
                    "live": True,
                }
            elif hrv_drop <= -8:
                factors["live_hrv_boost"] = {
                    "delta": round(-0.04 * staleness, 3),
                    "label": f"HRV {abs(hrv_drop):.1f}% above baseline{stale_label} — player fresh",
                    "direction": "down",
                    "live": True,
                }

        hr_delta = wellness.get("hr_delta")   # positive = HR above baseline (bad)
        if hr_delta is not None:
            if hr_delta >= 10:
                factors["live_hr_elevated"] = {
                    "delta": round(0.08 * staleness, 3),
                    "label": f"Resting HR +{hr_delta:.0f} bpm above baseline{stale_label}",
                    "direction": "up",
                    "live": True,
                }
            elif hr_delta >= 5:
                factors["live_hr_elevated"] = {
                    "delta": round(0.04 * staleness, 3),
                    "label": f"Resting HR +{hr_delta:.0f} bpm above baseline{stale_label}",
                    "direction": "up",
                    "live": True,
                }

        fat_delta = wellness.get("fatigue_delta")   # positive = higher fatigue than baseline
        if fat_delta is not None:
            if fat_delta >= 2:
                factors["live_fatigue"] = {
                    "delta": round(0.08 * staleness, 3),
                    "label": f"Fatigue {fat_delta:+.1f} above baseline{stale_label}",
                    "direction": "up",
                    "live": True,
                }
            elif fat_delta >= 1:
                factors["live_fatigue"] = {
                    "delta": round(0.04 * staleness, 3),
                    "label": f"Fatigue {fat_delta:+.1f} above baseline{stale_label}",
                    "direction": "up",
                    "live": True,
                }

        sor_delta = wellness.get("soreness_delta")
        if sor_delta is not None:
            if sor_delta >= 2:
                factors["live_soreness"] = {
                    "delta": round(0.06 * staleness, 3),
                    "label": f"Soreness {sor_delta:+.1f} above baseline{stale_label}",
                    "direction": "up",
                    "live": True,
                }
            elif sor_delta >= 1:
                factors["live_soreness"] = {
                    "delta": round(0.03 * staleness, 3),
                    "label": f"Soreness {sor_delta:+.1f} above baseline{stale_label}",
                    "direction": "up",
                    "live": True,
                }

        str_delta = wellness.get("stress_delta")
        if str_delta is not None and str_delta >= 2:
            factors["live_stress"] = {
                "delta": round(0.05 * staleness, 3),
                "label": f"Stress {str_delta:+.1f} above baseline{stale_label}",
                "direction": "up",
                "live": True,
            }

        slq_delta = wellness.get("slq_delta")   # negative = sleep quality worse
        slh_delta = wellness.get("slh_delta")   # negative = fewer hours than baseline
        sleep_penalty = 0.0
        sleep_labels = []
        if slq_delta is not None and slq_delta <= -2:
            sleep_penalty += 0.07 * staleness
            sleep_labels.append(f"quality {slq_delta:.1f}")
        elif slq_delta is not None and slq_delta <= -1:
            sleep_penalty += 0.04 * staleness
            sleep_labels.append(f"quality {slq_delta:.1f}")
        if slh_delta is not None and slh_delta <= -2:
            sleep_penalty += 0.06 * staleness
            sleep_labels.append(f"duration {slh_delta:.1f}h")
        elif slh_delta is not None and slh_delta <= -1:
            sleep_penalty += 0.03 * staleness
            sleep_labels.append(f"duration {slh_delta:.1f}h")
        if sleep_penalty > 0:
            factors["live_poor_sleep"] = {
                "delta": round(sleep_penalty, 3),
                "label": f"Poor sleep ({', '.join(sleep_labels)} vs baseline){stale_label}",
                "direction": "up",
                "live": True,
            }

        acwr = wellness.get("acwr")
        if acwr is not None:
            if acwr >= 1.5:
                factors["live_acwr_spike"] = {
                    "delta": round(0.10 * staleness, 3),
                    "label": f"ACWR {acwr:.2f} — danger zone (>1.5){stale_label}",
                    "direction": "up",
                    "live": True,
                }
            elif acwr >= 1.3:
                factors["live_acwr_spike"] = {
                    "delta": round(0.06 * staleness, 3),
                    "label": f"ACWR {acwr:.2f} — elevated workload spike{stale_label}",
                    "direction": "up",
                    "live": True,
                }
            elif acwr < 0.8:
                factors["live_acwr_low"] = {
                    "delta": round(0.03 * staleness, 3),
                    "label": f"ACWR {acwr:.2f} — under-loaded, possible deconditioning{stale_label}",
                    "direction": "up",
                    "live": True,
                }

    total = round(sum(f["delta"] for f in factors.values()), 4)
    return {
        "total_adjustment": total,
        "factors": factors,
    }


def is_currently_injured(player_id: str) -> tuple[bool, str]:
    """Return (True, reason) if TM data shows an active injury.

    Active = until_date in the future, OR until_date is null and from_date
    is within the last 90 days (injury with unknown recovery end).
    """
    summary = _injury_summaries.get(player_id)
    if not summary:
        return False, ""
    mr = summary.get("most_recent")
    if not mr:
        return False, ""
    label = mr.get("injury") or "Injury"
    until_raw = mr.get("until_date")
    try:
        if until_raw:
            until = date.fromisoformat(until_raw)
            if (date.today() - until).days < 0:
                return True, f"{label} (until {until.strftime('%b %d')})"
        else:
            # null until_date: active if from_date is within last 90 days
            from_raw = mr.get("from_date")
            if from_raw:
                from_dt = date.fromisoformat(from_raw)
                if (date.today() - from_dt).days <= 90:
                    return True, f"{label} (ongoing)"
    except (ValueError, TypeError):
        pass
    return False, ""
