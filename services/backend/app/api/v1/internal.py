"""Service-to-service endpoints, gated by `X-Internal-Token`.

Consumers:
  * `video-ingestion` polls `/cameras/_internal` to discover active cameras,
    and posts `/cameras/{id}/heartbeat_internal` to mark liveness (Finding 7).
  * `tracking-engine` polls `/zones/_internal` to map detections to workstations.

These routes are intentionally `include_in_schema=False` so they don't appear
in OpenAPI, and nginx is configured to refuse any external request whose path
contains `/_internal` (see `infrastructure/nginx/nginx.*.conf`).
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_internal_token
from app.db.session import get_db
from app.db.models import Camera, Zone, Workstation
from app.services.zone_queries import latest_zone_ids

router = APIRouter(dependencies=[Depends(require_internal_token)])


@router.get("/cameras/_internal", include_in_schema=False)
async def list_active_cameras_internal(db: AsyncSession = Depends(get_db)) -> list[dict]:
    res = await db.execute(select(Camera).where(Camera.is_active.is_(True)))
    return [
        {"id": str(c.id), "rtsp_url": c.rtsp_url, "fps_target": c.fps_target}
        for c in res.scalars()
    ]


@router.post("/cameras/{camera_id}/_internal/heartbeat", include_in_schema=False)
async def camera_heartbeat_internal(camera_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Mark a camera alive. Gated by X-Internal-Token (Finding 7): the old route
    on the public cameras router was unauthenticated, letting anyone keep a dead
    camera looking alive or enumerate camera IDs. The path contains `_internal`
    so nginx blocks it from outside the cluster network."""
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(404, "not found")
    cam.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.get("/zones/_internal", include_in_schema=False)
async def list_zones_internal(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Returns every active zone joined with its workstation's camera, so the
    tracking engine can index zones by `camera_id` without a second round-trip.
    """
    stmt = (
        select(Zone, Workstation.camera_id)
        .join(Workstation, Workstation.id == Zone.workstation_id)
        .where(Zone.id.in_(latest_zone_ids()))
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": str(z.id),
            "workstation_id": str(z.workstation_id),
            "camera_id": str(camera_id),
            "kind": z.kind,
            "polygon": z.polygon,
            "layout_version": z.layout_version,
        }
        for z, camera_id in rows
    ]
