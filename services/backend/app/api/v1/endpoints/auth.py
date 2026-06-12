from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import User
from app.core.security import verify_password, make_access_token, make_refresh_token, decode_token
from app.schemas.auth import LoginRequest, TokenPair, RefreshRequest

router = APIRouter()


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    claims = {"superuser": user.is_superuser}
    return TokenPair(
        access_token=make_access_token(str(user.id), claims),
        refresh_token=make_refresh_token(str(user.id)),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if claims.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="not a refresh token")
    sub = claims["sub"]
    return TokenPair(
        access_token=make_access_token(sub),
        refresh_token=make_refresh_token(sub),
    )
