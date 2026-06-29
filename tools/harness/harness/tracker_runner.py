"""Run a tracker over a synthetic scene and measure it against ground truth.

This is the heart of Step 1. It feeds the scene's detections (via the stub, so
detection is perfect and only the tracker is under test) frame-by-frame into a
tracker that exposes the project's `update(detections) -> tracks` interface, and
computes metrics we can assert on:

  * peak_live_tracks     -- max simultaneous track count the tracker reported
  * final_live_tracks    -- track count at the end of the run
  * unique_track_ids      -- how many distinct ids were ever created
  * id_switches (approx)  -- ground-truth workers that got >1 id over their life
  * ghost_tracks          -- tracks still alive after their gt worker left

The metric that exposes Finding 1 is `final_live_tracks` / `ghost_tracks`:
in scene_enter_leave, ground truth ends with 1 worker, so a correct tracker
ends with 1 live track. The buggy tracker keeps the departed worker alive ->
final_live_tracks stays 2 and ghost_tracks > 0.

A "tracker" here is any object with .update(list[dict]) -> iterable of objects
that have .id and .xyxy. The project's ByteTrack matches this already.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .scene import Scene
from .detector import StubDetector, StubNoise


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter + 1e-9)


@dataclass
class TrackerMetrics:
    frames: int = 0
    peak_live_tracks: int = 0
    final_live_tracks: int = 0
    unique_track_ids: int = 0
    id_switches: int = 0
    ghost_tracks: int = 0
    gt_peak_workers: int = 0
    gt_final_workers: int = 0
    per_frame_live: list[int] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"frames={self.frames}\n"
            f"ground-truth peak workers   = {self.gt_peak_workers}\n"
            f"ground-truth final workers  = {self.gt_final_workers}\n"
            f"tracker peak live tracks    = {self.peak_live_tracks}\n"
            f"tracker final live tracks   = {self.final_live_tracks}\n"
            f"unique track ids created    = {self.unique_track_ids}\n"
            f"approx id switches          = {self.id_switches}\n"
            f"ghost tracks (alive post-exit) = {self.ghost_tracks}"
        )


def run_tracker_over_scene(tracker, scene: Scene,
                           noise: StubNoise | None = None) -> TrackerMetrics:
    """Drive `tracker` through `scene` using the stub detector; return metrics."""
    det = StubDetector(scene, noise=noise)
    m = TrackerMetrics()
    m.gt_peak_workers = scene.max_concurrent_workers()

    seen_ids: set[int] = set()
    # map ground-truth worker_id -> set of tracker ids assigned to it
    gt_to_track_ids: dict[int, set[int]] = {}

    times = scene.frame_times()
    last_t = times[-1] if times else 0.0
    for t in times:
        detections = det.infer_at(t)
        tracks = list(tracker.update(detections))
        live = len(tracks)
        m.per_frame_live.append(live)
        m.peak_live_tracks = max(m.peak_live_tracks, live)
        for tr in tracks:
            seen_ids.add(tr.id)

        # associate each reported track to the nearest ground-truth worker by IoU
        gt = scene.ground_truth_at(t)
        for tr in tracks:
            best_wid, best_iou = None, 0.3
            for wid, gxyxy, _c in gt:
                i = _iou(tr.xyxy, gxyxy)
                if i > best_iou:
                    best_iou, best_wid = i, wid
            if best_wid is not None:
                gt_to_track_ids.setdefault(best_wid, set()).add(tr.id)

    m.frames = len(times)
    m.final_live_tracks = m.per_frame_live[-1] if m.per_frame_live else 0
    m.unique_track_ids = len(seen_ids)
    m.gt_final_workers = len(scene.ground_truth_at(last_t))
    # an id switch ~ a ground-truth worker that accumulated more than one track id
    m.id_switches = sum(max(0, len(ids) - 1) for ids in gt_to_track_ids.values())
    # ghosts: tracker holds more live tracks at the end than there are gt workers
    m.ghost_tracks = max(0, m.final_live_tracks - m.gt_final_workers)
    return m


def make_current_tracker():
    """Instantiate the project's CURRENT (vendored) ByteTrack as-is."""
    from ._vendor.bytetrack_current import ByteTrack
    return ByteTrack()
