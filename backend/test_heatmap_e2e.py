"""
End-to-end integration test for the WC 2026 heatmap service.

Run from the backend/ directory:
    python test_heatmap_e2e.py

This test proves the full flow:
  1. get_team_matches("BRA") -> finds finished matches
  2. get_match_players(event_id) -> returns players from lineups
  3. generate_heatmap_bytes(player_name, player_id, event_id) -> returns PNG bytes

Requires: Playwright Chromium installed (playwright install chromium)
Runtime: ~30-60 s on first run (Chromium launch + 3 page navigations)
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


async def main():
    from app.services.heatmap_sofascore import (
        get_team_matches,
        get_match_players,
        generate_heatmap_bytes,
    )

    print("=" * 60)
    print("WC 2026 Heatmap E2E Test")
    print("=" * 60)

    # ── Step 1: matches ──────────────────────────────────────────
    print("\n[1] get_team_matches('BRA') ...")
    matches = await get_team_matches("BRA")
    if not matches:
        print("  FAIL: no matches returned for BRA")
        return
    print(f"  OK: {len(matches)} match(es)")
    for m in matches:
        print(f"    event_id={m['event_id']}  {m['date']}  "
              f"{m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}")

    first_match = matches[0]
    event_id    = first_match["event_id"]

    # ── Step 2: players ──────────────────────────────────────────
    print(f"\n[2] get_match_players({event_id}) ...")
    players = await get_match_players(event_id)
    if not players:
        print("  FAIL: no players returned")
        return
    print(f"  OK: {len(players)} player(s)")
    for p in players[:5]:
        print(f"    [{p['team_name']}] {p['player_name']} (id={p['player_id']})")

    # ── Step 3: heatmap ──────────────────────────────────────────
    first_player = players[0]
    p_name = first_player["player_name"]
    p_id   = first_player["player_id"]
    print(f"\n[3] generate_heatmap_bytes('{p_name}', {p_id}, {event_id}) ...")
    png = await generate_heatmap_bytes(p_name, p_id, event_id)
    if not png or not png.startswith(b"\x89PNG"):
        print("  FAIL: PNG bytes invalid or empty")
        return

    out_path = f"heatmap_e2e_{p_id}_{event_id}.png"
    with open(out_path, "wb") as f:
        f.write(png)
    print(f"  OK: {len(png):,} bytes written to {out_path}")

    print("\n=== ALL STEPS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
