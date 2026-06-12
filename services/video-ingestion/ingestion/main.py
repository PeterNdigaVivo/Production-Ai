from __future__ import annotations
import asyncio
import os
import uuid

import structlog
from redis.asyncio import Redis

from ingestion.config import settings
from ingestion.registry import CameraRegistry
from ingestion.rtsp_worker import RTSPWorker

log = structlog.get_logger(__name__)


async def amain() -> None:
    redis = Redis.from_url(settings.redis_url)
    static_url = os.environ.get("STATIC_RTSP_URL")
    static_id = os.environ.get("STATIC_CAMERA_ID")
    if static_url and static_id:
        # Single-camera dev mode (skip backend registry).
        worker = RTSPWorker(uuid.UUID(static_id), static_url, redis)
        log.info("ingest.static_mode", camera_id=static_id)
        await worker.run()
        return

    reg = CameraRegistry(redis)
    log.info("ingest.starting_registry")
    await reg.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
