"""ByteTrack-style tracker — fixed and hardened.

Drop-in replacement for the original tracking/bytetrack.py. The public interface
is unchanged: `ByteTrack().update(detections) -> list[Track]`, where each Track
exposes `.id`, `.xyxy`, `.cls`, `.conf`, `.hits` (everything the tracking-engine
and the harness read).

What changed vs the original, and why:

  1. CORRECT AGING (Finding 1). The original inferred "matched" from misses==0
     *after* mutating misses, so unmatched tracks never aged out and IDs were
     immortal. Here every surviving track is marked missed at the top of the
     frame and matching clears it — the standard, unambiguous pattern.

  2. KALMAN PREDICTION. The original matched this frame's detections against the
     *previous* frame's raw boxes. Any real motion or a dropped frame breaks the
     IoU overlap and causes ID switches. We add a lightweight constant-velocity
     Kalman filter per track (state = cx, cy, aspect, height + velocities) and
     match against the *predicted* box. This is what real ByteTrack/SORT do and
     is the single biggest accuracy win.

  3. TWO-STAGE ASSOCIATION kept (high-confidence first, then low-confidence
     against leftovers) — the actual "BYTE" idea — but cleaned up so the second
     stage can also reset misses and the dead no-op loops are gone.

Dependencies: numpy + scipy only (both already in the service's requirements).
No new runtime dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from scipy.optimize import linear_sum_assignment


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU between boxes a [N,4] and b [M,4] in xyxy."""
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


def _xyxy_to_z(xyxy) -> np.ndarray:
    """Measurement vector z = [cx, cy, aspect, height]."""
    x1, y1, x2, y2 = xyxy
    # 1-pixel floor. The previous 1e-3 clamp *created* degenerate 0.001-pixel
    # boxes when Kalman aspect/height drifted toward zero on lost tracks,
    # rather than dropping them; boxes narrower than 1px are measurement
    # noise, not signal.
    w = max(1.0, x2 - x1)
    h = max(1.0, y2 - y1)
    return np.array([x1 + w / 2.0, y1 + h / 2.0, w / h, h], dtype=np.float64)


def _x_to_xyxy(x: np.ndarray) -> tuple[float, float, float, float]:
    cx, cy, a, h = x[0], x[1], x[2], x[3]
    w = max(1.0, a * h)
    h = max(1.0, h)
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


# --------------------------------------------------------------------------- #
# Per-track constant-velocity Kalman filter (8-dim state)
# state = [cx, cy, aspect, height, vcx, vcy, vaspect, vheight]
# --------------------------------------------------------------------------- #
class _Kalman:
    def __init__(self, z0: np.ndarray) -> None:
        self.x = np.zeros(8, dtype=np.float64)
        self.x[:4] = z0
        # Covariance — moderately uncertain on position, more on velocity.
        self.P = np.eye(8, dtype=np.float64)
        self.P[4:, 4:] *= 1000.0
        self.P *= 10.0
        # Constant-velocity transition.
        self.F = np.eye(8, dtype=np.float64)
        for i in range(4):
            self.F[i, i + 4] = 1.0
        # We only measure the 4 position/shape components.
        self.H = np.zeros((4, 8), dtype=np.float64)
        for i in range(4):
            self.H[i, i] = 1.0
        self.Q = np.eye(8, dtype=np.float64) * 1.0
        self.Q[4:, 4:] *= 0.01
        self.R = np.eye(4, dtype=np.float64) * 1.0

    def predict(self) -> None:
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q

    def update(self, z: np.ndarray) -> None:
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(8) - K @ self.H) @ self.P

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return _x_to_xyxy(self.x)


