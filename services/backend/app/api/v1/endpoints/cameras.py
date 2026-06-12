from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.session import get_db
from app.db.models import Camera
from app.schemas.tenancy import CameraCreate, CameraRead
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("", response_model=list[CameraRead])
async def list_cameras(line_id: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Camera)
    if line_id:
        stmt = stmt.where(Camera.line_id == line_id)
    res = await db.execute(stmt)
    return list(res.scalars())


@router.post("", response_model=CameraRead, status_code=201)
async def create_camera(body: CameraCreate, db: AsyncSession = Depends(get_db)):
    cam = Camera(
        line_id=body.line_id, name=body.name, rtsp_url=body.rtsp_url,
        fps_target=body.fps_target, resolution=body.resolution,
    )
    db.add(cam)
    await db.commit()
    await db.refresh(cam)
    return cam


@router.post("/{camera_id}/heartbeat")
async def heartbeat(camera_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Called by the ingestion service to mark the camera as alive."""
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(404, "not found")
    cam.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}
