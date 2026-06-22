from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import Tenant
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("")
async def list_tenants(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Tenant))
    return [{"id": str(t.id), "name": t.name, "slug": t.slug} for t in res.scalars()]
