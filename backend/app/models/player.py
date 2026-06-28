import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    jersey_number: Mapped[int | None] = mapped_column(SmallInteger)
    position: Mapped[str] = mapped_column(String(30), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    height_cm: Mapped[int | None] = mapped_column(SmallInteger)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    dominant_foot: Mapped[str | None] = mapped_column(String(5))
    caps: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    team: Mapped["Team"] = relationship("Team", back_populates="players", lazy="noload")
    baseline: Mapped["PlayerBaselineProfile | None"] = relationship("PlayerBaselineProfile", back_populates="player", uselist=False, lazy="noload")
    daily_metrics: Mapped[list["DailyMetric"]] = relationship("DailyMetric", back_populates="player", lazy="noload")


class PlayerBaselineProfile(Base):
    __tablename__ = "player_baseline_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("players.id"), unique=True, nullable=False)
    max_hr_bpm: Mapped[int | None] = mapped_column(SmallInteger)
    resting_hr_bpm_baseline: Mapped[int | None] = mapped_column(SmallInteger)
    hrv_baseline_ms: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    vo2max_ml_kg_min: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    lactate_threshold_kmh: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    typical_weekly_load_au: Mapped[int | None] = mapped_column(Integer)
    chronic_load_baseline_au: Mapped[int | None] = mapped_column(Integer)
    measured_at: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped["Player"] = relationship("Player", back_populates="baseline", lazy="noload")
