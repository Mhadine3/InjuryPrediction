import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    fifa_code: str
    group_code: str | None
    confederation: str | None
    is_active: bool
