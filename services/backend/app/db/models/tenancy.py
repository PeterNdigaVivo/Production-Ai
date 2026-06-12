from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, JSON, Integer, DateTime, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)

    factories: Mapped[list[Factory]] = relationship(back_populates="tenant", default_factory=list, init=False)


class Factory(Base):
    __tablename__ = "factories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255), default=None)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)

    tenant: Mapped[Tenant] = relationship(back_populates="factories", init=False)
    lines: Mapped[list[ProductionLine]] = relationship(back_populates="factory", default_factory=list, init=False)


class ProductionLine(Base):
    __tablename__ = "production_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    factory_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("factories.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    target_pieces_per_hour: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)

    factory: Mapped[Factory] = relationship(back_populates="lines", init=False)
    cameras: Mapped[list[Camera]] = relationship(back_populates="line", default_factory=list, init=False)
    workstations: Mapped[list[Workstation]] = relationship(back_populates="line", default_factory=list, init=False)


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_lines.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    rtsp_url: Mapped[str] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    resolution: Mapped[str | None] = mapped_column(String(32), default=None)
    fps_target: Mapped[int] = mapped_column(Integer, default=8)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)

    line: Mapped[ProductionLine] = relationship(back_populates="cameras", init=False)
    workstations: Mapped[list[Workstation]] = relationship(back_populates="camera", default_factory=list, init=False)


class Workstation(Base):
    __tablename__ = "workstations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_lines.id", ondelete="CASCADE"))
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    layout_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)

    line: Mapped[ProductionLine] = relationship(back_populates="workstations", init=False)
    camera: Mapped[Camera] = relationship(back_populates="workstations", init=False)
    zones: Mapped[list[Zone]] = relationship(back_populates="workstation", default_factory=list, init=False)


class Zone(Base):
    """Polygon zones associated with a workstation.

    `kind` is one of: machine | input_tray | output_tray | seat.
    `polygon` is a list of [x, y] points in pixel coordinates of the camera image.
    """
    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid, init=False)
    workstation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workstations.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    polygon: Mapped[list] = mapped_column(JSONB, default=list)
    layout_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), init=False)

    workstation: Mapped[Workstation] = relationship(back_populates="zones", init=False)
