"""Pure-function tests for the promotion tool.

Only exercises the DB-free helpers (validation, merge union, auto-numbering,
bounds check). The DB path is one atomic transaction we cover through
integration on the tower — same pattern as insert_stations_4_5.py.

Run:  pytest tests/test_promote_zones.py -v
"""
from __future__ import annotations

import pytest

from app.scripts.promote_zones import (
    _bbox_from_polygon,
    _bbox_union,
    _bboxes_overlap,
    _next_station_number,
    _polygon_from_bbox,
    _polygon_in_frame,
    assign_names,
    resolve_promotions,
    validate_decisions,
)


CAM = "00000000-0000-4000-8000-000000000001"


def _rect(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _proposals(*specs):
    return {
        "camera_id": CAM,
        "sampling": {"frame": {"w": 1280, "h": 720}},
        "proposals": [
            {"label": lbl, "polygon": poly, "dwell_seconds": 999}
            for lbl, poly in specs
        ],
    }


def _decisions(*items, camera=CAM, line="Line A"):
    return {"camera_id": camera, "line": line, "decisions": list(items)}


# ---------------------------------------------------------------------------- #
# Geometry helpers
# ---------------------------------------------------------------------------- #
def test_bbox_and_polygon_roundtrip():
    poly = _rect(10, 20, 100, 80)
    assert _bbox_from_polygon(poly) == (10, 20, 100, 80)
    assert _polygon_from_bbox((10, 20, 100, 80)) == poly


def test_bbox_union_wraps_all_inputs():
    b = _bbox_union((10, 10, 20, 20), (15, 25, 30, 35), (5, 5, 12, 12))
    assert b == (5, 5, 30, 35)


def test_bboxes_touching_edges_do_not_overlap():
    assert _bboxes_overlap((0, 0, 100, 100), (100, 0, 200, 100)) is False


def test_bboxes_properly_overlapping_detected():
    assert _bboxes_overlap((0, 0, 100, 100), (50, 50, 150, 150)) is True


def test_polygon_in_frame():
    assert _polygon_in_frame(_rect(0, 0, 100, 100), 1280, 720) is True
    assert _polygon_in_frame(_rect(0, 0, 1281, 100), 1280, 720) is False
    assert _polygon_in_frame(_rect(-1, 0, 100, 100), 1280, 720) is False


def test_next_station_number_ignores_non_station_names():
    assert _next_station_number({"Station 1", "Station 3", "WS-01", "Nope"}) == 4
    assert _next_station_number(set()) == 1


# ---------------------------------------------------------------------------- #
# validate_decisions
# ---------------------------------------------------------------------------- #
def test_validate_passes_on_valid_input():
    props = _proposals(("S1", _rect(0, 0, 100, 100)))
    decs = _decisions({"label": "S1", "action": "approve"})
    validate_decisions(props, decs)  # no raise


def test_validate_camera_mismatch():
    props = _proposals(("S1", _rect(0, 0, 100, 100)))
    decs = _decisions({"label": "S1", "action": "approve"}, camera="other-cam")
    with pytest.raises(ValueError, match="camera_id mismatch"):
        validate_decisions(props, decs)


def test_validate_unreviewed_proposal_stops():
    props = _proposals(
        ("S1", _rect(0, 0, 100, 100)),
        ("S2", _rect(200, 0, 300, 100)),
    )
    decs = _decisions({"label": "S1", "action": "approve"})
    with pytest.raises(ValueError, match="unreviewed proposals.*S2"):
        validate_decisions(props, decs)


def test_validate_unknown_label_in_decisions():
    props = _proposals(("S1", _rect(0, 0, 100, 100)))
    decs = _decisions(
        {"label": "S1", "action": "approve"},
        {"label": "S99", "action": "reject", "reason": "nope"},
    )
    with pytest.raises(ValueError, match="not in proposals.*S99"):
        validate_decisions(props, decs)


def test_validate_duplicate_labels():
    props = _proposals(("S1", _rect(0, 0, 100, 100)))
    decs = _decisions(
        {"label": "S1", "action": "approve"},
        {"label": "S1", "action": "reject", "reason": "x"},
    )
    with pytest.raises(ValueError, match="duplicate decision labels"):
        validate_decisions(props, decs)


def test_validate_merge_target_is_reject_stops():
    props = _proposals(
        ("S1", _rect(0, 0, 100, 100)),
        ("S2", _rect(200, 0, 300, 100)),
    )
    decs = _decisions(
        {"label": "S1", "action": "reject", "reason": "no"},
        {"label": "S2", "action": "merge", "into": "S1"},
    )
    with pytest.raises(ValueError, match="must be 'approve'"):
        validate_decisions(props, decs)


def test_validate_chained_merge_stops():
    props = _proposals(
        ("S1", _rect(0, 0, 100, 100)),
        ("S2", _rect(200, 0, 300, 100)),
        ("S3", _rect(400, 0, 500, 100)),
    )
    decs = _decisions(
        {"label": "S1", "action": "approve"},
        {"label": "S2", "action": "merge", "into": "S1"},
        {"label": "S3", "action": "merge", "into": "S2"},   # chained
    )
    with pytest.raises(ValueError, match="chained merges are disallowed"):
        validate_decisions(props, decs)


def test_validate_unknown_action():
    props = _proposals(("S1", _rect(0, 0, 100, 100)))
    decs = _decisions({"label": "S1", "action": "wat"})
    with pytest.raises(ValueError, match="unknown action"):
        validate_decisions(props, decs)


# ---------------------------------------------------------------------------- #
# resolve_promotions
# ---------------------------------------------------------------------------- #
def test_resolve_approve_only_passes_polygon_through():
    props = _proposals(("S1", _rect(10, 20, 100, 80)))
    decs = _decisions({"label": "S1", "action": "approve", "name": "Station 6"})
    validate_decisions(props, decs)
    proms = resolve_promotions(props, decs)
    assert len(proms) == 1
    assert proms[0]["labels"] == ["S1"]
    assert proms[0]["polygon"] == _rect(10, 20, 100, 80)
    assert proms[0]["explicit_name"] == "Station 6"


def test_resolve_merge_unions_bboxes():
    props = _proposals(
        ("S1", _rect(10, 10, 50, 50)),      # small
        ("S2", _rect(40, 20, 90, 70)),      # overlapping, further right/down
    )
    decs = _decisions(
        {"label": "S1", "action": "approve"},
        {"label": "S2", "action": "merge", "into": "S1"},
    )
    validate_decisions(props, decs)
    proms = resolve_promotions(props, decs)
    assert len(proms) == 1
    assert set(proms[0]["labels"]) == {"S1", "S2"}
    # union: min corners of both, max corners of both
    assert proms[0]["polygon"] == _rect(10, 10, 90, 70)


def test_resolve_drops_rejects():
    props = _proposals(
        ("S1", _rect(0, 0, 100, 100)),
        ("S2", _rect(200, 0, 300, 100)),
    )
    decs = _decisions(
        {"label": "S1", "action": "approve"},
        {"label": "S2", "action": "reject", "reason": "tea corner"},
    )
    validate_decisions(props, decs)
    proms = resolve_promotions(props, decs)
    assert len(proms) == 1
    assert proms[0]["labels"] == ["S1"]


# ---------------------------------------------------------------------------- #
# assign_names
# ---------------------------------------------------------------------------- #
def test_assign_names_explicit_wins_then_autos_go_past_the_ceiling():
    """Explicit names claim their slot; autos then go PAST the highest
    claimed number, they do NOT retroactively backfill gaps. Rationale:
    if someone explicitly names Station 7, unused numbers below 7 were
    likely skipped deliberately (previously-existing stations that were
    removed). Filling them silently would collide with human intent."""
    props = _proposals(
        ("S1", _rect(0, 0, 100, 100)),
        ("S2", _rect(200, 0, 300, 100)),
        ("S3", _rect(400, 0, 500, 100)),
    )
    decs = _decisions(
        {"label": "S1", "action": "approve", "name": "Station 7"},
        {"label": "S2", "action": "approve"},
        {"label": "S3", "action": "approve"},
    )
    validate_decisions(props, decs)
    proms = resolve_promotions(props, decs)
    assign_names(proms, existing_workstation_names={"Station 1", "Station 3"})
    names = [p["assigned_name"] for p in proms]
    # existing {1,3} + explicit 7 → highest is 7 → autos get 8, 9
    assert names == ["Station 7", "Station 8", "Station 9"]


def test_assign_names_from_empty_start():
    props = _proposals(("S1", _rect(0, 0, 100, 100)))
    decs = _decisions({"label": "S1", "action": "approve"})
    validate_decisions(props, decs)
    proms = resolve_promotions(props, decs)
    assign_names(proms, existing_workstation_names=set())
    assert proms[0]["assigned_name"] == "Station 1"


def test_assign_names_case_insensitive_collision():
    props = _proposals(("S1", _rect(0, 0, 100, 100)))
    decs = _decisions({"label": "S1", "action": "approve", "name": "station 6"})
    validate_decisions(props, decs)
    proms = resolve_promotions(props, decs)
    # even though existing is 'Station 6', explicit 'station 6' is still
    # claimed; the DB layer detects the same-name reuse idempotently.
    assign_names(proms, existing_workstation_names={"Station 6"})
    assert proms[0]["assigned_name"] == "station 6"
