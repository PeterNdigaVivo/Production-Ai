"""Read-side helpers for zones.

Zones are versioned-immutable: `POST /api/v1/zones` bumps the workstation's
`layout_version` and INSERTs a new zone row at that version, leaving the
prior row in place so history stays for audit. Read paths must therefore
filter to the newest row per (workstation_id, kind) — otherwise editing a
seat leaves the old polygon live alongside the new one, and the tracking
engine (which matches the first containing polygon in `ZoneCache`) keeps
attributing operators to the stale zone, making it impossible to *move* a
seat through the API.

Every read endpoint composes with `latest_zone_ids()` so the editor and
the tracker agree on what is current.
"""
from __future__ import annotations

from sqlalchemy import Select, func, select

from app.db.models import Zone


def latest_zone_ids() -> Select:
    """Return a SELECT of Zone.id values that are the highest `layout_version`
    for their (workstation_id, kind) tuple.

    Uses ROW_NUMBER() over PARTITION BY (workstation_id, kind) ORDER BY
    layout_version DESC. This is supported by Postgres and by SQLite ≥3.25
    (so the same SQL runs against the unit test's in-memory SQLite and the
    tower's Postgres). The write path is unchanged: history rows still
    exist on disk, they just no longer surface in reads.
    """
    rn = func.row_number().over(
        partition_by=(Zone.workstation_id, Zone.kind),
        order_by=Zone.layout_version.desc(),
    ).label("rn")
    ranked = select(Zone.id.label("zone_id"), rn).subquery()
    return select(ranked.c.zone_id).where(ranked.c.rn == 1)
