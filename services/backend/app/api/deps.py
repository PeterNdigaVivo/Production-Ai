from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import get_settings
from app.core.security import decode_token

bearer = HTTPBearer(auto_error=False)


async def require_internal_token(
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
) -> None:
    expected = get_settings().internal_service_token
    if not expected or not x_internal_token or x_internal_token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


async def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token")
    try:
        payload = decode_token(creds.credentials)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="not an access token")
    return payload


def require_roles(*roles: str):
    async def _checker(user: dict = Depends(current_user)) -> dict:
        if user.get("superuser"):
            return user
        if not set(roles).intersection(set(user.get("roles", []))):
            raise HTTPException(status_code=403, detail="forbidden")
        return user
    return _checker
