"""Tracking engine entrypoint.

Subscribes to `stream:detections:*` via a per-stream CONSUMER GROUP, runs
ByteTrack per camera, assigns each track to a workstation via polygon zones, and
publishes:
  * `stream:tracks:<camera_id>`  — per-frame tracks with workstation assignment
  * `stream:events`              — `worker_detected` events for downstream consumers

Scaling/crash-safety: same consumer-group model as the detection engine. Policy
is ALL here — we never want to drop detections, since tracking needs every frame
to maintain identities.
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
from tracking.streambus.consumer import consume, BacklogPolicy, default_consumer_name

log = structlog.get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")
STREAM_DETECTIONS = os.environ.get("REDIS_STREAM_DETECTIONS", "stream:detections")
STREAM_TRACKS = os.environ.get("REDIS_STREAM_TRACKS", "stream:tracks")
STREAM_EVENTS = os.environ.get("REDIS_STREAM_EVENTS", "stream:events")
GROUP = "tracking-engine"


# Publish-boundary sanity filter for track boxes. The specific tracker is a
# config switch (TRACKER=bytetrack|botsort), and three services consume
# stream:tracks, so the geometric guarantee belongs here rather than only
# inside one tracker implementation. Drops boxes that are degenerate, tiny,
# or entirely outside the frame; clamps survivors to frame bounds.
_MIN_W = 8      # <8px wide at 1280px is measurement noise, not a person
_MIN_H = 16     # <16px tall likewise


def _valid_and_clamp(xyxy, frame_w: int, frame_h: int):
    """Return a clamped-to-frame xyxy tuple, or None if the box is not sane."""
    x1, y1, x2, y2 = xyxy
    if not (x2 > x1 and y2 > y1):
        return None
    if (x2 - x1) < _MIN_W or (y2 - y1) < _MIN_H:
        return None
    if frame_w > 0 and frame_h > 0:
        # box must intersect the frame at all
        if not (x2 > 0 and y2 > 0 and x1 < frame_w and y1 < frame_h):
            return None
        return (
            max(0.0, min(float(frame_w), x1)),
            max(0.0, min(float(frame_h), y1)),
            max(0.0, min(float(frame_w), x2)),
            max(0.0, min(float(frame_h), y2)),
        )
    # Frame dims unknown — keep non-degenerate boxes without clamping.
    return (x1, y1, x2, y2)


def _make_handler(redis: Redis, zones: ZoneCache, camera_id: str, out_tracks: str,
                  tracker: ByteTrack):
    # Per-camera drop counter; log at debug in batches so a real leak is
    # visible without per-drop log spam at 4fps.
    state = {"dropped_total": 0, "last_logged": 0}

    async def handle(entry_id: bytes, fields: dict) -> bool:
        payload = json.loads(fields[b"json"])
        persons = [d for d in payload["detections"] if d["cls"] == 0]
        tracks = tracker.update(persons)
        cam_zones = await zones.get(camera_id)
        frame_w = int(payload.get("w") or 0)
        frame_h = int(payload.get("h") or 0)

        track_records = []
        for t in tracks:
            clamped = _valid_and_clamp(t.xyxy, frame_w, frame_h)
            if clamped is None:
                state["dropped_total"] += 1
                continue
            ws_id = assign_workstation(cam_zones, clamped)
            track_records.append({
                "track_id": t.id, "xyxy": list(clamped),
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

        # Periodic drop-counter log (~every 100 dropped boxes) — never per drop.
        if state["dropped_total"] - state["last_logged"] >= 100:
            log.debug("tracker.filter.dropped_total",
                      camera=camera_id, n=state["dropped_total"])
            state["last_logged"] = state["dropped_total"]

        await redis.xadd(out_tracks, {"json": json.dumps({
            "camera_id": camera_id,
            "ts": payload.get("ts", time.time()),
            "tracks": track_records,
            # forward the per-workstation machine-running map (Step 8) so the
            # activity FSM downstream can make WORKING/IDLE fair. Absent if the
            # detection engine isn't computing it (MACHINE_FLOW_ENABLED=false).
            "machine_running": payload.get("machine_running", {}),
        })}, maxlen=600, approximate=True)
        return True
    return handle


async def process_camera(redis: Redis, zones: ZoneCache, key: str,
                         stop: asyncio.Event) -> None:
    camera_id = key.rsplit(":", 1)[-1]
    out_tracks = f"{STREAM_TRACKS}:{camera_id}"
    tracker = ByteTrack()
    log.info("tracker.attach", camera=camera_id, consumer=default_consumer_name())
    await consume(
        redis, key, GROUP,
        _make_handler(redis, zones, camera_id, out_tracks, tracker),
        policy=BacklogPolicy.ALL,  # never drop detections
        block_ms=5_000, count=1, stop=stop,
    )


async def amain() -> None:
    redis = Redis.from_url(REDIS_URL)
    zones = ZoneCache(BACKEND_URL)
    stop = asyncio.Event()
    tasks: dict[str, asyncio.Task] = {}
    while not stop.is_set():
        keys = []
        async for k in redis.scan_iter(match=f"{STREAM_DETECTIONS}:*"):
            keys.append(k.decode() if isinstance(k, bytes) else k)
        for k in keys:
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(
                    process_camera(redis, zones, k, stop), name=f"trk-{k}")
        await asyncio.sleep(15.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
