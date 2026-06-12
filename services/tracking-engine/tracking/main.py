"""Tracking engine entrypoint.

Subscribes to `stream:detections:*`, runs ByteTrack per camera, assigns each
track to a workstation via polygon zones, and publishes:
  * `stream:tracks:<camera_id>`  — per-frame tracks with workstation assignment
  * `stream:events`              — `worker_detected` events for downstream consumers
"""
from __future__ import annotations
import asyncio
import json
import os
import time

import structlog
from redis.asyncio import Redis

from tracking.bytetrack import ByteTrack
from tracking.zones import ZoneCache, assign_workstation

log = structlog.get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")
STREAM_DETECTIONS = os.environ.get("REDIS_STREAM_DETECTIONS", "stream:detections")
STREAM_TRACKS = os.environ.get("REDIS_STREAM_TRACKS", "stream:tracks")
STREAM_EVENTS = os.environ.get("REDIS_STREAM_EVENTS", "stream:events")


async def process_camera(redis: Redis, zones: ZoneCache, key: str) -> None:
    camera_id = key.rsplit(":", 1)[-1]
    out_tracks = f"{STREAM_TRACKS}:{camera_id}"
    tracker = ByteTrack()
    last_id = "$"
    log.info("tracker.attach", camera=camera_id)
    while True:
        try:
            resp = await redis.xread({key: last_id}, block=5_000, count=1)
            if not resp:
                continue
            _stream, entries = resp[0]
            for entry_id, fields in entries:
                last_id = entry_id
                payload = json.loads(fields[b"json"])
                # Only person class (COCO 0) for tracking.
                persons = [d for d in payload["detections"] if d["cls"] == 0]
                tracks = tracker.update(persons)
                cam_zones = await zones.get(camera_id)
                track_records = []
                for t in tracks:
                    ws_id = assign_workstation(cam_zones, t.xyxy)
                    track_records.append({
                        "track_id": t.id, "xyxy": list(t.xyxy),
                        "conf": t.conf, "workstation_id": ws_id, "hits": t.hits,
                    })
                    await redis.xadd(STREAM_EVENTS, {
                        "type": "worker_detected",
                        "ts": str(payload.get("ts", time.time())),
                        "camera_id": camera_id,
                        "workstation_id": ws_id or "",
                        "track_id": str(t.id),
                        "conf": str(t.conf),
                    }, maxlen=10_000, approximate=True)
                await redis.xadd(out_tracks, {"json": json.dumps({
                    "camera_id": camera_id,
                    "ts": payload.get("ts", time.time()),
                    "tracks": track_records,
                })}, maxlen=600, approximate=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("tracker.error", camera=camera_id, error=str(e))
            await asyncio.sleep(1.0)


async def amain() -> None:
    redis = Redis.from_url(REDIS_URL)
    zones = ZoneCache(BACKEND_URL)
    tasks: dict[str, asyncio.Task] = {}
    while True:
        keys = []
        async for k in redis.scan_iter(match=f"{STREAM_DETECTIONS}:*"):
            keys.append(k.decode() if isinstance(k, bytes) else k)
        for k in keys:
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(process_camera(redis, zones, k), name=f"trk-{k}")
        await asyncio.sleep(15.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
