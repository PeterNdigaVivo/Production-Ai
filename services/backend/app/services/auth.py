"""Auth service — build JWT claims from the database.

Single source of truth for what goes into an access token, so login and refresh
cannot drift. Before this, login put only `superuser` in the token and refresh
put nothing — so the `tenant_id` claim that the API relies on for tenant
isolation, and the `roles` claim that `require_roles()` checks, were never
present (Finding 3). RBAC and multi-tenancy were effectively inert.

Claims produced:
  * sub        — user id (set by the token encoder, not here)
  * superuser  — bool
  * tenant_id  — str | None  (used by analytics + every tenant-scoped query)
  * roles      — list[str]   (used by require_roles())

Both login and refresh call build_user_claims() against the live DB, so a token
always reflects the user's CURRENT tenant and roles — e.g. if an admin revokes a
role, the next refreshed token no longer carries it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, Role, UserRole


async def load_role_names(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Distinct role names assigned to the user (across all factories)."""
    stmt = (
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .distinct()
    )
    return sorted({row[0] for row in (await db.execute(stmt)).all()})


async def build_user_claims(db: AsyncSession, user: User) -> dict:
    """Assemble the custom JWT claims for a user from the database."""
    roles = await load_role_names(db, user.id)
    return {
        "superuser": bool(user.is_superuser),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "roles": roles,
    }


async def get_active_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    """Fetch an active user by id (used on refresh to rebuild claims)."""
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user
