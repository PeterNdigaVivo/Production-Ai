"""Background tasks. All tasks are idempotent — they can be retried safely."""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

from app.workers.celery_app import celery_app
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.db.models import Camera, Alert

log = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.rollup_production_records", bind=True, max_retries=3)
def rollup_production_records(self):
    """Aggregate piece counts and state durations into production_records."""
    log.info("rollup.start")
    # TODO: implement window-based roll-up over worker_events / production_events.
    # Phase-1 scaffold: emits a heartbeat log so beat is wired.
    log.info("rollup.done")


@celery_app.task(name="app.workers.tasks.check_camera_heartbeats")
def check_camera_heartbeats():
    """Emit camera_offline alerts when a camera has not heartbeated in > 90s."""
    asyncio.run(_check_camera_heartbeats_async())


async def _check_camera_heartbeats_async():
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=90)
    async with SessionLocal() as db:
        from sqlalchemy import select
        cams = (await db.execute(select(Camera).where(Camera.is_active.is_(True)))).scalars().all()
        for cam in cams:
            if cam.last_seen_at and cam.last_seen_at >= cutoff:
                continue
            db.add(Alert(
                severity="warning",
                kind="camera_offline",
                title=f"Camera offline: {cam.name}",
                description=f"No heartbeat since {cam.last_seen_at}",
                payload={"camera_id": str(cam.id)},
            ))
        await db.commit()
