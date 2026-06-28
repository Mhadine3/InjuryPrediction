import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Player, Team
from app.schemas import PlayerOut, PlayerDetail

router = APIRouter(prefix="/players", tags=["players"])


@router.get("", response_model=list[PlayerOut])
async def list_players(
    team_code: str | None = Query(None, description="Filter by FIFA team code (BRA, MAR, HAI, SCO)"),
    position: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Player).where(Player.is_active == True)
    if team_code:
        q = q.join(Team).where(Team.fifa_code == team_code.upper())
    if position:
        q = q.where(Player.position == position)
    q = q.order_by(Player.last_name)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{player_id}", response_model=PlayerDetail)
async def get_player(player_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Player)
        .options(selectinload(Player.baseline))
        .where(Player.id == player_id)
    )
    player = result.scalar_one_or_none()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player
