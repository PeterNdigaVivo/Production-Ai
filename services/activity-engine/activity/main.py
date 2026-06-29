"""Activity engine entrypoint.

Consumes `stream:tracks:*` via a per-stream CONSUMER GROUP, maintains a
per-(camera, track_id) FSM, and emits `worker_state_changed` events when the FSM
transitions.

IMPORTANT — statefulness and scaling. This engine (like tracking) holds
PER-CAMERA STATE (the FSMs). A consumer group gives crash-safety (recover
unacked messages on restart) and lets you split *different cameras* across
replicas. It must NOT have two replicas consuming the SAME camera's stream, or
each would see only half that camera's frames and the FSM would be wrong.

The discovery loop below assigns one task per camera within a single process,
which is correct. For multi-replica scale-out, shard cameras across replicas by
camera_id (e.g. a hash modulo replica count) so each camera is owned by exactly
one replica. See docs/steps/step5_consumer_groups.md for the sharding note.
Policy is ALL — never drop track frames feeding the FSM.
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
from activity.streambus.consumer import consume, BacklogPolicy, default_consumer_name

log = structlog.get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
STREAM_TRACKS = os.environ.get("REDIS_STREAM_TRACKS", "stream:tracks")
STREAM_EVENTS = os.environ.get("REDIS_STREAM_EVENTS", "stream:events")
GROUP = "activity-engine"


class CameraState:
    def __init__(self) -> None:
        self.fsms: dict[int, WorkerActivityFSM] = {}
        self.last_centroid: dict[int, tuple[float, float]] = {}


def _centroid(xyxy):
    x1, y1, x2, y2 = xyxy
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _make_handler(redis: Redis, camera_id: str, state: CameraState):
    async def handle(entry_id: bytes, fields: dict) -> bool:
        payload = json.loads(fields[b"json"])
        ts = float(payload.get("ts", time.time()))
        machine_map = payload.get("machine_running", {})  # workstation_id -> bool
        for tr in payload["tracks"]:
            tid = tr["track_id"]
            cx, cy = _centroid(tr["xyxy"])
            prev = state.last_centroid.get(tid)
            state.last_centroid[tid] = (cx, cy)
            motion = 0.0
            if prev is not None:
                dx, dy = cx - prev[0], cy - prev[1]
                motion = min(1.0, math.hypot(dx, dy) / 50.0)
            # is the machine at this worker's station running? (Step 8 fair signal)
            ws_id = tr.get("workstation_id")
            machine_running = bool(machine_map.get(ws_id, False)) if ws_id else False
            fsm = state.fsms.setdefault(tid, WorkerActivityFSM())
            prev_state = fsm.state
            new_state = fsm.update(ts, ActivitySignals(
                motion_magnitude=motion, machine_running=machine_running))
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
        return True
    return handle


async def process_camera(redis: Redis, key: str, stop: asyncio.Event) -> None:
    camera_id = key.rsplit(":", 1)[-1]
    state = CameraState()
    log.info("activity.attach", camera=camera_id, consumer=default_consumer_name())
    await consume(
        redis, key, GROUP, _make_handler(redis, camera_id, state),
        policy=BacklogPolicy.ALL,  # FSM needs every track frame
        block_ms=5_000, count=1, stop=stop,
    )


async def amain() -> None:
    redis = Redis.from_url(REDIS_URL)
    stop = asyncio.Event()
    tasks: dict[str, asyncio.Task] = {}
    while not stop.is_set():
        keys = []
        async for k in redis.scan_iter(match=f"{STREAM_TRACKS}:*"):
            keys.append(k.decode() if isinstance(k, bytes) else k)
        for k in keys:
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(
                    process_camera(redis, k, stop), name=f"act-{k}")
        await asyncio.sleep(15.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
