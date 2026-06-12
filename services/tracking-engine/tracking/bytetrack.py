"""Minimal ByteTrack-style tracker.

Self-contained implementation to avoid pulling in heavy YOLOX/lap deps in the
tracking container. For production accuracy, swap in `supervision.ByteTrack` or
the official YOLOX ByteTrack. The interface is intentionally identical.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from scipy.optimize import linear_sum_assignment


def _iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU matrix between boxes a [N,4] and b [M,4] in xyxy."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    ax1, ay1, ax2, ay2 = a[:, 0:1], a[:, 1:2], a[:, 2:3], a[:, 3:4]
    bx1, by1, bx2, by2 = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    inter = (np.minimum(ax2, bx2) - np.maximum(ax1, bx1)).clip(0) * \
            (np.minimum(ay2, by2) - np.maximum(ay1, by1)).clip(0)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter
    return inter / np.maximum(union, 1e-6)


@dataclass
class Track:
    id: int
    xyxy: tuple[float, float, float, float]
    cls: int
    conf: float
    age: int = 0
    misses: int = 0
    hits: int = 1
    history: list[tuple[float, float, float, float]] = field(default_factory=list)


class ByteTrack:
    def __init__(self, high_thresh: float = 0.5, low_thresh: float = 0.1,
                 iou_match_thresh: float = 0.3, max_age: int = 30) -> None:
        self.high_thresh = high_thresh
        self.low_thresh = low_thresh
        self.iou_thresh = iou_match_thresh
        self.max_age = max_age
        self.next_id = 1
        self.tracks: list[Track] = []

    def _assign(self, tracks: list[Track], det_boxes: np.ndarray) -> list[tuple[int, int]]:
        if not tracks or len(det_boxes) == 0:
            return []
        track_boxes = np.array([t.xyxy for t in tracks], dtype=np.float32)
        iou = _iou(track_boxes, det_boxes)
        cost = 1.0 - iou
        cost[iou < self.iou_thresh] = 1.0
        row, col = linear_sum_assignment(cost)
        return [(r, c) for r, c in zip(row, col) if iou[r, c] >= self.iou_thresh]

    def update(self, detections: Iterable[dict]) -> list[Track]:
        dets = list(detections)
        if not dets:
            for t in self.tracks:
                t.age += 1
                t.misses += 1
            self.tracks = [t for t in self.tracks if t.misses < self.max_age]
            return list(self.tracks)

        high = [d for d in dets if d["conf"] >= self.high_thresh]
        low = [d for d in dets if self.low_thresh <= d["conf"] < self.high_thresh]
        high_boxes = np.array([d["xyxy"] for d in high], dtype=np.float32)
        low_boxes = np.array([d["xyxy"] for d in low], dtype=np.float32)

        unmatched_tracks = list(range(len(self.tracks)))
        unmatched_high = list(range(len(high)))

        for r, c in self._assign(self.tracks, high_boxes):
            t = self.tracks[r]
            d = high[c]
            t.xyxy = tuple(d["xyxy"])  # type: ignore[arg-type]
            t.cls = d.get("cls", t.cls)
            t.conf = d["conf"]
            t.hits += 1
            t.misses = 0
            t.history.append(t.xyxy)
            t.history = t.history[-30:]
            unmatched_tracks.remove(r)
            unmatched_high.remove(c)

        # Second association: leftover tracks with low-confidence detections.
        leftover = [self.tracks[i] for i in unmatched_tracks]
        for r, c in self._assign(leftover, low_boxes):
            t = leftover[r]
            d = low[c]
            t.xyxy = tuple(d["xyxy"])  # type: ignore[arg-type]
            t.conf = d["conf"]
            t.hits += 1
            t.misses = 0

        # New tracks from unmatched high-conf detections.
        for i in unmatched_high:
            d = high[i]
            self.tracks.append(Track(
                id=self.next_id, xyxy=tuple(d["xyxy"]),  # type: ignore[arg-type]
                cls=d.get("cls", 0), conf=d["conf"],
                history=[tuple(d["xyxy"])],  # type: ignore[list-item]
            ))
            self.next_id += 1

        # Age out lost tracks.
        for t in self.tracks:
            t.age += 1
            if t.misses > 0:
                continue
        for t in self.tracks:
            if t not in [self.tracks[r] for r in []]:
                pass
        # Mark misses for any track not matched this frame.
        matched_ids = {t.id for t in self.tracks if t.misses == 0}
        for t in self.tracks:
            if t.id not in matched_ids:
                t.misses += 1
        self.tracks = [t for t in self.tracks if t.misses < self.max_age]
        return list(self.tracks)
