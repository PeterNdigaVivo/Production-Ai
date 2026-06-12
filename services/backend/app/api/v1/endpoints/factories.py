from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import Factory
from app.schemas.tenancy import FactoryCreate, FactoryRead
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("", response_model=list[FactoryRead])
async def list_factories(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Factory))
    return list(res.scalars())


@router.post("", response_model=FactoryRead, status_code=201)
async def create_factory(body: FactoryCreate, db: AsyncSession = Depends(get_db)):
    f = Factory(tenant_id=body.tenant_id, name=body.name, location=body.location, timezone=body.timezone)
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return f


@router.get("/{factory_id}", response_model=FactoryRead)
async def get_factory(factory_id: str, db: AsyncSession = Depends(get_db)):
    f = await db.get(Factory, factory_id)
    if not f:
        raise HTTPException(404, "not found")
    return f
