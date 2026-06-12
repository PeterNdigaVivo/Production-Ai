"""Seed a demo tenant, factory, line, camera, and admin user. Idempotent."""
import asyncio
import os
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import Tenant, Factory, ProductionLine, Camera, User, Role
from app.core.security import hash_password


async def main() -> None:
    async with SessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.slug == "demo"))).scalar_one_or_none()
        if not t:
            t = Tenant(name="Demo Co.", slug="demo")
            db.add(t)
            await db.flush()

        f = (await db.execute(select(Factory).where(Factory.name == "Plant 1"))).scalar_one_or_none()
        if not f:
            f = Factory(tenant_id=t.id, name="Plant 1", location="Nairobi", timezone="Africa/Nairobi")
            db.add(f)
            await db.flush()

        line = (await db.execute(select(ProductionLine).where(ProductionLine.name == "Line A"))).scalar_one_or_none()
        if not line:
            line = ProductionLine(factory_id=f.id, name="Line A", target_pieces_per_hour=80)
            db.add(line)
            await db.flush()

        cam = (await db.execute(select(Camera).where(Camera.name == "Cam-101"))).scalar_one_or_none()
        if not cam:
            db.add(Camera(
                line_id=line.id, name="Cam-101",
                rtsp_url=os.environ.get("DEMO_RTSP", "rtsp://user:pass@nvr.local:554/Streaming/Channels/101"),
                fps_target=8,
            ))

        for role_name in ("super_admin", "factory_manager", "production_manager", "supervisor", "viewer"):
            r = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
            if not r:
                db.add(Role(name=role_name))

        admin_email = os.environ.get("ADMIN_EMAIL", "admin@local")
        u = (await db.execute(select(User).where(User.email == admin_email))).scalar_one_or_none()
        if not u:
            db.add(User(
                email=admin_email,
                hashed_password=hash_password(os.environ.get("ADMIN_PASSWORD", "admin")),
                full_name="Admin",
                tenant_id=t.id,
                is_superuser=True,
            ))

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
