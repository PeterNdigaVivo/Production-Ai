"""Activity engine entrypoint.

Consumes `stream:tracks:*` via a per-stream CONSUMER GROUP, maintains a
per-(camera, workstation) FSM, and emits `worker_state_changed` events when the
FSM transitions.

The FSM used to be keyed by track_id, which made AWAY unreachable: a track
that vanishes because the operator stood up leaves nothing to iterate, so
the handler never called fsm.update() and no AWAY transition was ever
emitted. Absence is a property of the SEAT, not of the track. Keying the
FSM by workstation_id — and driving `present` from the roster the tracking
engine now publishes alongside the tracks — makes AWAY reachable by
symmetry with every other state. See docstring on `state_machine.py` and
the ROADMAP change log entry for the surrounding context.

IMPORTANT — statefulness and scaling. This engine (like tracking) holds
PER-CAMERA STATE (the FSMs). A consumer group gives crash-safety (recover
unacked messages on restart) and lets you split *different cameras* across
replicas. It must NOT have two replicas consuming the SAME camera's stream, or
each would see only half that camera's frames and the FSM would be wrong.

The discovery loop below assigns one task per camera within a single process,
which is correct. For multi-replica scale-out, shard cameras across replicas by
camera_id (e.g. a hash modulo replica count) so each camera is owned by exactly
one replica. See docs/steps/step5_consumer_groups.md for the sharding note.
Policy is ALL — never drop track frames feeding the FSM.
"""
from __future__ import annotations
import asyncio
import json
import math
import os
import time

import structlog
from redis.asyncio import Redis

from activity.state_machine import WorkerActivityFSM, ActivitySignals
from activity.streambus.consumer import consume, BacklogPolicy, default_consumer_name

log = structlog.get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
STREAM_TRACKS = os.environ.get("REDIS_STREAM_TRACKS", "stream:tracks")
STREAM_EVENTS = os.environ.get("REDIS_STREAM_EVENTS", "stream:events")
GROUP = "activity-engine"

# Distinguish a legitimately-quiet pipeline (worker on break, no one in frame)
# from a broken pipeline (ffmpeg dropped, backend restart). If the gap
# between successive tracks payloads exceeds THIS threshold, we hold state
# and log a warning instead of emitting AWAY on every seat — recording
# operators as absent because the video feed died is the exact class of
# number that destroys dashboard trust.
DETECTION_TARGET_FPS = float(os.environ.get("DETECTION_TARGET_FPS", "4"))
PIPELINE_OUTAGE_SECONDS = 3.0 / DETECTION_TARGET_FPS


class CameraState:
    """Per-camera state for the activity engine.

    Keyed by workstation_id (str), not track_id — see module docstring.
    Everything a workstation carries lives here so eviction on roster change
    stays a single-dict-per-item cleanup.
    """
    def __init__(self) -> None:
        self.fsms: dict[str, WorkerActivityFSM] = {}
        self.last_centroid: dict[str, tuple[float, float]] = {}
        # Last successfully-processed tracks-payload ts (seconds). Used to
        # detect pipeline outages that must NOT be recorded as AWAY.
        self.last_payload_ts: float | None = None


