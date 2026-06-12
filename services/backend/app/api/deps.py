from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_token

bearer = HTTPBearer(auto_error=False)


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
