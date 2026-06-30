from pathlib import Path
from typing import Annotated
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/injury_prediction"
    ML_MODELS_DIR: Path = Path(__file__).resolve().parents[2] / "ml" / "models"
    ENVIRONMENT: str = "development"
    # NoDecode: stop pydantic-settings from JSON-decoding the env value so a plain
    # comma-separated string (e.g. CORS_ORIGINS=https://a.com,https://b.com) works
    # and is handled by _split_cors below instead of raising a SettingsError.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://localhost:8081"]

    # ACWR thresholds — Gabbett 2016
    ACWR_MODERATE_MIN: float = 1.30
    ACWR_HIGH_MIN: float = 1.50
    ACWR_VERY_HIGH_MIN: float = 2.00
    ACWR_UNDER_MIN: float = 0.80        # below = under-training risk
    ACWR_DETRAINING_HIGH: float = 0.65  # below = significant detraining risk (U-shape)

    # BSD Bzzoiro Sports Data
    BSD_TOKEN: str = ""
    BSD_BASE_URL: str = "https://sports.bzzoiro.com/api/v2"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors(cls, v):
        # Accept a comma-separated string from the env file, e.g.
        # CORS_ORIGINS=https://a.com,https://b.com
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


settings = Settings()
