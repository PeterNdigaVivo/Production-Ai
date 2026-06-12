from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class WorkerEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ts: datetime
    camera_id: uuid.UUID
    workstation_id: uuid.UUID | None
    worker_track_id: int
    state: str
    confidence: float


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ts: datetime
    severity: str
    kind: str
    title: str
    description: str | None
    acknowledged: bool
