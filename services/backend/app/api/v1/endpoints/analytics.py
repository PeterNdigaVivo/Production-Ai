"""Analytics endpoints.

Thin HTTP layer over app.services.analytics. The heavy lifting (duration
reconstruction, the fairness rule, tenant scoping) lives in the service module
so the live API and the batch roll-up share one implementation.
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.api.deps import current_user
from app.services.analytics import (
    compute_line_kpis, LineNotFoundError,
)

router = APIRouter(dependencies=[Depends(current_user)])


def _caller_tenant(user: dict) -> str | None:
    """Tenant from the JWT, when present.

    Today the access token does not yet carry `tenant_id` (Finding 3, fixed in a
    later step). Until then this returns None and scoping is enforced purely at
    the data layer (the line must resolve to a real tenant). Once the claim
    lands, this automatically adds a second, token-level check.
    Superusers are allowed to cross tenants for support/debugging.
    """
    if user.get("superuser"):
        return None
    return user.get("tenant_id")


@router.get("/kpis/line/{line_id}")
async def line_kpis(
    line_id: str,
    hours: int = Query(8, ge=1, le=72),
    user: dict = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Duration-based KPIs for one line over the last `hours` hours.

    Returns time (seconds) spent in each worker state, the fair productivity
    ratio (WAITING_FOR_INPUT excluded from the denominator), and piece output.
    Scoped to the caller's tenant; unknown or cross-tenant lines return 404.
    """
    try:
        kpis = await compute_line_kpis(
            db, line_id, hours=hours, caller_tenant_id=_caller_tenant(user),
        )
    except LineNotFoundError:
        # 404 (not 403) so we don't reveal whether a line exists in another tenant.
        raise HTTPException(status_code=404, detail="line not found")
    return kpis.to_dict()
