"""
heatmap_service.py
==================
StatsBomb Open Data touch heatmap generator for the AthleteOS dashboard.

Data  : FIFA World Cup 2022 (competition_id=43, season_id=106)
        Available for our 8 teams: Brazil, Morocco, France, Senegal only
        (Haiti, Scotland, Iraq, Norway did not qualify for WC 2022).

Flow  :
  1. mplsoccer Sbopen fetches JSON from StatsBomb's GitHub (cached locally
     by mplsoccer in ~/.mplsoccer/ after the first download).
  2. Events are filtered to the chosen player's on-ball touches with coords.
  3. Coordinates are normalised so the player always attacks left→right
     (StatsBomb uses a fixed origin; direction flips between halves).
  4. A KDE heatmap is rendered via mplsoccer + matplotlib and returned as
     raw PNG bytes.  Generated images are cached to data/heatmaps/ on disk
     so repeated requests return instantly.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")   # headless — must be set before any pyplot import

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from mplsoccer import Pitch, Sbopen

# ─── Constants ────────────────────────────────────────────────────────────────

WC_COMP_ID   = 43
WC_SEASON_ID = 106

# Only these 4 of our 8 teams have StatsBomb WC 2022 open data
SB_TEAMS: dict[str, str] = {
    "BRA": "Brazil",
    "MAR": "Morocco",
    "FRA": "France",
    "SEN": "Senegal",
}

PITCH_LENGTH = 120.0
PITCH_WIDTH  = 80.0

# Event types that represent the player physically touching the ball
ON_BALL_TYPES = {
    "Pass", "Ball Receipt", "Carry", "Shot", "Dribble",
    "Ball Recovery", "Clearance", "Interception",
    "Miscontrol", "Dispossessed", "Block",
}

ROOT       = Path(__file__).resolve().parents[3]
_CACHE_DIR = ROOT / "data" / "heatmaps"

# ─── In-memory caches (per process lifetime) ──────────────────────────────────
_matches_df  = None                        # loaded once from StatsBomb
_events_cache: dict[int, object] = {}      # match_id → events DataFrame


# ─── Private helpers ──────────────────────────────────────────────────────────

def _get_all_matches():
    """Fetch (or return cached) WC 2022 matches DataFrame."""
    global _matches_df
    if _matches_df is None:
        _matches_df = Sbopen().match(
            competition_id=WC_COMP_ID, season_id=WC_SEASON_ID
        )
    return _matches_df


def _load_events(match_id: int):
    """Fetch (or return cached) events DataFrame for one match."""
    if match_id not in _events_cache:
        events, _, _, _ = Sbopen().event(match_id)
        _events_cache[match_id] = events
    return _events_cache[match_id]


def _normalise_attack_direction(touches, player_team: str, home_team: str):
    """
    Flip x/y in halves where the player's team defends toward x=120.

    StatsBomb uses a fixed origin: home team attacks x=0→120 in period 1.
    Flipping both axes (x=120-x, y=80-y) mirrors the whole pitch perspective
    so the player always appears to attack left→right in the final image.
    """
    is_home    = (player_team == home_team)
    flip_perds = {2, 4} if is_home else {1, 3, 5}
    mask = touches["period"].isin(flip_perds)
    touches.loc[mask, "x"] = PITCH_LENGTH - touches.loc[mask, "x"]
    touches.loc[mask, "y"] = PITCH_WIDTH  - touches.loc[mask, "y"]
    return touches


def _render_png(touches, player_name: str, team_name: str,
                match_label: str) -> bytes:
    """Render the KDE heatmap and return raw PNG bytes (Agg backend)."""
    event_counts = (
        touches.groupby("type_name")
        .size()
        .sort_values(ascending=False)
        .to_dict()
    )
    n    = len(touches)
    top  = ", ".join(f"{k} ({v})" for k, v in list(event_counts.items())[:4])
    title = (
        f"{player_name}  ·  {team_name}\n"
        f"{match_label}  ·  {n} on-ball actions\n"
        f"{top}"
    )

    fig, ax = plt.subplots(figsize=(14, 9.5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    pitch = Pitch(
        pitch_type="statsbomb",
        pitch_color="#1a1f2e",
        line_color="#3d4a6b",
        linewidth=1.5,
        goal_type="box",
        corner_arcs=True,
    )
    pitch.draw(ax=ax)

    pitch.kdeplot(
        touches["x"], touches["y"],
        ax=ax,
        cmap="YlOrRd",
        fill=True,
        levels=100,
        thresh=0.04,
        alpha=0.75,
        zorder=2,
    )
    pitch.scatter(
        touches["x"], touches["y"],
        ax=ax,
        s=16, color="white", alpha=0.22, zorder=3, edgecolors="none",
    )

    ax.annotate(
        "defending  ◄                                      ►  attacking",
        xy=(0.5, 0.025), xycoords="axes fraction",
        ha="center", va="bottom", fontsize=8.5, color="#556688",
        fontfamily="monospace",
    )
    fig.text(
        0.5, 0.975, title,
        ha="center", va="top", fontsize=12, color="white",
        fontweight="bold", linespacing=1.65,
        path_effects=[pe.withStroke(linewidth=3, foreground="#0d1117")],
    )

    sm = plt.cm.ScalarMappable(cmap="YlOrRd")
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, orientation="vertical",
                        fraction=0.018, pad=0.02, shrink=0.7)
    cbar.ax.yaxis.set_tick_params(color="#8899bb", labelcolor="#8899bb",
                                   labelsize=8)
    cbar.set_label("Touch density", color="#8899bb", fontsize=9)
    cbar.outline.set_edgecolor("#3d4a6b")

    fig.text(
        0.01, 0.008,
        "Data: StatsBomb Open Data  ·  FIFA World Cup 2022  ·  scouting reference",
        ha="left", va="bottom", fontsize=7, color="#334466",
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ─── Public API ───────────────────────────────────────────────────────────────

def get_team_matches(tla: str) -> list[dict]:
    """
    Return WC 2022 matches for a team (sorted by date), or [] if not available.
    """
    sb_name = SB_TEAMS.get(tla.upper())
    if not sb_name:
        return []
    df = _get_all_matches()
    team_df = df[
        (df.home_team_name == sb_name) |
        (df.away_team_name == sb_name)
    ].sort_values("match_date")
    return [
        {
            "match_id":   int(r.match_id),
            "home_team":  r.home_team_name,
            "away_team":  r.away_team_name,
            "home_score": int(r.home_score),
            "away_score": int(r.away_score),
            "date":       str(r.match_date)[:10],
        }
        for _, r in team_df.iterrows()
    ]


def get_match_players(match_id: int) -> list[dict]:
    """Return all players who appear in a match, sorted by team then name."""
    events = _load_events(match_id)
    players = (
        events[["player_name", "team_name"]]
        .dropna(subset=["player_name"])
        .drop_duplicates()
        .sort_values(["team_name", "player_name"])
    )
    return [
        {"player_name": r.player_name, "team_name": r.team_name}
        for _, r in players.iterrows()
    ]


def generate_heatmap_bytes(player_name: str, match_id: int) -> bytes:
    """
    Generate a PNG heatmap for the given player in the given match.
    Returns cached PNG bytes if previously generated.
    Raises ValueError if the player is not found or has no location data.
    """
    # Disk cache key (sanitise player name for filesystem)
    safe = "".join(
        c if c.isalnum() else "_"
        for c in player_name.replace(" ", "_")
    )
    cache_path = _CACHE_DIR / f"{match_id}_{safe}.png"

    if cache_path.exists():
        return cache_path.read_bytes()

    # ── Load events ───────────────────────────────────────────────────────────
    events = _load_events(match_id)

    player_rows = events[events["player_name"] == player_name]
    if player_rows.empty:
        available = sorted(events["player_name"].dropna().unique().tolist())
        raise ValueError(
            f"Player '{player_name}' not found. "
            f"Available: {available[:5]} ..."
        )

    touches = player_rows[
        player_rows["type_name"].isin(ON_BALL_TYPES)
        & player_rows["x"].notna()
        & player_rows["y"].notna()
    ].copy()

    if len(touches) < 5:
        raise ValueError(
            f"Too few on-ball touches with coordinates for '{player_name}' "
            f"({len(touches)}). Cannot render a meaningful heatmap."
        )

    # ── Direction normalisation ───────────────────────────────────────────────
    df = _get_all_matches()
    match_row = df[df["match_id"] == match_id]
    home_team = match_row.iloc[0].home_team_name if not match_row.empty else ""
    team_name = str(touches["team_name"].mode()[0])

    if home_team:
        touches = _normalise_attack_direction(touches, team_name, home_team)

    # ── Match label for the title ─────────────────────────────────────────────
    if not match_row.empty:
        r = match_row.iloc[0]
        match_label = (
            f"{r.home_team_name} {r.home_score}–{r.away_score} "
            f"{r.away_team_name}  ·  {str(r.match_date)[:10]}"
        )
    else:
        match_label = f"match {match_id}"

    # ── Render ────────────────────────────────────────────────────────────────
    png_bytes = _render_png(touches, player_name, team_name, match_label)

    # ── Persist to disk cache ─────────────────────────────────────────────────
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes)

    return png_bytes
