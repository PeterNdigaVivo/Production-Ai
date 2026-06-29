"""Adapter exposing supervision's ByteTrack behind our tracker interface.

Used ONLY as a reference oracle in the head-to-head benchmark — not a runtime
dependency of the project. supervision is mid-migration (sv.ByteTrack is
deprecated in favour of the separate `trackers` package), which is exactly why
we benchmark against it rather than adopt it: if our lean numpy/scipy tracker
matches it on our scenes, we keep the lean one and avoid the churn.

Requires: pip install supervision  (optional; skipped gracefully if absent)
"""
from __future__ import annotations
import numpy as np


class _Tr:
    __slots__ = ("id", "xyxy", "cls", "conf", "hits")
    def __init__(self, id, xyxy, cls, conf, hits=1):
        self.id = int(id); self.xyxy = tuple(float(v) for v in xyxy)
        self.cls = int(cls); self.conf = float(conf); self.hits = hits


class SupervisionByteTrack:
    """Wraps sv.ByteTrack; .update(list[dict]) -> list[_Tr]."""
    def __init__(self):
        import supervision as sv  # lazy; only when benchmarking
        self._sv = sv
        self._t = sv.ByteTrack()

    def update(self, detections):
        sv = self._sv
        dets = list(detections)
        if dets:
            xyxy = np.array([d["xyxy"] for d in dets], dtype=np.float32)
            conf = np.array([d["conf"] for d in dets], dtype=np.float32)
            cls = np.array([d.get("cls", 0) for d in dets], dtype=int)
        else:
            xyxy = np.empty((0, 4), np.float32)
            conf = np.empty((0,), np.float32)
            cls = np.empty((0,), int)
        d = sv.Detections(xyxy=xyxy, confidence=conf, class_id=cls)
        out = self._t.update_with_detections(d)
        res = []
        if out.tracker_id is not None:
            for i in range(len(out)):
                tid = out.tracker_id[i]
                if tid is None:
                    continue
                res.append(_Tr(tid, out.xyxy[i],
                               out.class_id[i] if out.class_id is not None else 0,
                               out.confidence[i] if out.confidence is not None else 0.0))
        return res
