"""Tests for the new user-facing camera endpoints.

  GET /api/v1/cameras/{camera_id}/frame
  GET /api/v1/cameras/{camera_id}/zones

Same test shape as `test_tenants_access.py`: build a minimal FastAPI app
mounting the real router, override the DB and (for frame) the Redis
dependency, hit through TestClient. Auth uses the real `current_user`
dependency and a signed access token, so a regression that swaps
`Depends(current_user)` for something looser will fail these tests too.
"""
from __future__ import annotations

import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "y" * 32)

import io
import struct
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import fakeredis.aioredis as fr
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.api.v1.endpoints import cameras as cameras_ep
from app.db.session import get_db

SECRET = os.environ["JWT_SECRET"]
ALGO = "HS256"

CAM_ID = "00000000-0000-4000-8000-00000000c001"
MISSING_CAM_ID = "00000000-0000-4000-8000-0000000decea"
WS_ID = "00000000-0000-4000-8000-00000000ff01"
ZONE_ID = "00000000-0000-4000-8000-00000000ff11"


def _access(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "typ": "access", "roles": ["viewer"], "superuser": False,
         "exp": datetime.now(timezone.utc) + timedelta(seconds=900)},
        SECRET, algorithm=ALGO,
    )


def _hdr(sub: str = None) -> dict:
    return {"Authorization": f"Bearer {_access(sub or str(uuid.uuid4()))}"}


# ---------------------------------------------------------------------------- #
# DB stub — enough to satisfy the endpoints' `db.get()` and `db.execute()`
# calls without needing a real Postgres. Test fixture parameterises what
# `get(Camera, id)` returns and what rows the zone SELECT yields.
# ---------------------------------------------------------------------------- #
class _StubSession:
    def __init__(self, camera=None, zone_rows=None):
        self._camera = camera
        self._zone_rows = zone_rows or []

    async def get(self, model, pk):
        # cameras_ep only calls db.get(Camera, camera_id).
        return self._camera

    async def execute(self, _stmt):
        rows = self._zone_rows
        class _Result:
            def all(self_inner):
                return rows
        return _Result()


def _make_app(camera=None, zone_rows=None, redis_client=None):
    """Fresh FastAPI app with the cameras router mounted at /api/v1/cameras and
    both DB and Redis dependencies overridden."""
    app = FastAPI()
    app.include_router(cameras_ep.router, prefix="/api/v1/cameras")

    async def _db():
        yield _StubSession(camera=camera, zone_rows=zone_rows)

    app.dependency_overrides[get_db] = _db
    if redis_client is not None:
        app.dependency_overrides[cameras_ep.get_frames_redis] = lambda: redis_client
    return app


# ---------------------------------------------------------------------------- #
# /frame
# ---------------------------------------------------------------------------- #
def _fake_jpeg_bytes() -> bytes:
    # Not a real JPEG — the endpoint just passes the payload through, so any
    # bytes are fine. Prefixed with the JPEG SOI marker so anything downstream
    # that sniffs bytes gets the right hint.
    return b"\xff\xd8\xff\xe0" + b"IMAGINE THIS IS A REAL FRAME " * 8


@pytest_asyncio.fixture
async def fresh_redis():
    r = fr.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


def test_frame_requires_auth():
    app = _make_app(camera=SimpleNamespace(id=CAM_ID))
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM_ID}/frame")
    assert r.status_code == 401


def test_frame_404_when_camera_unknown(fresh_redis):
    app = _make_app(camera=None, redis_client=fresh_redis)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{MISSING_CAM_ID}/frame", headers=_hdr())
    assert r.status_code == 404
    assert "camera not found" in r.json()["detail"].lower()


def test_frame_404_when_stream_empty(fresh_redis):
    """Camera exists in DB but no frames have been published yet."""
    app = _make_app(camera=SimpleNamespace(id=CAM_ID), redis_client=fresh_redis)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM_ID}/frame", headers=_hdr())
    assert r.status_code == 404
    assert "no frames" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_frame_returns_jpeg_and_dimension_headers(fresh_redis):
    jpg = _fake_jpeg_bytes()
    # Mirror the ingestion worker's XADD payload exactly (rtsp_worker.py:110).
    await fresh_redis.xadd(
        f"stream:frames:{CAM_ID}",
        {
            "camera_id": CAM_ID,
            "ts": struct.pack("d", 1_700_000_000.0),
            "w": 1280,
            "h": 720,
            "jpg": jpg,
        },
    )
    app = _make_app(camera=SimpleNamespace(id=CAM_ID), redis_client=fresh_redis)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM_ID}/frame", headers=_hdr())
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-frame-width"] == "1280"
    assert r.headers["x-frame-height"] == "720"
    assert r.content == jpg


@pytest.mark.asyncio
async def test_frame_returns_newest_when_multiple_published(fresh_redis):
    """XREVRANGE COUNT 1 must return the *latest* frame, not the first."""
    old = b"\xff\xd8" + b"old" * 4
    new = b"\xff\xd8" + b"new" * 4
    key = f"stream:frames:{CAM_ID}"
    await fresh_redis.xadd(key, {"camera_id": CAM_ID, "w": 1280, "h": 720, "jpg": old})
    await fresh_redis.xadd(key, {"camera_id": CAM_ID, "w": 1920, "h": 1080, "jpg": new})
    app = _make_app(camera=SimpleNamespace(id=CAM_ID), redis_client=fresh_redis)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM_ID}/frame", headers=_hdr())
    assert r.status_code == 200
    assert r.content == new
    assert r.headers["x-frame-width"] == "1920"
    assert r.headers["x-frame-height"] == "1080"


# ---------------------------------------------------------------------------- #
# /zones
# ---------------------------------------------------------------------------- #
def test_zones_requires_auth():
    app = _make_app(camera=SimpleNamespace(id=CAM_ID))
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM_ID}/zones")
    assert r.status_code == 401


def test_zones_404_when_camera_unknown():
    app = _make_app(camera=None)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{MISSING_CAM_ID}/zones", headers=_hdr())
    assert r.status_code == 404
    assert "camera not found" in r.json()["detail"].lower()


def test_zones_returns_empty_list_for_camera_without_zones():
    app = _make_app(camera=SimpleNamespace(id=CAM_ID), zone_rows=[])
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM_ID}/zones", headers=_hdr())
    assert r.status_code == 200
    assert r.json() == []


def test_zones_returns_zone_plus_workstation_name():
    zone = SimpleNamespace(
        id=ZONE_ID,
        workstation_id=WS_ID,
        kind="seat",
        polygon=[[10, 20], [110, 20], [110, 120], [10, 120]],
        layout_version=1,
    )
    zone_rows = [(zone, WS_ID, "Station 1")]
    app = _make_app(camera=SimpleNamespace(id=CAM_ID), zone_rows=zone_rows)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM_ID}/zones", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert row["zone_id"] == ZONE_ID
    assert row["workstation_id"] == WS_ID
    assert row["workstation_name"] == "Station 1"
    assert row["kind"] == "seat"
    assert row["polygon"] == [[10, 20], [110, 20], [110, 120], [10, 120]]
    assert row["layout_version"] == 1
