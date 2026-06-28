import uuid
from datetime import date
from pydantic import BaseModel


class PlayerReadiness(BaseModel):
    player_id:            uuid.UUID
    full_name:            str
    position:             str
    caps:                 int
    readiness_score:      float
    readiness_category:   str
    acwr_score:           float
    hrv_score:            float
    wellness_score:       float
    fitness_trend_score:  float
    recommended_session:  str
    recommended_load_min: int
    recommended_load_max: int
    days_to_next_match:   int | None
    acwr:                 float | None
    flags:                list[str]


class TeamReadinessSummary(BaseModel):
    team_fifa_code:    str
    team_name:         str
    as_of_date:        date
    day_number:        int
    overall_readiness: float
    peak_count:        int
    ready_count:       int
    moderate_count:    int
    low_count:         int
    rest_count:        int
    players:           list[PlayerReadiness]
