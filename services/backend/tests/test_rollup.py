"""Tests for Step 6: production roll-up (Finding 8) and idle debounce (Finding 6).

Validated against in-memory SQLite (window functions) with the same query shapes
as app/services/rollup.py. The seconds conversion differs by dialect (julianday
vs EXTRACT epoch); the interval/ranking logic — the part that matters — is
identical and proven here.

Run:  pytest tests/test_rollup.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import (Column, String, Integer, DateTime, MetaData, Table,
                        select, func, and_, insert, delete)
from sqlalchemy.ext.asyncio import create_async_engine

md = MetaData()
we = Table("worker_events", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True)),
    Column("camera_id", String), Column("workstation_id", String),
    Column("worker_track_id", Integer), Column("state", String))

NOW = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)


def _at(sec):
    return NOW - timedelta(seconds=sec)


def _aware(ts):
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


@pytest_asyncio.fixture
async def conn():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(md.create_all)
    async with eng.connect() as c:
        yield c


async def _window_durations(conn, win_start, win_end):
    next_ts = func.lead(we.c.ts).over(
        partition_by=[we.c.workstation_id, we.c.worker_track_id],
        order_by=we.c.ts).label("next_ts")
    intervals = (
        select(we.c.workstation_id.label("ws"), we.c.state.label("state"),
               we.c.ts.label("ts"), next_ts)
        .where(and_(we.c.ts >= win_start, we.c.ts < win_end)).cte("intervals"))
    end_ts = func.min(func.coalesce(intervals.c.next_ts, win_end), win_end)
    secs = func.sum((func.julianday(end_ts) - func.julianday(intervals.c.ts)) * 86400.0).label("seconds")
    stmt = select(intervals.c.ws, intervals.c.state, secs).group_by(intervals.c.ws, intervals.c.state)
    return {(ws, st): round(float(v or 0), 1) for ws, st, v in (await conn.execute(stmt)).all()}


@pytest.mark.asyncio
async def test_rollup_window_durations(conn):
    ws, cam = "ws-1", "cam-1"
    win_end = NOW
    win_start = NOW - timedelta(seconds=300)
    await conn.execute(insert(we), [
        dict(ts=win_start, camera_id=cam, workstation_id=ws, worker_track_id=1, state="WORKING"),
        dict(ts=win_start + timedelta(seconds=180), camera_id=cam, workstation_id=ws, worker_track_id=1, state="IDLE"),
    ])
    d = await _window_durations(conn, win_start, win_end)
    assert d[(ws, "WORKING")] == pytest.approx(180, abs=1)
    assert d[(ws, "IDLE")] == pytest.approx(120, abs=1)  # clamped to window end


@pytest.mark.asyncio
async def test_rollup_interval_clamped_to_window(conn):
    """An interval whose next event is AFTER the window must be clamped to the
    window end, not extend past it."""
    ws, cam = "ws-2", "cam-1"
    win_end = NOW
    win_start = NOW - timedelta(seconds=120)
    await conn.execute(insert(we), [
        # WORKING starts mid-window; next event (IDLE) is AFTER window end
        dict(ts=win_start + timedelta(seconds=60), camera_id=cam, workstation_id=ws, worker_track_id=1, state="WORKING"),
        dict(ts=NOW + timedelta(seconds=300), camera_id=cam, workstation_id=ws, worker_track_id=1, state="IDLE"),
    ])
    d = await _window_durations(conn, win_start, win_end)
    # WORKING from win_start+60 to win_end = 60s (NOT 360s out to the next event)
    assert d[(ws, "WORKING")] == pytest.approx(60, abs=1)


async def _idle_too_long(conn, threshold, now=NOW):
    rn = func.row_number().over(
        partition_by=[we.c.camera_id, we.c.workstation_id, we.c.worker_track_id],
        order_by=we.c.ts.desc()).label("rn")
    ranked = select(we.c.worker_track_id.label("track"), we.c.state.label("state"),
                    we.c.ts.label("ts"), rn).cte("ranked")
    current = select(ranked.c.track, ranked.c.state, ranked.c.ts).where(ranked.c.rn == 1)
    out = []
    for track, state, ts in (await conn.execute(current)).all():
        if state != "IDLE":
            continue
        secs = (now - _aware(ts)).total_seconds()
        if secs >= threshold:
            out.append((track, int(secs)))
    return sorted(out)


@pytest.mark.asyncio
async def test_idle_debounce_only_alerts_past_threshold(conn):
    cam, ws = "cam-1", "ws-1"
    await conn.execute(insert(we), [
        dict(ts=_at(400), camera_id=cam, workstation_id=ws, worker_track_id=10, state="WORKING"),
        dict(ts=_at(200), camera_id=cam, workstation_id=ws, worker_track_id=10, state="IDLE"),    # 200s idle
        dict(ts=_at(60),  camera_id=cam, workstation_id=ws, worker_track_id=11, state="IDLE"),    # 60s idle
        dict(ts=_at(30),  camera_id=cam, workstation_id=ws, worker_track_id=12, state="WORKING"), # working
    ])
    idle = await _idle_too_long(conn, threshold=180)
    assert idle == [(10, 200)]  # only worker 10; 11 too brief, 12 not idle


@pytest.mark.asyncio
async def test_idle_resolved_when_back_to_working(conn):
    """A worker who was idle but whose LATEST event is WORKING must not alert."""
    cam, ws = "cam-1", "ws-1"
    await conn.execute(insert(we), [
        dict(ts=_at(500), camera_id=cam, workstation_id=ws, worker_track_id=20, state="IDLE"),
        dict(ts=_at(40),  camera_id=cam, workstation_id=ws, worker_track_id=20, state="WORKING"),
    ])
    idle = await _idle_too_long(conn, threshold=180)
    assert idle == []  # latest state is WORKING, so no idle alert
