#!/usr/bin/env python3
"""
touch_heatmap.py
================
Player on-ball touch heatmap from StatsBomb Open Data.

Data source : StatsBomb Open Data (https://github.com/statsbomb/open-data)
Library     : mplsoccer — StatsBomb JSON parsing + pitch drawing
Pitch       : 120 × 80 yards (StatsBomb standard)
Output      : output/{player}_{match_id}.png

Coordinate system note
----------------------
StatsBomb uses a *fixed* origin: (0, 0) = bottom-left corner when the home
team attacks left→right in period 1. This flips in period 2 (both teams swap
ends). This script normalises every event so the chosen player always attacks
left→right, giving a single coherent zone of activity.

Usage
-----
  python touch_heatmap.py                            # built-in CONFIG defaults
  python touch_heatmap.py --player "Lionel Andrés Messi Cuccittini"
  python touch_heatmap.py --match 3869152 --player "Kylian Mbappé Lottin"
"""

import argparse
import io
import sys
from pathlib import Path

# Force UTF-8 on Windows consoles that default to CP1252
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from mplsoccer import Pitch, Sbopen

# ─── CONFIG  (defaults; all overrideable via CLI) ──────────────────────────────
CONFIG = {
    "competition_id": 43,               # FIFA World Cup
    "season_id":      106,              # 2022
    "match_id":       3869685,          # Argentina 3-3 France — World Cup Final
    "player_name":    "Kylian Mbappé Lottin",
}

# Event types that represent a player having the ball at their feet.
# Excludes purely off-ball events (Pressure, Duel, Dribbled Past).
ON_BALL_TYPES = {
    "Pass",
    "Ball Receipt",
    "Carry",
    "Shot",
    "Dribble",
    "Ball Recovery",
    "Clearance",
    "Interception",
    "Miscontrol",
    "Dispossessed",
    "Block",
}

# StatsBomb pitch dimensions (yards)
PITCH_LENGTH = 120.0
PITCH_WIDTH  = 80.0

OUTPUT_DIR = Path(__file__).parent / "output"


# ─── Data loading ─────────────────────────────────────────────────────────────

def load_events(match_id: int):
    """Fetch event DataFrame for one StatsBomb match via mplsoccer's Sbopen."""
    parser = Sbopen()
    # Sbopen.event() returns (events, related, freeze, tactics)
    events, _, _, _ = parser.event(match_id)
    return events


def load_match_meta(competition_id: int, season_id: int, match_id: int) -> dict:
    """Return a dict with home_team, away_team, score, date for one match."""
    parser = Sbopen()
    matches = parser.match(competition_id=competition_id, season_id=season_id)
    row = matches[matches["match_id"] == match_id]
    if row.empty:
        return {}
    r = row.iloc[0]
    return {
        "home_team": r.home_team_name,
        "away_team": r.away_team_name,
        "home_score": int(r.home_score),
        "away_score": int(r.away_score),
        "date": str(r.match_date)[:10],
    }


# ─── Player filtering ─────────────────────────────────────────────────────────

def get_player_touches(events, player_name: str):
    """
    Filter events to one player's on-ball actions that have a valid location.

    Returns:
        touches  : filtered DataFrame (may be empty if player not found)
        available: sorted list of all player names in this match
    """
    available = sorted(events["player_name"].dropna().unique().tolist())

    player_rows = events[events["player_name"] == player_name]
    if player_rows.empty:
        return player_rows, available

    touches = player_rows[
        player_rows["type_name"].isin(ON_BALL_TYPES)
        & player_rows["x"].notna()
        & player_rows["y"].notna()
    ].copy()

    return touches, available


# ─── Direction normalisation ──────────────────────────────────────────────────

def normalise_attack_direction(touches, player_team: str, home_team: str):
    """
    Flip x/y coordinates in halves where the player's team attacks toward x=0
    so that every touch ends up in an "attacks left→right" frame of reference.

    Rules (StatsBomb fixed origin):
      - Periods 1, 3 (first halves): home team attacks left→right (x increasing)
      - Periods 2, 4 (second halves): home team attacks right→left (x decreasing)

    A flip means: x = PITCH_LENGTH - x,  y = PITCH_WIDTH - y
    """
    is_home = (player_team == home_team)

    # Periods where this player's team attacks right→left (needs flipping)
    flip_periods = {2, 4} if is_home else {1, 3, 5}

    mask = touches["period"].isin(flip_periods)
    touches.loc[mask, "x"] = PITCH_LENGTH - touches.loc[mask, "x"]
    touches.loc[mask, "y"] = PITCH_WIDTH  - touches.loc[mask, "y"]

    return touches


# ─── Plotting ─────────────────────────────────────────────────────────────────

def build_title(player_name: str, team_name: str, match_label: str,
                n_events: int, event_counts: dict) -> str:
    """Compose a three-line figure title."""
    top = ", ".join(f"{k} ({v})" for k, v in list(event_counts.items())[:4])
    return (
        f"{player_name}  ·  {team_name}\n"
        f"{match_label}  ·  {n_events} on-ball actions\n"
        f"{top}"
    )


