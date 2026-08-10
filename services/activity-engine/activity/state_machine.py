"""Hysteresis state machine for worker activity.

States: WORKING | IDLE | AWAY | WAITING_FOR_INPUT | BREAK
Transitions require a sustained signal over a debounce window to avoid
oscillation. This is the Phase-1 placeholder that consumes only positional
motion; Phase 2 plugs in pose + optical-flow + sewing-machine motion.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from typing import Deque

STATES = ("WORKING", "IDLE", "AWAY", "WAITING_FOR_INPUT", "BREAK", "UNKNOWN")


@dataclass
class ActivitySignals:
    motion_magnitude: float = 0.0       # 0..1 — body motion proxy
    hand_motion: float = 0.0            # 0..1 — Phase 2
    machine_running: bool = False       # Phase 2 — visual sewing machine inference
    input_tray_empty: bool = False      # Phase 3 — tray detector
    present: bool = True                # is the worker in any zone?


@dataclass
class WorkerActivityFSM:
    debounce_seconds: float = 60.0
    state: str = "UNKNOWN"
    window: Deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=900))

    def _candidate(self, sig: ActivitySignals) -> str:
        if not sig.present:
            return "AWAY"
        if sig.machine_running and sig.motion_magnitude > 0.05:
            return "WORKING"
        if sig.input_tray_empty and sig.motion_magnitude < 0.05:
            return "WAITING_FOR_INPUT"
        if sig.motion_magnitude < 0.03 and not sig.machine_running:
            return "IDLE"
        return "WORKING"

    def discard_samples_before(self, cutoff_ts: float) -> None:
        """Prune all in-window samples with ts < cutoff_ts.

        Called by the handler when a pipeline outage is detected, BEFORE
        calling update() on the post-outage frame. Without this, the last
        pre-outage sample's weight is `next.ts - self.ts`, i.e. the entire
        gap — meaning a 30s outage would credit 30s of unobserved WORKING
        (or whatever the state was) to the debounce vote. Dropping the
        pre-outage samples entirely makes the gap contribute no vote weight,
        which is the honest reading.

        Long gaps (> debounce) already self-heal via the `t >= cutoff`
        filter in update() — this helper only matters for gaps SHORTER than
        the debounce window.
        """
        self.window = deque(
            ((t, s) for (t, s) in self.window if t >= cutoff_ts),
            maxlen=self.window.maxlen,
        )

    def update(self, ts: float, sig: ActivitySignals) -> str:
        cand = self._candidate(sig)
        self.window.append((ts, cand))
        # Confirm only if `cand` held the majority of TIME over the debounce
        # window. Time-weighted, not frame-count weighted (Finding 12): under
        # variable frame rates (which the ingestion layer produces on reconnects)
        # counting frames lets a burst of frames swing the vote. We weight each
        # sample by the seconds it represents (gap to the next sample), so the
        # debounce is genuinely a 60s-of-time majority regardless of fps.
        cutoff = ts - self.debounce_seconds
        # collect in-window samples oldest->newest
        samples = [(t, s) for (t, s) in self.window if t >= cutoff]
        if not samples:
            return self.state
        votes: dict[str, float] = {}
        for i, (t, s) in enumerate(samples):
            # duration this sample represents: until the next sample, or until
            # `ts` for the most recent one (min 1ms to avoid zero weight)
            nxt = samples[i + 1][0] if i + 1 < len(samples) else ts
            weight = max(nxt - t, 1e-3)
            votes[s] = votes.get(s, 0.0) + weight
        if not votes:
            return self.state
        winner = max(votes, key=votes.get)
        total = sum(votes.values())
        # Confirm a transition only when the winner holds a strict majority of the
        # window's TIME (> 50%) and we have at least a few seconds of evidence.
        if (winner != self.state
                and votes[winner] > total / 2.0
                and total >= 3.0):
            self.state = winner
        return self.state
