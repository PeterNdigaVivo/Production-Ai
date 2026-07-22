from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import Tenant
from app.api.deps import require_roles

# Step 9 — scope tightening.
# Production-AI is a single-tenant deployment: one factory client, no plans for
# multi-tenant SaaS. The tenants list is an admin lookup only — there is no
# self-service use case for a viewer/supervisor/production_manager to enumerate
# tenants. The previous `Depends(current_user)` guard let any authenticated
# user read this endpoint, which was wider than needed. Restricted to
# super_admin; a token with `superuser: true` still passes per require_roles'
# existing bypass, which is how the seeded admin@local works.
router = APIRouter(dependencies=[Depends(require_roles("super_admin"))])


@router.get("")
async def list_tenants(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Tenant))
    return [{"id": str(t.id), "name": t.name, "slug": t.slug} for t in res.scalars()]
