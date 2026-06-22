from fastapi import APIRouter
from app.api.v1 import internal
from app.api.v1.endpoints import (
    auth, tenants, factories, lines, cameras, workstations, zones,
    events, alerts, analytics, ws,
)

api_router = APIRouter()
# Internal router is mounted first; its routes carry the `X-Internal-Token` gate
# and `include_in_schema=False`. Nginx blocks any external `/_internal` path.
api_router.include_router(internal.router, tags=["internal"])
api_router.include_router(auth.router,         prefix="/auth",        tags=["auth"])
api_router.include_router(tenants.router,      prefix="/tenants",     tags=["tenants"])
api_router.include_router(factories.router,    prefix="/factories",   tags=["factories"])
api_router.include_router(lines.router,        prefix="/lines",       tags=["lines"])
api_router.include_router(cameras.router,      prefix="/cameras",     tags=["cameras"])
api_router.include_router(workstations.router, prefix="/workstations", tags=["workstations"])
api_router.include_router(zones.router,        prefix="/zones",       tags=["zones"])
api_router.include_router(events.router,       prefix="/events",      tags=["events"])
api_router.include_router(alerts.router,       prefix="/alerts",      tags=["alerts"])
api_router.include_router(analytics.router,    prefix="/analytics",   tags=["analytics"])
api_router.include_router(ws.router,           prefix="/ws",          tags=["ws"])
