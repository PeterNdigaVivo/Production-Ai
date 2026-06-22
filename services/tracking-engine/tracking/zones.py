"""Workstation zone lookup. Pulls polygons from the backend on a TTL cache."""
from __future__ import annotations
import asyncio
import os
import time
import httpx
from shapely.geometry import Polygon, Point


class ZoneCache:
    def __init__(self, backend_url: str, ttl_seconds: float = 60.0,
                 internal_token: str | None = None) -> None:
        self.backend_url = backend_url
        self.ttl = ttl_seconds
        self.expires_at = 0.0
        # camera_id -> list[(workstation_id, kind, Polygon)]
        self.zones: dict[str, list[tuple[str, str, Polygon]]] = {}
        token = internal_token or os.environ.get("INTERNAL_SERVICE_TOKEN", "")
        self._client = httpx.AsyncClient(timeout=10.0, headers={"X-Internal-Token": token})
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        try:
            r = await self._client.get(f"{self.backend_url}/api/v1/zones/_internal")
            r.raise_for_status()
            grouped: dict[str, list[tuple[str, str, Polygon]]] = {}
            for z in r.json():
                cam = z["camera_id"]
                poly = Polygon(z["polygon"])
                if not poly.is_valid:
                    continue
                grouped.setdefault(cam, []).append((z["workstation_id"], z["kind"], poly))
            self.zones = grouped
            self.expires_at = time.time() + self.ttl
        except Exception:
            # Keep serving stale zones.
            self.expires_at = time.time() + 10.0

    async def get(self, camera_id: str) -> list[tuple[str, str, Polygon]]:
        if time.time() >= self.expires_at:
            async with self._lock:
                if time.time() >= self.expires_at:
                    await self._refresh()
        return self.zones.get(camera_id, [])


def assign_workstation(zones: list[tuple[str, str, Polygon]],
                       xyxy: tuple[float, float, float, float]) -> str | None:
    """Returns workstation_id whose seat zone contains the bottom-center of the box."""
    x1, y1, x2, y2 = xyxy
    foot = Point((x1 + x2) / 2, y2)
    best: tuple[str, str, Polygon] | None = None
    for ws_id, kind, poly in zones:
        if kind == "seat" and poly.contains(foot):
            best = (ws_id, kind, poly)
            break
    if best is None:
        # Fall back to any zone containing the foot.
        for ws_id, kind, poly in zones:
            if poly.contains(foot):
                best = (ws_id, kind, poly)
                break
    return best[0] if best else None
