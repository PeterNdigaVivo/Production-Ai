"""Roll-up + alerting helpers, built on the Step-3 duration logic.

Two functions the Celery beat tasks call:

  * rollup_window(...) — for a fixed [start, end) window, compute per-workstation
    state durations and piece counts and UPSERT them into production_records.
    Idempotent: re-running the same window overwrites that window's rows rather
    than duplicating them. This is Finding 8 (the roll-up was a no-op).

  * workers_idle_too_long(...) — find workers whose CURRENT (open) state has been
    IDLE for >= threshold seconds, with no intervening WAITING_FOR_INPUT. This is
    what the debounced idle alert (Finding 6) is based on: a periodic check, not
    an alert on every transition.

Both reuse the same interval-reconstruction approach proven in
app/services/analytics.py, so live KPIs and rolled-up records agree.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    WorkerEvent, ProductionEvent, ProductionRecord, Workstation, ProductionLine,
)

WORKING_STATES = ("WORKING",)
WAITING_STATE = "WAITING_FOR_INPUT"


# --------------------------------------------------------------------------- #
# Roll-up (Finding 8)
# --------------------------------------------------------------------------- #
async def _line_factory_map(db: AsyncSession, line_ids: list[uuid.UUID]) -> dict:
    if not line_ids:
        return {}
    stmt = select(ProductionLine.id, ProductionLine.factory_id).where(
        ProductionLine.id.in_(line_ids))
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def _workstation_line_map(db: AsyncSession) -> dict:
    stmt = select(Workstation.id, Workstation.line_id)
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def rollup_window(db: AsyncSession, window_start: datetime,
                        window_end: datetime) -> int:
    """Aggregate worker_events + production_events into production_records for the
    given window, per workstation. Returns the number of rows written.

    Idempotent: existing production_records whose window_start matches are deleted
    first, so re-running a window is safe (last-write-wins for that window).
    """
    ws_line = await _workstation_line_map(db)
    if not ws_line:
        return 0
    line_factory = await _line_factory_map(db, list(set(ws_line.values())))

    # Per-(workstation, state) seconds via LEAD() interval reconstruction, but the
    # last interval is clamped to window_end (not "now"), so a window is final.
    next_ts = func.lead(WorkerEvent.ts).over(
        partition_by=[WorkerEvent.workstation_id, WorkerEvent.worker_track_id],
        order_by=WorkerEvent.ts,
    ).label("next_ts")
    intervals = (
        select(
            WorkerEvent.workstation_id.label("ws"),
            WorkerEvent.state.label("state"),
            WorkerEvent.ts.label("ts"),
            next_ts,
        )
        .where(and_(WorkerEvent.ts >= window_start, WorkerEvent.ts < window_end))
        .cte("intervals")
    )
    # clamp interval end to window_end
    end_ts = func.least(func.coalesce(intervals.c.next_ts, window_end), window_end)
    seconds = func.sum(func.extract("epoch", end_ts - intervals.c.ts)).label("seconds")
    dur_stmt = (
        select(intervals.c.ws, intervals.c.state, seconds)
        .group_by(intervals.c.ws, intervals.c.state)
    )
    rows = (await db.execute(dur_stmt)).all()

    # piece counts per workstation in window
    pieces_stmt = (
        select(ProductionEvent.workstation_id, func.count())
        .where(and_(
            ProductionEvent.kind == "piece_completed",
            ProductionEvent.ts >= window_start,
            ProductionEvent.ts < window_end,
        ))
        .group_by(ProductionEvent.workstation_id)
    )
    pieces = {ws: n for ws, n in (await db.execute(pieces_stmt)).all()}

    # assemble per-workstation aggregates
    agg: dict[uuid.UUID, dict] = {}
    for ws, state, secs in rows:
        if ws is None:
            continue
        d = agg.setdefault(ws, {"working": 0.0, "idle": 0.0, "away": 0.0, "waiting": 0.0})
        secs = float(secs or 0.0)
        if state in WORKING_STATES:
            d["working"] += secs
        elif state == "IDLE":
            d["idle"] += secs
        elif state == "AWAY":
            d["away"] += secs
        elif state == WAITING_STATE:
            d["waiting"] += secs

    # idempotency: clear this exact window first
    await db.execute(delete(ProductionRecord).where(
        ProductionRecord.window_start == window_start))

    written = 0
    for ws, d in agg.items():
        line_id = ws_line.get(ws)
        if line_id is None:
            continue
        factory_id = line_factory.get(line_id)
        db.add(ProductionRecord(
            window_start=window_start,
            window_end=window_end,
            factory_id=factory_id,
            line_id=line_id,
            workstation_id=ws,
            pieces_completed=int(pieces.get(ws, 0)),
            effective_working_s=int(d["working"]),
            idle_s=int(d["idle"]),
            away_s=int(d["away"]),
            waiting_s=int(d["waiting"]),
        ))
        written += 1
    await db.commit()
    return written


# --------------------------------------------------------------------------- #
# Debounced idle detection (Finding 6)
# --------------------------------------------------------------------------- #
@dataclass
class IdleWorker:
    workstation_id: uuid.UUID | None
    camera_id: uuid.UUID
    worker_track_id: int
    idle_since: datetime
    idle_seconds: float


async def workers_idle_too_long(db: AsyncSession, threshold_seconds: int,
                                now: datetime | None = None,
                                lookback_hours: int = 6) -> list[IdleWorker]:
    """Workers whose latest event is IDLE and has persisted >= threshold without
    a later WAITING_FOR_INPUT or WORKING. Uses the most recent event per
    (camera, workstation, track) as the current state.
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=lookback_hours)

    # rank events per (camera, ws, track) newest-first; row_number==1 is current
    rn = func.row_number().over(
        partition_by=[WorkerEvent.camera_id, WorkerEvent.workstation_id,
                      WorkerEvent.worker_track_id],
        order_by=WorkerEvent.ts.desc(),
    ).label("rn")
    ranked = (
        select(
            WorkerEvent.camera_id.label("camera_id"),
            WorkerEvent.workstation_id.label("ws"),
            WorkerEvent.worker_track_id.label("track"),
            WorkerEvent.state.label("state"),
            WorkerEvent.ts.label("ts"),
            rn,
        )
        .where(WorkerEvent.ts >= since)
        .cte("ranked")
    )
    current = (
        select(ranked.c.camera_id, ranked.c.ws, ranked.c.track,
               ranked.c.state, ranked.c.ts)
        .where(ranked.c.rn == 1)
    )
    out: list[IdleWorker] = []
    for cam, ws, track, state, ts in (await db.execute(current)).all():
        if state != "IDLE":
            continue
        # Some drivers return tz-naive datetimes; normalise to UTC-aware.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        idle_seconds = (now - ts).total_seconds()
        if idle_seconds >= threshold_seconds:
            out.append(IdleWorker(
                workstation_id=ws, camera_id=cam, worker_track_id=int(track),
                idle_since=ts, idle_seconds=idle_seconds,
            ))
    return out
