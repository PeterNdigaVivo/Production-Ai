"""Event engine.

Consumes `stream:events` via a Redis consumer group (idempotent retries) and
persists `worker_state_changed` events to `worker_events`.

Idle alerting used to live here and fired on EVERY transition into IDLE (Finding
6 — alert spam). That has been removed. Debounced, de-duplicated idle alerting is
now a periodic Celery task (`check_idle_workers`) that only alerts when a worker
has been continuously IDLE past a threshold without WAITING_FOR_INPUT. A periodic
check is the correct design because a worker who stays idle emits no further
transitions, so there is no event here to react to.
"""
from __future__ import annotations
import asyncio
import os
import uuid
from datetime import datetime, timezone

import asyncpg
import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
STREAM_EVENTS = os.environ.get("REDIS_STREAM_EVENTS", "stream:events")
DB_URL = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
GROUP = "event-engine"
CONSUMER = os.environ.get("HOSTNAME", "event-engine-1")


async def ensure_group(r: Redis) -> None:
    try:
        await r.xgroup_create(STREAM_EVENTS, GROUP, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise


async def persist_worker_event(pool: asyncpg.Pool, fields: dict) -> None:
    if fields.get(b"type") != b"worker_state_changed":
        return
    workstation = fields.get(b"workstation_id", b"").decode() or None
    await pool.execute(
        """
        INSERT INTO worker_events (ts, camera_id, workstation_id, worker_track_id, state, confidence)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        datetime.fromtimestamp(float(fields[b"ts"]), tz=timezone.utc),
        uuid.UUID(fields[b"camera_id"].decode()),
        uuid.UUID(workstation) if workstation else None,
        int(fields.get(b"track_id", b"0")),
        fields[b"state"].decode(),
        1.0,
    )


async def amain() -> None:
    r = Redis.from_url(REDIS_URL)
    pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=10)
    await ensure_group(r)
    log.info("event-engine.ready")
    while True:
        try:
            resp = await r.xreadgroup(GROUP, CONSUMER, streams={STREAM_EVENTS: ">"}, count=64, block=5_000)
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    try:
                        await persist_worker_event(pool, fields)
                        await r.xack(STREAM_EVENTS, GROUP, entry_id)
                    except Exception as e:
                        log.warning("event-engine.processing_error", id=entry_id, error=str(e))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("event-engine.loop_error", error=str(e))
            await asyncio.sleep(2.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
