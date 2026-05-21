from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Patient
from backend.db.session import get_db
from backend.schemas.patient import PatientCreate, PatientRead, PatientUpdate

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=list[PatientRead])
async def list_patients(
    phone: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Patient)
    if phone:
        stmt = stmt.where(Patient.phone == phone)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=PatientRead, status_code=201)
async def create_patient(payload: PatientCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Patient).where(Patient.phone == payload.phone))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="patient with this phone already exists")
    patient = Patient(**payload.model_dump())
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.get("/{patient_id}", response_model=PatientRead)
async def get_patient(patient_id: UUID, db: AsyncSession = Depends(get_db)):
    patient = await db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    return patient


@router.patch("/{patient_id}", response_model=PatientRead)
async def update_patient(
    patient_id: UUID,
    payload: PatientUpdate,
    db: AsyncSession = Depends(get_db),
):
    patient = await db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, key, value)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=204)
async def delete_patient(patient_id: UUID, db: AsyncSession = Depends(get_db)):
    patient = await db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="patient not found")
    await db.delete(patient)
    await db.commit()
