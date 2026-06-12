"""Activity engine entrypoint.

Consumes `stream:tracks:*`, maintains a per-(camera, track_id) FSM, and emits
`worker_state_changed` events when the FSM transitions. Phase-1 derives motion
magnitude from bbox-centroid displacement; Phase 2 will integrate pose,
optical flow, and machine-state signals.
"""
from __future__ import annotations
import asyncio
import json
import math
import os
import time

import structlog
from redis.asyncio import Redis

from activity.state_machine import WorkerActivityFSM, ActivitySignals

log = structlog.get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
STREAM_TRACKS = os.environ.get("REDIS_STREAM_TRACKS", "stream:tracks")
STREAM_EVENTS = os.environ.get("REDIS_STREAM_EVENTS", "stream:events")


class CameraState:
    def __init__(self) -> None:
        self.fsms: dict[int, WorkerActivityFSM] = {}
        self.last_centroid: dict[int, tuple[float, float]] = {}


def _centroid(xyxy):
    x1, y1, x2, y2 = xyxy
    return ((x1 + x2) / 2, (y1 + y2) / 2)


async def process_camera(redis: Redis, key: str) -> None:
    camera_id = key.rsplit(":", 1)[-1]
    state = CameraState()
    last_id = "$"
    log.info("activity.attach", camera=camera_id)
    while True:
        try:
            resp = await redis.xread({key: last_id}, block=5_000, count=1)
            if not resp:
                continue
            _stream, entries = resp[0]
            for entry_id, fields in entries:
                last_id = entry_id
                payload = json.loads(fields[b"json"])
                ts = float(payload.get("ts", time.time()))
                for tr in payload["tracks"]:
                    tid = tr["track_id"]
                    cx, cy = _centroid(tr["xyxy"])
                    prev = state.last_centroid.get(tid)
                    state.last_centroid[tid] = (cx, cy)
                    motion = 0.0
                    if prev is not None:
                        dx, dy = cx - prev[0], cy - prev[1]
                        motion = min(1.0, math.hypot(dx, dy) / 50.0)
                    fsm = state.fsms.setdefault(tid, WorkerActivityFSM())
                    prev_state = fsm.state
                    new_state = fsm.update(ts, ActivitySignals(motion_magnitude=motion))
                    if new_state != prev_state:
                        await redis.xadd(STREAM_EVENTS, {
                            "type": "worker_state_changed",
                            "ts": str(ts),
                            "camera_id": camera_id,
                            "workstation_id": tr.get("workstation_id") or "",
                            "track_id": str(tid),
                            "state": new_state,
                            "prev_state": prev_state,
                        }, maxlen=10_000, approximate=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("activity.error", camera=camera_id, error=str(e))
            await asyncio.sleep(1.0)


async def amain() -> None:
    redis = Redis.from_url(REDIS_URL)
    tasks: dict[str, asyncio.Task] = {}
    while True:
        keys = []
        async for k in redis.scan_iter(match=f"{STREAM_TRACKS}:*"):
            keys.append(k.decode() if isinstance(k, bytes) else k)
        for k in keys:
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(process_camera(redis, k), name=f"act-{k}")
        await asyncio.sleep(15.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
