"""Synthetic scene definition with known ground truth.

The whole point of this module is that *we* decide exactly how many workers
exist, where they are, and when they enter/leave. Because ground truth is known
by construction, any disagreement reported downstream (wrong worker count,
unstable IDs, a track that never dies) is unambiguously a pipeline bug, not a
detection miss.

Coordinate space is pixel coordinates of the camera image (top-left origin),
matching what the real detection/tracking code expects in `xyxy`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Literal


Motion = Literal["sit", "sway", "walk_lr", "drift"]


@dataclass
class WorkerSpec:
    """One synthetic worker (a moving box) with a known life span and motion.

    enter_s / leave_s define when the worker is visible. Outside that window the
    worker emits no box at all -- this is how we test that the tracker ages out
    a departed worker (Finding 1) and recovers a returning one.
    """
    worker_id: int                      # ground-truth identity (NOT the tracker's id)
    seat_x: float                       # nominal seated centre x
    seat_y: float                       # nominal seated centre y
    box_w: float = 90.0
    box_h: float = 200.0
    motion: Motion = "sway"
    amplitude: float = 6.0              # px of motion (small = "still hands" sewing)
    period_s: float = 4.0              # motion period in seconds
    enter_s: float = 0.0
    leave_s: float = float("inf")
    conf: float = 0.92                 # confidence the stub detector reports

    def visible_at(self, t: float) -> bool:
        return self.enter_s <= t < self.leave_s

    def center_at(self, t: float) -> tuple[float, float]:
        """Ground-truth centre at time t (seconds)."""
        phase = 2 * math.pi * (t / self.period_s)
        if self.motion == "sit":
            dx, dy = 0.0, 0.0
        elif self.motion == "sway":
            dx, dy = self.amplitude * math.sin(phase), 0.0
        elif self.motion == "drift":
            dx = self.amplitude * math.sin(phase)
            dy = self.amplitude * math.cos(phase)
        elif self.motion == "walk_lr":
            # travels a wide horizontal path -- stresses IoU matching the most
            dx = (self.amplitude * 10.0) * math.sin(phase)
            dy = 0.0
        else:
            dx, dy = 0.0, 0.0
        return self.seat_x + dx, self.seat_y + dy

    def xyxy_at(self, t: float) -> tuple[float, float, float, float]:
        cx, cy = self.center_at(t)
        return (cx - self.box_w / 2, cy - self.box_h / 2,
                cx + self.box_w / 2, cy + self.box_h / 2)


@dataclass
class Scene:
    """A full synthetic scene: frame size, fps, duration, and the workers."""
    width: int = 1280
    height: int = 720
    fps: float = 8.0
    duration_s: float = 30.0
    workers: list[WorkerSpec] = field(default_factory=list)
    # Optional per-frame hook to perturb timing (e.g. simulate variable fps).
    time_jitter: Callable[[float], float] | None = None

    def frame_times(self) -> list[float]:
        n = int(self.duration_s * self.fps)
        base = [i / self.fps for i in range(n)]
        if self.time_jitter:
            base = [self.time_jitter(t) for t in base]
        return base

    def ground_truth_at(self, t: float) -> list[tuple[int, tuple[float, float, float, float], float]]:
        """Returns [(worker_id, xyxy, conf), ...] for all workers visible at t."""
        out = []
        for w in self.workers:
            if w.visible_at(t):
                out.append((w.worker_id, w.xyxy_at(t), w.conf))
        return out

    def max_concurrent_workers(self) -> int:
        """Peak number of simultaneously-visible workers across the timeline."""
        peak = 0
        for t in self.frame_times():
            peak = max(peak, len(self.ground_truth_at(t)))
        return peak


# ---- Prebuilt scenes used by the tests and the demo --------------------------

def scene_steady_two() -> Scene:
    """Two workers, both present the whole time, gentle sway. Baseline sanity:
    tracker should report exactly 2 stable IDs and never more."""
    return Scene(
        duration_s=20.0, fps=8.0,
        workers=[
            WorkerSpec(worker_id=1, seat_x=400, seat_y=400, motion="sway", amplitude=5),
            WorkerSpec(worker_id=2, seat_x=850, seat_y=400, motion="sway", amplitude=5),
        ],
    )


def scene_enter_leave() -> Scene:
    """Worker 2 leaves at 8s and never returns. This is the scene that exposes
    Finding 1: a correct tracker drops back to 1 live track; the buggy one keeps
    worker 2's id alive forever."""
    return Scene(
        duration_s=20.0, fps=8.0,
        workers=[
            WorkerSpec(worker_id=1, seat_x=400, seat_y=400, motion="sway", amplitude=5),
            WorkerSpec(worker_id=2, seat_x=850, seat_y=400, motion="sway",
                       amplitude=5, enter_s=0.0, leave_s=8.0),
        ],
    )


def scene_leave_return() -> Scene:
    """Worker 2 leaves at 6s, returns at 12s. Tests re-entry handling."""
    return Scene(
        duration_s=20.0, fps=8.0,
        workers=[
            WorkerSpec(worker_id=1, seat_x=400, seat_y=400, motion="sit"),
            WorkerSpec(worker_id=2, seat_x=850, seat_y=400, motion="sway",
                       amplitude=5, enter_s=0.0, leave_s=6.0),
            # same seat, comes back -- ground-truth-identical worker returning
            WorkerSpec(worker_id=2, seat_x=850, seat_y=400, motion="sway",
                       amplitude=5, enter_s=12.0, leave_s=20.0),
        ],
    )


def scene_crossing() -> Scene:
    """Two workers walk wide horizontal paths that cross in the middle. This is
    the scene that separates a Kalman tracker from a raw-IoU one: at the crossing
    the boxes overlap, and only motion prediction keeps the IDs from swapping."""
    return Scene(
        duration_s=16.0, fps=8.0,
        workers=[
            WorkerSpec(worker_id=1, seat_x=640, seat_y=380, motion="walk_lr",
                       amplitude=28, period_s=16.0),                 # L->R->L
            WorkerSpec(worker_id=2, seat_x=640, seat_y=380, motion="walk_lr",
                       amplitude=-28, period_s=16.0),                # R->L->R (mirror)
        ],
    )


def scene_dropout() -> Scene:
    """Three steady workers, but the detector will randomly drop boxes (set via
    StubNoise in the runner). Tests that brief misses don't kill a track and
    that low-confidence recovery / aging tolerance behave."""
    return Scene(
        duration_s=20.0, fps=8.0,
        workers=[
            WorkerSpec(worker_id=1, seat_x=300, seat_y=400, motion="sway", amplitude=4),
            WorkerSpec(worker_id=2, seat_x=640, seat_y=400, motion="sway", amplitude=4),
            WorkerSpec(worker_id=3, seat_x=980, seat_y=400, motion="sway", amplitude=4),
        ],
    )