def plot_heatmap(touches, player_name: str, team_name: str,
                 match_label: str, output_path: Path) -> None:
    """
    Draw KDE touch density on a dark-themed horizontal pitch and save to PNG.

    Pitch orientation: attacking left → right.
    """
    # ── event summary ─────────────────────────────────────────────────────────
    event_counts = (
        touches.groupby("type_name")
        .size()
        .sort_values(ascending=False)
        .to_dict()
    )
    n = len(touches)

    # ── figure ────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 9.5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    # ── pitch (mplsoccer) ─────────────────────────────────────────────────────
    pitch = Pitch(
        pitch_type="statsbomb",
        pitch_color="#1a1f2e",
        line_color="#3d4a6b",
        linewidth=1.5,
        goal_type="box",
        corner_arcs=True,
    )
    pitch.draw(ax=ax)

    # ── KDE heatmap ───────────────────────────────────────────────────────────
    # pitch.kdeplot wraps seaborn kdeplot; thresh masks near-zero regions
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

    # ── individual touch dots (subtle, for granularity) ───────────────────────
    pitch.scatter(
        touches["x"], touches["y"],
        ax=ax,
        s=16,
        color="white",
        alpha=0.22,
        zorder=3,
        edgecolors="none",
    )

    # ── attacking-direction label ─────────────────────────────────────────────
    ax.annotate(
        "◀  defending                                      attacking  ▶",
        xy=(0.5, 0.025), xycoords="axes fraction",
        ha="center", va="bottom",
        fontsize=8.5, color="#556688",
        fontfamily="monospace",
    )

    # ── figure title (three lines) ────────────────────────────────────────────
    title = build_title(player_name, team_name, match_label, n, event_counts)
    fig.text(
        0.5, 0.975, title,
        ha="center", va="top",
        fontsize=12.5, color="white",
        fontweight="bold",
        linespacing=1.65,
        path_effects=[pe.withStroke(linewidth=3, foreground="#0d1117")],
    )

    # ── colour bar ────────────────────────────────────────────────────────────
    sm = plt.cm.ScalarMappable(cmap="YlOrRd")
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, orientation="vertical",
                        fraction=0.018, pad=0.02, shrink=0.7)
    cbar.ax.yaxis.set_tick_params(color="#8899bb", labelcolor="#8899bb",
                                   labelsize=8)
    cbar.set_label("Touch density", color="#8899bb", fontsize=9)
    cbar.outline.set_edgecolor("#3d4a6b")

    # ── watermark ─────────────────────────────────────────────────────────────
    fig.text(
        0.01, 0.008, "Data: StatsBomb Open Data · github.com/statsbomb/open-data",
        ha="left", va="bottom", fontsize=7, color="#334466",
    )

    # ── save ──────────────────────────────────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Saved → {output_path}")


# ─── CLI + orchestration ──────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="StatsBomb player on-ball touch heatmap"
    )
    p.add_argument("--competition", type=int, default=CONFIG["competition_id"],
                   help="StatsBomb competition_id")
    p.add_argument("--season",      type=int, default=CONFIG["season_id"],
                   help="StatsBomb season_id")
    p.add_argument("--match",       type=int, default=CONFIG["match_id"],
                   help="StatsBomb match_id")
    p.add_argument("--player",      type=str, default=CONFIG["player_name"],
                   help="Exact player name as it appears in StatsBomb data")
    return p.parse_args()


def main():
    args = parse_args()

    # ── Step 1: load match metadata (for direction normalisation + title) ──────
    print(f"\nFetching match metadata  (comp={args.competition} season={args.season} match={args.match}) ...")
    meta = load_match_meta(args.competition, args.season, args.match)
    if meta:
        match_label = (
            f"{meta['home_team']} {meta['home_score']}–{meta['away_score']} "
            f"{meta['away_team']}  ·  {meta['date']}"
        )
        home_team = meta["home_team"]
        print(f"  {match_label}")
    else:
        match_label = f"match {args.match}"
        home_team = ""
        print("  (match metadata not found — direction normalisation disabled)")

    # ── Step 2: load events ───────────────────────────────────────────────────
    print(f"\nLoading events ...")
    events = load_events(args.match)
    print(f"  {len(events)} total events")

    # ── Step 3: filter to player on-ball touches ──────────────────────────────
    touches, available = get_player_touches(events, args.player)

    if touches.empty:
        print(f"\nERROR: player '{args.player}' not found in this match.")
        print("\nAvailable players:")
        for name in available:
            print(f"  {name}")
        sys.exit(1)

    team_name = touches["team_name"].mode()[0]

    # ── Step 4: normalise attack direction ────────────────────────────────────
    if home_team:
        touches = normalise_attack_direction(touches, team_name, home_team)

    # ── Step 5: print summary ─────────────────────────────────────────────────
    event_counts = (
        touches.groupby("type_name")
        .size()
        .sort_values(ascending=False)
        .to_dict()
    )
    print(f"\nPlayer  : {args.player}")
    print(f"Team    : {team_name}  ({'home' if team_name == home_team else 'away'})")
    print(f"Touches : {len(touches)}")
    print("Breakdown:")
    for evt_type, cnt in event_counts.items():
        bar = "#" * min(cnt // 2, 30)
        print(f"  {evt_type:<22} {cnt:>4}  {bar}")

    # ── Step 6: build output path (safe filename) ─────────────────────────────
    safe_name = args.player.replace(" ", "_")
    safe_name = "".join(c for c in safe_name
                        if c.isalnum() or c in "_-")
    output_path = OUTPUT_DIR / f"{safe_name}_{args.match}.png"

    # ── Step 7: plot ──────────────────────────────────────────────────────────
    print(f"\nPlotting heatmap ...")
    plot_heatmap(touches, args.player, team_name, match_label, output_path)

    print(f"\nDone. {len(touches)} on-ball events plotted.")


if __name__ == "__main__":
    main()
