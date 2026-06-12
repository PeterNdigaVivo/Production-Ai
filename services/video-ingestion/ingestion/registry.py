"""Polls the backend for the list of active cameras and reconciles workers."""
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass

import httpx
import structlog
from redis.asyncio import Redis

from ingestion.config import settings
from ingestion.rtsp_worker import RTSPWorker

log = structlog.get_logger(__name__)


@dataclass
class CameraSpec:
    id: uuid.UUID
    rtsp_url: str
    fps_target: int


class CameraRegistry:
    """Reconciles a set of RTSPWorkers with the backend's active-cameras list.

    Cameras are not registered here; they are created through the backend admin
    API. ONVIF auto-discovery can populate the backend separately.
    """
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.workers: dict[uuid.UUID, tuple[RTSPWorker, asyncio.Task]] = {}
        self._client = httpx.AsyncClient(timeout=10.0)

    async def fetch_cameras(self) -> list[CameraSpec]:
        # Phase-1: ingestion service has no auth token. In production this calls
        # an internal service token. For now we use an unauthenticated `/internal`
        # endpoint or the public `/health` polling for connectivity only.
        try:
            r = await self._client.get(f"{settings.backend_url}/api/v1/cameras/_internal")
            r.raise_for_status()
            return [
                CameraSpec(uuid.UUID(c["id"]), c["rtsp_url"], int(c.get("fps_target", 8)))
                for c in r.json()
            ]
        except Exception as e:
            log.warning("registry.fetch_failed", error=str(e))
            return []

    async def reconcile(self) -> None:
        desired = {c.id: c for c in await self.fetch_cameras()}
        current = set(self.workers.keys())
        to_start = set(desired) - current
        to_stop = current - set(desired)

        for cid in to_stop:
            worker, task = self.workers.pop(cid)
            await worker.stop()
            task.cancel()

        for cid in to_start:
            spec = desired[cid]
            worker = RTSPWorker(spec.id, spec.rtsp_url, self.redis)
            task = asyncio.create_task(worker.run(), name=f"rtsp-{cid}")
            self.workers[cid] = (worker, task)
            log.info("registry.started", camera_id=str(cid))

    async def run_forever(self, interval: float = 30.0) -> None:
        while True:
            await self.reconcile()
            await asyncio.sleep(interval)
