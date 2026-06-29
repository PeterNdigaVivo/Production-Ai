"""Refresh-token rotation + revocation store (Finding 11).

The original refresh flow re-signed tokens forever with no server-side state, so
a leaked refresh token was valid for its full 30-day TTL with no way to revoke it
short of rotating JWT_SECRET (which logs everyone out).

This adds minimal server-side state in Redis (already a dependency — no DB
migration): each refresh token carries a unique `jti`, and only the currently
"active" jti per user is accepted. On every refresh the jti is rotated, which
invalidates the previous refresh token. Reusing an old refresh token (e.g. a
leaked one after the legit client has refreshed) is rejected.

Keys:  refresh:active:<user_id> -> <jti>   (TTL = refresh token TTL)

This is intentionally simple (one active refresh token per user). For
multi-device sessions you would store a set of valid jtis per user; the same
mechanism extends to that.
"""
from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import get_settings

_settings = get_settings()
_redis: Redis | None = None


def _client() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(_settings.redis_url, decode_responses=True)
    return _redis


def _key(user_id: str) -> str:
    return f"refresh:active:{user_id}"


async def set_active_jti(user_id: str, jti: str) -> None:
    """Record `jti` as the only valid refresh jti for the user (rotation)."""
    await _client().set(_key(user_id), jti, ex=_settings.jwt_refresh_ttl)


async def is_active_jti(user_id: str, jti: str) -> bool:
    """True iff `jti` is the currently active refresh jti for the user."""
    current = await _client().get(_key(user_id))
    return current is not None and current == jti


async def revoke_user(user_id: str) -> None:
    """Revoke the user's refresh token entirely (e.g. on logout)."""
    await _client().delete(_key(user_id))
