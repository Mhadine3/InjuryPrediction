import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, computed_field
from datetime import date as date_type


class BaselineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    hrv_baseline_ms: Decimal | None
    resting_hr_bpm_baseline: int | None
    vo2max_ml_kg_min: Decimal | None
    typical_weekly_load_au: int | None
    chronic_load_baseline_au: int | None
    measured_at: date


class PlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    first_name: str
    last_name: str
    jersey_number: int | None
    position: str
    date_of_birth: date
    caps: int
    is_active: bool

    @computed_field
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @computed_field
    @property
    def age(self) -> int:
        today = date_type.today()
        dob = self.date_of_birth
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


class PlayerDetail(PlayerOut):
    height_cm: int | None
    weight_kg: Decimal | None
    dominant_foot: str | None
    baseline: BaselineOut | None
