"""Machine-running detector via optical flow over the machine zone.

This is classical computer vision -- NO training required -- so it works on day
one, before any custom models exist. It answers: "is the sewing machine at this
workstation actually running right now?"

How: within the workstation's `machine` zone polygon, compute dense optical-flow
magnitude between consecutive frames. A running sewing machine produces sustained
high-frequency motion (needle bar, fabric feed) localised to the machine head;
an idle machine produces near-zero flow there. We threshold the mean flow
magnitude inside the zone.

Why this matters for fairness: the Phase-1 FSM guessed activity from whole-body
bbox motion, so a worker sewing with a still torso read as IDLE. Feeding a real
`machine_running` signal into the FSM (which already has the slot) makes
WORKING vs IDLE defensible -- you are sewing if the machine is running, even if
your body is still.

CALIBRATION: the flow threshold depends on resolution, fps, and the machine. The
default below is a sane starting point; `calibrate_threshold()` derives a
per-camera value from a short clip of known running/idle segments (this is what
the calibration footage is for). Until calibrated, the default is conservative.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class MachineFlowConfig:
    flow_threshold: float = 1.2      # mean flow magnitude (px) above which "running"
    min_zone_area_px: int = 200      # ignore absurdly small zones
    downscale: float = 0.5           # speed: compute flow at half-res inside the zone


class MachineRunningDetector:
    """Per-workstation optical-flow machine-running detector.

    Keeps the previous grayscale crop per workstation so it can compute flow
    between consecutive frames. Call `update(workstation_id, frame, polygon)`
    each frame; returns (is_running, mean_flow).
    """

    def __init__(self, config: MachineFlowConfig | None = None) -> None:
        self.cfg = config or MachineFlowConfig()
        self._prev_gray: dict[str, np.ndarray] = {}

    @staticmethod
    def _zone_mask_bbox(polygon, w, h):
        pts = np.array(polygon, dtype=np.int32)
        x1, y1 = pts[:, 0].min(), pts[:, 1].min()
        x2, y2 = pts[:, 0].max(), pts[:, 1].max()
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        return x1, y1, x2, y2, pts

    def update(self, workstation_id: str, frame_bgr: np.ndarray,
               machine_polygon) -> tuple[bool, float]:
        h, w = frame_bgr.shape[:2]
        x1, y1, x2, y2, pts = self._zone_mask_bbox(machine_polygon, w, h)
        if (x2 - x1) * (y2 - y1) < self.cfg.min_zone_area_px:
            return False, 0.0

        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return False, 0.0
        if self.cfg.downscale != 1.0:
            crop = cv2.resize(crop, None, fx=self.cfg.downscale, fy=self.cfg.downscale)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        prev = self._prev_gray.get(workstation_id)
        self._prev_gray[workstation_id] = gray
        if prev is None or prev.shape != gray.shape:
            return False, 0.0  # need two frames

        flow = cv2.calcOpticalFlowFarneback(
            prev, gray, None,
            pyr_scale=0.5, levels=2, winsize=15,
            iterations=2, poly_n=5, poly_sigma=1.1, flags=0,
        )
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        mean_flow = float(mag.mean())
        return mean_flow >= self.cfg.flow_threshold, mean_flow

    def reset(self, workstation_id: str | None = None) -> None:
        if workstation_id is None:
            self._prev_gray.clear()
        else:
            self._prev_gray.pop(workstation_id, None)


def calibrate_threshold(running_flows: list[float], idle_flows: list[float]) -> float:
    """Derive a flow threshold from labelled running/idle samples.

    Picks the midpoint between the idle distribution's high end and the running
    distribution's low end (a simple, robust separator). Feed this the mean-flow
    values produced by update() over a calibration clip where you know which
    segments were running vs idle. Returns a threshold; falls back to the default
    midpoint if the classes overlap.
    """
    if not running_flows or not idle_flows:
        return MachineFlowConfig().flow_threshold
    idle_hi = np.percentile(idle_flows, 90)
    run_lo = np.percentile(running_flows, 10)
    if run_lo <= idle_hi:
        # classes overlap; use the mean of medians as a best-effort separator
        return float((np.median(running_flows) + np.median(idle_flows)) / 2.0)
    return float((idle_hi + run_lo) / 2.0)
