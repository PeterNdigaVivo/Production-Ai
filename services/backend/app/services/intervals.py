"""Per-(workstation, state) duration inside [window_start, window_end),
computed as the UNION of intervals per workstation.

Approach: classic gaps-and-islands.
  1. Per workstation, reconstruct the state interval from
     [ts, LEAD(ts) clamped to window_end).
  2. Order intervals per (workstation, state) by start. Mark a new "island"
     whenever the current interval starts strictly beyond the running max of
     all prior end times for that (ws, state). Overlapping and adjacent
     intervals share an island.
  3. Per island, coverage is (MAX(end) - MIN(start)). Sum island coverages
     per (workstation, state).

Partitioning. LEAD partitions on `workstation_id` alone. Previously it
partitioned on (workstation_id, worker_track_id) — but with the FSM
re-keyed to workstation (activity-engine/main.py) worker_track_id is a
sentinel and no longer carries a partition-worthy meaning. Two consequences:

  * AWAY events (which have no owning track — the absence of a track IS the
    event) chain LEAD correctly against the preceding WORKING event. Under
    the old partition the WORKING interval clamped to window_end (no next
    event in its track) AND the AWAY event started a fresh partition,
    double-counting at exactly the transitions this fix introduces.
  * Historical rows written pre-fix by the track-keyed activity engine
    re-summarize under the new partition (their intervals merge across
    tracks at one workstation instead of being reconstructed track-by-
    track). The union-of-intervals island logic stays as the safety net
    for accidental same-ts double writes and for that historical
    re-summarization — do not remove it.

Both rollup.py (batch, Celery) and analytics.py (live API, /kpis) call this
helper so the two paths cannot drift again.

Postgres-only: uses EXTRACT(epoch FROM interval). SQLite (used by tests)
requires the julianday variant — the tests reimplement the same shape.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkerEvent


async def merged_state_seconds(
    db: AsyncSession,
    window_start: datetime,
    window_end: datetime,
    workstation_ids: list[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, str, float]]:
    """Returns (workstation_id, state, seconds) tuples with total seconds
    per (workstation, state) computed as the union of overlapping intervals.

    `workstation_ids=None` means "any workstation" — used by the rollup which
    covers every workstation. Otherwise the query is restricted, which the
    live per-line KPI endpoint needs for tenant scoping.
    """
    # TODO: cross-window under-count. This CTE only sees events with
    # ts >= window_start. A state that started BEFORE window_start and is
    # still open at window_start contributes zero to this window because no
    # event within the window says "in state X". Separate fix — flagged during
    # Bug-2 investigation but left for a follow-up.
    next_ts = func.lead(WorkerEvent.ts).over(
        partition_by=[WorkerEvent.workstation_id],
        order_by=WorkerEvent.ts,
    ).label("next_ts")
    end_ts = func.least(func.coalesce(next_ts, window_end), window_end).label("end_ts")

    where = [WorkerEvent.ts >= window_start, WorkerEvent.ts < window_end]
    if workstation_ids is not None:
        where.append(WorkerEvent.workstation_id.in_(workstation_ids))

    intervals = (
        select(
            WorkerEvent.workstation_id.label("ws"),
            WorkerEvent.state.label("state"),
            WorkerEvent.ts.label("start_ts"),
            end_ts,
        )
        .where(and_(*where))
        .cte("intervals")
    )

    # Running max of prior end_ts, per (ws, state) ordered by start_ts.
    # ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING.
    prev_max_end = func.max(intervals.c.end_ts).over(
        partition_by=[intervals.c.ws, intervals.c.state],
        order_by=intervals.c.start_ts,
        rows=(None, -1),
    ).label("prev_max_end")
    ranked = (
        select(
            intervals.c.ws,
            intervals.c.state,
            intervals.c.start_ts,
            intervals.c.end_ts,
            prev_max_end,
        )
        .cte("ranked")
    )

    # Start a new island whenever the current interval begins strictly beyond
    # the running max of prior end times (i.e. there is a real gap).
    is_new_island = case(
        (or_(ranked.c.prev_max_end.is_(None),
             ranked.c.start_ts > ranked.c.prev_max_end), 1),
        else_=0,
    )
    island_id = func.sum(is_new_island).over(
        partition_by=[ranked.c.ws, ranked.c.state],
        order_by=ranked.c.start_ts,
    ).label("island_id")
    tagged = (
        select(
            ranked.c.ws, ranked.c.state,
            ranked.c.start_ts, ranked.c.end_ts,
            island_id,
        )
        .cte("tagged")
    )

    per_island = (
        select(
            tagged.c.ws, tagged.c.state, tagged.c.island_id,
            func.min(tagged.c.start_ts).label("s"),
            func.max(tagged.c.end_ts).label("e"),
        )
        .group_by(tagged.c.ws, tagged.c.state, tagged.c.island_id)
        .cte("per_island")
    )

    stmt = (
        select(
            per_island.c.ws,
            per_island.c.state,
            func.sum(func.extract("epoch", per_island.c.e - per_island.c.s)).label("seconds"),
        )
        .group_by(per_island.c.ws, per_island.c.state)
    )
    return [
        (ws, state, float(secs or 0.0))
        for ws, state, secs in (await db.execute(stmt)).all()
    ]
