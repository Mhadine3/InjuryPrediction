from functools import partial
import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.services.heatmap_service import (
    get_team_matches       as sb_team_matches,
    get_match_players      as sb_match_players,
    generate_heatmap_bytes as sb_heatmap_bytes,
)
from app.services.heatmap_sofascore import (
    get_team_matches                  as ss_team_matches,
    get_match_players                 as ss_match_players,
    get_player_matches                as ss_player_matches,
    generate_heatmap_bytes            as ss_heatmap_bytes,
    get_danger_zones_png              as ss_danger_zones,
    get_player_danger_zones_png       as ss_player_danger_zones,
    get_team_recent_matches           as ss_team_recent_matches,
    get_team_aggregate_danger_zones_png as ss_team_agg_danger_zones,
)

router = APIRouter(prefix="/heatmap", tags=["heatmap"])


# ── WC 2022  (StatsBomb Open Data, synchronous) ───────────────────────────────

@router.get("/matches/{tla}")
def team_heatmap_matches(tla: str):
    matches = sb_team_matches(tla.upper())
    return {"tla": tla.upper(), "matches": matches, "has_data": len(matches) > 0}


@router.get("/players/{match_id}")
def match_players(match_id: int):
    return {"match_id": match_id, "players": sb_match_players(match_id)}


@router.get("/image")
async def heatmap_image(
    player: str = Query(...),
    match_id: int = Query(...),
):
    loop = asyncio.get_event_loop()
    try:
        png_bytes = await loop.run_in_executor(
            None, partial(sb_heatmap_bytes, player, match_id)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ── WC 2026  (SofaScore live, fully async) ────────────────────────────────────

@router.get("/2026/matches/{tla}")
async def team_heatmap_matches_2026(tla: str):
    try:
        matches = await ss_team_matches(tla.upper())
        has_finished = any(m["status"] == "finished" for m in matches)
        return {
            "tla":          tla.upper(),
            "matches":      matches,
            "has_data":     len(matches) > 0,
            "has_finished": has_finished,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/2026/players/{event_id}")
async def match_players_2026(event_id: int):
    try:
        players = await ss_match_players(event_id)
        return {"event_id": event_id, "players": players}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/player-matches/{player_id}")
async def player_match_history(player_id: int):
    try:
        matches = await ss_player_matches(player_id)
        return {"player_id": player_id, "matches": matches}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/2026/danger-zones")
async def danger_zones_2026(
    event_id: int = Query(...),
    tla:      str = Query(...),
):
    try:
        png_bytes = await ss_danger_zones(event_id, tla.upper())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/2026/image")
async def heatmap_image_2026(
    player_name: str = Query(...),
    player_id:   int  = Query(...),
    event_id:    int  = Query(...),
):
    try:
        png_bytes = await ss_heatmap_bytes(player_name, player_id, event_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/2026/player-danger-zones")
async def player_danger_zones_2026(
    player_id:   int = Query(...),
    player_name: str = Query(...),
):
    try:
        png_bytes = await ss_player_danger_zones(player_id, player_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/2026/team-recent-matches/{tla}")
async def team_recent_matches_2026(tla: str):
    try:
        matches = await ss_team_recent_matches(tla.upper())
        return {"tla": tla.upper(), "matches": matches}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/2026/team-aggregate-danger-zones/{tla}")
async def team_aggregate_danger_zones_2026(tla: str):
    try:
        png_bytes = await ss_team_agg_danger_zones(tla.upper())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
