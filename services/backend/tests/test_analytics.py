"""Tests for the analytics core: duration reconstruction + fairness rule.

These run against an in-memory SQLite database (window functions supported on
SQLite >= 3.25). The duration-to-seconds conversion differs by dialect
(julianday on SQLite vs EXTRACT(epoch) on Postgres), so these tests validate the
LOGIC — interval reconstruction, per-track partitioning, and the fairness
denominator — using a small dialect-agnostic helper that mirrors the production
query's structure. The production query itself is exercised against Postgres in
integration (see docs/steps/step3_analytics.md).

Run:  pytest tests/test_analytics.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import (Column, String, Integer, DateTime, MetaData, Table,
                        select, func, and_, insert)
from sqlalchemy.ext.asyncio import create_async_engine

md = MetaData()
worker_events = Table(
    "worker_events", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), index=True),
    Column("workstation_id", String),
    Column("worker_track_id", Integer),
    Column("state", String),
)

PRODUCTIVE = ("WORKING", "IDLE", "AWAY")  # WAITING excluded — fairness rule
NOW = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)


def _at(sec: float) -> datetime:
    return NOW - timedelta(seconds=sec)


async def _durations(conn, ws_ids, since):
    """Mirror of app.services.analytics duration query (SQLite seconds variant).

    LEAD partitions on workstation_id alone — matches the production change
    that shipped with the AWAY-reachability fix (see intervals.py docstring).
    Kept as a naive per-workstation reconstruction (no union-of-islands) so
    the tests below can highlight what changed relative to the historic
    per-track behaviour.
    """
    next_ts = func.lead(worker_events.c.ts).over(
        partition_by=[worker_events.c.workstation_id],
        order_by=worker_events.c.ts,
    ).label("next_ts")
    intervals = (
        select(worker_events.c.state.label("state"),
               worker_events.c.ts.label("ts"), next_ts)
        .where(and_(worker_events.c.workstation_id.in_(ws_ids),
                    worker_events.c.ts >= since))
        .cte("intervals")
    )
    end_ts = func.coalesce(intervals.c.next_ts, NOW)
    seconds = func.sum(
        (func.julianday(end_ts) - func.julianday(intervals.c.ts)) * 86400.0
    ).label("seconds")
    stmt = select(intervals.c.state, seconds).group_by(intervals.c.state)
    rows = (await conn.execute(stmt)).all()
    return {s: round(float(v or 0.0), 1) for s, v in rows}


@pytest_asyncio.fixture
async def conn():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(md.create_all)
    async with eng.connect() as c:
        yield c


@pytest.mark.asyncio
async def test_durations_and_fairness_single_worker(conn):
    rows = [
        dict(ts=_at(600), workstation_id="A", worker_track_id=1, state="WORKING"),
        dict(ts=_at(300), workstation_id="A", worker_track_id=1, state="WAITING_FOR_INPUT"),
        dict(ts=_at(180), workstation_id="A", worker_track_id=1, state="IDLE"),
        dict(ts=_at(60),  workstation_id="A", worker_track_id=1, state="WORKING"),
    ]
    await conn.execute(insert(worker_events), rows)
    d = await _durations(conn, ["A"], _at(100000))

    assert d["WORKING"] == pytest.approx(360, abs=0.5)   # 300 + 60
    assert d["WAITING_FOR_INPUT"] == pytest.approx(120, abs=0.5)
    assert d["IDLE"] == pytest.approx(120, abs=0.5)

    working = d.get("WORKING", 0)
    denom = sum(d.get(s, 0) for s in PRODUCTIVE)         # waiting excluded
    productivity = working / denom
    assert productivity == pytest.approx(0.75, abs=0.01)  # 360 / 480

    # The naive (unfair) calc would fold waiting into the denominator:
    naive = working / (denom + d.get("WAITING_FOR_INPUT", 0))
    assert naive == pytest.approx(0.60, abs=0.01)
    assert productivity > naive  # fairness rule helps the worker


@pytest.mark.asyncio
async def test_two_tracks_same_station_partitioned(conn):
    """Post-fix (workstation-alone LEAD partition), events at one seat chain
    strictly by timestamp regardless of which track wrote them. The scenario
    below is a historical row shape — the workstation-keyed FSM cannot
    produce two concurrent WORKING streams at one seat live, so this test
    exists purely to pin the historical-row reconstruction.

    Events sorted by ts (t=0 is NOW):
      -600 WORKING (t1)
      -300 AWAY    (t2)
      -100 WORKING (t2)
       -60 WORKING (t1)
    Under LEAD partition by workstation alone:
      [-600,-300) WORKING = 300s
      [-300,-100) AWAY    = 200s
      [-100, -60) WORKING =  40s
      [ -60, NOW) WORKING =  60s
    Totals: WORKING = 400s, AWAY = 200s.

    Pre-fix (partition by ws+track) this asserted WORKING=700s because
    track 1's WORKING chain skipped over track 2's AWAY entirely. That was
    the exact class of over-count the union-of-intervals safety net was
    introduced to catch, and the safety net is still there for anything
    the naive query might mis-count.
    """
    rows = [
        dict(ts=_at(600), workstation_id="A", worker_track_id=1, state="WORKING"),
        dict(ts=_at(60),  workstation_id="A", worker_track_id=1, state="WORKING"),
        dict(ts=_at(300), workstation_id="A", worker_track_id=2, state="AWAY"),
        dict(ts=_at(100), workstation_id="A", worker_track_id=2, state="WORKING"),
    ]
    await conn.execute(insert(worker_events), rows)
    d = await _durations(conn, ["A"], _at(100000))
    assert d["WORKING"] == pytest.approx(400, abs=1.0)
    assert d["AWAY"] == pytest.approx(200, abs=0.5)


@pytest.mark.asyncio
async def test_only_listed_workstations_count(conn):
    """Events on a station NOT in the line's workstation set must be ignored —
    this is the tenant/line scoping guarantee (Finding 4)."""
    rows = [
        dict(ts=_at(300), workstation_id="A", worker_track_id=1, state="WORKING"),
        dict(ts=_at(300), workstation_id="OTHER", worker_track_id=9, state="WORKING"),
    ]
    await conn.execute(insert(worker_events), rows)
    d = await _durations(conn, ["A"], _at(100000))  # only station A
    assert d["WORKING"] == pytest.approx(300, abs=0.5)  # OTHER excluded
