"""Per-task async engine for Celery workers.

Each Celery task enters async via asyncio.run(...), which creates a fresh
event loop, runs the coroutine, then closes it. A module-level asyncpg pool
binds its connections to the FIRST loop that touches it and then fails on
the next task with:

    RuntimeError: Task ... got Future attached to a different loop

That's why tasks MUST NOT import the shared async sessionmaker from
app.db.session — the FastAPI app keeps its long-lived pooled engine bound
to uvicorn's one loop and works correctly; only the workers hop loops.

We use `NullPool` here on purpose. NullPool opens a connection on checkout
and closes it on checkin, so no connection can outlive the event loop that
created it — the guarantee is STRUCTURAL, not dependent on `engine.dispose()`
being reached. If a task raises mid-flight after acquiring several connections,
NullPool guarantees they're released as their contexts unwind; a small
`pool_size` would leak up to that many sockets to a dead loop. Postgres on
this host is memory-capped and shared with another production database, so
that leak matters.

Cost: one TCP handshake per checkout. At the current beat cadence (5-min
rollup, per-minute heartbeat check) that's a rounding error.

Usage:
    async with task_session() as db:
        ...
"""
from __future__ import annotations
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@asynccontextmanager
async def task_session() -> AsyncSession:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            yield session
    finally:
        await engine.dispose()
