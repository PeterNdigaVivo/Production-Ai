from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import Workstation
from app.schemas.tenancy import WorkstationCreate, WorkstationRead
from app.api.deps import current_user

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("", response_model=list[WorkstationRead])
async def list_workstations(line_id: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Workstation)
    if line_id:
        stmt = stmt.where(Workstation.line_id == line_id)
    res = await db.execute(stmt)
    return list(res.scalars())


@router.post("", response_model=WorkstationRead, status_code=201)
async def create_workstation(body: WorkstationCreate, db: AsyncSession = Depends(get_db)):
    ws = Workstation(line_id=body.line_id, camera_id=body.camera_id, code=body.code, name=body.name)
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return ws
