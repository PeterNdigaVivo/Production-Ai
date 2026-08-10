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

# Absolute threshold — deliberately NOT derived from DETECTION_TARGET_FPS.
# Roadmap capacity ceiling is 3–4 fps for 2 cameras and detection is capped
# at 2/4 CPUs, so a target-fps-derived threshold trips on every frame
# whenever the pipeline runs below its target, silently disabling the
# engine. 10s is >100× the target inter-sample interval at 4fps and
# tolerates a ~20× throughput collapse to 0.5fps, while still catching an
# ffmpeg drop / backend restart quickly. One value, one place.
PIPELINE_OUTAGE_SECONDS = 10.0

# How often to re-warn about the same legacy-payload condition per camera.
# Log on the first bad frame, then hold for this many seconds before
# warning again — never per frame.
LEGACY_WARN_INTERVAL_S = 60.0


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
        # Once-per-outage log: True while we are in a detected outage.
        # Flips False again on the first payload inside the threshold.
        self.outage_active: bool = False
        # Rate-limit the "no workstations key" warning (see handler).
        self.last_legacy_warn_ts: float | None = None


def _centroid(xyxy):
    x1, y1, x2, y2 = xyxy
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _make_handler(redis: Redis, camera_id: str, state: CameraState):
    async def handle(entry_id: bytes, fields: dict) -> bool:
        payload = json.loads(fields[b"json"])
        ts = float(payload.get("ts", time.time()))
        machine_map = payload.get("machine_running", {})  # workstation_id -> bool

        # Roster: every workstation this camera can attribute to (see the
        # invariant in tracking/main.py where the roster is derived).
        # ABSENCE of the key is a legacy/deploy-order symptom, not a quiet
        # factory. See BLOCKER 2 in the code review that shipped this fix.
        roster_raw = payload.get("workstations")
        if roster_raw is None:
            # Legacy or partial-deploy payload. Rate-limit the warning so it
            # doesn't spam at 4fps for a full deploy window.
            now = time.time()
            if (state.last_legacy_warn_ts is None
                    or now - state.last_legacy_warn_ts > LEGACY_WARN_INTERVAL_S):
                log.warning(
                    "activity.legacy_tracks_payload_no_roster",
                    camera=camera_id,
                    hint=("tracks payload has no 'workstations' key — "
                          "tracking-engine is likely running older code "
                          "than activity-engine; holding FSM state"),
                )
                state.last_legacy_warn_ts = now
            # Hold state: no roster means we cannot know present/absent, and
            # a rolling deploy where activity restarts before tracking must
            # not wipe every FSM (BLOCKER 2 in the review). Return without
            # touching fsms or last_centroid.
            state.last_payload_ts = ts
            return True
        roster: list[str] = list(roster_raw)

        # Pipeline-outage guard. Long gaps between payloads mean the video
        # feed died (ffmpeg drop, backend restart, network glitch). We
        # process the current frame normally but PRUNE each FSM's window of
        # any samples older than the gap start, so the gap contributes no
        # vote weight to the debounce vote. Skipping the frame does NOT
        # solve the weighting problem: for gaps shorter than the debounce
        # window the last pre-outage sample would still get the full gap as
        # weight, crediting an unobserved state.
        outage_gap: float | None = None
        if state.last_payload_ts is not None:
            gap = ts - state.last_payload_ts
            if gap > PIPELINE_OUTAGE_SECONDS:
                outage_gap = gap
                if not state.outage_active:
                    log.warning(
                        "activity.pipeline_outage_start",
                        camera=camera_id,
                        gap_seconds=round(gap, 2),
                        threshold=PIPELINE_OUTAGE_SECONDS,
                    )
                    state.outage_active = True
                # Prune all FSMs' windows so pre-outage samples don't lend
                # weight to the gap. Cutoff = current frame's ts (strict):
                # this drops EVERY pre-outage sample, including the one
                # right at the gap start. Keeping any sample earlier than
                # `ts` would let its weight become `ts - that.ts`, i.e.
                # the whole gap — the exact bug the guard is here to stop.
                for fsm in state.fsms.values():
                    fsm.discard_samples_before(ts)
            else:
                if state.outage_active:
                    log.info("activity.pipeline_outage_resumed",
                             camera=camera_id, gap_seconds=round(gap, 2))
                    state.outage_active = False
        state.last_payload_ts = ts

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
        # (seat deleted, seat re-zoned onto another camera). GUARD: only
        # evict when the roster is non-empty. An empty roster on this path
        # is a legacy/partial-deploy signal handled above, but belt-and-
        # braces — a caller change that lets an empty list through must not
        # wipe every FSM for the camera (BLOCKER 2 in the review). No log
        # line, indistinguishable from a quiet factory, HANDOFF lesson 1
        # in a new disguise.
        if roster:
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
