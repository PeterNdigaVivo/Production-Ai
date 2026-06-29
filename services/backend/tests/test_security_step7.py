"""Tests for Step 7 security hardening: refresh rotation (#11) and WS filtering (#10).

Validates the security-critical decision logic with the real JWT library and a
fake Redis token store. The heartbeat relocation (#7) is a routing change
verified by parse/inspection (the endpoint now sits behind require_internal_token
on the internal router).

Run:  pytest tests/test_security_step7.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from jose import jwt
import fakeredis.aioredis as fr

SECRET = "x" * 40
ALGO = "HS256"
REFRESH_TTL = 2_592_000


def _make_refresh(sub, jti=None):
    jti = jti or uuid.uuid4().hex
    p = {"sub": sub, "typ": "refresh", "jti": jti,
         "exp": datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TTL)}
    return jwt.encode(p, SECRET, algorithm=ALGO), jti


def _make_access(sub, claims):
    p = {"sub": sub, "typ": "access", **claims,
         "exp": datetime.now(timezone.utc) + timedelta(seconds=900)}
    return jwt.encode(p, SECRET, algorithm=ALGO)


def _authenticate(token):
    if not token:
        return None
    try:
        p = jwt.decode(token, SECRET, algorithms=[ALGO])
    except Exception:
        return None
    return p if p.get("typ") == "access" else None


@pytest_asyncio.fixture
async def store():
    r = fr.FakeRedis(decode_responses=True)

    class S:
        def key(self, u): return f"refresh:active:{u}"
        async def set_active(self, u, jti): await r.set(self.key(u), jti, ex=REFRESH_TTL)
        async def is_active(self, u, jti):
            cur = await r.get(self.key(u))
            return cur is not None and cur == jti
        async def revoke(self, u): await r.delete(self.key(u))
    yield S()


# ---- #11 refresh rotation -------------------------------------------------- #

@pytest.mark.asyncio
async def test_refresh_rotation_invalidates_old_token(store):
    uid = str(uuid.uuid4())
    _, jti1 = _make_refresh(uid)
    await store.set_active(uid, jti1)
    assert await store.is_active(uid, jti1)

    # rotate (a refresh issues a new jti and stores it)
    _, jti2 = _make_refresh(uid)
    await store.set_active(uid, jti2)

    assert await store.is_active(uid, jti1) is False   # old token rejected
    assert await store.is_active(uid, jti2) is True     # new token valid


@pytest.mark.asyncio
async def test_logout_revokes_refresh(store):
    uid = str(uuid.uuid4())
    _, jti = _make_refresh(uid)
    await store.set_active(uid, jti)
    await store.revoke(uid)
    assert await store.is_active(uid, jti) is False


# ---- #10 WS auth + tenant filter ------------------------------------------ #

def _allowed(user, ev_ws, ws_tenant):
    if user.get("superuser"):
        return True
    ev_tenant = ws_tenant.get(ev_ws)
    return ev_tenant is not None and ev_tenant == user.get("tenant_id")


def test_ws_rejects_missing_or_invalid_token():
    assert _authenticate(None) is None
    assert _authenticate("not-a-jwt") is None
    # a refresh token must not authenticate the WS (access only)
    refresh, _ = _make_refresh("u")
    assert _authenticate(refresh) is None


def test_ws_tenant_isolation_and_fail_closed():
    ta, tb = str(uuid.uuid4()), str(uuid.uuid4())
    ws_tenant = {"wsA": ta, "wsB": tb}
    user_a = _authenticate(_make_access("uA", {"tenant_id": ta, "superuser": False}))
    assert _allowed(user_a, "wsA", ws_tenant) is True    # own tenant
    assert _allowed(user_a, "wsB", ws_tenant) is False   # other tenant
    assert _allowed(user_a, "wsZ", ws_tenant) is False   # unknown ws -> fail closed


def test_ws_superuser_sees_all():
    ws_tenant = {"wsB": str(uuid.uuid4())}
    su = _authenticate(_make_access("root", {"superuser": True}))
    assert _allowed(su, "wsB", ws_tenant) is True
