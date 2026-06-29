"""Zone provider for the detection engine's machine-flow signal.

The tracking engine has its own shapely-based ZoneCache for point-in-polygon
assignment. Detection only needs the machine-zone polygons as raw point lists
(for optical-flow bbox cropping), so this is a lighter cache that pulls the same
`/zones/_internal` endpoint and returns (workstation_id, kind, polygon_points).

Returns a callable: `await provider(camera_id) -> list[(ws_id, kind, points)]`.
"""
from __future__ import annotations

import os
import time

import httpx


class _MachineZoneCache:
    def __init__(self, backend_url: str, ttl_seconds: float = 60.0,
                 internal_token: str | None = None) -> None:
        self.backend_url = backend_url
        self.ttl = ttl_seconds
        self.expires_at = 0.0
        self.zones: dict[str, list[tuple[str, str, list]]] = {}
        token = internal_token or os.environ.get("INTERNAL_SERVICE_TOKEN", "")
        self._client = httpx.AsyncClient(timeout=10.0, headers={"X-Internal-Token": token})

    async def _refresh(self) -> None:
        try:
            r = await self._client.get(f"{self.backend_url}/api/v1/zones/_internal")
            r.raise_for_status()
            grouped: dict[str, list[tuple[str, str, list]]] = {}
            for z in r.json():
                grouped.setdefault(z["camera_id"], []).append(
                    (z["workstation_id"], z["kind"], z["polygon"]))
            self.zones = grouped
            self.expires_at = time.time() + self.ttl
        except Exception:
            self.expires_at = time.time() + 10.0  # serve stale, retry soon

    async def get(self, camera_id: str) -> list[tuple[str, str, list]]:
        if time.time() >= self.expires_at:
            await self._refresh()
        return self.zones.get(camera_id, [])


def make_zone_provider():
    backend_url = os.environ.get("BACKEND_URL", "http://backend:8000")
    cache = _MachineZoneCache(backend_url)

    async def provider(camera_id: str):
        return await cache.get(camera_id)

    return provider
