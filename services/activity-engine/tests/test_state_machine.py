"""Unit tests for the WorkerActivityFSM.

Pure-function tests — no Redis, no DB. The FSM decides every number a
supervisor sees on the dashboard, so it earns direct coverage rather
than relying on the integration path (which was how the AWAY-never-fires
bug survived so long).

Run:
  PYTHONPATH=services/activity-engine python -m pytest \\
      services/activity-engine/tests/test_state_machine.py -v
"""
from __future__ import annotations

import pytest

from activity.state_machine import ActivitySignals, WorkerActivityFSM


# --------------------------------------------------------------------------- #
# Helpers — drive the FSM with a synthesised time series
# --------------------------------------------------------------------------- #
def _tick_series(fsm: WorkerActivityFSM, series):
    """Feed (ts, signals) pairs and return the final state.

    Each element is (float ts_seconds, ActivitySignals). Timestamps must be
    monotonically increasing (the FSM's time-weighted vote assumes so)."""
    last = fsm.state
    for ts, sig in series:
        last = fsm.update(ts, sig)
    return last


def _present(motion: float = 0.10, machine: bool = True) -> ActivitySignals:
    """A quick 'operator is here and busy' signal."""
    return ActivitySignals(motion_magnitude=motion, machine_running=machine, present=True)


def _absent() -> ActivitySignals:
    """The signal that used to be unreachable — no operator, no track."""
    return ActivitySignals(motion_magnitude=0.0, machine_running=False, present=False)


# --------------------------------------------------------------------------- #
# AWAY reachability — the whole point of the workstation-keyed FSM
# --------------------------------------------------------------------------- #
def test_sustained_absence_past_debounce_confirms_away():
    """present=False for longer than the debounce window MUST transition to
    AWAY. This is the case the pre-fix code couldn't produce at all — the
    handler never called update() for a vanished track, so this state was
    unreachable no matter how long the operator stayed away."""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    # Establish a WORKING baseline...
    _tick_series(fsm, [(t, _present()) for t in range(0, 90, 2)])
    assert fsm.state == "WORKING"
    # ...then sustain absence past the debounce window.
    _tick_series(fsm, [(t, _absent()) for t in range(200, 400, 2)])
    assert fsm.state == "AWAY", (
        f"expected AWAY after sustained absence, got {fsm.state}"
    )


def test_brief_absence_shorter_than_debounce_does_not_flip():
    """A single glitch frame or two-second dropout must NOT swing the FSM to
    AWAY — the debounce is what protects the number from tracker gaps."""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    _tick_series(fsm, [(t, _present()) for t in range(0, 120, 2)])
    assert fsm.state == "WORKING"
    # 10-second gap of absence within a longer WORKING trend.
    _tick_series(fsm, [(t, _absent()) for t in range(120, 130, 2)])
    _tick_series(fsm, [(t, _present()) for t in range(130, 200, 2)])
    assert fsm.state == "WORKING", (
        f"a 10s absence flipped the FSM ({fsm.state}) — debounce is broken"
    )


def test_away_returns_to_working_when_operator_comes_back():
    """A confirmed AWAY must recover to WORKING once the operator returns and
    the debounce window fills with present samples again."""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    # Send FSM to AWAY.
    _tick_series(fsm, [(t, _absent()) for t in range(0, 200, 2)])
    assert fsm.state == "AWAY"
    # Operator returns and sustains present past the debounce window.
    _tick_series(fsm, [(t, _present()) for t in range(200, 400, 2)])
    assert fsm.state == "WORKING", (
        f"AWAY→WORKING transition failed on operator return: {fsm.state}"
    )


# --------------------------------------------------------------------------- #
# `present` beats every other signal — regression guard
# --------------------------------------------------------------------------- #
def test_machine_running_with_absent_still_resolves_to_away():
    """A signal contradiction: machine=True but present=False. The
    `_candidate` present-check must win — a machine left running while the
    operator stepped out is exactly the FALSE-AWAY signal that historical
    logs show most often, and it is also the strongest audit clue for the
    Phase 3 event-review screen. AWAY is still the honest answer.
    (See ROADMAP change log: "machine_running while AWAY" flagged as free
    accuracy evidence for the review screen — do NOT build it here.)"""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    for t in range(0, 200, 2):
        fsm.update(t, ActivitySignals(
            motion_magnitude=0.0,
            machine_running=True,      # ← machine still running
            present=False,             # ← operator gone
        ))
    assert fsm.state == "AWAY"


# --------------------------------------------------------------------------- #
# Time-weighted voting under variable frame rate — the scenario the
# time-weighting was originally written for.
# --------------------------------------------------------------------------- #
def test_time_weighted_vote_ignores_high_frequency_burst():
    """A short burst of many frames must not swing the vote against a
    long-running signal that just happens to be sampled less often. This is
    what pre-fix frame-count weighting got wrong; the fix weights each
    sample by the seconds it represents."""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    # 50 seconds of steady low-rate WORKING (one sample every 2s).
    _tick_series(fsm, [(t, _present()) for t in range(0, 50, 2)])
    # Suddenly a 3-second burst at 30fps of AWAY. Frame-count would let this
    # dominate (~90 samples vs 25); time-weight caps it at ~3 seconds of
    # evidence and the 50-second WORKING signal keeps the state.
    burst = [(50 + i / 30.0, _absent()) for i in range(90)]
    _tick_series(fsm, burst)
    # Final tick to move `ts` past the burst so the debounce evaluates.
    fsm.update(65.0, _present())
    assert fsm.state != "AWAY", (
        f"a 3s high-frequency AWAY burst swung a 50s WORKING baseline to {fsm.state} "
        f"— vote is not time-weighted"
    )


# --------------------------------------------------------------------------- #
# Evidence-floor guard
# --------------------------------------------------------------------------- #
def test_transition_requires_at_least_three_seconds_of_evidence():
    """Even a candidate holding a strict majority of the (very short) window
    must not transition until we have >= 3s of evidence. This prevents the
    very first frame or two after boot from committing an unstable state."""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    # Only two absent samples spanning ~1.5s of time — well under the floor.
    fsm.update(0.0, _absent())
    fsm.update(1.5, _absent())
    assert fsm.state != "AWAY", (
        f"transitioned to AWAY with <3s of evidence: state={fsm.state}"
    )
    # Extend past the floor and the same signal should now confirm.
    for t in (3.0, 5.0, 10.0, 30.0, 90.0):
        fsm.update(t, _absent())
    assert fsm.state == "AWAY"


def test_stable_working_when_signals_agree_and_debounce_fills():
    """Sanity: with strong present+machine+motion signals across the window,
    the FSM lands on WORKING (i.e. AWAY-reachability didn't accidentally
    break the happy path)."""
    fsm = WorkerActivityFSM(debounce_seconds=60.0)
    _tick_series(fsm, [(t, _present(motion=0.20, machine=True))
                       for t in range(0, 200, 2)])
    assert fsm.state == "WORKING"
