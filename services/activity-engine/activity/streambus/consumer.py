"""VENDORED from services/common/streambus/consumer.py — keep in sync.

Each engine builds from its own Docker context, so this shared helper is
copied into each service package. Canonical copy + tests live in
services/common/streambus/. After editing canonical, run
  bash services/common/streambus/sync.sh

Used by detection-engine, tracking-engine, and activity-engine so all three get
identical, crash-safe, horizontally-scalable stream consumption instead of the
original plain `XREAD` from `$` (which lost in-flight data on restart and made
replicas double-process every message).

Guarantees this provides:
  * At-least-once delivery. Read with XREADGROUP ">", process, then XACK. On
    startup, pending (unacked) entries from a previous crash are recovered first.
  * Horizontal scaling. Multiple replicas join the same group with distinct
    consumer names (default: hostname) and the group splits messages between
    them, instead of each replica seeing every message.

Two backlog policies, because live video and event streams want different things:
  * BacklogPolicy.ALL     — drain pending, then read new. Use for
    detections->tracks and tracks->activity: never drop a detection.
  * BacklogPolicy.NEWEST  — recover pending once, then only ever process the
    newest message per read (trim the rest). Use for frames->detection: when
    inference falls behind, you want the freshest frame, not a stale backlog.

The helper is intentionally tiny and has no dependencies beyond redis.asyncio.
"""
from __future__ import annotations

import asyncio
import contextlib
import enum
import os
import socket
from typing import AsyncIterator, Awaitable, Callable

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)


class BacklogPolicy(enum.Enum):
    ALL = "all"
    NEWEST = "newest"


def default_consumer_name() -> str:
    """Stable, unique-per-replica consumer name."""
    return os.environ.get("HOSTNAME") or socket.gethostname() or "consumer-1"


async def ensure_group(redis: Redis, stream: str, group: str) -> None:
    """Create the consumer group if it doesn't exist (idempotent).

    id="0" so the group can see everything already in the stream at creation
    time; mkstream=True so we can attach before the producer has created it.
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        log.info("streambus.group_created", stream=stream, group=group)
    except Exception as e:  # redis raises if the group already exists
        if "BUSYGROUP" not in str(e):
            raise


async def _drain_pending(redis: Redis, stream: str, group: str, consumer: str,
                         handler: Callable[[bytes, dict], Awaitable[bool]],
                         count: int) -> int:
    """Reprocess this consumer's own pending (unacked) entries after a restart.

    Reads with id "0" which returns already-delivered-but-unacked messages for
    THIS consumer. Returns how many were recovered.
    """
    recovered = 0
    last = "0"
    while True:
        resp = await redis.xreadgroup(group, consumer, streams={stream: last}, count=count)
        if not resp:
            break
        _stream, entries = resp[0]
        if not entries:
            break
        for entry_id, fields in entries:
            ok = await handler(entry_id, fields)
            if ok:
                await redis.xack(stream, group, entry_id)
            recovered += 1
            last = entry_id
        if len(entries) < count:
            break
    if recovered:
        log.info("streambus.recovered_pending", stream=stream, n=recovered)
    return recovered


async def _read_or_stop(redis, group, consumer, stream, count, block_ms, stop):
    """xreadgroup with block, but return None promptly if `stop` is signalled.

    A plain blocking XREADGROUP can hold for the full block window even after a
    shutdown is requested. Racing it against the stop event keeps shutdown snappy
    without busy-polling.
    """
    read = asyncio.ensure_future(
        redis.xreadgroup(group, consumer, streams={stream: ">"},
                         count=count, block=block_ms)
    )
    if stop is None:
        return await read
    stopper = asyncio.ensure_future(stop.wait())
    done, pending = await asyncio.wait(
        {read, stopper}, return_when=asyncio.FIRST_COMPLETED,
    )
    if read in done:
        stopper.cancel()
        return read.result()
    # stop fired first; cancel the read and signal shutdown
    read.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await read
    return None


async def consume(
    redis: Redis,
    stream: str,
    group: str,
    handler: Callable[[bytes, dict], Awaitable[bool]],
    *,
    consumer: str | None = None,
    policy: BacklogPolicy = BacklogPolicy.ALL,
    block_ms: int = 5_000,
    count: int = 1,
    stop: asyncio.Event | None = None,
) -> None:
    """Consume `stream` via a consumer group, calling `handler(id, fields)`.

    `handler` returns True to acknowledge (message done) or False to leave it
    pending for retry. Exceptions in the handler leave the message pending too.
    """
    consumer = consumer or default_consumer_name()
    await ensure_group(redis, stream, group)

    # Recover anything this consumer had in flight when it last died.
    await _drain_pending(redis, stream, group, consumer, handler, count)

    read_count = count if policy is BacklogPolicy.ALL else max(count, 32)
    while stop is None or not stop.is_set():
        try:
            resp = await _read_or_stop(
                redis, group, consumer, stream, read_count, block_ms, stop,
            )
            if resp is None:  # stop was signalled while blocked
                break
            if not resp:
                continue
            _stream, entries = resp[0]
            if not entries:
                continue

            if policy is BacklogPolicy.NEWEST and len(entries) > 1:
                # Ack-and-skip everything but the newest, so we don't grind a
                # stale backlog. The freshest frame is the last entry.
                *stale, newest = entries
                if stale:
                    await redis.xack(stream, group, *[eid for eid, _ in stale])
                entries = [newest]

            for entry_id, fields in entries:
                try:
                    ok = await handler(entry_id, fields)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("streambus.handler_error", stream=stream,
                                id=entry_id, error=str(e))
                    ok = False  # leave pending for retry
                if ok:
                    await redis.xack(stream, group, entry_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("streambus.loop_error", stream=stream, error=str(e))
            await asyncio.sleep(1.0)
