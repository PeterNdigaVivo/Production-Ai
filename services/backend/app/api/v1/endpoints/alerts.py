from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime, timezone

from app.db.session import get_db
from app.db.models import Alert
from app.schemas.events import AlertRead
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("", response_model=list[AlertRead])
async def list_alerts(unack_only: bool = False, db: AsyncSession = Depends(get_db)):
    stmt = select(Alert).order_by(desc(Alert.ts)).limit(200)
    if unack_only:
        stmt = stmt.where(Alert.acknowledged.is_(False))
    res = await db.execute(stmt)
    return list(res.scalars())


@router.post("/{alert_id}/ack", response_model=AlertRead)
async def acknowledge(alert_id: str, user: dict = Depends(current_user), db: AsyncSession = Depends(get_db)):
    a = await db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "not found")
    a.acknowledged = True
    a.acknowledged_at = datetime.now(timezone.utc)
    a.acknowledged_by = user["sub"]
    await db.commit()
    await db.refresh(a)
    return a
