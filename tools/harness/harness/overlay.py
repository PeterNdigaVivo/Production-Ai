"""Debug overlay -- the 'microscope' from the audit.

Draws, on top of a rendered scene frame:
  * detection boxes (yellow)
  * track id + box (cycling colours per id) so you can SEE id stability/ghosts
  * zone polygons (if provided)

Two entry points:
  * annotate_frame(...)  -> returns a BGR image with overlays (used by tests/demo)
  * render_overlay_video(...) -> writes an .mp4 of a whole scene+tracker run so
    you can watch ids stay stable (or not). This is the visual proof to look at
    before/after the Step-2 tracker fix.
"""
from __future__ import annotations

import cv2
import numpy as np

from .render import render_frame
from .detector import StubDetector, StubNoise


_PALETTE = [
    (66, 135, 245), (245, 167, 66), (66, 245, 138), (245, 66, 173),
    (245, 230, 66), (147, 66, 245), (66, 245, 233), (245, 66, 66),
]


def _color_for(track_id: int):
    return _PALETTE[track_id % len(_PALETTE)]


def annotate_frame(frame_bgr, detections, tracks, zones=None, t=None):
    img = frame_bgr.copy()
    if zones:
        for _ws, kind, poly in zones:
            pts = np.array(poly, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(img, [pts], True, (90, 160, 90), 2)

    for d in detections:
        x1, y1, x2, y2 = [int(v) for v in d["xyxy"]]
        cv2.rectangle(img, (x1, y1), (x2, y2), (60, 220, 230), 1)

    for tr in tracks:
        x1, y1, x2, y2 = [int(v) for v in tr.xyxy]
        col = _color_for(tr.id)
        cv2.rectangle(img, (x1, y1), (x2, y2), col, 3)
        label = f"ID {tr.id}"
        cv2.rectangle(img, (x1, y1 - 22), (x1 + 70, y1), col, -1)
        cv2.putText(img, label, (x1 + 4, y1 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 2)

    hud = f"live tracks: {len(tracks)}   detections: {len(detections)}"
    if t is not None:
        hud = f"t={t:5.1f}s   " + hud
    cv2.rectangle(img, (0, 0), (img.shape[1], 28), (25, 25, 25), -1)
    cv2.putText(img, hud, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1)
    return img


def render_overlay_video(tracker, scene, out_path: str, zones=None,
                         noise: StubNoise | None = None, fps: int | None = None,
                         log=print) -> str:
    """Run tracker over scene and write an annotated .mp4. Returns out_path."""
    det = StubDetector(scene, noise=noise)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps or scene.fps,
                             (scene.width, scene.height))
    for t in scene.frame_times():
        frame = render_frame(scene, t, zones=zones)
        dets = det.infer_at(t)
        tracks = list(tracker.update(dets))
        ann = annotate_frame(frame, dets, tracks, zones=zones, t=t)
        writer.write(ann)
    writer.release()
    log(f"[overlay] wrote {out_path}")
    return out_path


def render_overlay_frame_png(tracker, scene, t_target: float, out_path: str,
                             zones=None, noise: StubNoise | None = None) -> str:
    """Render a single annotated PNG at ~t_target seconds (for quick eyeballing)."""
    det = StubDetector(scene, noise=noise)
    last = None
    for t in scene.frame_times():
        frame = render_frame(scene, t, zones=zones)
        dets = det.infer_at(t)
        tracks = list(tracker.update(dets))
        if t >= t_target:
            last = annotate_frame(frame, dets, tracks, zones=zones, t=t)
            break
        last = annotate_frame(frame, dets, tracks, zones=zones, t=t)
    if last is not None:
        cv2.imwrite(out_path, last)
    return out_path
