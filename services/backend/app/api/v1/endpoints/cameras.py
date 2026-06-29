from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

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


# NOTE: the camera heartbeat endpoint moved to the internal router
# (app/api/v1/internal.py) so it is gated by X-Internal-Token and blocked
# externally by nginx, matching the other service-to-service routes (Finding 7).
# The ingestion service already holds INTERNAL_SERVICE_TOKEN.
