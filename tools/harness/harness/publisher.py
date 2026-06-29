"""Publishes synthetic frames to Redis in the EXACT format the real ingestion
service uses, so the unmodified detection-engine can consume them.

Reference: services/video-ingestion/ingestion/rtsp_worker.py publishes to
`stream:frames:<camera_id>` with fields: camera_id, ts (struct-packed double),
w, h, jpg (bytes). We reproduce that byte-for-byte.

This module is OS-agnostic (pure Python + redis). It needs a running Redis,
which on Windows is easiest via Docker (`docker run -p 6379:6379 redis:7-alpine`)
but can be any reachable Redis.
"""
from __future__ import annotations

import struct
import time

from .render import render_frame, encode_jpeg


def publish_scene(scene, camera_id: str, redis_url: str = "redis://localhost:6379/0",
                  stream_prefix: str = "stream:frames", realtime: bool = True,
                  maxlen: int = 600, loop: bool = False, log=print) -> None:
    """Publish every frame of `scene` to stream:frames:<camera_id>.

    realtime=True paces frames to the scene fps (so timestamps and downstream
    debounce windows behave like production). realtime=False blasts as fast as
    possible -- handy for quick fills, not for timing-sensitive tests.
    """
    import redis as redis_lib

    r = redis_lib.Redis.from_url(redis_url)
    stream_key = f"{stream_prefix}:{camera_id}"
    times = scene.frame_times()
    log(f"[publisher] camera={camera_id} stream={stream_key} frames={len(times)} "
        f"realtime={realtime} loop={loop}")

    wall_start = time.time()
    pass_no = 0
    while True:
        pass_no += 1
        for i, t in enumerate(times):
            frame = render_frame(scene, t)
            jpg = encode_jpeg(frame)
            now = time.time()
            r.xadd(stream_key, {
                "camera_id": camera_id,
                "ts": struct.pack("d", now),
                "w": scene.width,
                "h": scene.height,
                "jpg": jpg,
            }, maxlen=maxlen, approximate=True)
            if realtime:
                target = wall_start + t + (pass_no - 1) * scene.duration_s
                sleep = target - time.time()
                if sleep > 0:
                    time.sleep(sleep)
            if i % int(max(1, scene.fps)) == 0:
                log(f"[publisher] t={t:5.1f}s frame={i} workers={len(scene.ground_truth_at(t))}")
        if not loop:
            break
    log("[publisher] done")
