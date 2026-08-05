"""Zone read endpoints must return only the latest layout_version per
(workstation_id, kind).

Setup mimics an edit: workstation A gets a `seat` zone at layout_version=1
and a second `seat` zone at layout_version=2 (the edit). A `machine` zone
at v1 rides along to prove the "latest" is scoped per kind — the seat's v2
must NOT hide the machine. Workstation B (same camera) gets its own seat at
v1 as a control.

We use SQLite in-memory + the real ORM models so the exact SQLAlchemy
expressions the endpoints run get executed end-to-end. `Zone.polygon` is
typed as PostgreSQL JSONB in production; before creating the schema we
swap it to generic JSON so SQLite can render the CREATE TABLE (this only
affects the test process — production code is untouched). Foreign-key
tables aren't materialised because SQLite doesn't validate FKs by default,
which keeps the fixture minimal.

Run: pytest tests/test_zone_latest_read.py -v
"""
from __future__ import annotations

import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "y" * 32)

import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import JSON, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# --- Local schema shim -------------------------------------------------------
# Two production-only dialect features need per-test-process swaps so the same
# ORM classes work against SQLite:
#   * `Zone.polygon` is `postgresql.JSONB` — SQLite can't render it → `JSON`.
#   * UUID columns are `UUID(as_uuid=True)`, which trips two SQLite gotchas:
#     (a) FastAPI hands `str` from URL params into `Column == str` comparisons
#         which SQLAlchemy's bind processor rejects with `no attribute 'hex'`;
#     (b) `insertmanyvalues` uses a sentinel post-processor that pipes returned
#         IDs through `str(uuid.UUID(value))`, adding hyphens and de-correlating
#         inserted rows from returned rows — silently produces duplicate-key
#         collisions in batch commits.
#     Replacing the columns with plain `String(36)` sidesteps both without
#     touching production code.
from app.db.models import Camera, Zone, Workstation

Zone.__table__.c.polygon.type = JSON()
for _col in (
    Workstation.__table__.c.id,
    Workstation.__table__.c.line_id,
    Workstation.__table__.c.camera_id,
    Zone.__table__.c.id,
    Zone.__table__.c.workstation_id,
    Camera.__table__.c.id,
    Camera.__table__.c.line_id,
):
    _col.type = String(36)
# Model default factory returns `uuid.UUID`; force `str` so auto-generated IDs
# also round-trip through the swapped String columns cleanly.
import app.db.models.tenancy as _tenancy
_tenancy._uuid = lambda: str(uuid.uuid4())

from app.api.v1.endpoints import cameras as cameras_ep
from app.api.v1.endpoints import zones as zones_ep
from app.api.v1 import internal as internal_ep
from app.api.deps import require_internal_token
from app.db.session import get_db

SECRET = os.environ["JWT_SECRET"]
INTERNAL_TOKEN = os.environ["INTERNAL_SERVICE_TOKEN"]
ALGO = "HS256"


def _access_token() -> str:
    return jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "typ": "access",
            "roles": ["viewer"],
            "superuser": False,
            "exp": datetime.now(timezone.utc) + timedelta(seconds=900),
        },
        SECRET,
        algorithm=ALGO,
    )


def _bearer() -> dict:
    return {"Authorization": f"Bearer {_access_token()}"}


# --- IDs (fixed so failure messages read cleanly) ---------------------------
# Passed as strings because the UUID-column swap above stores them as
# CHAR(32); using strings throughout keeps the fixture symmetric with URLs.
CAM = "00000000-0000-4000-8000-000000000c01"
LINE = "00000000-0000-4000-8000-000000000010"
WS_A = "00000000-0000-4000-8000-00000000a001"
WS_B = "00000000-0000-4000-8000-00000000a002"

# Polygons — v1 seats are the "old" position, v2 is the "moved" position.
SEAT_A_V1 = [[10, 10], [110, 10], [110, 110], [10, 110]]
SEAT_A_V2 = [[500, 500], [600, 500], [600, 600], [500, 600]]   # moved
MACHINE_A_V1 = [[200, 10], [300, 10], [300, 110], [200, 110]]
SEAT_B_V1 = [[700, 10], [800, 10], [800, 110], [700, 110]]


