"""One-off: insert Station 4 + Station 5 workstations and seat zones on
Line A for camera f524b92c-f155-4792-9ca0-908ddb7c1dc4.

Derived from two approved dwell-clustering runs (see
services/backend/app/scripts/discover_zones.py). Station 5 merges two
adjacent dwell blobs that discover_zones split because the seat is
clipped by the frame bottom — the resulting polygon runs from y=566 to
y=720 (frame_h) to reunite the seat.

Why a script rather than `POST /workstations` + `POST /zones`:
  1. `POST /zones` auto-bumps Workstation.layout_version before insert
     (see app/api/v1/endpoints/zones.py) — a brand-new workstation's
     first zone lands at layout_version=2. Stations 1–3 were seeded at
     layout_version=1, and this script preserves that convention by
     setting the field explicitly.
  2. Overlap-check-then-insert must be atomic. Two curl calls with the
     check in between is racy; this runs in one transaction.

Safety rails (per the brief):
  * Fetches every existing zone for this camera and refuses to insert
    if the new polygon's axis-aligned bbox overlaps any existing zone's
    bbox. For rectangular seat zones the bbox check is exact; for
    hypothetical non-rectangular zones it is a conservative superset,
    which fails in the safe direction.
  * Idempotent: if a Workstation with the same name already exists on
    the target line, its row is reused and no zone is inserted unless
    that workstation has no zones yet.
  * Never modifies existing zones (they are immutable — new edits create
    a new Zone row at a higher layout_version).

Usage
-----

    docker compose exec backend python -m app.scripts.insert_stations_4_5

Env override (rarely needed):
    LINE_NAME       default "Line A"
    CAMERA_ID       default f524b92c-f155-4792-9ca0-908ddb7c1dc4
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import ProductionLine, Workstation, Zone


# ---------------------------------------------------------------------------- #
# Data — the two zones to insert, from the approved dwell runs.
# ---------------------------------------------------------------------------- #
CAMERA_ID = os.environ.get(
    "CAMERA_ID", "f524b92c-f155-4792-9ca0-908ddb7c1dc4"
)
LINE_NAME = os.environ.get("LINE_NAME", "Line A")

TO_INSERT = [
    {
        "name": "Station 4",
        "code": "S4",
        "polygon": [[150, 310], [250, 310], [250, 410], [150, 410]],
    },
    {
        "name": "Station 5",
        "code": "S5",
        "polygon": [[166, 566], [266, 566], [266, 720], [166, 720]],
    },
]


# ---------------------------------------------------------------------------- #
# Pure helpers
# ---------------------------------------------------------------------------- #
def _bbox(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _bboxes_overlap(a: tuple, b: tuple) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    # touching edges (equal coord) do NOT count as overlap — that's what
    # lets stations sit side-by-side.
    return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)


# ---------------------------------------------------------------------------- #
# Main
# ---------------------------------------------------------------------------- #
async def amain() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with Session() as db:
            # --- resolve line -------------------------------------------- #
            line = (await db.execute(
                select(ProductionLine).where(ProductionLine.name == LINE_NAME)
            )).scalar_one_or_none()
            if line is None:
                print(f"STOPPED: no ProductionLine named {LINE_NAME!r}",
                      file=sys.stderr)
                return 2
            line_id = line.id
            print(f"[init] line: {LINE_NAME} ({line_id})")
            print(f"[init] camera: {CAMERA_ID}")

            # --- fetch existing zones for this camera -------------------- #
            existing_stmt = (
                select(Zone, Workstation.code, Workstation.name)
                .join(Workstation, Workstation.id == Zone.workstation_id)
                .where(Workstation.camera_id == uuid.UUID(CAMERA_ID))
            )
            existing = [
                (z, code, name)
                for z, code, name in (await db.execute(existing_stmt)).all()
            ]
            print(f"[init] existing zones on this camera: {len(existing)}")
            for z, code, name in existing:
                print(f"  - {name} ({code}) kind={z.kind} v={z.layout_version} "
                      f"bbox={tuple(int(v) for v in _bbox(z.polygon))}")

            # --- overlap safety check (fail loudly) ---------------------- #
            for spec in TO_INSERT:
                new_bbox = _bbox(spec["polygon"])
                for z, code, name in existing:
                    if _bboxes_overlap(new_bbox, _bbox(z.polygon)):
                        print(
                            f"STOPPED: proposed {spec['name']} polygon "
                            f"{spec['polygon']} overlaps existing zone "
                            f"{name} ({code}, kind={z.kind}, v={z.layout_version}) "
                            f"polygon {z.polygon}. Refusing to insert.",
                            file=sys.stderr,
                        )
                        return 2
            # also check the two new ones against each other
            b4 = _bbox(TO_INSERT[0]["polygon"])
            b5 = _bbox(TO_INSERT[1]["polygon"])
            if _bboxes_overlap(b4, b5):
                print("STOPPED: proposed Station 4 and Station 5 polygons "
                      "overlap each other.", file=sys.stderr)
                return 2

            print("[check] no overlap with existing or between new zones")

            # --- insert (idempotent by workstation name on this line) ---- #
            created: list[dict] = []
            for spec in TO_INSERT:
                ws = (await db.execute(
                    select(Workstation).where(
                        Workstation.line_id == line_id,
                        Workstation.name == spec["name"],
                    )
                )).scalar_one_or_none()

                if ws is None:
                    ws = Workstation(
                        line_id=line_id,
                        camera_id=uuid.UUID(CAMERA_ID),
                        code=spec["code"],
                        name=spec["name"],
                    )
                    db.add(ws)
                    await db.flush()   # populate ws.id
                    ws_created = True
                else:
                    ws_created = False
                    print(f"[idem] {spec['name']} already exists as "
                          f"{ws.code} ({ws.id}); reusing")

                # if the workstation exists but has no seat zone yet, add one
                z_existing = (await db.execute(
                    select(Zone).where(
                        Zone.workstation_id == ws.id,
                        Zone.kind == "seat",
                    )
                )).scalar_one_or_none()

                if z_existing is not None:
                    print(f"[idem] {spec['name']} already has a seat zone "
                          f"({z_existing.id}, v={z_existing.layout_version}); "
                          f"skipping zone insert")
                    zone_id = z_existing.id
                    zone_v = z_existing.layout_version
                    zone_created = False
                else:
                    z = Zone(
                        workstation_id=ws.id,
                        kind="seat",
                        polygon=spec["polygon"],
                        layout_version=1,  # match Stations 1–3 convention
                    )
                    db.add(z)
                    await db.flush()
                    zone_id = z.id
                    zone_v = z.layout_version
                    zone_created = True

                created.append({
                    "name": spec["name"],
                    "code": ws.code,
                    "workstation_id": str(ws.id),
                    "workstation_created": ws_created,
                    "zone_id": str(zone_id),
                    "zone_layout_version": zone_v,
                    "zone_created": zone_created,
                    "polygon": spec["polygon"],
                })

            await db.commit()

            # --- report -------------------------------------------------- #
            print("\n[done] inserts:")
            for c in created:
                ws_tag = "created" if c["workstation_created"] else "reused"
                z_tag = "created" if c["zone_created"] else "reused"
                print(f"  {c['name']} ({c['code']}) "
                      f"workstation_id={c['workstation_id']} [{ws_tag}]")
                print(f"    zone_id={c['zone_id']} v={c['zone_layout_version']} "
                      f"kind=seat [{z_tag}]")
                print(f"    polygon={c['polygon']}")

            # --- verification query -------------------------------------- #
            print("\n[verify] select w.name, z.polygon from zones z "
                  "join workstations w on w.id=z.workstation_id "
                  "order by w.name:")
            verify_stmt = (
                select(Workstation.name, Zone.polygon)
                .join(Zone, Zone.workstation_id == Workstation.id)
                .order_by(Workstation.name)
            )
            for name, polygon in (await db.execute(verify_stmt)).all():
                print(f"  {name}  {polygon}")
    finally:
        await engine.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
