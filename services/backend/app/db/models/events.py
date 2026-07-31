from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, Integer, Float, DateTime, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class WorkerEvent(Base):
    """Time-series of worker state transitions. Partition by `ts` in TimescaleDB."""
    __tablename__ = "worker_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    workstation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, default=None)
    worker_track_id: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(32), default="UNKNOWN")  # WORKING|IDLE|AWAY|WAITING_FOR_INPUT|BREAK
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    bbox: Mapped[list | None] = mapped_column(JSONB, default=None)
    extra: Mapped[dict | None] = mapped_column(JSONB, default=None)

    __table_args__ = (
        Index("ix_worker_events_camera_ts", "camera_id", "ts"),
        Index("ix_worker_events_workstation_ts", "workstation_id", "ts"),
    )


class ProductionEvent(Base):
    """Piece-level production events: piece_started, piece_completed, etc."""
    __tablename__ = "production_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    workstation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    factory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # piece_started|piece_completed|cycle_started|cycle_ended
    cycle_time_s: Mapped[float | None] = mapped_column(Float, default=None)
    payload: Mapped[dict | None] = mapped_column(JSONB, default=None)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)
    factory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info|warning|critical
    kind: Mapped[str] = mapped_column(String(64), default="generic")
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str | None] = mapped_column(String(2000), default=None)
    acknowledged: Mapped[bool] = mapped_column(default=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    payload: Mapped[dict | None] = mapped_column(JSONB, default=None)
