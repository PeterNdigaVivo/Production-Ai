"""Head-to-head tracker benchmark.

Runs each candidate tracker over each scene and reports the ground-truth metrics
plus wall-clock speed (updates/sec), so the fix-vs-replace decision is made with
numbers, not opinion.

Candidates:
  * current     -- the project's tracker as shipped (buggy; the baseline)
  * fixed       -- our hardened numpy/scipy tracker (Kalman + correct aging)
  * supervision -- sv.ByteTrack as a reference oracle (optional; skipped if the
                   package isn't installed)

Run:  python -m harness.bench
"""
from __future__ import annotations

import time

from .scene import (scene_steady_two, scene_enter_leave, scene_leave_return,
                    scene_crossing, scene_dropout)
from .detector import StubNoise
from .tracker_runner import run_tracker_over_scene, make_current_tracker

SCENES = {
    "steady_two": (scene_steady_two, None),
    "enter_leave": (scene_enter_leave, None),
    "leave_return": (scene_leave_return, None),
    "crossing": (scene_crossing, None),
    "dropout": (scene_dropout, StubNoise(dropout_p=0.25, seed=7)),
}


def make_fixed_tracker():
    from ._candidates.bytetrack_fixed import ByteTrack
    return ByteTrack()


def make_supervision_tracker():
    from ._candidates.supervision_adapter import SupervisionByteTrack
    return SupervisionByteTrack()


CANDIDATES = {
    "current": make_current_tracker,
    "fixed": make_fixed_tracker,
    "supervision": make_supervision_tracker,
}


def _time_updates(make_tracker, scene, noise):
    """Return updates/sec for a fresh tracker over the scene."""
    from .detector import StubDetector
    det = StubDetector(scene, noise=noise)
    tracker = make_tracker()
    frames = [det.infer_at(t) for t in scene.frame_times()]
    t0 = time.perf_counter()
    for dets in frames:
        tracker.update(dets)
    dt = time.perf_counter() - t0
    return len(frames) / dt if dt > 0 else float("inf")


def run():
    # discover which candidates are importable
    available = {}
    for name, mk in CANDIDATES.items():
        try:
            mk()
            available[name] = mk
        except Exception as e:
            print(f"[skip] candidate '{name}' unavailable: {e}")

    header = f"{'scene':<14}{'tracker':<13}{'gtPeak':>7}{'gtEnd':>6}{'peak':>6}{'end':>5}{'ghost':>7}{'idsw':>6}{'upd/s':>9}"
    for scene_name, (scene_fn, noise) in SCENES.items():
        print("=" * len(header))
        print(header)
        print("-" * len(header))
        for cname, mk in available.items():
            m = run_tracker_over_scene(mk(), scene_fn(), noise=noise)
            ups = _time_updates(mk, scene_fn(), noise)
            flag = "  <-- ghost" if m.ghost_tracks > 0 else ""
            print(f"{scene_name:<14}{cname:<13}{m.gt_peak_workers:>7}{m.gt_final_workers:>6}"
                  f"{m.peak_live_tracks:>6}{m.final_live_tracks:>5}{m.ghost_tracks:>7}"
                  f"{m.id_switches:>6}{ups:>9.0f}{flag}")
    print("=" * len(header))


if __name__ == "__main__":
    run()