# --- Fixtures ---------------------------------------------------------------
@pytest_asyncio.fixture
async def db_session():
    """Fresh in-memory SQLite with just the two tables we need, seeded with
    the workstations + zones described above. Yields a session factory the
    endpoint override can call for each request."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as c:
        await c.run_sync(lambda cx: Camera.__table__.create(cx, checkfirst=True))
        await c.run_sync(lambda cx: Workstation.__table__.create(cx, checkfirst=True))
        await c.run_sync(lambda cx: Zone.__table__.create(cx, checkfirst=True))

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    def _mkzone(zid: str, ws: str, kind: str, poly, ver: int) -> Zone:
        # Explicit str ID because the swapped String column can't accept the
        # model default_factory's uuid.UUID objects.
        z = Zone(workstation_id=ws, kind=kind, polygon=poly, layout_version=ver)
        z.id = zid
        return z

    async with Session() as s:
        # A real Camera row so `list_camera_zones`'s existence guard passes.
        cam = Camera(line_id=LINE, name="Cam-A", rtsp_url="rtsp://x/y")
        cam.id = CAM
        s.add(cam)
        # Explicit IDs so the assertions can reference them without a lookup.
        ws_a = Workstation(line_id=LINE, camera_id=CAM, code="WS-A", name="Station A")
        ws_b = Workstation(line_id=LINE, camera_id=CAM, code="WS-B", name="Station B")
        ws_a.id = WS_A
        ws_b.id = WS_B
        s.add_all([ws_a, ws_b])

        # Workstation A: seat v1 (should be hidden), seat v2 (should surface),
        # machine v1 (should surface — partitioning is per kind, so the seat
        # edit must not hide it).
        s.add(_mkzone("00000000-0000-4000-8000-000000000001", WS_A, "seat", SEAT_A_V1, 1))
        s.add(_mkzone("00000000-0000-4000-8000-000000000002", WS_A, "seat", SEAT_A_V2, 2))
        s.add(_mkzone("00000000-0000-4000-8000-000000000003", WS_A, "machine", MACHINE_A_V1, 1))
        # Workstation B: control — one seat, no edit. Must appear untouched.
        s.add(_mkzone("00000000-0000-4000-8000-000000000004", WS_B, "seat", SEAT_B_V1, 1))
        await s.commit()

    async def _dep():
        async with Session() as s:
            yield s

    yield _dep
    await engine.dispose()


def _mount_all(dep) -> FastAPI:
    app = FastAPI()
    app.include_router(zones_ep.router,    prefix="/api/v1/zones")
    app.include_router(cameras_ep.router,  prefix="/api/v1/cameras")
    app.include_router(internal_ep.router)  # includes /zones/_internal etc.
    app.dependency_overrides[get_db] = dep
    # Bypass the internal-token gate for the /zones/_internal test — we
    # verify the semantic contract, not the auth (which is covered elsewhere).
    app.dependency_overrides[require_internal_token] = lambda: None
    return app


# --- Tests ------------------------------------------------------------------
def test_list_zones_returns_only_latest_per_workstation_and_kind(db_session):
    app = _mount_all(db_session)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/zones?workstation_id={WS_A}", headers=_bearer())
    assert r.status_code == 200, r.text
    body = r.json()
    # Expect exactly 2 rows for WS_A: seat v2 and machine v1.
    kinds = sorted((z["kind"], z["layout_version"], z["polygon"]) for z in body)
    assert len(body) == 2, f"expected 2 zones (seat v2 + machine v1), got {len(body)}: {body}"
    # Seat must be v2 with the moved polygon.
    seats = [z for z in body if z["kind"] == "seat"]
    assert len(seats) == 1
    assert seats[0]["layout_version"] == 2
    assert seats[0]["polygon"] == SEAT_A_V2
    # Machine is untouched (still v1) — proves partitioning is per kind.
    machines = [z for z in body if z["kind"] == "machine"]
    assert len(machines) == 1
    assert machines[0]["layout_version"] == 1
    assert machines[0]["polygon"] == MACHINE_A_V1


def test_cameras_zones_endpoint_returns_only_latest(db_session):
    app = _mount_all(db_session)
    with TestClient(app) as c:
        r = c.get(f"/api/v1/cameras/{CAM}/zones", headers=_bearer())
    assert r.status_code == 200, r.text
    body = r.json()
    # Expect: WS_A seat v2, WS_A machine v1, WS_B seat v1. NO WS_A seat v1.
    assert len(body) == 3, f"expected 3 zones total, got {len(body)}: {body}"
    ws_a_seats = [z for z in body if z["workstation_id"] == str(WS_A) and z["kind"] == "seat"]
    assert len(ws_a_seats) == 1
    assert ws_a_seats[0]["layout_version"] == 2
    assert ws_a_seats[0]["polygon"] == SEAT_A_V2
    assert ws_a_seats[0]["workstation_name"] == "Station A"

    # WS_A machine still there (per-kind partitioning).
    ws_a_machines = [z for z in body if z["workstation_id"] == str(WS_A) and z["kind"] == "machine"]
    assert len(ws_a_machines) == 1
    assert ws_a_machines[0]["layout_version"] == 1

    # WS_B seat unaffected by WS_A's edit (per-workstation partitioning).
    ws_b_seats = [z for z in body if z["workstation_id"] == str(WS_B) and z["kind"] == "seat"]
    assert len(ws_b_seats) == 1
    assert ws_b_seats[0]["layout_version"] == 1
    assert ws_b_seats[0]["polygon"] == SEAT_B_V1


def test_internal_zones_endpoint_returns_only_latest(db_session):
    """Load-bearing endpoint for the tracking engine's ZoneCache — if this
    returns the stale seat v1, ZoneCache's first-containing-polygon match
    will keep attributing operators to the old position after an edit."""
    app = _mount_all(db_session)
    # Auth override in _mount_all bypasses the token; hit the endpoint.
    with TestClient(app) as c:
        r = c.get("/zones/_internal")
    assert r.status_code == 200, r.text
    body = r.json()
    # WS_A seat v2 + WS_A machine v1 + WS_B seat v1 = 3 rows. No WS_A seat v1.
    assert len(body) == 3, f"expected 3 zones total, got {len(body)}: {body}"
    ws_a_seats = [z for z in body if z["workstation_id"] == str(WS_A) and z["kind"] == "seat"]
    assert len(ws_a_seats) == 1
    assert ws_a_seats[0]["layout_version"] == 2
    assert ws_a_seats[0]["polygon"] == SEAT_A_V2
    # All returned rows must carry the correct camera_id (join sanity).
    assert all(z["camera_id"] == str(CAM) for z in body)
