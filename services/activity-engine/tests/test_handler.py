"""Handler-level tests for activity/main.py.

The FSM was never broken — the bug was always in the handler that iterates
payloads, wires signals into the FSM, and manages per-camera state. Yet
that handler had zero tests, which is how the AWAY-unreachable bug
survived so long AND how the pipeline-outage guard originally shipped with
a threshold value that would have silently disabled the engine on any
line running below its target fps. These tests cover the entire rewritten
surface: outage guard, roster iteration, empty-roster path, eviction,
multi-track selection, and low-frame-rate resilience.

No live Redis. FakeRedis records every xadd; assertions inspect it.

Run:
  PYTHONPATH=services/activity-engine python -m pytest \\
      services/activity-engine/tests/test_handler.py -v
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from activity.main import (
    CameraState,
    _make_handler,
    PIPELINE_OUTAGE_SECONDS,
)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeRedis:
    """Minimal redis.asyncio.Redis stand-in: records every xadd for later
    inspection. Nothing else on the interface is exercised by the handler."""
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def xadd(self, stream, fields, **kwargs):
        # Store a shallow copy — decoding not needed since we build inputs
        # in-Python. Include the stream name so tests can assert on it.
        self.published.append({"stream": stream, "fields": dict(fields)})


def _fields(payload: dict) -> dict:
    """Wrap a payload dict in the Redis-stream shape the handler consumes
    (`{"json": <json bytes>}`)."""
    return {b"json": json.dumps(payload).encode("utf-8")}


def _make_payload(*, ts: float, tracks: list[dict], roster: list[str] | None,
                  machine_running: dict[str, bool] | None = None,
                  omit_workstations: bool = False) -> dict:
    payload: dict[str, Any] = {
        "camera_id": "cam-A",
        "ts": ts,
        "tracks": tracks,
        "machine_running": machine_running or {},
    }
    if not omit_workstations:
        payload["workstations"] = roster if roster is not None else []
    return payload


def _track(track_id: int, ws: str | None, conf: float = 0.9,
           xyxy: tuple[float, float, float, float] = (100, 200, 180, 400)) -> dict:
    return {"track_id": track_id, "xyxy": list(xyxy), "conf": conf,
            "workstation_id": ws, "hits": 12}


def _state_events(fake: FakeRedis, ws: str | None = None) -> list[dict]:
    """Filter fake.published to `worker_state_changed` for one workstation."""
    rows = [p["fields"] for p in fake.published
            if p["fields"].get("type") == "worker_state_changed"]
    if ws is None:
        return rows
    return [r for r in rows if r.get("workstation_id") == ws]


async def _drive(handler, payloads: list[dict]) -> None:
    for i, p in enumerate(payloads):
        await handler(str(i).encode(), _fields(p))


# --------------------------------------------------------------------------- #
# 1. Empty seat on the roster emits AWAY after the debounce window
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_empty_seat_emits_away_after_debounce():
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # Occupied for a long time so the FSM lands on WORKING first — makes
    # the AWAY transition assertable (WORKING → AWAY).
    payloads = [
        _make_payload(ts=t, tracks=[_track(1, "ws-1", conf=0.95)],
                      roster=["ws-1"], machine_running={"ws-1": True})
        for t in range(0, 100, 2)
    ]
    # Then the operator vanishes for well past the 60s debounce window.
    payloads += [
        _make_payload(ts=t, tracks=[], roster=["ws-1"])
        for t in range(100, 300, 2)
    ]
    await _drive(handler, payloads)

    ws1 = _state_events(fake, "ws-1")
    states = [e["state"] for e in ws1]
    assert "WORKING" in states, f"expected an initial WORKING transition, got {states}"
    assert states[-1] == "AWAY", (
        f"empty seat should end in AWAY after sustained absence, got {states[-1]} "
        f"(sequence={states})"
    )


# --------------------------------------------------------------------------- #
# 2. Occupied seat sustained → WORKING; no AWAY
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_occupied_seat_never_emits_away():
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # 300 seconds of continuous presence + motion + machine.
    payloads = [
        _make_payload(ts=t,
                      tracks=[_track(1, "ws-1", conf=0.9,
                                     xyxy=(100 + (t % 20), 200, 180 + (t % 20), 400))],
                      roster=["ws-1"], machine_running={"ws-1": True})
        for t in range(0, 300, 2)
    ]
    await _drive(handler, payloads)

    ws1 = _state_events(fake, "ws-1")
    states = [e["state"] for e in ws1]
    assert "AWAY" not in states, (
        f"occupied seat should never emit AWAY, but sequence was {states}"
    )
    assert states[-1] == "WORKING"


# --------------------------------------------------------------------------- #
# 3. Aisle walker (workstation_id=None) drives no FSM at all
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_aisle_walker_drives_no_fsm():
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # Roster has ws-1 (empty). A person walks the aisle (workstation_id=None)
    # across the whole window. FSM for ws-1 progresses (unoccupied → AWAY);
    # no separate FSM for None ever gets created.
    payloads = [
        _make_payload(ts=t, tracks=[_track(99, None, conf=0.7)],
                      roster=["ws-1"])
        for t in range(0, 200, 2)
    ]
    await _drive(handler, payloads)

    assert set(state.fsms.keys()) == {"ws-1"}, (
        f"only ws-1 should have an FSM, got {set(state.fsms.keys())}"
    )
    # No None-keyed centroid either.
    assert None not in state.last_centroid
    # ws-1 was empty the whole time → ends in AWAY.
    ws1 = _state_events(fake, "ws-1")
    assert ws1[-1]["state"] == "AWAY"


# --------------------------------------------------------------------------- #
# 4. Multi-track same seat: higher-conf wins
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_multi_track_seat_picks_highest_conf():
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # Two tracks in the same seat, wildly different centroids. The chosen
    # motion should come from the higher-conf track. We verify by checking
    # last_centroid after the frame.
    payload = _make_payload(
        ts=1.0,
        tracks=[
            _track(41, "ws-1", conf=0.30, xyxy=(0, 0, 40, 80)),        # lower conf
            _track(42, "ws-1", conf=0.95, xyxy=(500, 500, 540, 580)),  # higher conf
        ],
        roster=["ws-1"],
    )
    await handler(b"e1", _fields(payload))

    # Centroid of the higher-conf track: ((500+540)/2, (500+580)/2) = (520, 540).
    cx, cy = state.last_centroid["ws-1"]
    assert (cx, cy) == (520.0, 540.0), (
        f"expected centroid from higher-conf track (520, 540), got ({cx}, {cy})"
    )


# --------------------------------------------------------------------------- #
# 5. Roster shrink evicts; empty roster does NOT
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_roster_shrink_evicts_seat():
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # First frame: roster has ws-1 + ws-2, both empty. FSMs get created.
    await handler(b"e1", _fields(_make_payload(
        ts=1.0, tracks=[], roster=["ws-1", "ws-2"])))
    assert set(state.fsms) == {"ws-1", "ws-2"}

    # Next frame: ws-2 was re-zoned away. Only ws-1 remains.
    await handler(b"e2", _fields(_make_payload(
        ts=2.0, tracks=[], roster=["ws-1"])))
    assert set(state.fsms) == {"ws-1"}, (
        f"ws-2 should have been evicted, still see {set(state.fsms)}"
    )


@pytest.mark.asyncio
async def test_empty_roster_does_not_wipe_state():
    """BLOCKER 2 regression guard: an empty `workstations` list must NOT
    evict every FSM for the camera. An empty roster is a legacy/partial-
    deploy signal (or a genuinely-empty camera line-up), never a signal
    to wipe state."""
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # Seed two seats.
    await handler(b"e1", _fields(_make_payload(
        ts=1.0, tracks=[], roster=["ws-1", "ws-2"])))
    assert set(state.fsms) == {"ws-1", "ws-2"}

    # Empty roster arrives (as a list, not omitted).
    await handler(b"e2", _fields(_make_payload(ts=2.0, tracks=[], roster=[])))
    assert set(state.fsms) == {"ws-1", "ws-2"}, (
        f"empty roster wiped state: {set(state.fsms)}"
    )


@pytest.mark.asyncio
async def test_missing_workstations_key_holds_state_and_warns_once():
    """BLOCKER 2 secondary path: a payload with NO `workstations` key at
    all (legacy tracking-engine, or an ordering-of-deploys mistake). We
    hold state entirely and log ONE warning per LEGACY_WARN_INTERVAL_S, not
    per frame."""
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # Seed two seats.
    await handler(b"e1", _fields(_make_payload(
        ts=1.0, tracks=[], roster=["ws-1", "ws-2"])))
    assert set(state.fsms) == {"ws-1", "ws-2"}

    # Legacy payloads flood in — 100 of them. State must be preserved
    # and nothing new emitted.
    baseline_events = len(fake.published)
    for i in range(100):
        legacy = _make_payload(ts=2.0 + i * 0.25, tracks=[], roster=None,
                               omit_workstations=True)
        await handler(f"leg-{i}".encode(), _fields(legacy))

    assert set(state.fsms) == {"ws-1", "ws-2"}, (
        f"legacy payload wiped state: {set(state.fsms)}"
    )
    # No new xadd calls — hold state means emit nothing.
    assert len(fake.published) == baseline_events, (
        f"legacy payload triggered emissions; delta="
        f"{len(fake.published) - baseline_events}"
    )


# --------------------------------------------------------------------------- #
# 6. Outage threshold exceeded holds state without emitting
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_outage_exceeded_prunes_window_and_does_not_credit_gap():
    """The outage guard prunes each FSM's window of pre-outage samples so
    the gap contributes no vote weight. Without the prune, the last
    pre-outage sample would carry the entire gap as its own weight and
    could swing the vote. With it, the FSM re-forms its picture from the
    post-outage frames alone."""
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # Build up a strong WORKING baseline...
    for t in range(0, 100, 2):
        await handler(str(t).encode(), _fields(_make_payload(
            ts=float(t),
            tracks=[_track(1, "ws-1", conf=0.9,
                           xyxy=(100 + (t % 20), 200, 180 + (t % 20), 400))],
            roster=["ws-1"], machine_running={"ws-1": True})))

    fsm = state.fsms["ws-1"]
    assert fsm.state == "WORKING"
    pre_len = len(fsm.window)
    assert pre_len > 0

    # ...then the pipeline goes quiet for well past the outage threshold.
    gap = PIPELINE_OUTAGE_SECONDS * 5   # 50s at threshold 10s
    outage_ts = 100.0 + gap
    payload = _make_payload(ts=outage_ts, tracks=[], roster=["ws-1"])
    await handler(b"outage", _fields(payload))

    # The window has been pruned of pre-outage samples (all had t < 100).
    # After prune + this one update, only the post-outage sample survives.
    assert len(fsm.window) == 1, (
        f"expected pruned window to hold only the post-outage sample, "
        f"got {len(fsm.window)}"
    )
    # And the outage was declared exactly once.
    assert state.outage_active is True


@pytest.mark.asyncio
async def test_outage_logs_once_per_outage_not_per_frame(monkeypatch):
    """Regression guard: at 4fps, a per-frame log would emit 4 warnings/s.
    We must log ONCE on entry and once (info) on resume. structlog isn't
    routed through stdlib logging in this service, so we monkeypatch the
    module-level `log` binding with a call-recorder."""
    from activity import main as act_main

    calls: list[tuple[str, str, dict]] = []

    class _Rec:
        def warning(self, event, **kw): calls.append(("warning", event, kw))
        def info(self, event, **kw):    calls.append(("info",    event, kw))
        def debug(self, event, **kw):   calls.append(("debug",   event, kw))
    monkeypatch.setattr(act_main, "log", _Rec())

    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # First payload establishes last_payload_ts.
    await handler(b"e1", _fields(_make_payload(
        ts=0.0, tracks=[], roster=["ws-1"])))
    # A batch of post-outage frames, all with gaps > threshold from their
    # predecessor's TS. Only the FIRST should emit `pipeline_outage_start`.
    for i in range(5):
        t = 100.0 + i * 0.25   # 100s from the seed frame → outage tripped
        await handler(f"o-{i}".encode(), _fields(_make_payload(
            ts=t, tracks=[], roster=["ws-1"])))

    outage_starts = [c for c in calls
                     if c[0] == "warning" and c[1] == "activity.pipeline_outage_start"]
    assert len(outage_starts) == 1, (
        f"expected 1 outage-start warning, got {len(outage_starts)}: {outage_starts}"
    )


# --------------------------------------------------------------------------- #
# 7. Low frame rate does NOT permanently suppress updates — the test that
#    would have caught BLOCKER 1 (target-fps-derived threshold at 0.75s).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sustained_low_frame_rate_still_advances_fsm():
    """A camera running at ~0.5fps (payload every 2s) is slow but alive.
    The outage guard MUST NOT trip on such inter-sample gaps or the FSM
    will never advance and the engine emits nothing indefinitely — the
    failure mode BLOCKER 1 was created to prevent."""
    fake = FakeRedis()
    state = CameraState()
    handler = _make_handler(fake, "cam-A", state)

    # 200 frames at 2s intervals = 400 seconds of steady 0.5fps presence.
    for i in range(200):
        t = i * 2.0
        await handler(str(i).encode(), _fields(_make_payload(
            ts=t,
            tracks=[_track(1, "ws-1", conf=0.9,
                           xyxy=(100 + (i % 20), 200, 180 + (i % 20), 400))],
            roster=["ws-1"], machine_running={"ws-1": True})))

    # The outage guard did NOT trip.
    assert state.outage_active is False, (
        "0.5fps stream tripped the outage guard — threshold too tight"
    )
    # And the FSM landed on WORKING as it should have.
    fsm = state.fsms["ws-1"]
    assert fsm.state == "WORKING", (
        f"FSM did not advance under a slow-but-alive stream: {fsm.state}"
    )
