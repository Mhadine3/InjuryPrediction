import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DailyMetric(Base):
    __tablename__ = "daily_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    session_type: Mapped[str | None] = mapped_column(String(20))

    # Hooper 1995 wellness (1-7 Likert)
    sleep_quality: Mapped[int | None] = mapped_column(SmallInteger)
    fatigue: Mapped[int | None] = mapped_column(SmallInteger)
    soreness: Mapped[int | None] = mapped_column(SmallInteger)
    stress: Mapped[int | None] = mapped_column(SmallInteger)
    sleep_duration_h: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))

    # Foster 2001 sRPE
    rpe: Mapped[int | None] = mapped_column(SmallInteger)
    session_duration_min: Mapped[int | None] = mapped_column(SmallInteger)
    srpe: Mapped[int | None] = mapped_column(Integer)  # rpe × duration

    # GPS — Bradley 2009 / Dellal 2010
    session_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    high_intensity_distance_m: Mapped[int | None] = mapped_column(Integer)
    sprints_count: Mapped[int | None] = mapped_column(SmallInteger)
    accel_decel_count: Mapped[int | None] = mapped_column(SmallInteger)

    # Buchheit 2014 recovery markers
    hrv_ms: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    resting_hr_bpm: Mapped[int | None] = mapped_column(SmallInteger)

    # Armstrong 1994 hydration
    hydration_usg: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    # Gabbett 2016 ACWR (computed, stored for fast queries)
    acute_load_7d: Mapped[int | None] = mapped_column(Integer)
    chronic_load_28d: Mapped[int | None] = mapped_column(Integer)
    acwr: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))

    # ML output
    injury_risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    risk_category: Mapped[str | None] = mapped_column(String(20))

    data_source: Mapped[str | None] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    player: Mapped["Player"] = relationship("Player", back_populates="daily_metrics", lazy="noload")
