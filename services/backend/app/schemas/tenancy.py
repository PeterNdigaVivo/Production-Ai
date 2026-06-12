from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class FactoryCreate(BaseModel):
    tenant_id: uuid.UUID
    name: str
    location: str | None = None
    timezone: str = "UTC"


class FactoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    location: str | None
    timezone: str
    created_at: datetime


class ProductionLineCreate(BaseModel):
    factory_id: uuid.UUID
    name: str
    target_pieces_per_hour: int = 0


class ProductionLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    factory_id: uuid.UUID
    name: str
    target_pieces_per_hour: int


class CameraCreate(BaseModel):
    line_id: uuid.UUID
    name: str
    rtsp_url: str
    fps_target: int = 8
    resolution: str | None = None


class CameraRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    line_id: uuid.UUID
    name: str
    rtsp_url: str
    is_active: bool
    fps_target: int
    resolution: str | None
    last_seen_at: datetime | None


class WorkstationCreate(BaseModel):
    line_id: uuid.UUID
    camera_id: uuid.UUID
    code: str
    name: str


class WorkstationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    line_id: uuid.UUID
    camera_id: uuid.UUID
    code: str
    name: str
    layout_version: int


class ZoneCreate(BaseModel):
    workstation_id: uuid.UUID
    kind: str = Field(..., pattern="^(machine|input_tray|output_tray|seat)$")
    polygon: list[list[float]] = Field(..., min_length=3)


class ZoneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workstation_id: uuid.UUID
    kind: str
    polygon: list
    layout_version: int
