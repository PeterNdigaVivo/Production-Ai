from __future__ import annotations
import uuid
from datetime import datetime, time
from sqlalchemy import String, ForeignKey, Integer, Float, DateTime, Time, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Worker(Base):
    """Optional persistent identity. Phase 1 uses track_ids only; this table
    holds long-lived worker records once Re-ID is enabled."""
    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    factory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("factories.id", ondelete="CASCADE"))
    employee_code: Mapped[str | None] = mapped_column(String(64), default=None, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)


class ShiftDefinition(Base):
    __tablename__ = "shift_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    factory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("factories.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class ProductionRecord(Base):
    """Roll-up of piece counts per worker/workstation/line over a window."""
    __tablename__ = "production_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    factory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    workstation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, default=None)
    pieces_completed: Mapped[int] = mapped_column(Integer, default=0)
    avg_cycle_time_s: Mapped[float | None] = mapped_column(Float, default=None)
    effective_working_s: Mapped[int] = mapped_column(Integer, default=0)
    idle_s: Mapped[int] = mapped_column(Integer, default=0)
    away_s: Mapped[int] = mapped_column(Integer, default=0)
    waiting_s: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict | None] = mapped_column(JSONB, default=None)
