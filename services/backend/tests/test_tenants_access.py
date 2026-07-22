"""Step 9 — /tenants restricted to super_admin.

Verifies the real current_user → require_roles chain against the actual
tenants router (not a stub). `get_db` is overridden with a minimal stub so
the endpoint's DB call is a no-op; auth is what we care about.

Run:  pytest tests/test_tenants_access.py -v
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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.api.v1.endpoints import tenants as tenants_ep
from app.api.deps import current_user
from app.db.session import get_db

SECRET = os.environ["JWT_SECRET"]
ALGO = "HS256"


def _access(sub: str, roles: list[str] | None = None, superuser: bool = False) -> str:
    p = {
        "sub": sub, "typ": "access",
        "roles": roles or [],
        "superuser": superuser,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=900),
    }
    return jwt.encode(p, SECRET, algorithm=ALGO)


async def _empty_db():
    """Stub session — the endpoint calls db.execute(...).scalars(), we return []."""
    class _Result:
        def scalars(self): return []
    class _Session:
        async def execute(self, *a, **kw): return _Result()
    yield _Session()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(tenants_ep.router, prefix="/api/v1/tenants")
    app.dependency_overrides[get_db] = _empty_db
    return TestClient(app)


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_viewer_role_forbidden(client):
    tok = _access(str(uuid.uuid4()), roles=["viewer"])
    r = client.get("/api/v1/tenants", headers=_hdr(tok))
    assert r.status_code == 403, r.text


def test_supervisor_role_forbidden(client):
    tok = _access(str(uuid.uuid4()), roles=["supervisor"])
    r = client.get("/api/v1/tenants", headers=_hdr(tok))
    assert r.status_code == 403, r.text


def test_production_manager_role_forbidden(client):
    tok = _access(str(uuid.uuid4()), roles=["production_manager"])
    r = client.get("/api/v1/tenants", headers=_hdr(tok))
    assert r.status_code == 403, r.text


def test_super_admin_role_allowed(client):
    tok = _access(str(uuid.uuid4()), roles=["super_admin"])
    r = client.get("/api/v1/tenants", headers=_hdr(tok))
    assert r.status_code == 200
    assert r.json() == []


def test_superuser_flag_bypasses_role_check(client):
    """Per require_roles' existing design, `superuser: true` short-circuits the
    role check. This is how the seeded admin@local (is_superuser=True) works."""
    tok = _access(str(uuid.uuid4()), superuser=True)
    r = client.get("/api/v1/tenants", headers=_hdr(tok))
    assert r.status_code == 200


def test_no_token_returns_401(client):
    r = client.get("/api/v1/tenants")
    assert r.status_code == 401


def test_router_does_not_depend_on_current_user_directly():
    """Regression pin: the direct router dependency must be `require_roles(...)`,
    NOT `current_user`. A future edit reverting to `Depends(current_user)` would
    silently re-open the endpoint to any authenticated user; this test catches
    that."""
    router_deps = [d.dependency for d in tenants_ep.router.dependencies]
    assert current_user not in router_deps, (
        "tenants router must not depend on current_user directly; "
        "use require_roles(...) instead"
    )
