"""Background tasks. All tasks are idempotent — they can be retried safely."""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_

from app.workers.celery_app import celery_app
from app.core.config import get_settings
from app.core.logging import get_logger
from app.workers.db import task_session
from app.db.models import Camera, Alert
from app.services.rollup import rollup_window, workers_idle_too_long

log = get_logger(__name__)
_settings = get_settings()

# Window length for production_records roll-up (matches the 5-min beat cadence).
ROLLUP_WINDOW_MINUTES = 5
# Idle-alert threshold (seconds of continuous IDLE before alerting).
IDLE_ALERT_SECONDS = 180


def _floor_to_window(now: datetime, minutes: int) -> datetime:
    """Floor `now` to the start of the most recently COMPLETED window."""
    epoch_min = int(now.timestamp() // 60)
    floored_min = (epoch_min // minutes) * minutes
    return datetime.fromtimestamp(floored_min * 60, tz=timezone.utc)


# --------------------------------------------------------------------------- #
# Finding 8 — production_records roll-up
# --------------------------------------------------------------------------- #
@celery_app.task(name="app.workers.tasks.rollup_production_records", bind=True, max_retries=3)
def rollup_production_records(self):
    """Aggregate the most recently completed window into production_records."""
    try:
        written = asyncio.run(_rollup_async())
        log.info("rollup.done", rows=written)
    except Exception as e:  # pragma: no cover
        log.warning("rollup.error", error=str(e))
        raise self.retry(exc=e, countdown=30)


async def _rollup_async() -> int:
    now = datetime.now(timezone.utc)
    # roll up the previous fully-closed window, e.g. at 12:07 -> [12:00, 12:05)
    window_end = _floor_to_window(now, ROLLUP_WINDOW_MINUTES)
    window_start = window_end - timedelta(minutes=ROLLUP_WINDOW_MINUTES)
    async with task_session() as db:
        return await rollup_window(db, window_start, window_end)


# --------------------------------------------------------------------------- #
# Finding 6 — debounced, de-duplicated idle alerts
# --------------------------------------------------------------------------- #
@celery_app.task(name="app.workers.tasks.check_idle_workers")
def check_idle_workers():
    """Raise ONE alert per worker who has been continuously IDLE past the
    threshold, and not while WAITING_FOR_INPUT. De-duplicated: if an unack'd
    idle alert already exists for that worker, don't raise another."""
    asyncio.run(_check_idle_workers_async())


async def _check_idle_workers_async():
    now = datetime.now(timezone.utc)
    async with task_session() as db:
        idle = await workers_idle_too_long(db, IDLE_ALERT_SECONDS, now=now)
        if not idle:
            return
        for w in idle:
            # De-dup key. Was `{camera_id}:{worker_track_id}` — per-track
            # dedup was always fragile (ByteTrack re-ids after occlusion or
            # AWAY-return would break it), and after the FSM re-key to
            # workstation there is no owning track anyway. Per-seat is the
            # correct semantics: one open alert per idle workstation.
            # DEPLOY NOTE: acknowledge any open `worker_idle` alerts before
            # deploying this — old payloads carry the track-shaped key and
            # would dedup-collide with the new shape until they auto-close.
            key = f"{w.camera_id}:{w.workstation_id}"
            # de-dup: skip if an unacknowledged idle alert already open for this seat
            existing = (await db.execute(
                select(Alert).where(and_(
                    Alert.kind == "worker_idle",
                    Alert.acknowledged.is_(False),
                ))
            )).scalars().all()
            if any((a.payload or {}).get("worker_key") == key for a in existing):
                continue
            db.add(Alert(
                severity="warning",
                kind="worker_idle",
                title="Worker idle past threshold",
                description=(
                    f"workstation={w.workstation_id} camera={w.camera_id} "
                    f"idle for {int(w.idle_seconds)}s"
                ),
                payload={
                    "worker_key": key,
                    "camera_id": str(w.camera_id),
                    "workstation_id": str(w.workstation_id) if w.workstation_id else None,
                    "idle_since": w.idle_since.isoformat(),
                    "idle_seconds": int(w.idle_seconds),
                },
            ))
        await db.commit()


# --------------------------------------------------------------------------- #
# Existing — camera heartbeat watchdog (unchanged behaviour, kept de-duped)
# --------------------------------------------------------------------------- #
@celery_app.task(name="app.workers.tasks.check_camera_heartbeats")
def check_camera_heartbeats():
    """Emit camera_offline alerts when a camera has not heartbeated in > 90s."""
    asyncio.run(_check_camera_heartbeats_async())


async def _check_camera_heartbeats_async():
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=90)
    async with task_session() as db:
        cams = (await db.execute(select(Camera).where(Camera.is_active.is_(True)))).scalars().all()
        # existing unack'd offline alerts, to de-dup
        open_alerts = (await db.execute(
            select(Alert).where(and_(
                Alert.kind == "camera_offline", Alert.acknowledged.is_(False)))
        )).scalars().all()
        open_cams = {(a.payload or {}).get("camera_id") for a in open_alerts}
        for cam in cams:
            if cam.last_seen_at and cam.last_seen_at >= cutoff:
                continue
            if str(cam.id) in open_cams:
                continue  # already alerted, not yet acknowledged
            db.add(Alert(
                severity="warning",
                kind="camera_offline",
                title=f"Camera offline: {cam.name}",
                description=f"No heartbeat since {cam.last_seen_at}",
                payload={"camera_id": str(cam.id)},
            ))
        await db.commit()
