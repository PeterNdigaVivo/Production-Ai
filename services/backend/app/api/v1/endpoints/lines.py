from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import ProductionLine
from app.schemas.tenancy import ProductionLineCreate, ProductionLineRead
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("", response_model=list[ProductionLineRead])
async def list_lines(factory_id: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(ProductionLine)
    if factory_id:
        stmt = stmt.where(ProductionLine.factory_id == factory_id)
    res = await db.execute(stmt)
    return list(res.scalars())


@router.post("", response_model=ProductionLineRead, status_code=201)
async def create_line(body: ProductionLineCreate, db: AsyncSession = Depends(get_db)):
    line = ProductionLine(
        factory_id=body.factory_id, name=body.name, target_pieces_per_hour=body.target_pieces_per_hour,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line
