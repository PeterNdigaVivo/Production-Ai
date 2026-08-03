"""Analytics core — duration-based KPIs with the fairness rule and tenant scoping.

This module is the single source of truth for how productivity is computed, so
the live API endpoint (analytics.py) and the batch roll-up (Celery, a later
step) cannot drift apart.

Two things the original implementation got wrong, fixed here:

  1. DURATIONS, NOT EVENT COUNTS. worker_events stores state *transitions*. To
     measure how long a worker spent WORKING vs IDLE we must pair each event with
     the next event for the same (workstation, track) and sum the elapsed time.
     We do this in the database with a LEAD() window function (cheap, set-based)
     rather than pulling every row into Python.

  2. THE FAIRNESS RULE. WAITING_FOR_INPUT means the worker is ready but has no
     work to do (empty input tray). It must NOT count against them: it is
     excluded from the productivity denominator entirely — not folded into idle.
     This is the core fairness guarantee the product is built on.

TENANT SCOPING (Finding 4). The original state query had no line/tenant filter
and aggregated across all tenants. Here every query is constrained to the
workstations of the requested line, and the line must belong to the caller's
tenant. We enforce this at the DATA layer (resolve the tenant from the DB), so
it holds regardless of whether the JWT yet carries a tenant_id claim (Finding 3,
a later step). When the claim is present we also cross-check it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    WorkerEvent, ProductionEvent, Workstation, ProductionLine, Factory,
)
from app.services.intervals import merged_state_seconds

# States that represent "the worker could have been producing". WAITING is
# deliberately absent — that is the fairness rule.
PRODUCTIVE_DENOMINATOR_STATES = ("WORKING", "IDLE", "AWAY")
WORKING_STATES = ("WORKING",)
WAITING_STATE = "WAITING_FOR_INPUT"


@dataclass
class LineKpis:
    line_id: str
    tenant_id: str | None
    window_start: datetime
    window_end: datetime
    state_seconds: dict[str, float] = field(default_factory=dict)
    working_s: float = 0.0
    idle_s: float = 0.0
    away_s: float = 0.0
    waiting_s: float = 0.0           # reported, but excluded from productivity
    productivity: float = 0.0        # working / (working+idle+away)   [0..1]
    pieces_completed: int = 0
    pieces_per_hour: float = 0.0
    workstation_count: int = 0

    def to_dict(self) -> dict:
        return {
            "line_id": self.line_id,
            "tenant_id": self.tenant_id,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "workstation_count": self.workstation_count,
            "state_seconds": {k: round(v, 1) for k, v in self.state_seconds.items()},
            "working_s": round(self.working_s, 1),
            "idle_s": round(self.idle_s, 1),
            "away_s": round(self.away_s, 1),
            "waiting_s": round(self.waiting_s, 1),
            "productivity": round(self.productivity, 4),
            "productivity_pct": round(self.productivity * 100, 1),
            "pieces_completed": self.pieces_completed,
            "pieces_per_hour": round(self.pieces_per_hour, 2),
            "fairness_note": (
                "WAITING_FOR_INPUT is excluded from the productivity "
                "denominator: workers are not penalised for an empty input tray."
            ),
        }


class LineNotFoundError(Exception):
    """The line does not exist, or does not belong to the caller's tenant."""


async def resolve_line_tenant(db: AsyncSession, line_id: str) -> uuid.UUID | None:
    """Return the tenant_id that owns `line_id` (line -> factory -> tenant)."""
    stmt = (
        select(Factory.tenant_id)
        .join(ProductionLine, ProductionLine.factory_id == Factory.id)
        .where(ProductionLine.id == line_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def workstation_ids_for_line(db: AsyncSession, line_id: str) -> list[uuid.UUID]:
    stmt = select(Workstation.id).where(Workstation.line_id == line_id)
    return [row[0] for row in (await db.execute(stmt)).all()]


async def compute_line_kpis(
    db: AsyncSession,
    line_id: str,
    hours: int = 8,
    caller_tenant_id: str | None = None,
    now: datetime | None = None,
) -> LineKpis:
    """Compute duration-based KPIs for one line over the last `hours` hours.

    Raises LineNotFoundError if the line is unknown or not owned by
    `caller_tenant_id` (when that is provided).
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    owning_tenant = await resolve_line_tenant(db, line_id)
    if owning_tenant is None:
        raise LineNotFoundError(line_id)
    # Defense-in-depth: if the caller's tenant is known, it must match.
    if caller_tenant_id is not None and str(owning_tenant) != str(caller_tenant_id):
        raise LineNotFoundError(line_id)

    ws_ids = await workstation_ids_for_line(db, line_id)
    kpis = LineKpis(
        line_id=str(line_id),
        tenant_id=str(owning_tenant),
        window_start=since,
        window_end=now,
        workstation_count=len(ws_ids),
    )
    if not ws_ids:
        return kpis

    # --- Duration reconstruction — UNION of intervals per workstation ----- #
    # For each (workstation, track) ordered by ts, the state interval ends at
    # the next event's ts, or at `now` for the last (still-open) interval.
    # We then MERGE overlapping intervals per (workstation, state) so total
    # time never exceeds wall clock. Same helper as the batch roll-up in
    # rollup.py so live KPIs and rolled-up records cannot drift.
    per_ws_rows = await merged_state_seconds(
        db, since, now, workstation_ids=ws_ids,
    )
    state_seconds: dict[str, float] = {}
    for _ws, state, secs in per_ws_rows:
        state_seconds[state] = state_seconds.get(state, 0.0) + secs
    kpis.state_seconds = state_seconds

    kpis.working_s = sum(state_seconds.get(s, 0.0) for s in WORKING_STATES)
    kpis.idle_s = state_seconds.get("IDLE", 0.0)
    kpis.away_s = state_seconds.get("AWAY", 0.0)
    kpis.waiting_s = state_seconds.get(WAITING_STATE, 0.0)

    denom = sum(state_seconds.get(s, 0.0) for s in PRODUCTIVE_DENOMINATOR_STATES)
    kpis.productivity = (kpis.working_s / denom) if denom > 0 else 0.0

    # --- Pieces (already line-scoped in the original; kept, with tenant safety) #
    pieces_stmt = select(func.count()).where(
        and_(
            ProductionEvent.line_id == line_id,
            ProductionEvent.kind == "piece_completed",
            ProductionEvent.ts >= since,
        )
    )
    kpis.pieces_completed = int((await db.execute(pieces_stmt)).scalar() or 0)
    kpis.pieces_per_hour = kpis.pieces_completed / hours if hours else 0.0

    return kpis
