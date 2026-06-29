"""Active-learning capture pipeline -- the data flywheel.

While the system runs in the factory it saves the frames it is LEAST sure about
into a labeling queue, with metadata. This turns the live deployment into a
data-collection engine: instead of hand-labeling hundreds of raw hours, you get
a curated set of exactly the hard cases the models need, ready to label and
train on (see ml/labeling/ and ml/training/).

What counts as "uncertain" (any of these triggers a capture, rate-limited):
  * a detection whose confidence is in an ambiguous band (near the threshold)
  * an activity state that flipped recently (FSM instability)
  * a frame with an unusual number of detections vs the rolling norm
  * an explicit "cycle boundary candidate" hint from a future cycle detector

Captured artefact = the JPEG + a JSON sidecar (camera, ts, reason, detections,
state). Frames are written under a capture dir and also indexed in Redis so a
labeling UI / export job can pull a worklist. Privacy-conscious: capture is
OFF by default and must be enabled explicitly; only flagged frames are stored,
never a continuous recording.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class CaptureConfig:
    enabled: bool = False                       # OFF by default (privacy)
    out_dir: str = "/data/capture"
    conf_low: float = 0.30                      # ambiguous-confidence band lower
    conf_high: float = 0.55                     # ambiguous-confidence band upper
    per_camera_min_interval_s: float = 5.0      # rate-limit captures per camera
    max_per_hour_per_camera: int = 120          # hard cap to bound disk use
    jpeg_quality: int = 85


@dataclass
class CaptureReason:
    ambiguous_confidence: bool = False
    unstable_state: bool = False
    detection_count_anomaly: bool = False
    cycle_boundary_candidate: bool = False

    def any(self) -> bool:
        return any(asdict(self).values())

    def labels(self) -> list[str]:
        return [k for k, v in asdict(self).items() if v]


class CaptureWriter:
    """Writes flagged frames + sidecars, with per-camera rate limiting.

    Storage is filesystem (jpg + json). If a Redis client is provided, each
    capture id is pushed to a per-camera list so a labeling/export job can pull a
    worklist without scanning the disk.
    """

    def __init__(self, config: CaptureConfig | None = None, redis=None) -> None:
        self.cfg = config or CaptureConfig()
        self.redis = redis
        self._last_capture: dict[str, float] = {}
        self._hour_bucket: dict[str, tuple[int, int]] = {}  # camera -> (hour, count)
        if self.cfg.enabled:
            Path(self.cfg.out_dir).mkdir(parents=True, exist_ok=True)

    # ---- decision: is this frame worth capturing? --------------------------- #
    def assess(self, detections: list[dict], state_changed_recently: bool,
               rolling_mean_count: float | None,
               cycle_boundary: bool = False) -> CaptureReason:
        reason = CaptureReason()
        for d in detections:
            c = d.get("conf", 1.0)
            if self.cfg.conf_low <= c <= self.cfg.conf_high:
                reason.ambiguous_confidence = True
                break
        if state_changed_recently:
            reason.unstable_state = True
        if rolling_mean_count is not None and rolling_mean_count > 0:
            if abs(len(detections) - rolling_mean_count) >= max(2.0, rolling_mean_count):
                reason.detection_count_anomaly = True
        if cycle_boundary:
            reason.cycle_boundary_candidate = True
        return reason

    def _rate_ok(self, camera_id: str, now: float) -> bool:
        last = self._last_capture.get(camera_id, 0.0)
        if now - last < self.cfg.per_camera_min_interval_s:
            return False
        hour = int(now // 3600)
        h, count = self._hour_bucket.get(camera_id, (hour, 0))
        if h != hour:
            h, count = hour, 0
        if count >= self.cfg.max_per_hour_per_camera:
            return False
        self._hour_bucket[camera_id] = (h, count + 1)
        self._last_capture[camera_id] = now
        return True

    # ---- write ------------------------------------------------------------- #
    def maybe_capture(self, camera_id: str, jpg_bytes: bytes, ts: float,
                      detections: list[dict], reason: CaptureReason,
                      extra: dict | None = None) -> str | None:
        """Capture this frame if enabled, flagged, and within rate limits.
        Returns the capture id, or None if skipped."""
        if not self.cfg.enabled or not reason.any():
            return None
        now = time.time()
        if not self._rate_ok(camera_id, now):
            return None

        cap_id = uuid.uuid4().hex
        day = time.strftime("%Y%m%d", time.gmtime(ts))
        cam_dir = Path(self.cfg.out_dir) / day / camera_id
        cam_dir.mkdir(parents=True, exist_ok=True)
        jpg_path = cam_dir / f"{cap_id}.jpg"
        meta_path = cam_dir / f"{cap_id}.json"

        jpg_path.write_bytes(jpg_bytes)
        meta = {
            "capture_id": cap_id,
            "camera_id": camera_id,
            "ts": ts,
            "reasons": reason.labels(),
            "detections": detections,
            "labeled": False,         # set True once a human labels it
            "extra": extra or {},
            "jpg": str(jpg_path),
        }
        meta_path.write_text(json.dumps(meta))

        if self.redis is not None:
            try:
                self.redis.rpush(f"capture:queue:{camera_id}", cap_id)
            except Exception:
                pass  # filesystem is the source of truth; queue is a convenience
        return cap_id
