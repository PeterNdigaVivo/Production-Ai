"""Regression guard for the union-of-intervals safety net.

History. `rollup_window()` and `compute_line_kpis()` used to sum LEAD-based
intervals per (workstation, track_id) — with the phantom-track bug that
produced ID divergence in production, a 300s window reported 2796s. The
first fix added the gaps-and-islands CTE (app/services/intervals.py) that
merges overlapping intervals per (workstation, state).

Then, when the FSM was re-keyed by workstation to make AWAY reachable,
LEAD's partition was reduced to workstation_id alone (worker_track_id
became a sentinel, see intervals.py docstring). The union-of-islands logic
stays as the safety net for accidental same-ts double writes and for the
historical rows written before the re-key.

These tests reproduce the gaps-and-islands SQL in dialect-agnostic form
(julianday instead of EXTRACT(epoch) so SQLite works) with the CURRENT
production partition (workstation_id alone). The production query itself is
PG-dialect and is exercised in integration.

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
        partition_by=[we.c.workstation_id],
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
    # Historical-row scenario. Timeline (t=0 is NOW; window is [-300, 0]):
    #   -300 WORKING (track 1)
    #   -200 WAITING_FOR_INPUT (track 1)
    #   -100 WORKING (track 2)
    # Under the workstation-alone LEAD partition:
    #   [-300,-200) WORKING = 100s
    #   [-200,-100) WAITING = 100s   (terminated by the next event at -100)
    #   [-100, NOW)  WORKING = 100s
    # Union WORKING = 100 + 100 = 200s (disjoint slices, correctly summed).
    # Union WAITING = 100s.
    # NOTE: pre-fix (partition by ws+track) this test asserted WAITING=200s
    # because track 1's WAITING stretched unterminated to NOW. That was an
    # artefact of the per-track partition, not the truth on the ground.
    await conn.execute(insert(we), [
        dict(ts=_at(300), workstation_id=ws, worker_track_id=1, state="WORKING"),
        dict(ts=_at(200), workstation_id=ws, worker_track_id=1, state="WAITING_FOR_INPUT"),
        dict(ts=_at(100), workstation_id=ws, worker_track_id=2, state="WORKING"),
    ])
    d = await _merged(conn, [ws], win_start, win_end)
    assert d[(ws, "WORKING")] == pytest.approx(200, abs=1.0)
    assert d[(ws, "WAITING_FOR_INPUT")] == pytest.approx(100, abs=1.0)


# --------------------------------------------------------------------------- #
# Adjacent, non-overlapping intervals of the same state must merge into one
# island (touching, not overlapping) and count once.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_partial_overlap_yields_union_not_sum(conn):
    ws = "WS-01"
    win_start = _at(300)
    win_end = NOW
    # Historical-row scenario with two overlapping tracks. Post-fix the FSM
    # is workstation-keyed and cannot produce this event shape live, but old
    # rows in the database still do. Under the workstation-alone LEAD
    # partition, all four events sort by ts and reconstruct as:
    #   -300 WORKING → next=-200 → 100s
    #   -200 WORKING → next=-100 → 100s   (adjacent to prior WORKING → merges)
    #   -100 IDLE    → next=-50  →  50s
    #    -50 IDLE    → next=NOW  →  50s   (adjacent to prior IDLE → merges)
    # Union WORKING = [-300, -100) = 200s. Union IDLE = [-100, NOW) = 100s.
    # Sum-per-track under the OLD partition would report 350s of WORKING —
    # the exact 2796s-shape bug this whole file exists to guard against.
    await conn.execute(insert(we), [
        dict(ts=_at(300), workstation_id=ws, worker_track_id=1, state="WORKING"),
        dict(ts=_at(100), workstation_id=ws, worker_track_id=1, state="IDLE"),
        dict(ts=_at(200), workstation_id=ws, worker_track_id=2, state="WORKING"),
        dict(ts=_at(50),  workstation_id=ws, worker_track_id=2, state="IDLE"),
    ])
    d = await _merged(conn, [ws], win_start, win_end)
    assert d[(ws, "WORKING")] == pytest.approx(200, abs=1.0)
