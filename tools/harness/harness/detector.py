"""Detector for the harness: a stub by default, real YOLO when asked.

Mode is chosen by the HARNESS_DETECTOR env var (or the `mode` arg):

  * "stub"  (default): emits the scene's known ground-truth boxes, optionally
    with controllable noise/dropout. Zero ML, runs anywhere, instant. This is
    what isolates tracker/analytics bugs from detection behaviour.

  * "yolo": runs a real Ultralytics YOLO model on the rendered frame (CPU is
    fine for the harness). Use this only when you specifically want to exercise
    detection itself; it is slower and adds a second variable.

The output format -- a list of {"cls","conf","xyxy"} dicts -- matches exactly
what the real detection-engine publishes, so anything consuming it (the tracker)
cannot tell the harness from production.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Literal

import numpy as np

DetectorMode = Literal["stub", "yolo"]
PERSON_CLASS = 0  # COCO person, matches the real pipeline


@dataclass
class StubNoise:
    """Knobs to make the stub detector imperfect, the way a real one is."""
    jitter_px: float = 0.0       # random +/- px added to each box edge
    dropout_p: float = 0.0       # probability a visible box is missed this frame
    false_positive_p: float = 0.0  # probability of an extra spurious box
    conf_jitter: float = 0.0     # random +/- added to confidence
    seed: int | None = 42

    def __post_init__(self):
        self._rng = random.Random(self.seed)


class StubDetector:
    """Emits the scene's ground-truth boxes (optionally perturbed)."""

    def __init__(self, scene, noise: StubNoise | None = None):
        self.scene = scene
        self.noise = noise or StubNoise()

    def infer_at(self, t: float) -> list[dict]:
        rng = self.noise._rng
        out: list[dict] = []
        for _wid, xyxy, conf in self.scene.ground_truth_at(t):
            if self.noise.dropout_p and rng.random() < self.noise.dropout_p:
                continue
            x1, y1, x2, y2 = xyxy
            if self.noise.jitter_px:
                j = self.noise.jitter_px
                x1 += rng.uniform(-j, j); y1 += rng.uniform(-j, j)
                x2 += rng.uniform(-j, j); y2 += rng.uniform(-j, j)
            c = conf
            if self.noise.conf_jitter:
                c = max(0.01, min(0.99, c + rng.uniform(-self.noise.conf_jitter, self.noise.conf_jitter)))
            out.append({"cls": PERSON_CLASS, "conf": round(c, 3),
                        "xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]})
        if self.noise.false_positive_p and rng.random() < self.noise.false_positive_p:
            w, h = self.scene.width, self.scene.height
            fx, fy = rng.uniform(0, w - 60), rng.uniform(0, h - 120)
            out.append({"cls": PERSON_CLASS, "conf": 0.55,
                        "xyxy": [fx, fy, fx + 60, fy + 120]})
        return out


class YoloDetector:
    """Real Ultralytics YOLO over a rendered BGR frame. CPU by default."""

    def __init__(self, model_path: str | None = None, device: str | None = None,
                 conf: float = 0.35, iou: float = 0.5, imgsz: int = 640):
        from ultralytics import YOLO  # imported lazily; only needed in yolo mode
        self.model = YOLO(model_path or os.environ.get("YOLO_MODEL_PATH", "yolo11n.pt"))
        self.device = device or os.environ.get("YOLO_DEVICE", "cpu")
        self.conf, self.iou, self.imgsz = conf, iou, imgsz

    def infer_frame(self, frame_bgr: np.ndarray) -> list[dict]:
        res = self.model.predict(frame_bgr, device=self.device, conf=self.conf,
                                 iou=self.iou, imgsz=self.imgsz,
                                 classes=[PERSON_CLASS], verbose=False)
        out: list[dict] = []
        if res and res[0].boxes is not None:
            for box in res[0].boxes:
                out.append({
                    "cls": int(box.cls.item()),
                    "conf": float(box.conf.item()),
                    "xyxy": [float(v) for v in box.xyxy[0].tolist()],
                })
        return out


def make_detector(scene, mode: DetectorMode | None = None, noise: StubNoise | None = None):
    """Factory honouring HARNESS_DETECTOR; defaults to the stub."""
    mode = mode or os.environ.get("HARNESS_DETECTOR", "stub")  # type: ignore[assignment]
    if mode == "yolo":
        return YoloDetector()
    return StubDetector(scene, noise=noise)
