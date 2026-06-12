from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime, timedelta, timezone

from app.db.session import get_db
from app.db.models import WorkerEvent
from app.schemas.events import WorkerEventRead
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("/worker", response_model=list[WorkerEventRead])
async def recent_worker_events(
    workstation_id: str | None = None,
    minutes: int = Query(15, ge=1, le=1440),
    limit: int = Query(200, le=1000),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    stmt = select(WorkerEvent).where(WorkerEvent.ts >= since)
    if workstation_id:
        stmt = stmt.where(WorkerEvent.workstation_id == workstation_id)
    stmt = stmt.order_by(desc(WorkerEvent.ts)).limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars())
