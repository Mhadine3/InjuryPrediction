import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InjuryPrediction(Base):
    __tablename__ = "injury_predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    risk_category: Mapped[str] = mapped_column(String(20), nullable=False)
    acwr_at_prediction: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    top_features_json: Mapped[str | None] = mapped_column(Text)  # JSON blob of top contributing features
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    player: Mapped["Player"] = relationship("Player", lazy="noload")
