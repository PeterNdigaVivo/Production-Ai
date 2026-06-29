"""WebSocket bridge — relays events from Redis streams to authenticated clients.

Two fixes over the original (Finding 10):

  1. AUTHENTICATION. The socket previously accepted any connection. Browsers
     cannot set Authorization headers on a WebSocket, so the access token is
     passed as a query parameter (?token=...). It is validated before the socket
     is accepted; an invalid/missing token is rejected with a policy-violation
     close.

  2. TENANT FILTERING. `stream:events` is global across tenants. Each event is
     mapped to its tenant via the workstation it belongs to (cached
     workstation->tenant map, refreshed on a TTL), and only events for the
     caller's tenant are forwarded. Superusers see everything. Events that can't
     be mapped to a tenant are withheld from non-superusers (fail closed).
"""
from __future__ import annotations
import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.session import SessionLocal
from app.db.models import Workstation, ProductionLine, Factory

router = APIRouter()
log = get_logger(__name__)
_settings = get_settings()

# workstation_id (str) -> tenant_id (str); refreshed on a TTL
_ws_tenant_cache: dict[str, str] = {}
_ws_tenant_expires: float = 0.0
_WS_TENANT_TTL = 60.0


async def _workstation_tenant_map() -> dict[str, str]:
    global _ws_tenant_cache, _ws_tenant_expires
    if time.time() < _ws_tenant_expires and _ws_tenant_cache:
        return _ws_tenant_cache
    async with SessionLocal() as db:
        stmt = (
            select(Workstation.id, Factory.tenant_id)
            .join(ProductionLine, ProductionLine.id == Workstation.line_id)
            .join(Factory, Factory.id == ProductionLine.factory_id)
        )
        rows = (await db.execute(stmt)).all()
    _ws_tenant_cache = {str(ws): str(tid) for ws, tid in rows}
    _ws_tenant_expires = time.time() + _WS_TENANT_TTL
    return _ws_tenant_cache


def _authenticate(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = decode_token(token)
    except ValueError:
        return None
    if payload.get("typ") != "access":
        return None
    return payload


@router.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    token = ws.query_params.get("token")
    user = _authenticate(token)
    if user is None:
        # Reject before accepting the socket.
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    is_superuser = bool(user.get("superuser"))
    tenant_id = user.get("tenant_id")

    r = Redis.from_url(_settings.redis_url, decode_responses=True)
    last_id = "$"
    try:
        while True:
            resp = await r.xread({_settings.stream_events: last_id}, block=5_000, count=50)
            if not resp:
                await ws.send_text(json.dumps({"type": "heartbeat"}))
                continue
            ws_tenant = None if is_superuser else await _workstation_tenant_map()
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id
                    if not is_superuser:
                        ev_ws = fields.get("workstation_id") or ""
                        ev_tenant = ws_tenant.get(ev_ws) if ev_ws else None
                        # fail closed: drop events not provably in the caller's tenant
                        if ev_tenant is None or ev_tenant != tenant_id:
                            continue
                    await ws.send_text(json.dumps({"type": "event", "id": entry_id, "data": fields}))
    except WebSocketDisconnect:
        log.info("ws.disconnect")
    except Exception as e:  # pragma: no cover
        log.warning("ws.error", error=str(e))
    finally:
        await r.aclose()
