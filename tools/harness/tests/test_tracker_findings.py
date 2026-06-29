"""Automated assertions for the tracker (Step 2 updated).

These now verify the FIXED tracker behaves correctly, and keep a regression
check that documents what the original buggy tracker did. The benchmark in
harness/bench.py covers the head-to-head with supervision.

Run:  pytest -v   (from tools/harness/)
"""
from __future__ import annotations

from harness.scene import (scene_steady_two, scene_enter_leave, scene_crossing,
                           scene_dropout)
from harness.detector import StubNoise
from harness.tracker_runner import run_tracker_over_scene, make_current_tracker


def make_fixed():
    from harness._candidates.bytetrack_fixed import ByteTrack
    return ByteTrack()


# ---- Harness sanity ---------------------------------------------------------

def test_steady_scene_exact_count_fixed():
    m = run_tracker_over_scene(make_fixed(), scene_steady_two())
    assert m.peak_live_tracks == 2
    assert m.final_live_tracks == 2
    assert m.ghost_tracks == 0
    assert m.unique_track_ids == 2


# ---- Finding 1: the fix --------------------------------------------------- #

def test_departed_worker_is_aged_out_fixed():
    """The fix: a worker who leaves must age out -> no ghost track."""
    m = run_tracker_over_scene(make_fixed(), scene_enter_leave())
    assert m.gt_final_workers == 1
    assert m.final_live_tracks == 1
    assert m.ghost_tracks == 0


def test_regression_original_tracker_had_ghost():
    """Documents the original defect so we never silently reintroduce it."""
    m = run_tracker_over_scene(make_current_tracker(), scene_enter_leave())
    assert m.ghost_tracks >= 1  # the bug, preserved as a regression marker


# ---- Identity stability under stress ------------------------------------- #

def test_crossing_keeps_two_tracks_fixed():
    m = run_tracker_over_scene(make_fixed(), scene_crossing())
    assert m.peak_live_tracks == 2
    assert m.final_live_tracks == 2
    assert m.ghost_tracks == 0


def test_dropout_holds_all_workers_fixed():
    """Under 25% random frame dropout the three workers must not vanish."""
    m = run_tracker_over_scene(make_fixed(), scene_dropout(),
                               noise=StubNoise(dropout_p=0.25, seed=7))
    assert m.gt_final_workers == 3
    assert m.final_live_tracks == 3
    assert m.ghost_tracks == 0
