"""Knockout bracket projection endpoints."""
from fastapi import APIRouter

from app.services.bracket_service import build_projection, STAGE_LABEL, _STAGES

router = APIRouter(prefix="/bracket", tags=["bracket"])


@router.get("/projection")
async def projection():
    """Full projected knockout bracket (actual results + model predictions)."""
    proj = build_projection()
    return {
        "generated_at": proj["generated_at"],
        "stage_order": _STAGES,
        "stage_labels": STAGE_LABEL,
        "stages": proj["stages"],
    }
