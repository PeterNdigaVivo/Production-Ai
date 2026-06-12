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

    def update(self, ts: float, sig: ActivitySignals) -> str:
        cand = self._candidate(sig)
        self.window.append((ts, cand))
        # Confirm only if `cand` has been the majority over the debounce window.
        cutoff = ts - self.debounce_seconds
        votes: dict[str, int] = {}
        for t, s in reversed(self.window):
            if t < cutoff:
                break
            votes[s] = votes.get(s, 0) + 1
        if not votes:
            return self.state
        winner = max(votes, key=votes.get)
        if winner != self.state and votes[winner] >= max(3, sum(votes.values()) // 2):
            self.state = winner
        return self.state
