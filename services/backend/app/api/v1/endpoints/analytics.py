from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone

from app.db.session import get_db
from app.db.models import WorkerEvent, ProductionEvent
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("/kpis/line/{line_id}")
async def line_kpis(
    line_id: str,
    hours: int = Query(8, ge=1, le=72),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Aggregate KPIs over the last `hours` hours for one line.

    Phase 1 returns presence/state counts directly from `worker_events`. Once the
    analytics-engine is materializing `production_records`, this endpoint
    should read from there instead.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    state_counts_stmt = (
        select(WorkerEvent.state, func.count())
        .where(WorkerEvent.ts >= since)
        .group_by(WorkerEvent.state)
    )
    state_counts = {state: n for state, n in (await db.execute(state_counts_stmt)).all()}
    pieces_stmt = (
        select(func.count())
        .where(
            and_(
                ProductionEvent.line_id == line_id,
                ProductionEvent.kind == "piece_completed",
                ProductionEvent.ts >= since,
            )
        )
    )
    pieces = (await db.execute(pieces_stmt)).scalar() or 0
    return {
        "line_id": line_id,
        "window_hours": hours,
        "state_counts": state_counts,
        "pieces_completed": pieces,
        "pieces_per_hour": pieces / hours if hours else 0,
    }