# --------------------------------------------------------------------------- #
# Track
# --------------------------------------------------------------------------- #
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
    kf: _Kalman | None = field(default=None, repr=False)

    def predict(self) -> None:
        if self.kf is not None:
            self.kf.predict()
            self.xyxy = self.kf.xyxy

    def correct(self, xyxy, conf, cls) -> None:
        if self.kf is not None:
            self.kf.update(_xyxy_to_z(xyxy))
            self.xyxy = self.kf.xyxy
        else:
            self.xyxy = tuple(xyxy)  # type: ignore[assignment]
        self.conf = conf
        self.cls = cls
        self.hits += 1
        self.misses = 0
        self.history.append(self.xyxy)
        self.history = self.history[-30:]


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
class ByteTrack:
    """Two-stage IoU tracker with Kalman prediction and correct aging.

    Parameters mirror common ByteTrack tuning knobs:
      high_thresh        — detections >= this start/continue tracks confidently
      low_thresh         — detections in [low, high) are used only to sustain
                           existing tracks (the "BYTE" recovery trick)
      iou_match_thresh   — minimum IoU to accept a match
      max_age            — frames a track may go unmatched before deletion
      min_hits           — (reserved) hits before a track is "confirmed"
    """

    def __init__(self, high_thresh: float = 0.5, low_thresh: float = 0.1,
                 iou_match_thresh: float = 0.3, max_age: int = 30,
                 min_hits: int = 1, use_kalman: bool = True) -> None:
        self.high_thresh = high_thresh
        self.low_thresh = low_thresh
        self.iou_thresh = iou_match_thresh
        self.max_age = max_age
        self.min_hits = min_hits
        self.use_kalman = use_kalman
        self.next_id = 1
        self.tracks: list[Track] = []

    def _match(self, tracks: list[Track], boxes: np.ndarray) -> list[tuple[int, int]]:
        if not tracks or len(boxes) == 0:
            return []
        track_boxes = np.array([t.xyxy for t in tracks], dtype=np.float32)
        iou = _iou_matrix(track_boxes, boxes)
        cost = 1.0 - iou
        cost[iou < self.iou_thresh] = 1.0
        row, col = linear_sum_assignment(cost)
        return [(r, c) for r, c in zip(row, col) if iou[r, c] >= self.iou_thresh]

    def update(self, detections: Iterable[dict]) -> list[Track]:
        dets = list(detections)

        # 1) Predict every existing track forward and mark it (optimistically)
        #    as missed; a successful match this frame clears the miss.
        for t in self.tracks:
            t.age += 1
            t.misses += 1
            t.predict()

        high = [d for d in dets if d["conf"] >= self.high_thresh]
        low = [d for d in dets if self.low_thresh <= d["conf"] < self.high_thresh]
        high_boxes = np.array([d["xyxy"] for d in high], dtype=np.float32) if high else np.empty((0, 4), np.float32)
        low_boxes = np.array([d["xyxy"] for d in low], dtype=np.float32) if low else np.empty((0, 4), np.float32)

        # 2) First association: all tracks vs high-confidence detections.
        unmatched_tracks = list(range(len(self.tracks)))
        matched_high: set[int] = set()
        for r, c in self._match(self.tracks, high_boxes):
            d = high[c]
            self.tracks[r].correct(d["xyxy"], d["conf"], d.get("cls", self.tracks[r].cls))
            if r in unmatched_tracks:
                unmatched_tracks.remove(r)
            matched_high.add(c)

        # 3) Second association: leftover tracks vs low-confidence detections
        #    (recovers occluded/low-score objects without spawning new IDs).
        leftover = [self.tracks[i] for i in unmatched_tracks]
        for r, c in self._match(leftover, low_boxes):
            leftover[r].correct(low[c]["xyxy"], low[c]["conf"], low[c].get("cls", leftover[r].cls))

        # 4) Spawn new tracks from unmatched high-confidence detections only.
        for i, d in enumerate(high):
            if i in matched_high:
                continue
            z = _xyxy_to_z(d["xyxy"])
            self.tracks.append(Track(
                id=self.next_id,
                xyxy=tuple(d["xyxy"]),  # type: ignore[arg-type]
                cls=d.get("cls", 0),
                conf=d["conf"],
                history=[tuple(d["xyxy"])],  # type: ignore[list-item]
                kf=_Kalman(z) if self.use_kalman else None,
            ))
            self.next_id += 1

        # 5) Delete tracks that have been missing too long. Kalman prediction
        #    extrapolates unboundedly when a track goes unobserved (this is
        #    what produced the -4009 / 2151 phantom boxes seen in prod), so
        #    such tracks stay INTERNAL to the tracker (a later frame may
        #    re-associate them) but are NOT PUBLISHED. Standard SORT/ByteTrack
        #    "time_since_update == 0 AND hits >= min_hits" contract.
        self.tracks = [t for t in self.tracks if t.misses < self.max_age]
        return [t for t in self.tracks if t.misses == 0 and t.hits >= self.min_hits]
