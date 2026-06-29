"""Tests for Step 8: fair activity FSM (#12), capture pipeline, machine-flow.

These run without a GPU, cameras, or Redis. The optical-flow detector is
exercised on synthetic frames; the FSM and capture logic are pure-Python.

Run:  pytest tests/test_step8.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Make the engine packages importable regardless of where pytest runs from.
_ROOT = Path(__file__).resolve().parents[1]
for p in [
    _ROOT / "services" / "activity-engine",
    _ROOT / "services" / "common",
]:
    sys.path.insert(0, str(p))

from activity.state_machine import WorkerActivityFSM, ActivitySignals  # noqa: E402
from capture.writer import CaptureWriter, CaptureConfig  # noqa: E402
from machine_flow.detector import MachineRunningDetector, MachineFlowConfig, calibrate_threshold  # noqa: E402


# --------------------------- #12 time-weighted FSM ------------------------- #
def _working():
    return ActivitySignals(motion_magnitude=0.3, machine_running=True)


def _idle():
    return ActivitySignals(motion_magnitude=0.0, machine_running=False)


def test_fsm_resists_framerate_burst():
    """A short burst of many idle frames (0.5s) must NOT flip a worker who has
    been WORKING for a long time — time-weighting, not frame counting."""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    t = 0.0
    for _ in range(50):           # 50s working at 1 fps
        fsm.update(t, _working()); t += 1.0
    assert fsm.state == "WORKING"
    for _ in range(5):            # 5 idle frames in 0.5s (fps spike)
        fsm.update(t, _idle()); t += 0.1
    assert fsm.state == "WORKING"  # burst is only 0.5s of a 60s window


def test_fsm_honours_sustained_idle():
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    t = 0.0
    for _ in range(50):
        fsm.update(t, _working()); t += 1.0
    for _ in range(40):           # 40s sustained idle
        fsm.update(t, _idle()); t += 1.0
    assert fsm.state == "IDLE"


# ------------------------------ capture pipeline --------------------------- #
def test_capture_flags_uncertain_only():
    w = CaptureWriter(CaptureConfig(enabled=True, out_dir=tempfile.mkdtemp(),
                                    per_camera_min_interval_s=0.0))
    assert w.assess([{"conf": 0.4}], False, 1).ambiguous_confidence is True
    assert w.assess([{"conf": 0.95}], False, 1).any() is False
    assert w.assess([{"conf": 0.95}], True, 1).unstable_state is True
    assert w.assess([{"conf": 0.9}] * 5, False, 1.0).detection_count_anomaly is True


def test_capture_writes_sidecar_with_labeled_false():
    tmp = tempfile.mkdtemp()
    w = CaptureWriter(CaptureConfig(enabled=True, out_dir=tmp, per_camera_min_interval_s=0.0))
    reason = w.assess([{"conf": 0.4}], False, 1)
    cap_id = w.maybe_capture("cam-1", b"\xff\xd8jpg", 1719600000.0,
                             [{"conf": 0.4, "xyxy": [1, 2, 3, 4]}], reason)
    assert cap_id is not None
    import json, glob
    metas = glob.glob(f"{tmp}/**/*.json", recursive=True)
    assert len(metas) == 1
    meta = json.loads(open(metas[0]).read())
    assert meta["labeled"] is False
    assert "ambiguous_confidence" in meta["reasons"]


def test_capture_rate_limited_and_off_by_default():
    w = CaptureWriter(CaptureConfig(enabled=True, out_dir=tempfile.mkdtemp(),
                                    per_camera_min_interval_s=5.0))
    r = w.assess([{"conf": 0.4}], False, 1)
    assert w.maybe_capture("c", b"x", 1.0, [{"conf": 0.4}], r) is not None
    assert w.maybe_capture("c", b"x", 1.0, [{"conf": 0.4}], r) is None  # rate-limited
    off = CaptureWriter(CaptureConfig(enabled=False))
    assert off.maybe_capture("c", b"x", 1.0, [{"conf": 0.4}], r) is None


# ------------------------------ machine-flow ------------------------------- #
def test_machine_flow_detects_motion_vs_still():
    """A moving texture in the zone should yield higher flow than a static one."""
    det = MachineRunningDetector(MachineFlowConfig(flow_threshold=0.5, downscale=1.0))
    poly = [[0, 0], [64, 0], [64, 64], [0, 64]]
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)

    # frame 1 (prime), then a SHIFTED frame -> motion
    det.update("ws", base, poly)
    shifted = np.roll(base, 5, axis=1)
    running, flow_moving = det.update("ws", shifted, poly)

    # now feed identical frames -> ~no motion
    det.reset("ws")
    det.update("ws", base, poly)
    _, flow_static = det.update("ws", base, poly)

    assert flow_moving > flow_static
    assert flow_static < 0.5  # static well below threshold


def test_calibrate_threshold_separates_classes():
    idle = [0.1, 0.2, 0.15, 0.05]
    running = [2.0, 2.5, 1.8, 3.0]
    thr = calibrate_threshold(running, idle)
    assert max(idle) < thr < min(running)
