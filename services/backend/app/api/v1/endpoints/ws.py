"""WebSocket bridge — relays events from Redis streams to connected clients."""
from __future__ import annotations
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)
_settings = get_settings()


@router.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    await ws.accept()
    r = Redis.from_url(_settings.redis_url, decode_responses=True)
    last_id = "$"
    try:
        while True:
            resp = await r.xread({_settings.stream_events: last_id}, block=5_000, count=50)
            if not resp:
                await ws.send_text(json.dumps({"type": "heartbeat"}))
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id
                    await ws.send_text(json.dumps({"type": "event", "id": entry_id, "data": fields}))
    except WebSocketDisconnect:
        log.info("ws.disconnect")
    except Exception as e:  # pragma: no cover
        log.warning("ws.error", error=str(e))
    finally:
        await r.aclose()
