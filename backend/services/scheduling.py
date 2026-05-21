"""Scheduling service.

All booking mutations use `SELECT ... FOR UPDATE` on the slot row so that two
concurrent attempts to book the same slot are serialized. The UNIQUE constraint
on `appointments.slot_id` provides a second line of defense against races.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.db.models import (
    Appointment,
    AppointmentStatus,
    AvailabilitySlot,
    Doctor,
)


class SchedulingError(Exception):
    """Base class for scheduling errors."""


class SlotUnavailableError(SchedulingError):
    pass


class PastTimeError(SchedulingError):
    pass


class DoctorUnavailableError(SchedulingError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def list_available_slots(
    db: AsyncSession,
    date_from: datetime,
    date_to: datetime,
    doctor_id: UUID | None = None,
    specialty: str | None = None,
    language: str | None = None,
) -> list[AvailabilitySlot]:
    """Return free, future slots in [date_from, date_to], with doctor eager-loaded."""
    floor = max(date_from, _utcnow())

    stmt = (
        select(AvailabilitySlot)
        .options(selectinload(AvailabilitySlot.doctor))
        .join(Doctor, AvailabilitySlot.doctor_id == Doctor.id)
        .where(AvailabilitySlot.is_booked.is_(False))
        .where(AvailabilitySlot.start_time >= floor)
        .where(AvailabilitySlot.start_time <= date_to)
    )

    if doctor_id is not None:
        stmt = stmt.where(AvailabilitySlot.doctor_id == doctor_id)
    if specialty is not None:
        stmt = stmt.where(Doctor.specialty == specialty)

    if language is not None:
        lang_priority = case(
            (Doctor.languages_spoken.contains([language]), 0),
            else_=1,
        )
        stmt = stmt.order_by(lang_priority, AvailabilitySlot.start_time)
    else:
        stmt = stmt.order_by(AvailabilitySlot.start_time)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def book_appointment(
    db: AsyncSession,
    patient_id: UUID,
    slot_id: UUID,
) -> Appointment:
    """Book `slot_id` for `patient_id`. Raises if unavailable or in the past."""
    try:
        slot = await _lock_slot(db, slot_id)
        _assert_slot_bookable(slot)

        if await _has_active_appointment(db, slot_id):
            raise SlotUnavailableError(f"slot {slot_id} already has an active appointment")

        appointment = Appointment(
            patient_id=patient_id,
            doctor_id=slot.doctor_id,
            slot_id=slot_id,
            status=AppointmentStatus.scheduled,
        )
        slot.is_booked = True
        db.add(appointment)

        await db.commit()
        await db.refresh(appointment)
        return appointment

    except SchedulingError:
        await db.rollback()
        raise
    except IntegrityError as e:
        await db.rollback()
        raise SlotUnavailableError("concurrent booking won the race") from e


async def reschedule_appointment(
    db: AsyncSession,
    appointment_id: UUID,
    new_slot_id: UUID,
) -> Appointment:
    """Move an appointment to a new slot. Frees the old slot."""
    try:
        appointment = await db.get(Appointment, appointment_id)
        if appointment is None:
            raise SlotUnavailableError(f"appointment {appointment_id} not found")
        if appointment.status != AppointmentStatus.scheduled:
            raise SlotUnavailableError(
                f"appointment {appointment_id} is {appointment.status.value}, not active"
            )

        if new_slot_id == appointment.slot_id:
            return appointment

        new_slot = await _lock_slot(db, new_slot_id)
        _assert_slot_bookable(new_slot)

        old_slot = await _lock_slot(db, appointment.slot_id)

        appointment.slot_id = new_slot_id
        appointment.doctor_id = new_slot.doctor_id
        new_slot.is_booked = True
        old_slot.is_booked = False

        await db.commit()
        await db.refresh(appointment)
        return appointment

    except SchedulingError:
        await db.rollback()
        raise
    except IntegrityError as e:
        await db.rollback()
        raise SlotUnavailableError("concurrent booking on the new slot won") from e


async def cancel_appointment(
    db: AsyncSession,
    appointment_id: UUID,
    reason: str,
) -> Appointment:
    """Cancel an appointment and free its slot."""
    appointment = await db.get(Appointment, appointment_id)
    if appointment is None:
        raise SlotUnavailableError(f"appointment {appointment_id} not found")
    if appointment.status == AppointmentStatus.cancelled:
        return appointment

    slot = await _lock_slot(db, appointment.slot_id)

    appointment.status = AppointmentStatus.cancelled
    note_line = f"[cancelled] {reason}"
    appointment.notes = f"{appointment.notes}\n{note_line}" if appointment.notes else note_line
    slot.is_booked = False

    await db.commit()
    await db.refresh(appointment)
    return appointment


async def find_alternatives(
    db: AsyncSession,
    slot_id: UUID,
    count: int = 3,
) -> list[AvailabilitySlot]:
    """Return up to `count` nearby slots — same doctor first, then same specialty."""
    target_stmt = (
        select(AvailabilitySlot)
        .options(selectinload(AvailabilitySlot.doctor))
        .where(AvailabilitySlot.id == slot_id)
    )
    target = (await db.execute(target_stmt)).scalar_one_or_none()
    if target is None:
        return []

    now = _utcnow()

    same_doctor_stmt = (
        select(AvailabilitySlot)
        .options(selectinload(AvailabilitySlot.doctor))
        .where(AvailabilitySlot.doctor_id == target.doctor_id)
        .where(AvailabilitySlot.id != slot_id)
        .where(AvailabilitySlot.is_booked.is_(False))
        .where(AvailabilitySlot.start_time > now)
    )
    candidates = list((await db.execute(same_doctor_stmt)).scalars().all())

    if len(candidates) < count:
        specialty_stmt = (
            select(AvailabilitySlot)
            .options(selectinload(AvailabilitySlot.doctor))
            .join(Doctor, AvailabilitySlot.doctor_id == Doctor.id)
            .where(Doctor.specialty == target.doctor.specialty)
            .where(AvailabilitySlot.doctor_id != target.doctor_id)
            .where(AvailabilitySlot.is_booked.is_(False))
            .where(AvailabilitySlot.start_time > now)
        )
        candidates.extend((await db.execute(specialty_stmt)).scalars().all())

    candidates.sort(
        key=lambda s: abs((s.start_time - target.start_time).total_seconds())
    )
    return candidates[:count]


# --- helpers ---------------------------------------------------------------


async def _lock_slot(db: AsyncSession, slot_id: UUID) -> AvailabilitySlot:
    stmt = (
        select(AvailabilitySlot)
        .where(AvailabilitySlot.id == slot_id)
        .with_for_update()
    )
    slot = (await db.execute(stmt)).scalar_one_or_none()
    if slot is None:
        raise SlotUnavailableError(f"slot {slot_id} not found")
    return slot


def _assert_slot_bookable(slot: AvailabilitySlot) -> None:
    if slot.start_time <= _utcnow():
        raise PastTimeError(f"slot {slot.id} starts in the past")
    if slot.is_booked:
        raise SlotUnavailableError(f"slot {slot.id} is already booked")


async def _has_active_appointment(db: AsyncSession, slot_id: UUID) -> bool:
    stmt = (
        select(Appointment.id)
        .where(Appointment.slot_id == slot_id)
        .where(Appointment.status != AppointmentStatus.cancelled)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None
