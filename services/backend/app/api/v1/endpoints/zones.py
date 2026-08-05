from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import Zone, Workstation
from app.schemas.tenancy import ZoneCreate, ZoneRead
from app.api.deps import current_user
from app.services.zone_queries import latest_zone_ids

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("", response_model=list[ZoneRead])
async def list_zones(workstation_id: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Zone).where(Zone.id.in_(latest_zone_ids()))
    if workstation_id:
        stmt = stmt.where(Zone.workstation_id == workstation_id)
    res = await db.execute(stmt)
    return list(res.scalars())


@router.post("", response_model=ZoneRead, status_code=201)
async def upsert_zone(body: ZoneCreate, db: AsyncSession = Depends(get_db)):
    ws = await db.get(Workstation, body.workstation_id)
    if not ws:
        raise HTTPException(404, "workstation not found")
    # Bump layout version: zones are immutable; new edits create a new row at v+1.
    ws.layout_version += 1
    z = Zone(workstation_id=ws.id, kind=body.kind, polygon=body.polygon, layout_version=ws.layout_version)
    db.add(z)
    await db.commit()
    await db.refresh(z)
    return z
