import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PredictionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: uuid.UUID
    prediction_date: date
    model_version: str
    risk_score: float
    risk_category: str
    acwr_at_prediction: float | None
    top_features: dict[str, float] | None = None


class PlayerRiskRow(BaseModel):
    player_id: uuid.UUID
    full_name: str
    position: str
    caps: int
    risk_score: float
    risk_category: str
    acwr: float | None
    alert: bool


class TeamRiskSummary(BaseModel):
    team_fifa_code: str
    team_name: str
    as_of_date: date
    total_players: int
    low_count: int
    moderate_count: int
    high_count: int
    very_high_count: int
    mean_risk_score: float
    players: list[PlayerRiskRow]


class HighRiskAlert(BaseModel):
    player_id: uuid.UUID
    full_name: str
    team_fifa_code: str
    position: str
    risk_score: float
    risk_category: str
    acwr: float | None
    top_driver: str | None
    as_of_date: date
