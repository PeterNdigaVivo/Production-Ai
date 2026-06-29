import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import get_settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_settings = get_settings()


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _pwd.verify(raw, hashed)


def _encode(payload: dict[str, Any], ttl: int) -> str:
    payload = payload.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def make_access_token(sub: str, claims: dict[str, Any] | None = None) -> str:
    return _encode({"sub": sub, "typ": "access", **(claims or {})}, _settings.jwt_access_ttl)


def make_refresh_token(sub: str, jti: str | None = None) -> tuple[str, str]:
    """Return (token, jti). A unique jti lets the server rotate/revoke refresh
    tokens (Finding 11). Caller stores the jti as the user's active refresh jti.
    """
    jti = jti or uuid.uuid4().hex
    token = _encode({"sub": sub, "typ": "refresh", "jti": jti}, _settings.jwt_refresh_ttl)
    return token, jti


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _settings.jwt_secret, algorithms=[_settings.jwt_algorithm])
    except JWTError as e:
        raise ValueError(f"invalid token: {e}") from e