def _centroid(xyxy):
    x1, y1, x2, y2 = xyxy
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _make_handler(redis: Redis, camera_id: str, state: CameraState):
    async def handle(entry_id: bytes, fields: dict) -> bool:
        payload = json.loads(fields[b"json"])
        ts = float(payload.get("ts", time.time()))
        machine_map = payload.get("machine_running", {})  # workstation_id -> bool

        # Roster: every workstation this camera can attribute to (see the
        # invariant in tracking/main.py where the roster is derived). Missing
        # key on a legacy payload → empty roster → we skip all FSM updates
        # for that frame rather than defaulting to something wrong.
        roster: list[str] = list(payload.get("workstations") or [])

        # Pipeline-outage guard. A long gap between payloads is a broken
        # pipeline, not an empty factory. Hold state and warn.
        outage = False
        if state.last_payload_ts is not None:
            gap = ts - state.last_payload_ts
            if gap > PIPELINE_OUTAGE_SECONDS:
                log.warning("activity.pipeline_outage_hold_state",
                            camera=camera_id, gap_seconds=round(gap, 2),
                            threshold=PIPELINE_OUTAGE_SECONDS)
                outage = True
        state.last_payload_ts = ts
        if outage:
            # State held; do NOT run FSM.update this frame. The next in-window
            # payload resumes normal processing.
            return True

        # Group this frame's tracks by workstation. A track with no
        # workstation is an aisle-walker; it never drives any FSM (per the
        # design rule: absence-of-a-seat is not an operator event).
        tracks_by_ws: dict[str, list[dict]] = {}
        for tr in payload.get("tracks", []):
            ws = tr.get("workstation_id")
            if ws is None:
                continue
            tracks_by_ws.setdefault(ws, []).append(tr)

        # Iterate the roster (not tracks) — this is what makes AWAY reachable.
        # A workstation with no track in this frame gets `present=False` and
        # rides the same 60s time-weighted debounce as every other state.
        for ws_id in roster:
            present = ws_id in tracks_by_ws
            motion = 0.0

            if present:
                # If two tracks land in this seat in the same frame (occlusion,
                # a supervisor leaning in), take the highest-confidence one for
                # the motion signal and ignore the other. Log at debug, not
                # per frame.
                candidates = tracks_by_ws[ws_id]
                if len(candidates) > 1:
                    log.debug("activity.multi_track_seat",
                              camera=camera_id, workstation=ws_id,
                              n=len(candidates))
                chosen = max(candidates, key=lambda t: float(t.get("conf", 0.0)))
                cx, cy = _centroid(chosen["xyxy"])
                prev = state.last_centroid.get(ws_id)
                state.last_centroid[ws_id] = (cx, cy)
                if prev is not None:
                    dx, dy = cx - prev[0], cy - prev[1]
                    motion = min(1.0, math.hypot(dx, dy) / 50.0)
            else:
                # No track this frame — clear the centroid so a returning
                # operator's first sample doesn't compare against a stale one.
                state.last_centroid.pop(ws_id, None)

            machine_running = bool(machine_map.get(ws_id, False))

            fsm = state.fsms.setdefault(ws_id, WorkerActivityFSM())
            prev_state = fsm.state
            new_state = fsm.update(ts, ActivitySignals(
                motion_magnitude=motion,
                machine_running=machine_running,
                present=present,
            ))
            if new_state != prev_state:
                await redis.xadd(STREAM_EVENTS, {
                    "type": "worker_state_changed",
                    "ts": str(ts),
                    "camera_id": camera_id,
                    "workstation_id": ws_id,
                    # worker_track_id is now a sentinel — the FSM is keyed
                    # by workstation, not track. Kept in the payload for
                    # backwards compatibility with downstream consumers that
                    # still index it, but no partition key uses it. See the
                    # ROADMAP change log entry that shipped with this fix.
                    "track_id": "0",
                    "state": new_state,
                    "prev_state": prev_state,
                }, maxlen=10_000, approximate=True)

        # Bound the registries: evict any workstation no longer on the roster
        # (seat deleted, seat re-zoned onto another camera). Also fixes the
        # unbounded-growth issue the previous track-keyed dicts had under
        # track-ID churn.
        stale = set(state.fsms) - set(roster)
        for ws_id in stale:
            state.fsms.pop(ws_id, None)
            state.last_centroid.pop(ws_id, None)
        if stale:
            log.info("activity.evict_stale_workstations",
                     camera=camera_id, workstations=sorted(stale))

        return True
    return handle


async def process_camera(redis: Redis, key: str, stop: asyncio.Event) -> None:
    camera_id = key.rsplit(":", 1)[-1]
    state = CameraState()
    log.info("activity.attach", camera=camera_id, consumer=default_consumer_name())
    await consume(
        redis, key, GROUP, _make_handler(redis, camera_id, state),
        policy=BacklogPolicy.ALL,  # FSM needs every track frame
        block_ms=5_000, count=1, stop=stop,
    )


async def amain() -> None:
    redis = Redis.from_url(REDIS_URL)
    stop = asyncio.Event()
    tasks: dict[str, asyncio.Task] = {}
    while not stop.is_set():
        keys = []
        async for k in redis.scan_iter(match=f"{STREAM_TRACKS}:*"):
            keys.append(k.decode() if isinstance(k, bytes) else k)
        for k in keys:
            if k not in tasks or tasks[k].done():
                tasks[k] = asyncio.create_task(
                    process_camera(redis, k, stop), name=f"act-{k}")
        await asyncio.sleep(15.0)


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
