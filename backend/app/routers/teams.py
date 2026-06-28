from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Team
from app.schemas import TeamOut

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=list[TeamOut])
async def list_teams(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Team).where(Team.is_active == True).order_by(Team.name))
    return result.scalars().all()


@router.get("/{fifa_code}", response_model=TeamOut)
async def get_team(fifa_code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Team).where(Team.fifa_code == fifa_code.upper())
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{fifa_code}' not found")
    return team
