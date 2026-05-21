from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Doctor
from backend.db.session import get_db
from backend.schemas.doctor import DoctorCreate, DoctorRead, DoctorUpdate

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.get("", response_model=list[DoctorRead])
async def list_doctors(
    specialty: str | None = None,
    language: str | None = None,
    name: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Doctor)
    if specialty:
        stmt = stmt.where(Doctor.specialty == specialty)
    if language:
        stmt = stmt.where(Doctor.languages_spoken.contains([language]))
    if name:
        stmt = stmt.where(func.lower(Doctor.name).contains(name.lower()))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=DoctorRead, status_code=201)
async def create_doctor(payload: DoctorCreate, db: AsyncSession = Depends(get_db)):
    doctor = Doctor(**payload.model_dump())
    db.add(doctor)
    await db.commit()
    await db.refresh(doctor)
    return doctor


@router.get("/{doctor_id}", response_model=DoctorRead)
async def get_doctor(doctor_id: UUID, db: AsyncSession = Depends(get_db)):
    doctor = await db.get(Doctor, doctor_id)
    if doctor is None:
        raise HTTPException(status_code=404, detail="doctor not found")
    return doctor


@router.patch("/{doctor_id}", response_model=DoctorRead)
async def update_doctor(
    doctor_id: UUID,
    payload: DoctorUpdate,
    db: AsyncSession = Depends(get_db),
):
    doctor = await db.get(Doctor, doctor_id)
    if doctor is None:
        raise HTTPException(status_code=404, detail="doctor not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(doctor, key, value)
    await db.commit()
    await db.refresh(doctor)
    return doctor


@router.delete("/{doctor_id}", status_code=204)
async def delete_doctor(doctor_id: UUID, db: AsyncSession = Depends(get_db)):
    doctor = await db.get(Doctor, doctor_id)
    if doctor is None:
        raise HTTPException(status_code=404, detail="doctor not found")
    await db.delete(doctor)
    await db.commit()
