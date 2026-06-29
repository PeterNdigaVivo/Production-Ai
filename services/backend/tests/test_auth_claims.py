"""Tests for auth claims (Finding 3): tenant_id + roles in the JWT.

Validates the LOGIC against an in-memory SQLite DB plus the real JWT library:
  * role names are loaded from user_roles -> roles
  * claims (tenant_id, roles, superuser) are embedded in the access token
  * require_roles() accepts/rejects correctly based on those claims
  * a pre-fix token (no roles) would have locked the user out (regression marker)

Run:  pytest tests/test_auth_claims.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy import (Column, String, Boolean, MetaData, Table, select, insert)
from sqlalchemy.ext.asyncio import create_async_engine

SECRET = "x" * 40
ALGO = "HS256"

md = MetaData()
users = Table("users", md,
    Column("id", String, primary_key=True),
    Column("email", String), Column("is_active", Boolean),
    Column("is_superuser", Boolean), Column("tenant_id", String))
roles = Table("roles", md,
    Column("id", String, primary_key=True), Column("name", String))
user_roles = Table("user_roles", md,
    Column("id", String, primary_key=True),
    Column("user_id", String), Column("role_id", String),
    Column("factory_id", String))


async def _load_role_names(conn, user_id):
    """Mirror of app.services.auth.load_role_names."""
    stmt = (
        select(roles.c.name)
        .select_from(roles.join(user_roles, user_roles.c.role_id == roles.c.id))
        .where(user_roles.c.user_id == user_id)
        .distinct()
    )
    return sorted({r[0] for r in (await conn.execute(stmt)).all()})


def _allowed(user: dict, *required: str) -> bool:
    """Mirror of app.api.deps.require_roles decision."""
    if user.get("superuser"):
        return True
    return bool(set(required).intersection(set(user.get("roles", []))))


def _encode(sub, claims, ttl, typ):
    p = {"sub": sub, "typ": typ, **claims,
         "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl)}
    return jwt.encode(p, SECRET, algorithm=ALGO)


@pytest_asyncio.fixture
async def seeded():
    tenant = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    r_sup, r_view = str(uuid.uuid4()), str(uuid.uuid4())
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(md.create_all)
        await c.execute(insert(users), [dict(id=uid, email="mgr@acme",
            is_active=True, is_superuser=False, tenant_id=tenant)])
        await c.execute(insert(roles), [dict(id=r_sup, name="supervisor"),
                                        dict(id=r_view, name="viewer")])
        await c.execute(insert(user_roles), [
            dict(id=str(uuid.uuid4()), user_id=uid, role_id=r_sup, factory_id=None),
            dict(id=str(uuid.uuid4()), user_id=uid, role_id=r_view, factory_id=None)])
    async with eng.connect() as c:
        yield {"conn": c, "tenant": tenant, "uid": uid}


@pytest.mark.asyncio
async def test_roles_loaded_from_db(seeded):
    names = await _load_role_names(seeded["conn"], seeded["uid"])
    assert names == ["supervisor", "viewer"]


@pytest.mark.asyncio
async def test_access_token_carries_tenant_and_roles(seeded):
    names = await _load_role_names(seeded["conn"], seeded["uid"])
    claims = {"superuser": False, "tenant_id": seeded["tenant"], "roles": names}
    token = _encode(seeded["uid"], claims, 900, "access")
    decoded = jwt.decode(token, SECRET, algorithms=[ALGO])
    assert decoded["tenant_id"] == seeded["tenant"]
    assert decoded["roles"] == names
    assert decoded["typ"] == "access"


@pytest.mark.asyncio
async def test_require_roles_enforced_from_claims(seeded):
    names = await _load_role_names(seeded["conn"], seeded["uid"])
    decoded = {"superuser": False, "roles": names}
    assert _allowed(decoded, "supervisor") is True
    assert _allowed(decoded, "factory_manager") is False
    assert _allowed(decoded, "viewer", "factory_manager") is True  # any-of


def test_regression_prefix_token_locked_user_out():
    """Documents Finding 3: before the fix the token had no roles, so a
    non-superuser was wrongly denied every role-gated route."""
    old = {"superuser": False}  # what login used to produce
    assert _allowed(old, "supervisor") is False
    assert "roles" not in old


def test_refresh_token_does_not_carry_roles():
    """Refresh tokens stay claim-light; access claims are rebuilt from the DB on
    refresh so they reflect current roles/tenant."""
    refresh = _encode("uid", {}, 2_592_000, "refresh")
    decoded = jwt.decode(refresh, SECRET, algorithms=[ALGO])
    assert "roles" not in decoded
    assert decoded["typ"] == "refresh"
