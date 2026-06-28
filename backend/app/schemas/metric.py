import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SessionType = Literal[
    "recovery", "technical", "tactical", "physical",
    "match_prep", "match_simulation", "rest"
]

RiskCategory = Literal["low", "moderate", "high", "very_high", "insufficient_data"]


class DailyMetricCreate(BaseModel):
    player_id: uuid.UUID
    metric_date: date
    session_type: SessionType | None = None

    # Hooper wellness (1-7)
    sleep_quality: int | None = Field(None, ge=1, le=7)
    fatigue: int | None = Field(None, ge=1, le=7)
    soreness: int | None = Field(None, ge=1, le=7)
    stress: int | None = Field(None, ge=1, le=7)
    sleep_duration_h: Decimal | None = Field(None, ge=0, le=24)

    # Foster sRPE
    rpe: int | None = Field(None, ge=0, le=10)
    session_duration_min: int | None = Field(None, ge=0, le=300)

    # GPS
    session_distance_km: Decimal | None = Field(None, ge=0, le=20)
    high_intensity_distance_m: int | None = Field(None, ge=0)
    sprints_count: int | None = Field(None, ge=0)
    accel_decel_count: int | None = Field(None, ge=0)

    # Recovery
    hrv_ms: Decimal | None = Field(None, ge=10, le=200)
    resting_hr_bpm: int | None = Field(None, ge=30, le=100)
    hydration_usg: Decimal | None = Field(None, ge=1.0, le=1.04)

    srpe: int | None = None
    data_source: str = "manual"

    @model_validator(mode="after")
    def compute_srpe(self) -> "DailyMetricCreate":
        if self.srpe is None and self.rpe is not None and self.session_duration_min is not None:
            self.srpe = self.rpe * self.session_duration_min
        return self


class DailyMetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    player_id: uuid.UUID
    metric_date: date
    session_type: str | None

    sleep_quality: int | None
    fatigue: int | None
    soreness: int | None
    stress: int | None
    sleep_duration_h: Decimal | None

    rpe: int | None
    session_duration_min: int | None
    srpe: int | None

    session_distance_km: Decimal | None
    high_intensity_distance_m: int | None
    sprints_count: int | None
    accel_decel_count: int | None

    hrv_ms: Decimal | None
    resting_hr_bpm: int | None
    hydration_usg: Decimal | None

    acute_load_7d: int | None
    chronic_load_28d: int | None
    acwr: Decimal | None

    injury_risk_score: Decimal | None
    risk_category: str | None


class SubmitAndPredictOut(BaseModel):
    metric_id: uuid.UUID
    player_id: uuid.UUID
    metric_date: date
    acwr: float | None
    acute_load_7d: int | None
    chronic_load_28d: int | None
    risk_score: float
    risk_category: str
    confidence: dict[str, float]
    top_features: dict[str, float]
    model_version: str
