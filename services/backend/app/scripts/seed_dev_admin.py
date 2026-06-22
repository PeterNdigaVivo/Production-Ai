"""Idempotent dev-admin seeder, invoked from the FastAPI startup lifespan.

LOCAL ONLY — the default credentials (`admin@local` / `admin`) are weak and
intentionally so for first-boot ergonomics. Override with `DEV_ADMIN_EMAIL` /
`DEV_ADMIN_PASSWORD`, and rotate before any non-localhost exposure.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.models import Role, Tenant, User
from app.db.session import SessionLocal

log = get_logger(__name__)

_DEV_ROLES = ("super_admin", "factory_manager", "production_manager", "supervisor", "viewer")


async def seed_dev_admin() -> None:
    """Create the dev admin user + standard roles + a default tenant.

    Idempotent — safe to call on every container restart. No-ops if entities
    already exist; never updates an existing admin's password.
    """
    settings = get_settings()
    async with SessionLocal() as db:
        for name in _DEV_ROLES:
            exists = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
            if not exists:
                db.add(Role(name=name))

        tenant = (await db.execute(
            select(Tenant).where(Tenant.slug == "default")
        )).scalar_one_or_none()
        if not tenant:
            tenant = Tenant(name="Default", slug="default")
            db.add(tenant)
            await db.flush()
            log.info("seed.default_tenant.created", tenant_id=str(tenant.id))

        existing = (await db.execute(
            select(User).where(User.email == settings.dev_admin_email)
        )).scalar_one_or_none()

        if existing:
            log.info("seed.dev_admin.already_exists", email=settings.dev_admin_email)
        else:
            db.add(User(
                email=settings.dev_admin_email,
                hashed_password=hash_password(settings.dev_admin_password),
                full_name="Dev Admin",
                tenant_id=tenant.id,
                is_superuser=True,
            ))
            log.info("seed.dev_admin.created", email=settings.dev_admin_email)

        await db.commit()
