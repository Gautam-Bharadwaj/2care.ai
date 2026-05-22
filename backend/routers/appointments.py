from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.models import Appointment, AvailabilitySlot, Doctor
from backend.db.session import get_db
from backend.schemas.appointment import (
    AppointmentCreate,
    AppointmentDetailRead,
    AppointmentRead,
    CancelRequest,
    RescheduleRequest,
)
from backend.services.scheduling import (
    PastTimeError,
    SchedulingError,
    SlotUnavailableError,
    book_appointment,
    cancel_appointment,
    reschedule_appointment,
)

router = APIRouter(prefix="/appointments", tags=["appointments"])


def _to_detail(appt: Appointment, slot: AvailabilitySlot, doctor: Doctor) -> dict:
    return {
        "id": appt.id,
        "patient_id": appt.patient_id,
        "doctor_id": appt.doctor_id,
        "doctor_name": doctor.name,
        "doctor_specialty": doctor.specialty,
        "slot_id": appt.slot_id,
        "start_time": slot.start_time,
        "end_time": slot.end_time,
        "status": appt.status,
        "notes": appt.notes,
        "created_at": appt.created_at,
    }


@router.get("", response_model=list[AppointmentDetailRead])
async def list_appointments(
    patient_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Appointment, AvailabilitySlot, Doctor)
        .join(AvailabilitySlot, Appointment.slot_id == AvailabilitySlot.id)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
    )
    if patient_id is not None:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    stmt = stmt.order_by(AvailabilitySlot.start_time)
    rows = (await db.execute(stmt)).all()
    return [_to_detail(a, s, d) for a, s, d in rows]


async def _load_detail(db: AsyncSession, appointment_id: UUID) -> dict | None:
    stmt = (
        select(Appointment, AvailabilitySlot, Doctor)
        .join(AvailabilitySlot, Appointment.slot_id == AvailabilitySlot.id)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .where(Appointment.id == appointment_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    a, s, d = row
    return _to_detail(a, s, d)


@router.post("", response_model=AppointmentDetailRead, status_code=201)
async def create_appointment(payload: AppointmentCreate, db: AsyncSession = Depends(get_db)):
    try:
        appointment = await book_appointment(db, payload.patient_id, payload.slot_id)
    except PastTimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SlotUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except SchedulingError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if payload.notes is not None:
        appointment.notes = payload.notes
        await db.commit()

    detail = await _load_detail(db, appointment.id)
    return detail


@router.get("/{appointment_id}", response_model=AppointmentDetailRead)
async def get_appointment(appointment_id: UUID, db: AsyncSession = Depends(get_db)):
    detail = await _load_detail(db, appointment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="appointment not found")
    return detail


@router.patch("/{appointment_id}/reschedule", response_model=AppointmentDetailRead)
async def reschedule(
    appointment_id: UUID,
    payload: RescheduleRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        await reschedule_appointment(db, appointment_id, payload.new_slot_id)
    except PastTimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SlotUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return await _load_detail(db, appointment_id)


@router.delete("/{appointment_id}", response_model=AppointmentRead)
async def cancel(
    appointment_id: UUID,
    payload: CancelRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    reason = payload.reason if payload else "patient requested cancellation"
    try:
        return await cancel_appointment(db, appointment_id, reason)
    except SlotUnavailableError as e:
        raise HTTPException(status_code=404, detail=str(e))
