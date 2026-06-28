from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import settings
from app.routers import teams, players, metrics, predictions, readiness
from app.routers import live_predictions
from app.routers import heatmap
from app.routers import player_profile
from app.services.ml_inference import InjuryModel

_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    InjuryModel.get()
    yield


app = FastAPI(
    title="Injury Prediction API",
    description="2026 FIFA World Cup Group C — Player Injury Risk Platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(teams.router,       prefix=API_PREFIX)
app.include_router(players.router,     prefix=API_PREFIX)
app.include_router(metrics.router,     prefix=API_PREFIX)
app.include_router(predictions.router, prefix=API_PREFIX)
app.include_router(readiness.router,       prefix=API_PREFIX)
app.include_router(live_predictions.router, prefix=API_PREFIX)
app.include_router(heatmap.router,          prefix=API_PREFIX)
app.include_router(player_profile.router,  prefix=API_PREFIX)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    return HTMLResponse((_WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.ENVIRONMENT}
