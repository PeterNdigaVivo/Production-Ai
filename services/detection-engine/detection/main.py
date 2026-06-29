"""Detection engine entrypoint.

Subscribes to every `stream:frames:*` Redis Stream via a per-stream CONSUMER
GROUP (crash-safe, horizontally scalable), decodes the JPEG, runs YOLO
inference, and publishes detections to `stream:detections:<camera_id>`.

Scaling: run N replicas. Each joins the same group per stream with a distinct
consumer name (hostname), so frames are split across replicas instead of every
replica processing every frame. On restart, a replica first recovers any frames
it had in flight.

Live-video policy: NEWEST — if inference falls behind, process the freshest
frame and skip the stale backlog rather than accumulating latency.

Step 8 additions (the data flywheel + fair activity):
  * CAPTURE: flagged-frame capture for active learning. Disabled by default
    (CAPTURE_ENABLED=true to turn on). When on, frames with ambiguous-confidence
    detections (etc.) are saved to the labeling queue. This is what turns the
    live deployment into a training-data collector without recording everything.
  * MACHINE-FLOW: optical-flow "is the sewing machine running" per machine zone.
    Detection is the only engine with the raw pixels, so it computes the signal
    and publishes a per-workstation `machine_running` map alongside detections;
    the activity FSM downstream consumes it to make WORKING/IDLE fair. Requires
    the machine-zone polygons (pulled from the backend, same source the tracking
    engine uses). Enabled with MACHINE_FLOW_ENABLED=true.
"""
from __future__ import annotations
import asyncio
import json
import os
import struct
import time

import cv2
import numpy as np
import structlog
from redis.asyncio import Redis

from detection.config import settings
from detection.detector import YoloDetector
from detection.streambus.consumer import consume, BacklogPolicy, default_consumer_name
from detection.capture.writer import CaptureWriter, CaptureConfig
from detection.machine_flow import MachineRunningDetector, MachineFlowConfig

log = structlog.get_logger(__name__)

GROUP = "detection-engine"

CAPTURE_ENABLED = os.environ.get("CAPTURE_ENABLED", "false").lower() == "true"
CAPTURE_DIR = os.environ.get("CAPTURE_DIR", "/data/capture")
MACHINE_FLOW_ENABLED = os.environ.get("MACHINE_FLOW_ENABLED", "false").lower() == "true"


async def discover_streams(redis: Redis) -> list[str]:
    keys: list[str] = []
    async for k in redis.scan_iter(match=f"{settings.stream_frames}:*"):
        keys.append(k.decode() if isinstance(k, bytes) else k)
    return keys


def _make_handler(redis: Redis, detector: YoloDetector, camera_id: str, out_key: str,
                  capture: CaptureWriter | None, machine_det: MachineRunningDetector | None,
                  zone_provider):
    # rolling mean of detection count per camera, for capture anomaly detection
    state = {"rolling_count": None, "last_state_change": 0.0}

    async def handle(entry_id: bytes, fields: dict) -> bool:
        jpg_raw = fields.get(b"jpg")
        if jpg_raw is None:
            return True
        arr = np.frombuffer(jpg_raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return True
        ts = struct.unpack("d", fields[b"ts"])[0] if b"ts" in fields else time.time()

        t0 = time.time()
        dets = detector.infer(frame)
        latency_ms = int((time.time() - t0) * 1000)
        det_dicts = [{"cls": d.cls, "conf": d.conf, "xyxy": list(d.xyxy)} for d in dets]

        # --- machine-running per machine zone (optional) ---
        machine_running: dict[str, bool] = {}
        if machine_det is not None and zone_provider is not None:
            zones = await zone_provider(camera_id)  # list[(ws_id, kind, polygon_pts)]
            for ws_id, kind, polygon in zones:
                if kind != "machine":
                    continue
                running, _flow = machine_det.update(f"{camera_id}:{ws_id}", frame, polygon)
                machine_running[ws_id] = running

        payload = {
            "camera_id": camera_id,
            "ts": ts,
            "latency_ms": latency_ms,
            "detections": det_dicts,
        }
        if machine_running:
            payload["machine_running"] = machine_running
        await redis.xadd(out_key, {"json": json.dumps(payload)}, maxlen=600, approximate=True)

        # --- capture (optional, the data flywheel) ---
        if capture is not None:
            rc = state["rolling_count"]
            n = len(det_dicts)
            state["rolling_count"] = n if rc is None else (0.9 * rc + 0.1 * n)
            reason = capture.assess(
                detections=det_dicts,
                state_changed_recently=False,   # detection has no FSM; left for future cycle hints
                rolling_mean_count=state["rolling_count"],
            )
            if reason.any():
                # re-encode the (already decoded) frame to jpg for storage
                ok, buf = cv2.imencode(".jpg", frame,
                                       [cv2.IMWRITE_JPEG_QUALITY, capture.cfg.jpeg_quality])
                if ok:
                    capture.maybe_capture(camera_id, buf.tobytes(), ts, det_dicts, reason)
        return True
    return handle


async def process_stream(redis, detector, key, stop, capture, machine_det, zone_provider):
    camera_id = key.rsplit(":", 1)[-1]
    out_key = f"{settings.stream_detections}:{camera_id}"
    log.info("detection.attach", camera=camera_id, consumer=default_consumer_name(),
             capture=bool(capture), machine_flow=bool(machine_det))
    await consume(
        redis, key, GROUP,
        _make_handler(redis, detector, camera_id, out_key, capture, machine_det, zone_provider),
        policy=BacklogPolicy.NEWEST,
        block_ms=5_000, count=1, stop=stop,
    )


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

    capture = None
    if CAPTURE_ENABLED:
        capture = CaptureWriter(CaptureConfig(enabled=True, out_dir=CAPTURE_DIR), redis=None)
        log.info("detection.capture_enabled", out_dir=CAPTURE_DIR)

    machine_det = None
    zone_provider = None
    if MACHINE_FLOW_ENABLED:
        machine_det = MachineRunningDetector(MachineFlowConfig())
        # lazy import to avoid hard dep when disabled
        from detection.zone_provider import make_zone_provider
        zone_provider = make_zone_provider()
        log.info("detection.machine_flow_enabled")

    stop = asyncio.Event()
    tasks: dict[str, asyncio.Task] = {}
    while not stop.is_set():
        keys = await discover_streams(redis)
        for k in keys:
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(
                    process_stream(redis, detector, k, stop, capture, machine_det, zone_provider),
                    name=f"det-{k}")
        await asyncio.sleep(15.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
