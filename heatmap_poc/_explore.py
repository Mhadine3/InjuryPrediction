"""
Step 0 exploration — run once, read the output, then delete.
Prints competitions, picks a World Cup season, lists matches,
picks one match, and prints columns / event types / players.
"""
from mplsoccer import Sbopen

parser = Sbopen()

# ── 1. All competitions ───────────────────────────────────────────────────────
comps = parser.competition()
print("=" * 60)
print("ALL COMPETITIONS (competition_name | season_name | comp_id | season_id)")
print("=" * 60)
for _, row in comps.sort_values(["competition_name", "season_name"]).iterrows():
    print(f"  {row.competition_name:<35} | {row.season_name:<20} | "
          f"comp={row.competition_id}  season={row.season_id}")

# ── 2. Pick FIFA World Cup 2022 ────────────────────────────────────────────────
TARGET_COMP   = "FIFA World Cup"
TARGET_SEASON = "2022"

wc = comps[
    (comps.competition_name == TARGET_COMP) &
    (comps.season_name == TARGET_SEASON)
]
if wc.empty:
    # fall back to any World Cup available
    wc = comps[comps.competition_name == TARGET_COMP]
    print(f"\nNo {TARGET_SEASON} World Cup found — using: {wc.season_name.values}")

row = wc.iloc[0]
COMP_ID   = int(row.competition_id)
SEASON_ID = int(row.season_id)

print(f"\n{'=' * 60}")
print(f"CHOSEN: {row.competition_name} — {row.season_name}")
print(f"  competition_id={COMP_ID}  season_id={SEASON_ID}")

# ── 3. Matches for that competition/season ────────────────────────────────────
matches = parser.match(competition_id=COMP_ID, season_id=SEASON_ID)
print(f"\n{'=' * 60}")
print(f"MATCHES ({len(matches)} total) — match_id | home vs away | score")
print("=" * 60)
for _, m in matches.sort_values("match_date").iterrows():
    print(f"  {m.match_id}  |  {m.home_team_name:<25} {m.home_score}-{m.away_score}  {m.away_team_name}")

# ── 4. Pick the Argentina vs France final ─────────────────────────────────────
final = matches[
    (matches.home_team_name.str.contains("Argentina") & matches.away_team_name.str.contains("France")) |
    (matches.home_team_name.str.contains("France") & matches.away_team_name.str.contains("Argentina"))
]
if final.empty:
    # Fall back to the last match chronologically
    final = matches.sort_values("match_date").iloc[[-1]]

pick = final.iloc[0]
MATCH_ID = int(pick.match_id)
print(f"\n{'=' * 60}")
print(f"CHOSEN MATCH: {pick.home_team_name} vs {pick.away_team_name}")
print(f"  Score : {pick.home_score}-{pick.away_score}")
print(f"  Date  : {pick.match_date}")
print(f"  match_id = {MATCH_ID}")

# ── 5. Load events, print columns + types + players ──────────────────────────
events, related, freeze, tactics = parser.event(MATCH_ID)

print(f"\n{'=' * 60}")
print("EVENT DATAFRAME COLUMNS:")
print("=" * 60)
for col in events.columns:
    print(f"  {col}")

print(f"\n{'=' * 60}")
print("DISTINCT EVENT TYPES:")
print("=" * 60)
for t in sorted(events.type_name.dropna().unique()):
    n = (events.type_name == t).sum()
    print(f"  {t:<35}  n={n}")

print(f"\n{'=' * 60}")
print("PLAYERS IN THIS MATCH:")
print("=" * 60)
players = (
    events[["player_name", "team_name"]]
    .dropna(subset=["player_name"])
    .drop_duplicates()
    .sort_values(["team_name", "player_name"])
)
for _, p in players.iterrows():
    print(f"  {p.team_name:<25}  {p.player_name}")

print(f"\nDone. Use COMP_ID={COMP_ID}, SEASON_ID={SEASON_ID}, MATCH_ID={MATCH_ID}")
