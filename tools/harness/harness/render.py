"""Renders a synthetic scene frame to a BGR image (and JPEG bytes).

Used two ways:
  * the Redis publisher needs JPEG bytes in the exact shape the real ingestion
    service produces, so detection-engine can consume harness frames unchanged;
  * the debug overlay draws boxes/IDs/zones on top of these frames.

Workers are drawn as filled rectangles with a head circle -- crude on purpose,
but enough for a real YOLO person-detector to fire in "yolo" mode, and clear
enough to eyeball in the overlay.
"""
from __future__ import annotations

import cv2
import numpy as np


def render_frame(scene, t: float, zones: dict | None = None) -> np.ndarray:
    """Render the scene at time t to a BGR uint8 image."""
    img = np.full((scene.height, scene.width, 3), 38, dtype=np.uint8)  # dark grey bg

    # subtle floor grid so motion is visible to the eye in the overlay
    for x in range(0, scene.width, 80):
        cv2.line(img, (x, 0), (x, scene.height), (52, 52, 52), 1)
    for y in range(0, scene.height, 80):
        cv2.line(img, (0, y), (scene.width, y), (52, 52, 52), 1)

    # optional zone polygons (camera_id -> list[(ws_id, kind, [[x,y],...])])
    if zones:
        for _ws, kind, poly in zones:
            pts = np.array(poly, dtype=np.int32).reshape((-1, 1, 2))
            color = {"seat": (90, 160, 90), "machine": (160, 120, 60),
                     "input_tray": (60, 120, 170), "output_tray": (120, 80, 160)}.get(kind, (120, 120, 120))
            cv2.polylines(img, [pts], isClosed=True, color=color, thickness=2)

    for _wid, xyxy, _conf in scene.ground_truth_at(t):
        x1, y1, x2, y2 = [int(v) for v in xyxy]
        cv2.rectangle(img, (x1, y1), (x2, y2), (200, 200, 210), -1)
        # head
        cx = (x1 + x2) // 2
        cv2.circle(img, (cx, y1 + 24), 20, (180, 180, 200), -1)
    return img


def encode_jpeg(frame_bgr: np.ndarray, quality: int = 80) -> bytes:
    ok, jpg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return jpg.tobytes()
