"""YOLOv11 detector wrapper.

Loads the model once, exposes `infer(frame_bgr) -> list[Detection]`. TensorRT
export is performed lazily on first use when CUDA is available and the model
file is a `.pt`; the cached `.engine` is reused on subsequent runs.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import structlog

log = structlog.get_logger(__name__)


@dataclass
class Detection:
    cls: int
    conf: float
    xyxy: tuple[float, float, float, float]


class YoloDetector:
    def __init__(self, model_path: str, device: str = "cuda:0",
                 half: bool = True, conf: float = 0.35, iou: float = 0.5,
                 imgsz: int = 640, classes: list[int] | None = None) -> None:
        from ultralytics import YOLO  # imported lazily so non-GPU tests stay light
        self.device = device
        self.half = half and device.startswith("cuda")
        self.conf = conf
        self.iou = iou
        self.imgsz = imgsz
        self.classes = classes

        engine_path = self._maybe_export_engine(model_path)
        self.model = YOLO(engine_path or model_path)
        log.info("detector.loaded", model=engine_path or model_path, device=device)

    def _maybe_export_engine(self, model_path: str) -> str | None:
        if not model_path.endswith(".pt") or not self.device.startswith("cuda"):
            return None
        engine_path = str(Path(model_path).with_suffix(".engine"))
        if os.path.exists(engine_path):
            return engine_path
        try:
            from ultralytics import YOLO
            log.info("detector.exporting_tensorrt", source=model_path)
            tmp = YOLO(model_path)
            exported = tmp.export(format="engine", half=self.half, imgsz=self.imgsz, device=self.device)
            log.info("detector.exported", engine=str(exported))
            return str(exported)
        except Exception as e:  # pragma: no cover
            log.warning("detector.tensorrt_export_failed", error=str(e))
            return None

    def infer(self, frame_bgr: np.ndarray) -> list[Detection]:
        results = self.model.predict(
            frame_bgr,
            device=self.device,
            half=self.half,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.imgsz,
            classes=self.classes,
            verbose=False,
        )
        out: list[Detection] = []
        if not results:
            return out
        r = results[0]
        if r.boxes is None:
            return out
        for box in r.boxes:
            cls = int(box.cls.item())
            conf = float(box.conf.item())
            xyxy = tuple(float(v) for v in box.xyxy[0].tolist())
            out.append(Detection(cls=cls, conf=conf, xyxy=xyxy))  # type: ignore[arg-type]
        return out
