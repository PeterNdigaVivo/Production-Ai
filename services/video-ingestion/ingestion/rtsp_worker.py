"""Single-camera RTSP ingestion worker.

Uses FFmpeg as a subprocess to decode the RTSP stream into raw frames (rawvideo
BGR24). Frames are downsampled to `target_fps`, JPEG-encoded, and published to a
per-camera Redis Stream.

Why FFmpeg subprocess rather than OpenCV's VideoCapture:
  * Reliable reconnects with `-rtsp_transport tcp`.
  * Hardware-accelerated decode (NVDEC) via `-hwaccel cuda`.
  * Lower latency, no Python GIL contention on decode.
"""
from __future__ import annotations
import asyncio
import shutil
import struct
import time
import uuid
from contextlib import suppress

import cv2
import httpx
import numpy as np
from redis.asyncio import Redis
import structlog

from ingestion.config import settings

log = structlog.get_logger(__name__)

# Standard NVR feed: 1920x1080 from Hikvision substream 102 is more common
# (704x576). The worker auto-detects via ffprobe-style header; we default here.
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720


def _build_ffmpeg_cmd(rtsp_url: str, width: int, height: int) -> list[str]:
    hw = settings.hw_accel.lower()
    cmd = ["ffmpeg", "-loglevel", "warning", "-nostdin"]
    if hw == "cuda":
        cmd += ["-hwaccel", "cuda"]
    elif hw == "qsv":
        cmd += ["-hwaccel", "qsv"]
    elif hw == "auto":
        # Try CUDA first; FFmpeg falls back transparently to software.
        cmd += ["-hwaccel", "auto"]
    cmd += [
        "-rtsp_transport", settings.transport,
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", rtsp_url,
        "-vf", f"scale={width}:{height},fps={settings.target_fps}",
        "-an",  # no audio
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "pipe:1",
    ]
    return cmd


class RTSPWorker:
    def __init__(self, camera_id: uuid.UUID, rtsp_url: str, redis: Redis,
                 width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.redis = redis
        self.width = width
        self.height = height
        self.frame_size = width * height * 3
        self.stream_key = f"{settings.stream_frames}:{camera_id}"
        self._stop = asyncio.Event()
        self._heartbeat_client = httpx.AsyncClient(
            timeout=5.0,
            headers={"X-Internal-Token": settings.internal_service_token or ""},
        )

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if shutil.which("ffmpeg") is None:
            log.error("ingest.ffmpeg_missing")
            return
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = 1.0
            except Exception as e:
                log.warning("ingest.error", camera_id=str(self.camera_id), error=str(e))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _run_once(self) -> None:
        cmd = _build_ffmpeg_cmd(self.rtsp_url, self.width, self.height)
        log.info("ingest.start", camera_id=str(self.camera_id), cmd=" ".join(cmd[:6]) + " ...")

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None

        last_heartbeat = 0.0
        try:
            while not self._stop.is_set():
                buf = await proc.stdout.readexactly(self.frame_size)
                frame = np.frombuffer(buf, dtype=np.uint8).reshape((self.height, self.width, 3))
                ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok or jpg.nbytes > settings.max_frame_bytes:
                    continue
                await self.redis.xadd(
                    self.stream_key,
                    {
                        "camera_id": str(self.camera_id),
                        "ts": struct.pack("d", time.time()),
                        "w": self.width,
                        "h": self.height,
                        "jpg": jpg.tobytes(),
                    },
                    maxlen=settings.stream_maxlen,
                    approximate=True,
                )
                now = time.time()
                if now - last_heartbeat > 10.0:
                    asyncio.create_task(self._heartbeat())
                    last_heartbeat = now
        finally:
            with suppress(ProcessLookupError):
                proc.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5.0)

    async def _heartbeat(self) -> None:
        with suppress(Exception):
            await self._heartbeat_client.post(
                f"{settings.backend_url}/api/v1/cameras/{self.camera_id}/_internal/heartbeat"
            )
