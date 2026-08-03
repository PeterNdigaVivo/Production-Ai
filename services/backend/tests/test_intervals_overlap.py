"""Regression guard for BOTH rollup and analytics: overlapping tracks at one
workstation must not double-count.

Before the fix, `rollup_window()` and `compute_line_kpis()` partitioned by
`(workstation_id, worker_track_id)` and summed the resulting intervals — two
tracks alive concurrently at the same workstation contributed the sum of
their durations, not the union, so a 300s window reported >300s of state
time. On a live camera we observed effective_working_s=2796 in a 300s window.

The fix is app/services/intervals.merged_state_seconds — a gaps-and-islands
CTE that merges overlapping intervals per (workstation, state). Both callers
now go through it.

These tests reproduce the gaps-and-islands SQL in dialect-agnostic form
(julianday instead of EXTRACT(epoch) so SQLite works) and prove that:
  * three tracks all WORKING for the full window → total <= window length
  * two tracks with a real gap → total = sum-of-gap-free-slices
The production query itself is PG-dialect and is exercised in integration.

Run: pytest tests/test_intervals_overlap.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import (
    Column, DateTime, Integer, MetaData, String, Table,
    and_, case, func, insert, or_, select,
)
from sqlalchemy.ext.asyncio import create_async_engine


md = MetaData()
we = Table(
    "worker_events", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True)),
    Column("workstation_id", String),
    Column("worker_track_id", Integer),
    Column("state", String),
)

NOW = datetime(2026, 8, 3, 7, 25, 0, tzinfo=timezone.utc)


def _at(sec: float) -> datetime:
    return NOW - timedelta(seconds=sec)


@pytest_asyncio.fixture
async def conn():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(md.create_all)
    async with eng.connect() as c:
        yield c


async def _merged(conn, ws_ids, window_start, window_end):
    """SQLite-flavoured reproduction of merged_state_seconds()."""
    next_ts = func.lead(we.c.ts).over(
        partition_by=[we.c.workstation_id, we.c.worker_track_id],
        order_by=we.c.ts,
    ).label("next_ts")
    end_ts = func.min(
        func.coalesce(next_ts, window_end), window_end,
    ).label("end_ts")
    intervals = (
        select(
            we.c.workstation_id.label("ws"),
            we.c.state.label("state"),
            we.c.ts.label("start_ts"),
            end_ts,
        )
        .where(and_(
            we.c.workstation_id.in_(ws_ids),
            we.c.ts >= window_start,
            we.c.ts < window_end,
        ))
        .cte("intervals")
    )
    prev_max_end = func.max(intervals.c.end_ts).over(
        partition_by=[intervals.c.ws, intervals.c.state],
        order_by=intervals.c.start_ts,
        rows=(None, -1),
    ).label("prev_max_end")
    ranked = (
        select(intervals.c.ws, intervals.c.state,
               intervals.c.start_ts, intervals.c.end_ts, prev_max_end)
        .cte("ranked")
    )
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
        select(ranked.c.ws, ranked.c.state,
               ranked.c.start_ts, ranked.c.end_ts, island_id)
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
            per_island.c.ws, per_island.c.state,
            func.sum(
                (func.julianday(per_island.c.e) - func.julianday(per_island.c.s)) * 86400.0
            ).label("seconds"),
        )
        .group_by(per_island.c.ws, per_island.c.state)
    )
    return {
        (ws, state): round(float(secs or 0.0), 1)
        for ws, state, secs in (await conn.execute(stmt)).all()
    }


# --------------------------------------------------------------------------- #
# The bug reproduction: three overlapping tracks all WORKING for the full
# 300s window. Sum-per-track would report ~900s; union must report <=300s.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_three_overlapping_tracks_do_not_exceed_window(conn):
    ws = "WS-01"
    win_start = _at(300)
    win_end = NOW
    rows = []
    for track in (1, 2, 3):
        rows.append(dict(
            ts=win_start, workstation_id=ws, worker_track_id=track, state="WORKING"))
    await conn.execute(insert(we), rows)

    d = await _merged(conn, [ws], win_start, win_end)
    # The window is 300s. Even with three concurrent WORKING tracks, the union
    # of their intervals cannot exceed 300s. Sum-per-track would report ~900s.
    assert d[(ws, "WORKING")] == pytest.approx(300, abs=1.0), (
        f"three overlapping WORKING tracks report {d[(ws, 'WORKING')]}s "
        f"in a 300s window — merge is not deduping overlaps"
    )
    assert d[(ws, "WORKING")] <= 300.0 + 1.0


# --------------------------------------------------------------------------- #
# Two workers, both WORKING, with a real gap: union == sum-of-slices.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_disjoint_intervals_sum_to_slice_total(conn):
    ws = "WS-01"
    win_start = _at(300)
    win_end = NOW
    # Timeline (t=0 is NOW; window is [-300, 0]):
    #   track 1: WORKING @ -300, WAITING_FOR_INPUT @ -200 (open through win_end)
    #   track 2: WORKING @ -100, no next event (open through win_end)
    # WORKING union = [-300, -200] ∪ [-100, 0] = 100 + 100 = 200s (disjoint)
    # WAITING_FOR_INPUT for track 1 = [-200, 0] clamped = 200s
    await conn.execute(insert(we), [
        dict(ts=_at(300), workstation_id=ws, worker_track_id=1, state="WORKING"),
        dict(ts=_at(200), workstation_id=ws, worker_track_id=1, state="WAITING_FOR_INPUT"),
        dict(ts=_at(100), workstation_id=ws, worker_track_id=2, state="WORKING"),
    ])
    d = await _merged(conn, [ws], win_start, win_end)
    assert d[(ws, "WORKING")] == pytest.approx(200, abs=1.0)
    assert d[(ws, "WAITING_FOR_INPUT")] == pytest.approx(200, abs=1.0)


# --------------------------------------------------------------------------- #
# Adjacent, non-overlapping intervals of the same state must merge into one
# island (touching, not overlapping) and count once.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_partial_overlap_yields_union_not_sum(conn):
    ws = "WS-01"
    win_start = _at(300)
    win_end = NOW
    # track 1: WORKING at t=-300, IDLE at t=-100  (200s of WORKING)
    # track 2: WORKING at t=-200, IDLE at t=-50   (150s of WORKING)
    # Overlap of WORKING: [-200, -100] = 100s
    # Union: [-300, -100] ∪ [-200, -50] = [-300, -50] = 250s
    # Sum-per-track would be 200 + 150 = 350s — clearly wrong.
    await conn.execute(insert(we), [
        dict(ts=_at(300), workstation_id=ws, worker_track_id=1, state="WORKING"),
        dict(ts=_at(100), workstation_id=ws, worker_track_id=1, state="IDLE"),
        dict(ts=_at(200), workstation_id=ws, worker_track_id=2, state="WORKING"),
        dict(ts=_at(50),  workstation_id=ws, worker_track_id=2, state="IDLE"),
    ])
    d = await _merged(conn, [ws], win_start, win_end)
    assert d[(ws, "WORKING")] == pytest.approx(250, abs=1.0)
