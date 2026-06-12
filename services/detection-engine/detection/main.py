"""Detection engine entrypoint.

Subscribes to every `stream:frames:*` Redis Stream, decodes the JPEG, runs YOLO
inference, and publishes detections to `stream:detections:<camera_id>`.

Phase-1 single-process implementation. Horizontal scaling: run N replicas with
a Redis consumer group; each replica claims a disjoint set of cameras via
`XGROUP` / `XREADGROUP`.
"""
from __future__ import annotations
import asyncio
import json
import struct
import time

import cv2
import numpy as np
import structlog
from redis.asyncio import Redis

from detection.config import settings
from detection.detector import YoloDetector

log = structlog.get_logger(__name__)


async def discover_streams(redis: Redis) -> list[str]:
    # SCAN for stream:frames:* keys; cheap (<1ms) at hundreds of cameras.
    keys: list[str] = []
    async for k in redis.scan_iter(match=f"{settings.stream_frames}:*"):
        keys.append(k.decode() if isinstance(k, bytes) else k)
    return keys


async def process_stream(redis: Redis, detector: YoloDetector, key: str) -> None:
    camera_id = key.rsplit(":", 1)[-1]
    out_key = f"{settings.stream_detections}:{camera_id}"
    last_id = "$"
    log.info("detection.attach", camera=camera_id)
    while True:
        try:
            resp = await redis.xread({key: last_id}, block=5_000, count=1)
            if not resp:
                continue
            _stream, entries = resp[0]
            for entry_id, fields in entries:
                last_id = entry_id
                jpg = fields[b"jpg"]
                arr = np.frombuffer(jpg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                t0 = time.time()
                dets = detector.infer(frame)
                latency_ms = int((time.time() - t0) * 1000)
                payload = json.dumps({
                    "camera_id": camera_id,
                    "ts": struct.unpack("d", fields[b"ts"])[0] if b"ts" in fields else time.time(),
                    "latency_ms": latency_ms,
                    "detections": [
                        {"cls": d.cls, "conf": d.conf, "xyxy": list(d.xyxy)} for d in dets
                    ],
                })
                await redis.xadd(out_key, {"json": payload}, maxlen=600, approximate=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("detection.error", camera=camera_id, error=str(e))
            await asyncio.sleep(1.0)


async def amain() -> None:
    redis = Redis.from_url(settings.redis_url)
    detector = YoloDetector(
        model_path=settings.model_path,
        device=settings.device,
        half=settings.half,
        conf=settings.conf,
        iou=settings.iou,
        imgsz=settings.imgsz,
        classes=settings.classes_of_interest,
    )

    tasks: dict[str, asyncio.Task] = {}
    while True:
        keys = await discover_streams(redis)
        for k in keys:
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(process_stream(redis, detector, k), name=f"det-{k}")
        await asyncio.sleep(15.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
