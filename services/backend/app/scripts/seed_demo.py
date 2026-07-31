"""Seed a demo tenant, factory, line, camera, workstations, zones, and admin
user. Idempotent.

Reuses the tenant the app already auto-creates on startup (seed_dev_admin
inserts a 'default' tenant when SEED_DEV_ADMIN=true is set) — this script
picks up whatever tenant exists first (oldest) instead of manufacturing a
parallel 'demo' tenant that would leave the app with two.
"""
import asyncio
import os
from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models import (
    Tenant, Factory, ProductionLine, Camera, Workstation, Zone, User, Role,
)
from app.core.security import hash_password


async def main() -> None:
    async with SessionLocal() as db:
        # Reuse the app-created tenant (oldest wins). Only fall back to
        # creating a 'demo' tenant if the table is empty.
        t = (await db.execute(
            select(Tenant).order_by(Tenant.created_at).limit(1)
        )).scalar_one_or_none()
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
            cam = Camera(
                line_id=line.id, name="Cam-101",
                rtsp_url=os.environ.get("DEMO_RTSP", "rtsp://user:pass@nvr.local:554/Streaming/Channels/101"),
                fps_target=8,
            )
            db.add(cam)
            await db.flush()

        # Two workstations on Line A, both bound to Cam-101. Each gets a
        # rectangular "work" zone covering its half of a 1280x720 frame so
        # tracking has something to map detections onto out of the box.
        workstations_spec = [
            ("WS-01", "Station 1", [[0, 0], [640, 0], [640, 720], [0, 720]]),
            ("WS-02", "Station 2", [[640, 0], [1280, 0], [1280, 720], [640, 720]]),
        ]
        for code, name, polygon in workstations_spec:
            ws = (await db.execute(
                select(Workstation).where(Workstation.code == code)
            )).scalar_one_or_none()
            if not ws:
                ws = Workstation(line_id=line.id, camera_id=cam.id, code=code, name=name)
                db.add(ws)
                await db.flush()
            zone = (await db.execute(
                select(Zone).where(Zone.workstation_id == ws.id, Zone.kind == "work")
            )).scalar_one_or_none()
            if not zone:
                db.add(Zone(workstation_id=ws.id, kind="work", polygon=polygon))

        for role_name in ("super_admin", "factory_manager", "production_manager", "supervisor", "viewer"):
            r = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
            if not r:
                db.add(Role(name=role_name))

        # Prefer DEV_ADMIN_EMAIL (matches the app-side seed + .env.example);
        # fall back to legacy ADMIN_EMAIL; default to a real TLD so EmailStr
        # on the login route accepts it — the old 'admin@local' default was
        # rejected as invalid and locked the seeded admin out.
        admin_email = (
            os.environ.get("DEV_ADMIN_EMAIL")
            or os.environ.get("ADMIN_EMAIL")
            or "admin@local.dev"
        )
        u = (await db.execute(select(User).where(User.email == admin_email))).scalar_one_or_none()
        if not u:
            db.add(User(
                email=admin_email,
                hashed_password=hash_password(
                    os.environ.get("DEV_ADMIN_PASSWORD")
                    or os.environ.get("ADMIN_PASSWORD")
                    or "admin"
                ),
                full_name="Admin",
                tenant_id=t.id,
                is_superuser=True,
            ))

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
