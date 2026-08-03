"""Regression guard for BUG 1: the tracker used to publish Kalman-only
predictions of tracks that had gone unobserved, letting them extrapolate
unboundedly (we saw y1=-4009, y2=2151, and 0.001-pixel-wide boxes in prod).

The fix in bytetrack.py restricts `update()` to tracks matched THIS frame:
  return [t for t in self.tracks if t.misses == 0 and t.hits >= self.min_hits]

This test proves: one detection creates a track, then any number of frames
with NO detections must return an empty list — the track survives internally
(may be recovered later) but is not published.

Run: pytest services/tracking-engine/tests/test_tracker_unmatched.py -v
"""
from __future__ import annotations
import sys
from pathlib import Path

# tests/ lives next to the tracking/ package but neither is on sys.path by
# default when pytest is run from the service root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracking.bytetrack import ByteTrack  # noqa: E402


def _det(x1, y1, x2, y2, conf=0.9, cls=0):
    return {"xyxy": [x1, y1, x2, y2], "conf": conf, "cls": cls}


def test_first_frame_with_detection_publishes_it():
    t = ByteTrack()
    out = t.update([_det(100, 100, 180, 260)])
    assert len(out) == 1
    assert out[0].hits == 1
    assert out[0].misses == 0


def test_immediately_next_no_detection_frame_returns_empty():
    """The first frame with no detection must NOT publish the Kalman
    prediction. Previously the tracker returned it (misses < max_age)."""
    t = ByteTrack()
    t.update([_det(100, 100, 180, 260)])
    assert t.update([]) == []


def test_many_no_detection_frames_never_publish_predictions():
    """Simulate 20 consecutive empty frames — the whole "missing but not yet
    dropped" window. Not a single frame should return a phantom prediction."""
    t = ByteTrack()
    t.update([_det(100, 100, 180, 260)])
    for _ in range(20):
        assert t.update([]) == []


def test_track_survives_internally_and_can_be_reassociated():
    """After a gap, if a matching detection reappears, the SAME track id
    must be recovered — proves the unmatched track is kept internally, not
    dropped, during its gap."""
    t = ByteTrack()
    initial = t.update([_det(100, 100, 180, 260)])
    original_id = initial[0].id

    for _ in range(5):
        t.update([])                       # no publishes during the gap

    recovered = t.update([_det(102, 101, 181, 261)])
    assert len(recovered) == 1
    assert recovered[0].id == original_id  # same identity, not a fresh spawn


def test_boxes_are_never_degenerate_or_far_out_of_frame():
    """Even during recovery, published boxes must be geometrically sane —
    the 1-pixel floor in _xyxy_to_z/_x_to_xyxy prevents the 0.001-pixel and
    the -4000/+2000 pathologies we saw in prod."""
    t = ByteTrack()
    t.update([_det(100, 100, 180, 260)])
    for _ in range(10):
        t.update([])
    out = t.update([_det(102, 101, 181, 261)])
    assert len(out) == 1
    x1, y1, x2, y2 = out[0].xyxy
    # Non-degenerate.
    assert x2 - x1 >= 1.0
    assert y2 - y1 >= 1.0
    # Sane order of magnitude — within a few hundred px of where we put it,
    # certainly not thousands of pixels away.
    assert -500 < x1 < 2000
    assert -500 < y1 < 2000
