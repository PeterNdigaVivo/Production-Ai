"""Auth endpoints — login, refresh (with rotation), logout.

Builds on Step 4 (tenant_id + roles in the access token). Step 7 adds refresh
token rotation/revocation (Finding 11):

  * login issues a refresh token with a unique jti and records it as the user's
    active jti in Redis.
  * refresh validates that the presented jti is the active one, then ROTATES it
    (issues a new jti, stores it, invalidating the old refresh token). A reused
    old refresh token is rejected — so a leaked token stops working as soon as
    the legitimate client refreshes.
  * logout revokes the user's refresh token.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import User
from app.core.security import (
    verify_password, make_access_token, make_refresh_token, decode_token,
)
from app.services.auth import build_user_claims, get_active_user_by_id
from app.services.token_store import set_active_jti, is_active_jti, revoke_user
from app.api.deps import current_user
from app.schemas.auth import LoginRequest, TokenPair, RefreshRequest

router = APIRouter()


async def _issue_pair(db: AsyncSession, user: User) -> TokenPair:
    claims = await build_user_claims(db, user)
    access = make_access_token(str(user.id), claims)
    refresh, jti = make_refresh_token(str(user.id))
    await set_active_jti(str(user.id), jti)
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    return await _issue_pair(db, user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    try:
        decoded = decode_token(payload.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if decoded.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="not a refresh token")

    sub = decoded.get("sub")
    jti = decoded.get("jti")
    if not sub or not jti:
        raise HTTPException(status_code=401, detail="malformed refresh token")

    # Rotation check: only the currently-active jti is accepted. A reused/leaked
    # old token fails here once the legit client has refreshed.
    if not await is_active_jti(sub, jti):
        raise HTTPException(status_code=401, detail="refresh token revoked or rotated")

    user = await get_active_user_by_id(db, sub)
    if user is None:
        raise HTTPException(status_code=401, detail="user no longer active")

    # issuing a new pair rotates the jti (set_active_jti overwrites the old one)
    return await _issue_pair(db, user)


@router.post("/logout")
async def logout(user: dict = Depends(current_user)) -> dict:
    """Revoke the caller's refresh token. The short-lived access token will
    expire on its own; the refresh token is invalidated immediately."""
    await revoke_user(user["sub"])
    return {"ok": True}
